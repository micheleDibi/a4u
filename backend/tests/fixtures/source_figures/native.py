"""PDF di prova con raster a ppi esatto per costruzione (ritaglio v2, §7.2).

Solo pypdfium2 (già dipendenza): ogni immagine ha una matrice nota, quindi
ppi nativo, rettangolo sulla pagina e orientamento sono verità di terreno.

- `raster_page(...)`: una pagina con un raster a un ppi dato, facoltativi
  etichette di testo e frecce vettoriali sopra (figura «mista»), testo
  invisibile (strato OCR di una scansione), un rettangolo bianco dietro,
  JPEG (DCT) o CMYK, ribaltamento, rotazione di 90° dell'immagine o della
  pagina, un Form XObject scalato;
- `noise_image(...)`: immagine deterministica con pixel tutti diversi fra
  vicini (ogni ricampionamento si vede);
- `line_art_image()` e `photo_image()`: tratto e foto per la codifica.

Coordinate restituite: spazio di visualizzazione (pt, origine in alto a
sinistra della pagina ruotata), come i bbox del rilevatore.
"""

from __future__ import annotations

import ctypes
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

PAGE_A4 = (595.0, 842.0)


@dataclass(frozen=True)
class BuiltPage:
    pdf: bytes
    # Rettangolo del raster nello spazio di visualizzazione (x0, top, x1, bottom).
    raster_rect: tuple[float, float, float, float]
    ppi: float


def noise_image(width: int = 131, height: int = 97, seed: int = 3) -> Image.Image:
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 256, (height, width, 3), dtype=np.uint8))


def line_art_image(width: int = 900, height: int = 600) -> Image.Image:
    """Schema a blocchi con testo e frecce su fondo bianco."""
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for i in range(4):
        x = 40 + i * 210
        draw.rectangle((x, 220, x + 160, 340), outline="black", width=4)
        draw.text((x + 20, 270), f"Blocco {i + 1}", fill="black")
        if i < 3:
            draw.line((x + 160, 280, x + 210, 280), fill="navy", width=4)
    draw.line((40, 500, 860, 500), fill="black", width=2)
    return image


