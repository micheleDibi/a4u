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

from PIL import Image

from app.services.document_figures.captions import clip_caption, is_figure_caption
from app.services.document_figures.cropper import on_white

_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_EMBED = f"{{{_NS['r']}}}embed"
_RASTER_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
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


def _open_image(zf: zipfile.ZipFile, media: str) -> tuple[Image.Image | None, str | None]:
    ext = posixpath.splitext(media)[1].lower()
    if ext not in _RASTER_EXT:
        return None, "unsupported_image_format"
    info = zf.getinfo(media)
    if info.file_size > MAX_MEDIA_BYTES:
        return None, "too_large"
    try:
        image = Image.open(io.BytesIO(zf.read(info)))
        if image.width * image.height > MAX_MEDIA_PIXELS:
            return None, "too_large"
        image.load()
    except Exception:
        return None, "unsupported_image_format"
    return on_white(image), None


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


def docx_images(path: str) -> list[OfficeImage]:
    with _open_zip(path) as zf:
        body = _parse_part(zf, "word/document.xml", MAX_DOCX_BODY_BYTES)
        rels = _relationships(zf, "word/document.xml")
        paragraphs = list(body.iter(f"{{{_NS['w']}}}p"))
        texts = [_paragraph_text(p) for p in paragraphs]
        has_image = [next(p.iter(f"{{{_NS['a']}}}blip"), None) is not None for p in paragraphs]
        names = set(zf.namelist())
        out: list[OfficeImage] = []
        seen: set[str] = set()
        for index, paragraph in enumerate(paragraphs):
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
                image, reason = _open_image(zf, media)
                out.append(
                    OfficeImage(
                        order=len(out) + 1,
                        media_name=media,
                        image=image,
                        reject_reason=reason,
                        caption=caption,
                        context=" ".join(context_parts)[:700] or None,
                    )
                )
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


def pptx_images(path: str) -> list[OfficeImage]:
    with _open_zip(path) as zf:
        out: list[OfficeImage] = []
        for slide_no, part in enumerate(_pptx_slides(zf), start=1):
            root = _parse_part(zf, part)
            rels = _relationships(zf, part)
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
                image, reason = _open_image(zf, media)
                out.append(
                    OfficeImage(
                        order=len(seen),
                        media_name=media,
                        image=image,
                        reject_reason=reason,
                        caption=caption,
                        context=context[:700] or None,
                        page=slide_no,
                    )
                )
        return out
