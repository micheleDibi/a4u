"""Ritaglio del bbox di una figura con PDFium (pypdfium2, BSD/Apache).

Ritaglio v1 (`render_crop`, crop_version 1, dietro
FIGURE_EXTRACTION_NATIVE_CROP_ENABLED=false): i vettoriali si rendono fra
300 e 600 dpi puntando a 2400 px sul lato lungo, i raster al loro ppi
nativo limitato a [150, 300]; tetto di 12 megapixel. Codifica: JPEG q90
per le foto, PNG (scala di grigi, palette o RGB ottimizzato) per il resto.

Ritaglio v2 (`render_native_crop`, crop_version 2, doc 18 §22): il raster
dominante si rende sulla SUA griglia di pixel (bit-identico all'oggetto
nativo), con i tratti vettoriali sopra a k volte il nativo; niente
pavimento a 150 dpi (un raster a 76 ppi resta a 76 ppi, e la classe di
risoluzione lo dice) né soffitto a 300; PNG senza perdita per il tratto,
JPEG q95 4:4:4 solo per le foto da sorgente con perdita molto pesanti.
"""

from __future__ import annotations

import ctypes
import io
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from app.services.document_figures.geometry import BBox
from app.services.document_figures.regions import (
    PageRegions,
    RasterRegion,
    overlay_marks,
    union_coverage,
)

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


def on_white(image: Image.Image) -> Image.Image:
    """RGB con le zone trasparenti su fondo bianco. `convert("RGB")` scarta
    l'alfa: i PNG trasparenti (rendering di Commons, immagini di DOCX e
    PPTX) diventavano neri, con il testo nero invisibile."""
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if not has_alpha:
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, rgba).convert("RGB")


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


# --- Ritaglio v2: griglia nativa e codifica per contenuto (doc 18 §22) -------

# Correzione verso l'interno (px): i bordi dell'immagine cadono a n + δ e a
# n + W − δ, così l'arrotondamento del rettangolo di destinazione di PDFium
# non aggiunge né toglie una riga (giuria Q4: senza δ esatto in 4 casi su
# 14, con δ bit-identico all'oggetto nativo).
ALIGN_DELTA_PX = 0.01
# Quota del bbox (unione dei raster) oltre la quale la figura è raster.
RASTER_SHARE_MIN = 0.5
# Tratti vettoriali sopra un raster (etichette, frecce): render a k volte il
# nativo, con k intero, fino ad almeno questi dpi.
MIXED_MIN_DPI = 300
MIXED_MAX_FACTOR = 8
# Pixel per asse di un raster «quadrato» (ppi_x ≈ ppi_y): sopra, la griglia
# nativa distorcerebbe i tratti vettoriali.
ANISOTROPY_TOL = 0.005
# Anti-bomba: nessun bitmap di render oltre questo numero di pixel.
MAX_RENDER_PIXELS = 60_000_000
# JPEG solo per foto da sorgente con perdita, con PNG oltre il tetto.
JPEG_QUALITY_V2 = 95
CROP_MODES = ("raster_native", "mixed", "vector")


@dataclass(frozen=True)
class CropPlan:
    mode: str
    # Pixel per punto lungo X e Y della pagina.
    scale_x: float
    scale_y: float
    # Raster alla cui griglia allinearsi (None = render normale).
    anchor: RasterRegion | None
    factor: int
    native_ppi: float | None
    source_lossy: bool


@dataclass(frozen=True)
class NativeCrop:
    data: bytes
    mime: str
    width: int
    height: int
    # dpi del render (px per pollice della pagina), come per la v1.
    dpi: int
    is_vector: bool
    image: Image.Image
    mode: str
    native_ppi: float | None
    natural_width_mm: float
    aligned: bool
    source_lossy: bool


