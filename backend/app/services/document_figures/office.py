"""Immagini e testo dei documenti Office (DOCX e PPTX).

Solo zipfile + XML della libreria standard (niente python-pptx):

- DOCX: immagini del corpo nell'ordine in cui compaiono, con la didascalia
  del paragrafo adiacente se ha un'etichetta di figura; le immagini di
  testate e piè di pagina (loghi) non sono nel corpo e restano fuori;
- PPTX: slide nell'ordine di `presentation.xml`, immagini (`p:pic`) di
  ogni slide con la slide come «pagina», didascalia da un paragrafo della
  slide con un'etichetta di figura, contesto dal testo della slide; le
  immagini dei layout e dei master (loghi, sfondi) restano fuori.

Formati vettoriali Office (EMF/WMF) → `unsupported_image_format`.

Con la geometria (ritaglio v2, `geometry=True`): ritaglio `srcRect`,
ribaltamenti e rotazioni a quarti di giro del riquadro applicati come li
mostra Office; dal riquadro in EMU (`wp:extent`, `a:ext`, scalato dai
gruppi) si ricavano il ppi nativo e la misura naturale normalizzata alla
pagina (`sectPr/pgSz` del DOCX, `sldSz` del PPTX). Orientamento EXIF
applicato; i byte escono sempre dal codificatore del figlio.

Anti-bomba: il testo PPTX gira anche nel processo principale (riassunto),
quindi ogni parte si legge solo se la sua dimensione dichiarata nello zip
sta sotto un tetto (zipfile non decomprime mai oltre `file_size`), il
pacchetto intero ha un tetto e le slide ripetute in `presentation.xml`
contano una volta sola.
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET
from xml.parsers import expat

from PIL import Image, ImageOps

from app.services.document_figures.captions import clip_caption, is_figure_caption
from app.services.document_figures.cropper import on_white

_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
}
_EMBED = f"{{{_NS['r']}}}embed"
_RASTER_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
_LOSSY_EXT = {".jpg", ".jpeg"}
EMU_PER_INCH = 914_400
EMU_PER_MM = 36_000
EMU_PER_PT = 12_700
# `a:srcRect` e i valori percentuali di DrawingML sono in millesimi di punto
# percentuale (100000 = 100%); `rot` in 60000-esimi di grado.
_PCT = 100_000
_DEG = 60_000
# Tetti anti-bomba: dimensione di un'immagine incorporata e numero di pixel.
MAX_MEDIA_BYTES = 40 * 1024 * 1024
MAX_MEDIA_PIXELS = 60_000_000
# Parti XML (slide, relazioni, presentation.xml) e corpo del DOCX.
MAX_XML_PART_BYTES = 16 * 1024 * 1024
MAX_DOCX_BODY_BYTES = 64 * 1024 * 1024
# Pacchetto intero (somma delle dimensioni decompresse) e numero di membri.
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_PACKAGE_MEMBERS = 20_000
# Testo PPTX restituito al riassunto (come `course_document_max_chars_hard`).
MAX_TEXT_CHARS = 2_000_000


class OfficeFormatError(RuntimeError):
    """Pacchetto Office illeggibile (zip o XML corrotti)."""


@dataclass
class OfficeImage:
    order: int
    media_name: str
    image: Image.Image | None
    reject_reason: str | None
    caption: str | None
    context: str | None
    # Numero di slide per i PPTX; None per i DOCX.
    page: int | None = None
    # Ritaglio v2 (`geometry=True`): ppi nativo e misura naturale (mm,
    # normalizzata alla pagina); None se il riquadro non è noto.
    native_ppi: float | None = None
    natural_width_mm: float | None = None
    source_lossy: bool = False


@dataclass(frozen=True)
class Placement:
    """Come il documento mostra un'immagine incorporata."""

    # Riquadro (non ruotato) in EMU.
    cx: int | None
    cy: int | None
    # Ritaglio in frazioni (sinistra, alto, destra, basso); negativi = 0.
    src_rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    flip_h: bool = False
    flip_v: bool = False
    # Rotazione oraria in quarti di giro; None se non è multipla di 90°.
    quarter_turns: int | None = 0


def _emu(value: str | None) -> int | None:
    try:
        number = int(value) if value is not None else None
    except ValueError:
        return None
    return number if number is not None and number > 0 else None


def _fraction(value: str | None) -> float:
    try:
        return max(0.0, min(1.0, int(value or 0) / _PCT))
    except ValueError:
        return 0.0


