"""Limiti sulle immagini e sui PDF scaricati dalla letteratura aperta.

Un file di terzi può essere una bomba: poche centinaia di KB che si
decompattano in gigabyte di pixel, o un PDF da migliaia di pagine. Qui i
controlli avvengono PRIMA di decodificare:

- immagini: `Image.open` legge solo l'intestazione; formato fra quelli
  ammessi (PNG, JPEG, GIF, WebP; niente SVG, niente formati esotici),
  larghezza × altezza entro `max_pixels`, un solo fotogramma usato; solo
  dopo si chiama `load()` e si ricodifica come i ritagli dei documenti
  (PNG o JPEG, niente metadati);
- PDF: numero di pagine letto con pypdfium2 e confrontato con `max_pages`
  prima di qualunque rendering.

Errori → `ImageLimitError(code)`, mai un'immagine parziale.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass

from PIL import Image

from app.services.document_figures.cropper import encode_image, looks_like_photo, on_white

ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "GIF", "WEBP"})


class ImageLimitError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SafeImage:
    image: Image.Image
    data: bytes
    mime: str
    width: int
    height: int


def load_image(data: bytes, *, max_pixels: int) -> SafeImage:
    """Apre e ricodifica un'immagine di terzi entro i limiti."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(data))
            fmt = (probe.format or "").upper()
            width, height = probe.size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageLimitError("too_many_pixels", str(exc)) from exc
    except Exception as exc:
        raise ImageLimitError("unreadable", f"immagine non leggibile: {exc}") from exc
    if fmt not in ALLOWED_FORMATS:
        raise ImageLimitError("unsupported_format", f"formato {fmt or '?'} non ammesso")
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise ImageLimitError("too_many_pixels", f"{width}×{height} oltre {max_pixels} pixel")
    try:
        # Solo il primo fotogramma (GIF/WebP animati): `load()` decodifica
        # quello corrente, già entro il tetto di pixel controllato sopra.
        probe.seek(0)
        probe.load()
        image = on_white(probe)
    except Exception as exc:
        raise ImageLimitError("unreadable", f"immagine non decodificabile: {exc}") from exc
    encoded, mime = encode_image(image, photo=looks_like_photo(image))
    return SafeImage(image=image, data=encoded, mime=mime, width=image.width, height=image.height)


def pdf_page_count(data: bytes, *, max_pages: int) -> int:
    """Pagine del PDF, rifiutato oltre `max_pages` (P_max della letteratura)."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:
        raise ImageLimitError("unreadable", f"PDF non leggibile: {exc}") from exc
    try:
        pages = len(pdf)
    finally:
        pdf.close()
    if pages <= 0:
        raise ImageLimitError("unreadable", "PDF senza pagine")
    if pages > max_pages:
        raise ImageLimitError("too_many_pages", f"{pages} pagine oltre {max_pages}")
    return pages
