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
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from PIL import Image

from app.services.document_figures.captions import clip_caption, is_figure_caption

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


def _relationships(zf: zipfile.ZipFile, part: str) -> dict[str, str]:
    folder, name = posixpath.split(part)
    rels_path = posixpath.join(folder, "_rels", f"{name}.rels")
    if rels_path not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read(rels_path))
    out = {}
    for rel in root.findall("rel:Relationship", _NS):
        target = rel.get("Target") or ""
        if rel.get("TargetMode") == "External":
            continue
        out[rel.get("Id") or ""] = posixpath.normpath(posixpath.join(folder, target))
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
        image = Image.open(io.BytesIO(zf.read(media)))
        if image.width * image.height > MAX_MEDIA_PIXELS:
            return None, "too_large"
        image.load()
    except Exception:
        return None, "unsupported_image_format"
    return image.convert("RGB"), None


def docx_images(path: str) -> list[OfficeImage]:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise OfficeFormatError(str(exc)) from exc
    with zf:
        try:
            body = ET.fromstring(zf.read("word/document.xml"))
        except (KeyError, ET.ParseError) as exc:
            raise OfficeFormatError(str(exc)) from exc
        rels = _relationships(zf, "word/document.xml")
        paragraphs = list(body.iter(f"{{{_NS['w']}}}p"))
        texts = [_paragraph_text(p) for p in paragraphs]
        out: list[OfficeImage] = []
        seen: set[str] = set()
        for index, paragraph in enumerate(paragraphs):
            for blip in paragraph.iter(f"{{{_NS['a']}}}blip"):
                media = rels.get(blip.get(_EMBED) or "")
                if not media or media in seen or media not in zf.namelist():
                    continue
                seen.add(media)
                caption = None
                for neighbour in (index + 1, index - 1):
                    if 0 <= neighbour < len(texts) and is_figure_caption(texts[neighbour]):
                        caption = clip_caption(texts[neighbour])
                        break
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
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise OfficeFormatError(str(exc)) from exc


def _pptx_slides(zf: zipfile.ZipFile) -> list[str]:
    """Parti delle slide nell'ordine della presentazione."""
    try:
        root = ET.fromstring(zf.read("ppt/presentation.xml"))
    except (KeyError, ET.ParseError) as exc:
        raise OfficeFormatError(str(exc)) from exc
    rels = _relationships(zf, "ppt/presentation.xml")
    names = set(zf.namelist())
    slides = []
    for slide_id in root.iter(f"{{{_NS['p']}}}sldId"):
        target = rels.get(slide_id.get(f"{{{_NS['r']}}}id") or "")
        if target and target in names:
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
        for part in _pptx_slides(zf):
            try:
                paragraphs = _slide_paragraphs(ET.fromstring(zf.read(part)))
            except ET.ParseError as exc:
                raise OfficeFormatError(str(exc)) from exc
            if paragraphs:
                blocks.append("\n".join(paragraphs))
        return "\n\n".join(blocks)


def pptx_images(path: str) -> list[OfficeImage]:
    with _open_zip(path) as zf:
        out: list[OfficeImage] = []
        for slide_no, part in enumerate(_pptx_slides(zf), start=1):
            try:
                root = ET.fromstring(zf.read(part))
            except ET.ParseError as exc:
                raise OfficeFormatError(str(exc)) from exc
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