def _placement(
    blip_fill: ET.Element | None,
    xfrm: ET.Element | None,
    extent: tuple[int | None, int | None] | None,
    scale: tuple[float, float] = (1.0, 1.0),
) -> Placement:
    src = blip_fill.find("a:srcRect", _NS) if blip_fill is not None else None
    src_rect = (
        (
            _fraction(src.get("l")),
            _fraction(src.get("t")),
            _fraction(src.get("r")),
            _fraction(src.get("b")),
        )
        if src is not None
        else (0.0, 0.0, 0.0, 0.0)
    )
    cx = cy = None
    turns: int | None = 0
    flip_h = flip_v = False
    if xfrm is not None:
        ext = xfrm.find("a:ext", _NS)
        if ext is not None:
            cx, cy = _emu(ext.get("cx")), _emu(ext.get("cy"))
        flip_h = xfrm.get("flipH") in ("1", "true")
        flip_v = xfrm.get("flipV") in ("1", "true")
        try:
            rot = int(xfrm.get("rot") or 0) % (360 * _DEG)
        except ValueError:
            rot = 0
        turns = rot // (90 * _DEG) if rot % (90 * _DEG) == 0 else None
    if extent is not None and extent[0] and extent[1]:
        cx, cy = extent
    if cx is not None and cy is not None:
        cx, cy = int(cx * scale[0]), int(cy * scale[1])
    return Placement(
        cx=cx, cy=cy, src_rect=src_rect, flip_h=flip_h, flip_v=flip_v, quarter_turns=turns
    )


def apply_placement(
    image: Image.Image, placement: Placement, *, page_w_pt: float | None
) -> tuple[Image.Image, float | None, float | None]:
    """(immagine come la mostra il documento, ppi nativo, misura naturale
    in mm normalizzata alla pagina)."""
    from app.services.source_figure_resolution import page_factor

    left, top, right, bottom = placement.src_rect
    width, height = image.size
    box = (
        round(width * left),
        round(height * top),
        round(width * (1.0 - right)),
        round(height * (1.0 - bottom)),
    )
    if box[2] - box[0] >= 1 and box[3] - box[1] >= 1 and box != (0, 0, width, height):
        image = image.crop(box)
    shown_w_px = image.width
    if placement.flip_h:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if placement.flip_v:
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    turns = placement.quarter_turns or 0
    if turns == 1:
        image = image.transpose(Image.Transpose.ROTATE_270)
    elif turns == 2:
        image = image.transpose(Image.Transpose.ROTATE_180)
    elif turns == 3:
        image = image.transpose(Image.Transpose.ROTATE_90)
    if not placement.cx or not placement.cy:
        return image, None, None
    native_ppi = shown_w_px / (placement.cx / EMU_PER_INCH)
    visual_emu = placement.cy if turns in (1, 3) else placement.cx
    natural = visual_emu / EMU_PER_MM * page_factor(page_w_pt)
    return image, round(native_ppi, 2), round(natural, 2)


def package_problem(zf: zipfile.ZipFile) -> str | None:
    """Motivo per cui il pacchetto è sospetto (bomba zip), o None."""
    infos = zf.infolist()
    if len(infos) > MAX_PACKAGE_MEMBERS:
        return f"troppi membri nel pacchetto ({len(infos)})"
    total = sum(info.file_size for info in infos)
    if total > MAX_PACKAGE_BYTES:
        return f"pacchetto troppo grande una volta decompresso ({total} byte)"
    return None


def _read_part(zf: zipfile.ZipFile, name: str, limit: int | None = None) -> bytes:
    """Legge una parte solo se la dimensione dichiarata sta sotto il tetto
    (di default `MAX_XML_PART_BYTES`, letto al momento della chiamata)."""
    limit = MAX_XML_PART_BYTES if limit is None else limit
    try:
        info = zf.getinfo(name)
    except KeyError as exc:
        raise OfficeFormatError(f"parte assente: {name}") from exc
    if info.file_size > limit:
        raise OfficeFormatError(f"parte troppo grande: {name} ({info.file_size} byte)")
    return zf.read(info)


class _DtdRefusedError(Exception):
    pass


def _refuse_dtd(*_args: object) -> None:
    raise _DtdRefusedError