def plan_crop(box: BBox, regions: PageRegions) -> CropPlan:
    """Come rendere il bbox: raster nativo, misto o vettoriale."""
    rasters = [r for r in regions.rasters if r.rect.intersection_area(box) > 0]
    share = union_coverage(box, [r.rect for r in rasters])
    if share < RASTER_SHARE_MIN:
        dpi = choose_dpi(box, is_vector=True, native_ppi=None)
        return CropPlan("vector", dpi / 72.0, dpi / 72.0, None, 1, None, False)
    dominant = max(rasters, key=lambda r: r.rect.intersection_area(box))
    overlay = regions.truncated or bool(overlay_marks(box, regions))
    factor = 1
    if overlay:
        factor = math.ceil(MIXED_MIN_DPI / max(dominant.ppi, 1e-6) - 1e-9)
        factor = max(1, min(MIXED_MAX_FACTOR, factor))
    square = abs(dominant.ppi_x / dominant.ppi_y - 1.0) <= ANISOTROPY_TOL
    # Tetto dei pixel: prima si riduce il fattore dei misti.
    while factor > 1 and _pixels(box, factor * dominant.ppi_x, factor * dominant.ppi_y) > (
        MAX_PIXELS
    ):
        factor -= 1
    anchor = dominant if dominant.axis_aligned and square else None
    if anchor is not None:
        scale_x = factor * dominant.ppi_x / 72.0
        scale_y = factor * dominant.ppi_y / 72.0
    else:
        scale_x = scale_y = factor * max(dominant.ppi_x, dominant.ppi_y) / 72.0
    return CropPlan(
        "mixed" if overlay else "raster_native",
        scale_x,
        scale_y,
        anchor,
        factor,
        dominant.ppi,
        dominant.lossy,
    )


def _pixels(box: BBox, dpi_x: float, dpi_y: float) -> float:
    return (box.width / 72.0 * dpi_x) * (box.height / 72.0 * dpi_y)


