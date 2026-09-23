"""Ritaglio del bbox di una figura con PDFium (pypdfium2, BSD/Apache).

Risoluzione adattiva (deviazione dichiarata D21 dal «200-300 dpi» del
brief): i vettoriali si rendono fra 300 e 600 dpi puntando a 2400 px sul
lato lungo, i raster al loro ppi nativo limitato a [150, 300] (niente
ingrandimenti finti); tetto di 12 megapixel. Codifica: JPEG q90 per le
foto, PNG (scala di grigi, palette o RGB ottimizzato) per il resto.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from app.services.document_figures.geometry import BBox

VECTOR_TARGET_LONG_SIDE_PX = 2400
VECTOR_DPI_MIN = 300
VECTOR_DPI_MAX = 600
RASTER_DPI_MIN = 150
RASTER_DPI_MAX = 300
MAX_PIXELS = 12_000_000
PNG_SOFT_LIMIT_BYTES = 1_500_000
PREVIEW_LONG_SIDE_PX = 768
# Margine attorno al bbox rilevato (i rilevatori tagliano spesso al tratto).
CROP_MARGIN_PT = 4.0
BLANK_STDDEV = 3.0


@dataclass(frozen=True)
class Crop:
    data: bytes
    mime: str
    width: int
    height: int
    dpi: int
    is_vector: bool
    image: Image.Image


def choose_dpi(bbox: BBox, *, is_vector: bool, native_ppi: float | None) -> int:
    long_side_in = max(bbox.width, bbox.height) / 72.0
    if long_side_in <= 0:
        return VECTOR_DPI_MIN
    if is_vector:
        dpi = VECTOR_TARGET_LONG_SIDE_PX / long_side_in
        dpi = min(VECTOR_DPI_MAX, max(VECTOR_DPI_MIN, dpi))
    else:
        dpi = native_ppi if native_ppi and native_ppi > 0 else RASTER_DPI_MAX
        dpi = min(RASTER_DPI_MAX, max(RASTER_DPI_MIN, dpi))
    pixels = (bbox.width / 72.0 * dpi) * (bbox.height / 72.0 * dpi)
    if pixels > MAX_PIXELS:
        dpi = dpi * (MAX_PIXELS / pixels) ** 0.5
    return max(1, int(dpi))


def is_blank(image: Image.Image) -> bool:
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    return float(gray.std()) < BLANK_STDDEV


def _encode(image: Image.Image, *, photo: bool) -> tuple[bytes, str]:
    rgb = image.convert("RGB")
    buf = io.BytesIO()
    if photo:
        rgb.save(buf, format="JPEG", quality=90, optimize=True)
        return buf.getvalue(), "image/jpeg"
    arr = np.asarray(rgb, dtype=np.int16)
    if (
        int(np.abs(arr[..., 0] - arr[..., 1]).max()) < 8
        and int(np.abs(arr[..., 1] - arr[..., 2]).max()) < 8
    ):
        rgb.convert("L").save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png"
    if rgb.getcolors(maxcolors=256) is not None:
        rgb.quantize(colors=256, dither=Image.Dither.NONE).save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png"
    rgb.save(buf, format="PNG", optimize=True)
    if buf.tell() > PNG_SOFT_LIMIT_BYTES:
        buf = io.BytesIO()
        rgb.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(
            buf, format="PNG", optimize=True
        )
    return buf.getvalue(), "image/png"


def render_crop(
    page: Any,
    bbox: BBox,
    *,
    page_w: float,
    page_h: float,
    is_vector: bool,
    native_ppi: float | None = None,
) -> Crop:
    """Rende il solo bbox (più un margine) della pagina `pypdfium2.PdfPage`."""
    box = bbox.expand(CROP_MARGIN_PT).clamp(page_w, page_h)
    dpi = choose_dpi(box, is_vector=is_vector, native_ppi=native_ppi)
    crop = (box.x0, page_h - box.bottom, page_w - box.x1, box.top)
    bitmap = page.render(scale=dpi / 72.0, crop=crop, fill_color=(255, 255, 255, 255))
    image = bitmap.to_pil().convert("RGB")
    data, mime = _encode(image, photo=not is_vector)
    return Crop(
        data=data,
        mime=mime,
        width=image.width,
        height=image.height,
        dpi=dpi,
        is_vector=is_vector,
        image=image,
    )


def encode_image(image: Image.Image, *, photo: bool) -> tuple[bytes, str]:
    """Codifica un'immagine già estratta (DOCX/PPTX) come i ritagli PDF."""
    return _encode(image, photo=photo)


def preview(image: Image.Image) -> bytes:
    """Anteprima JPEG (lato lungo 768 px) per l'editor e la Vision."""
    thumb = image.convert("RGB")
    thumb.thumbnail((PREVIEW_LONG_SIDE_PX, PREVIEW_LONG_SIDE_PX), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    thumb.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue()


def looks_like_photo(image: Image.Image) -> bool:
    """Molti colori distinti su una miniatura → foto (JPEG)."""
    small = image.convert("RGB").resize((96, 96))
    colors = small.getcolors(maxcolors=96 * 96)
    return colors is None or len(colors) > 2500