def safe_fromstring(data: bytes) -> ET.Element:
    """`ET.fromstring` senza DTD. Office Open XML non ne usa mai; una
    dichiarazione `<!DOCTYPE>` o `<!ENTITY>` serve solo a espandere
    entità: 17 KB di PPTX diventavano ~3 GB di RAM nel processo principale
    (Fase D). Un primo passaggio con expat si ferma al primo DOCTYPE, poi
    si costruisce l'albero."""
    probe = expat.ParserCreate()
    probe.StartDoctypeDeclHandler = _refuse_dtd
    probe.EntityDeclHandler = _refuse_dtd
    try:
        probe.Parse(data, True)
    except _DtdRefusedError as exc:
        raise OfficeFormatError("XML con DTD o entità: non ammesso") from exc
    except expat.ExpatError as exc:
        raise OfficeFormatError(str(exc)) from exc
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise OfficeFormatError(str(exc)) from exc


def _refuse_dtd_in(data: bytes) -> None:
    probe = expat.ParserCreate()
    probe.StartDoctypeDeclHandler = _refuse_dtd
    probe.EntityDeclHandler = _refuse_dtd
    try:
        probe.Parse(data, True)
    except _DtdRefusedError as exc:
        raise OfficeFormatError("XML con DTD o entità: non ammesso") from exc
    except expat.ExpatError:
        return  # malformato: lo rifiuterà chi lo legge davvero


def assert_safe_package(path: str) -> None:
    """Pacchetto zip Office letto da librerie di terzi (`docx2txt` per i
    `.doc`): niente bomba zip e nessuna parte XML con DTD, prima di passarlo
    (Fase D: un «.doc» di 1 KB diventava 43 MB di testo)."""
    with _open_zip(path) as zf:
        for info in zf.infolist():
            if info.filename.endswith((".xml", ".rels")):
                if info.file_size > MAX_DOCX_BODY_BYTES:
                    raise OfficeFormatError(f"parte troppo grande: {info.filename}")
                _refuse_dtd_in(zf.read(info))


def _parse_part(zf: zipfile.ZipFile, name: str, limit: int | None = None) -> ET.Element:
    return safe_fromstring(_read_part(zf, name, limit))


def _resolve_target(folder: str, target: str) -> str:
    # Un Target assoluto («/ppt/slides/slide1.xml») parte dalla radice del
    # pacchetto; uno relativo dalla cartella della parte.
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(folder, target))


def _relationships(zf: zipfile.ZipFile, part: str) -> dict[str, str]:
    folder, name = posixpath.split(part)
    rels_path = posixpath.join(folder, "_rels", f"{name}.rels")
    if rels_path not in zf.namelist():
        return {}
    root = _parse_part(zf, rels_path)
    out = {}
    for rel in root.findall("rel:Relationship", _NS):
        target = rel.get("Target") or ""
        if rel.get("TargetMode") == "External":
            continue
        out[rel.get("Id") or ""] = _resolve_target(folder, target)
    return out


def _paragraph_text(paragraph: ET.Element) -> str:
    return "".join(t.text or "" for t in paragraph.iter(f"{{{_NS['w']}}}t")).strip()


def _open_image(
    zf: zipfile.ZipFile, media: str, *, exif: bool = False
) -> tuple[Image.Image | None, str | None]:
    ext = posixpath.splitext(media)[1].lower()
    if ext not in _RASTER_EXT:
        return None, "unsupported_image_format"
    info = zf.getinfo(media)
    if info.file_size > MAX_MEDIA_BYTES:
        return None, "too_large"
    try:
        opened = Image.open(io.BytesIO(zf.read(info)))
        if opened.width * opened.height > MAX_MEDIA_PIXELS:
            return None, "too_large"
        opened.load()
        image: Image.Image = opened
        if exif:
            image = ImageOps.exif_transpose(opened) or opened
    except Exception:
        return None, "unsupported_image_format"
    return on_white(image), None


def _with_geometry(
    item: OfficeImage, placement: Placement | None, page_w_pt: float | None
) -> OfficeImage:
    item.source_lossy = posixpath.splitext(item.media_name)[1].lower() in _LOSSY_EXT
    if item.image is None or placement is None:
        return item
    item.image, item.native_ppi, item.natural_width_mm = apply_placement(
        item.image, placement, page_w_pt=page_w_pt
    )
    return item