def photo_image(width: int = 1400, height: int = 1000, seed: int = 11) -> Image.Image:
    """«Foto»: gradienti e rumore a tutta superficie, migliaia di colori."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:height, 0:width]
    base = np.stack(
        [
            (x * 255 / width),
            (y * 255 / height),
            ((x + y) * 127 / (width + height)) + 64,
        ],
        axis=-1,
    )
    noisy = base + rng.normal(0, 18, base.shape)
    return Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8))


def _text(pdf, page, text: str, x: float, y: float, size: float, *, invisible: bool) -> None:  # type: ignore[no-untyped-def]
    import pypdfium2.raw as raw

    font = raw.FPDFText_LoadStandardFont(pdf, b"Helvetica")
    obj = raw.FPDFPageObj_NewTextObj(pdf, b"Helvetica", ctypes.c_float(size))
    encoded = (text + "\0").encode("utf-16-le")
    buffer = ctypes.create_string_buffer(encoded)
    raw.FPDFText_SetText(obj, ctypes.cast(buffer, ctypes.POINTER(raw.FPDF_WCHAR)))
    if invisible:
        raw.FPDFTextObj_SetTextRenderMode(obj, raw.FPDF_TEXTRENDERMODE_INVISIBLE)
    raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
    raw.FPDFPage_InsertObject(page, obj)
    del font


def _arrow(page, x0: float, y0: float, x1: float, y1: float) -> None:  # type: ignore[no-untyped-def]
    import pypdfium2.raw as raw

    path = raw.FPDFPageObj_CreateNewPath(ctypes.c_float(x0), ctypes.c_float(y0))
    raw.FPDFPath_LineTo(path, ctypes.c_float(x1), ctypes.c_float(y1))
    raw.FPDFPageObj_SetStrokeColor(path, 200, 0, 0, 255)
    raw.FPDFPageObj_SetStrokeWidth(path, ctypes.c_float(1.5))
    raw.FPDFPath_SetDrawMode(path, 0, 1)
    raw.FPDFPage_InsertObject(page, path)


def _white_rect(page, x: float, y: float, w: float, h: float) -> None:  # type: ignore[no-untyped-def]
    import pypdfium2.raw as raw

    rect = raw.FPDFPageObj_CreateNewRect(
        ctypes.c_float(x), ctypes.c_float(y), ctypes.c_float(w), ctypes.c_float(h)
    )
    raw.FPDFPageObj_SetFillColor(rect, 255, 255, 255, 255)
    raw.FPDFPath_SetDrawMode(rect, raw.FPDF_FILLMODE_WINDING, 0)
    raw.FPDFPage_InsertObject(page, rect)


def raster_page(
    image: Image.Image,
    *,
    ppi: float,
    x: float = 80.25,
    y: float = 300.5,
    page_size: tuple[float, float] = PAGE_A4,
    jpeg: bool = False,
    cmyk: bool = False,
    flip: bool = False,
    rot90: bool = False,
    page_rotation: int = 0,
    labels: bool = False,
    ocr_text: bool = False,
    background: bool = False,
    form_scale: float | None = None,
) -> BuiltPage:
    """Pagina con `image` a `ppi` (x, y = angolo in basso a sinistra, pt)."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(*page_size)
    w = image.width / ppi * 72.0
    h = image.height / ppi * 72.0
    if form_scale is not None:
        # Il raster sta in un Form XObject disegnato a scala `form_scale`:
        # ppi sulla pagina = ppi / form_scale.
        w, h = w * form_scale, h * form_scale
    if background:
        _white_rect(page.raw, x - 6, y - 6, w + 12, h + 12)
    obj = pdfium.PdfImage.new(pdf)
    if jpeg or cmyk:
        buf = io.BytesIO()
        (image.convert("CMYK") if cmyk else image).save(buf, format="JPEG", quality=92)
        buf.seek(0)
        obj.load_jpeg(buf, inline=False)
    else:
        obj.set_bitmap(pdfium.PdfBitmap.from_pil(image))
    if form_scale is not None:
        # Il Form si costruisce da una pagina sorgente con il raster a
        # scala 1, poi si disegna ridotto.
        src = pdfium.PdfDocument.new()
        src_page = src.new_page(image.width / ppi * 72.0 + 20, image.height / ppi * 72.0 + 20)
        inner = pdfium.PdfImage.new(src)
        inner.set_bitmap(pdfium.PdfBitmap.from_pil(image))
        inner.set_matrix(
            pdfium.PdfMatrix().scale(image.width / ppi * 72.0, image.height / ppi * 72.0)
        )
        src_page.insert_obj(inner)
        src_page.gen_content()
        xobject = raw.FPDF_NewXObjectFromPage(pdf.raw, src.raw, 0)
        form = raw.FPDF_NewFormObjectFromXObject(xobject)
        raw.FPDFPageObj_Transform(form, form_scale, 0, 0, form_scale, x, y)
        raw.FPDFPage_InsertObject(page.raw, form)
        raw.FPDF_CloseXObject(xobject)
    else:
        if rot90:
            # Colonne dell'immagine lungo −y, righe lungo +x: sulla pagina il
            # raster è largo quanto la sua altezza in pixel.
            w, h = h, w
            matrix = pdfium.PdfMatrix(0, -h, w, 0, x, y + h)
        elif flip:
            matrix = pdfium.PdfMatrix(-w, 0, 0, h, x + w, y)
        else:
            matrix = pdfium.PdfMatrix().scale(w, h).translate(x, y)
        obj.set_matrix(matrix)
        page.insert_obj(obj)
    if labels:
        _text(pdf.raw, page.raw, "Laser", x + 5, y + h * 0.5, 9, invisible=False)
        _arrow(page.raw, x + w * 0.2, y + h * 0.2, x + w * 0.8, y + h * 0.8)
    if ocr_text:
        _text(pdf.raw, page.raw, "testo OCR invisibile", x + 10, y + h * 0.4, 10, invisible=True)
    page.gen_content()
    if page_rotation:
        page.set_rotation(page_rotation)
    out = io.BytesIO()
    pdf.save(out)
    # Rettangolo di visualizzazione (pagina non ruotata: y verso il basso).
    page_w, page_h = page_size
    rect = (x, page_h - (y + h), x + w, page_h - y)
    if page_rotation == 90:
        rect = (y, x, y + h, x + w)
    elif page_rotation == 180:
        rect = (page_w - (x + w), y, page_w - x, y + h)
    elif page_rotation == 270:
        rect = (page_h - (y + h), page_w - (x + w), page_h - y, page_w - x)
    effective = ppi / form_scale if form_scale else ppi
    return BuiltPage(pdf=out.getvalue(), raster_rect=rect, ppi=effective)