def render_aligned(page: Any, box: BBox, anchor: RasterRegion, *, factor: int = 1) -> Image.Image:
    """Rende `box` (spazio di visualizzazione) con la griglia dei pixel
    allineata a quella di `anchor`: ogni pixel dell'immagine nativa diventa
    esattamente `factor × factor` pixel, campionati senza interpolazione
    (`FPDF_RENDER_NO_SMOOTHIMAGE`). Maschere, CMYK, JPX e clip restano
    quelli di PDFium."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    delta = ALIGN_DELTA_PX
    rect = anchor.rect
    sx = (factor * anchor.px_x - 2 * delta) / rect.width
    sy = (factor * anchor.px_y - 2 * delta) / rect.height
    x_img = round((rect.x0 - box.x0) * sx)
    y_img = round((rect.top - box.top) * sy)
    tx = x_img + delta - sx * rect.x0
    ty = y_img + delta - sy * rect.top
    out_w = max(1, math.ceil(sx * box.x1 + tx - 1e-9))
    out_h = max(1, math.ceil(sy * box.bottom + ty - 1e-9))
    if out_w * out_h > MAX_RENDER_PIXELS:
        raise ValueError(f"bitmap di {out_w}×{out_h} oltre il tetto anti-bomba")
    bitmap = pdfium.PdfBitmap.new_native(out_w, out_h, raw.FPDFBitmap_BGRx, rev_byteorder=True)
    bitmap.fill_rect((255, 255, 255, 255), 0, 0, out_w, out_h)
    matrix = raw.FS_MATRIX(sx, 0.0, 0.0, sy, tx, ty)
    clip = raw.FS_RECTF(0.0, 0.0, float(out_w), float(out_h))
    flags = raw.FPDF_ANNOT | raw.FPDF_RENDER_NO_SMOOTHIMAGE | raw.FPDF_REVERSE_BYTE_ORDER
    raw.FPDF_RenderPageBitmapWithMatrix(
        bitmap, page, ctypes.byref(matrix), ctypes.byref(clip), flags
    )
    return bitmap.to_pil().convert("RGB")


def _render_scaled(page: Any, box: BBox, *, page_w: float, page_h: float, scale: float) -> Any:
    crop = (box.x0, page_h - box.bottom, page_w - box.x1, box.top)
    bitmap = page.render(scale=scale, crop=crop, fill_color=(255, 255, 255, 255))
    return bitmap.to_pil().convert("RGB")


def render_v1_at(page: Any, bbox: BBox, *, page_w: float, page_h: float, dpi: float) -> Image.Image:
    """Il ritaglio v1 riprodotto ai dpi salvati (verifica d'identità del
    ri-ritaglio: stesso bbox, stesso margine, stesso render)."""
    box = bbox.expand(CROP_MARGIN_PT).clamp(page_w, page_h)
    image: Image.Image = _render_scaled(
        page, box, page_w=page_w, page_h=page_h, scale=float(dpi) / 72.0
    )
    return image


def render_native_crop(
    page: Any,
    bbox: BBox,
    regions: PageRegions,
    *,
    page_w: float,
    page_h: float,
) -> NativeCrop:
    """Ritaglio v2 del bbox (più il margine): griglia nativa per i raster,
    k volte il nativo con i tratti vettoriali sopra, dpi vettoriali
    altrimenti; niente pavimento a 150 né soffitto a 300 dpi; tetto di 12
    megapixel (riduzione LANCZOS dopo il render nativo)."""
    box = bbox.expand(CROP_MARGIN_PT).clamp(page_w, page_h)
    plan = plan_crop(box, regions)
    aligned = False
    if plan.anchor is not None and _pixels(box, plan.scale_x * 72, plan.scale_y * 72) <= (
        MAX_RENDER_PIXELS
    ):
        image = render_aligned(page, box, plan.anchor, factor=plan.factor)
        aligned = True
        scale = plan.scale_x
    else:
        scale = plan.scale_x
        pixels = _pixels(box, scale * 72, scale * 72)
        if pixels > MAX_RENDER_PIXELS:
            scale *= (MAX_PIXELS / pixels) ** 0.5
        image = _render_scaled(page, box, page_w=page_w, page_h=page_h, scale=scale)
    if image.width * image.height > MAX_PIXELS:
        ratio = (MAX_PIXELS / (image.width * image.height)) ** 0.5
        size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
        image = image.resize(size, Image.Resampling.LANCZOS)
        scale *= ratio
        aligned = False
    data, mime = encode_figure(image, source_lossy=plan.source_lossy)
    from app.services.source_figure_resolution import natural_width_from_bbox

    return NativeCrop(
        data=data,
        mime=mime,
        width=image.width,
        height=image.height,
        # dpi dalla scala del render (esatta), non dai pixel del bitmap, che
        # comprendono il margine arrotondato.
        dpi=max(1, min(32767, round(scale * 72.0))),
        is_vector=plan.mode == "vector",
        image=image,
        mode=plan.mode,
        native_ppi=round(plan.native_ppi, 2) if plan.native_ppi else None,
        natural_width_mm=round(natural_width_from_bbox(box.width, page_w), 2),
        aligned=aligned,
        source_lossy=plan.source_lossy,
    )


def is_line_art(image: Image.Image) -> bool:
    """Tratto (schemi, grafici, testo, scansioni di disegni) contro foto:
    su una miniatura, un colore di fondo dominante e pochi colori distinti
    (a 5 bit per canale). Un tratto non esce mai in JPEG."""
    small = image.convert("RGB")
    small.thumbnail((256, 256))
    arr = np.asarray(small, dtype=np.uint16) >> 3
    codes = (arr[..., 0] << 10) | (arr[..., 1] << 5) | arr[..., 2]
    counts = np.bincount(codes.ravel(), minlength=1 << 15)
    top_share = float(counts.max()) / float(codes.size)
    distinct = int((counts > 0).sum())
    return top_share >= 0.25 and distinct <= 3000


def _png_lossless(rgb: Image.Image) -> bytes:
    """PNG senza perdita: scala di grigi se i tre canali coincidono, palette
    esatta se i colori sono al più 256, RGB altrimenti."""
    arr = np.asarray(rgb)
    buf = io.BytesIO()
    if np.array_equal(arr[..., 0], arr[..., 1]) and np.array_equal(arr[..., 1], arr[..., 2]):
        Image.fromarray(arr[..., 0], mode="L").save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    flat = arr.reshape(-1, 3)
    colors, index = np.unique(flat, axis=0, return_inverse=True)
    if len(colors) <= 256:
        paletted = Image.fromarray(index.reshape(arr.shape[:2]).astype(np.uint8), mode="P")
        paletted.putpalette(colors.astype(np.uint8).ravel().tolist())
        paletted.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    rgb.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def encode_figure(image: Image.Image, *, source_lossy: bool) -> tuple[bytes, str]:
    """Codifica del ritaglio v2 (ogni byte esce da qui: mai i byte della
    fonte così come sono). PNG senza perdita per il tratto e per le sorgenti
    senza perdita; JPEG q95 4:4:4 solo per una foto da sorgente con perdita
    il cui PNG supera `PNG_SOFT_LIMIT_BYTES`, e solo se più leggero."""
    rgb = on_white(image)
    png = _png_lossless(rgb)
    if len(png) <= PNG_SOFT_LIMIT_BYTES or not source_lossy or is_line_art(rgb):
        return png, "image/png"
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=JPEG_QUALITY_V2, subsampling=0, optimize=True)
    if buf.tell() < len(png):
        return buf.getvalue(), "image/jpeg"
    return png, "image/png"