def _docx_caption_index(texts: list[str], has_image: list[bool], index: int) -> int | None:
    """Paragrafo della didascalia di un'immagine: di norma quello dopo; se
    però quello dopo introduce un'altra immagine (didascalie SOPRA le
    figure: «Fig. 1», img 1, «Fig. 2», img 2) e c'è una didascalia prima,
    vale quella prima."""
    after = index + 1 if index + 1 < len(texts) and is_figure_caption(texts[index + 1]) else None
    before = index - 1 if index >= 1 and is_figure_caption(texts[index - 1]) else None
    if after is not None and before is not None and after + 1 < len(texts) and has_image[after + 1]:
        return before
    return after if after is not None else before


def _docx_page_width_pt(body: ET.Element) -> float | None:
    """Larghezza della pagina (pt) dall'ultima `w:sectPr/w:pgSz` del corpo."""
    sizes = list(body.iter(f"{{{_NS['w']}}}pgSz"))
    if not sizes:
        return None
    try:
        twips = int(sizes[-1].get(f"{{{_NS['w']}}}w") or 0)
    except ValueError:
        return None
    return twips / 20.0 if twips > 0 else None


def _docx_placements(paragraph: ET.Element) -> dict[int, Placement]:
    """{id(a:blip) → Placement} delle immagini `pic:pic` del paragrafo."""
    out: dict[int, Placement] = {}
    for drawing in paragraph.iter(f"{{{_NS['w']}}}drawing"):
        pics = list(drawing.iter(f"{{{_NS['pic']}}}pic"))
        extent_el = next(drawing.iter(f"{{{_NS['wp']}}}extent"), None)
        extent = (
            (_emu(extent_el.get("cx")), _emu(extent_el.get("cy")))
            if extent_el is not None and len(pics) == 1
            else None
        )
        for pic in pics:
            blip_fill = pic.find("pic:blipFill", _NS)
            blip = blip_fill.find("a:blip", _NS) if blip_fill is not None else None
            if blip is None:
                continue
            xfrm = pic.find("pic:spPr/a:xfrm", _NS)
            out[id(blip)] = _placement(blip_fill, xfrm, extent)
    return out


def docx_images(path: str, *, geometry: bool = False) -> list[OfficeImage]:
    with _open_zip(path) as zf:
        body = _parse_part(zf, "word/document.xml", MAX_DOCX_BODY_BYTES)
        rels = _relationships(zf, "word/document.xml")
        page_w_pt = _docx_page_width_pt(body) if geometry else None
        paragraphs = list(body.iter(f"{{{_NS['w']}}}p"))
        texts = [_paragraph_text(p) for p in paragraphs]
        has_image = [next(p.iter(f"{{{_NS['a']}}}blip"), None) is not None for p in paragraphs]
        names = set(zf.namelist())
        out: list[OfficeImage] = []
        seen: set[str] = set()
        for index, paragraph in enumerate(paragraphs):
            placements = _docx_placements(paragraph) if geometry else {}
            for blip in paragraph.iter(f"{{{_NS['a']}}}blip"):
                media = rels.get(blip.get(_EMBED) or "")
                if not media or media in seen or media not in names:
                    continue
                seen.add(media)
                caption_at = _docx_caption_index(texts, has_image, index)
                caption = clip_caption(texts[caption_at]) if caption_at is not None else None
                context_parts = [
                    t
                    for t in texts[max(0, index - 2) : index + 3]
                    if t and (caption is None or t not in caption)
                ]
                image, reason = _open_image(zf, media, exif=geometry)
                item = OfficeImage(
                    order=len(out) + 1,
                    media_name=media,
                    image=image,
                    reject_reason=reason,
                    caption=caption,
                    context=" ".join(context_parts)[:700] or None,
                )
                if geometry:
                    item = _with_geometry(item, placements.get(id(blip)), page_w_pt)
                out.append(item)
        return out


# --- PPTX ---------------------------------------------------------------------


def _open_zip(path: str) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise OfficeFormatError(str(exc)) from exc
    problem = package_problem(zf)
    if problem is not None:
        zf.close()
        raise OfficeFormatError(problem)
    return zf


def _pptx_slides(zf: zipfile.ZipFile) -> list[str]:
    """Parti delle slide nell'ordine della presentazione (ognuna una volta)."""
    root = _parse_part(zf, "ppt/presentation.xml")
    rels = _relationships(zf, "ppt/presentation.xml")
    names = set(zf.namelist())
    slides: list[str] = []
    for slide_id in root.iter(f"{{{_NS['p']}}}sldId"):
        target = rels.get(slide_id.get(f"{{{_NS['r']}}}id") or "")
        if target and target in names and target not in slides:
            slides.append(target)
    return slides


