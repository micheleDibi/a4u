"""Immagini incorporate nei documenti Office (DOCX; PPTX in WP2f).

Solo zipfile + XML della libreria standard (niente python-pptx): si
leggono le immagini nel corpo del documento nell'ordine in cui compaiono,
con la didascalia del paragrafo adiacente se ha un'etichetta di figura. Le
immagini di testate e piè di pagina (loghi) non sono nel corpo e restano
fuori. Formati vettoriali Office (EMF/WMF) → `unsupported_image_format`.
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