def _slide_paragraphs(root: ET.Element) -> list[str]:
    out = []
    for paragraph in root.iter(f"{{{_NS['a']}}}p"):
        text = "".join(t.text or "" for t in paragraph.iter(f"{{{_NS['a']}}}t")).strip()
        if text:
            out.append(text)
    return out


def pptx_slide_count(path: str) -> int:
    with _open_zip(path) as zf:
        return len(_pptx_slides(zf))


def pptx_text(path: str) -> str:
    """Testo delle slide (una slide per blocco), per il riassunto."""
    with _open_zip(path) as zf:
        blocks = []
        total = 0
        for part in _pptx_slides(zf):
            paragraphs = _slide_paragraphs(_parse_part(zf, part))
            if paragraphs:
                block = "\n".join(paragraphs)
                blocks.append(block)
                total += len(block) + 2
                if total >= MAX_TEXT_CHARS:
                    break
        return "\n\n".join(blocks)[:MAX_TEXT_CHARS]


def _pptx_slide_width_pt(zf: zipfile.ZipFile) -> float | None:
    size = _parse_part(zf, "ppt/presentation.xml").find("p:sldSz", _NS)
    cx = _emu(size.get("cx")) if size is not None else None
    return cx / EMU_PER_PT if cx else None


def _group_scales(root: ET.Element) -> dict[int, tuple[float, float]]:
    """{id(p:pic) → scala dei gruppi che la contengono}: le coordinate di
    una figura in un gruppo sono quelle «figlie» (`chExt`), scalate
    dall'estensione del gruppo (`ext`)."""
    out: dict[int, tuple[float, float]] = {}

    def walk(node: ET.Element, scale: tuple[float, float]) -> None:
        for child in node:
            if child.tag == f"{{{_NS['p']}}}grpSp":
                xfrm = child.find("p:grpSpPr/a:xfrm", _NS)
                sx, sy = scale
                if xfrm is not None:
                    ext, ch_ext = xfrm.find("a:ext", _NS), xfrm.find("a:chExt", _NS)
                    if ext is not None and ch_ext is not None:
                        ex, ey = _emu(ext.get("cx")), _emu(ext.get("cy"))
                        cx, cy = _emu(ch_ext.get("cx")), _emu(ch_ext.get("cy"))
                        if ex and ey and cx and cy:
                            sx, sy = sx * ex / cx, sy * ey / cy
                walk(child, (sx, sy))
            elif child.tag == f"{{{_NS['p']}}}pic":
                out[id(child)] = scale
            else:
                walk(child, scale)

    walk(root, (1.0, 1.0))
    return out


def pptx_images(path: str, *, geometry: bool = False) -> list[OfficeImage]:
    with _open_zip(path) as zf:
        out: list[OfficeImage] = []
        page_w_pt = _pptx_slide_width_pt(zf) if geometry else None
        for slide_no, part in enumerate(_pptx_slides(zf), start=1):
            root = _parse_part(zf, part)
            rels = _relationships(zf, part)
            scales = _group_scales(root) if geometry else {}
            paragraphs = _slide_paragraphs(root)
            caption = next((clip_caption(p) for p in paragraphs if is_figure_caption(p)), None)
            context = " ".join(p for p in paragraphs if caption is None or p not in caption)
            seen: set[str] = set()
            for picture in root.iter(f"{{{_NS['p']}}}pic"):
                blip = next(picture.iter(f"{{{_NS['a']}}}blip"), None)
                media = rels.get(blip.get(_EMBED) or "") if blip is not None else None
                if not media or media in seen or media not in zf.namelist():
                    continue
                seen.add(media)
                image, reason = _open_image(zf, media, exif=geometry)
                item = OfficeImage(
                    order=len(seen),
                    media_name=media,
                    image=image,
                    reject_reason=reason,
                    caption=caption,
                    context=context[:700] or None,
                    page=slide_no,
                )
                if geometry:
                    placement = _placement(
                        picture.find("p:blipFill", _NS),
                        picture.find("p:spPr/a:xfrm", _NS),
                        None,
                        scales.get(id(picture), (1.0, 1.0)),
                    )
                    item = _with_geometry(item, placement, page_w_pt)
                out.append(item)
        return out
