"""`image_limits` (W5-T2): immagini e PDF di terzi entro i tetti.

- una PNG che dichiara un'area enorme (bomba di decompressione: pochi byte,
  intestazione con 60 000 × 60 000 pixel) è rifiutata PRIMA di decodificare;
- formati fuori elenco (SVG, BMP) rifiutati; un'immagine valida è
  ricodificata come i ritagli dei documenti (PNG o JPEG, senza metadati);
- GIF animata: si usa il primo fotogramma;
- PDF oltre il tetto di pagine rifiutato, sotto il tetto contato.
"""

from __future__ import annotations

import io
import struct
import zlib

import pytest
from PIL import Image

from app.services.image_limits import ImageLimitError, load_image, pdf_page_count


def _png(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


def _bomb(width: int, height: int) -> bytes:
    """PNG da 1×1 con l'intestazione riscritta: dichiara `width×height`."""
    data = bytearray(_png(1, 1))
    ihdr = data.index(b"IHDR")
    data[ihdr + 4 : ihdr + 12] = struct.pack(">II", width, height)
    crc = zlib.crc32(bytes(data[ihdr : ihdr + 17])) & 0xFFFFFFFF
    data[ihdr + 17 : ihdr + 21] = struct.pack(">I", crc)
    return bytes(data)


def test_pixel_bomb_is_rejected_before_decoding() -> None:
    with pytest.raises(ImageLimitError) as excinfo:
        load_image(_bomb(60_000, 60_000), max_pixels=40_000_000)
    assert excinfo.value.code == "too_many_pixels"
    # Sotto il tetto di PIL ma sopra il nostro.
    with pytest.raises(ImageLimitError) as small_cap:
        load_image(_png(300, 300), max_pixels=50_000)
    assert small_cap.value.code == "too_many_pixels"


@pytest.mark.parametrize(
    "payload",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"not an image at all",
    ],
)
def test_unreadable_or_vector_payloads_are_rejected(payload: bytes) -> None:
    with pytest.raises(ImageLimitError) as excinfo:
        load_image(payload, max_pixels=40_000_000)
    assert excinfo.value.code == "unreadable"


def test_bmp_is_not_an_allowed_format() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buf, format="BMP")
    with pytest.raises(ImageLimitError) as excinfo:
        load_image(buf.getvalue(), max_pixels=40_000_000)
    assert excinfo.value.code == "unsupported_format"


def test_valid_image_is_reencoded_without_metadata() -> None:
    image = Image.new("RGB", (400, 200), "white")
    for x in range(0, 400, 20):
        for y in range(200):
            image.putpixel((x, y), (0, 0, 120))
    buf = io.BytesIO()
    image.save(buf, format="PNG", pnginfo=_pnginfo("Author", "Mallory"))
    safe = load_image(buf.getvalue(), max_pixels=40_000_000)
    assert (safe.width, safe.height) == (400, 200)
    assert safe.mime in ("image/png", "image/jpeg")
    assert b"Mallory" not in safe.data


def _transparent_schematic(mode: str) -> Image.Image:
    """Schema nero su fondo trasparente, come i rendering PNG di Commons
    (il file «IPv4 address structure…» è in modo LA)."""
    rgba = Image.new("RGBA", (300, 120), (0, 0, 0, 0))
    for x in range(20, 280):
        for y in (30, 31, 90, 91):
            rgba.putpixel((x, y), (0, 0, 0, 255))
    if mode == "LA":
        return rgba.convert("LA")
    if mode == "P":
        return rgba.convert("P")  # trasparenza nella palette (`info["transparency"]`)
    return rgba


@pytest.mark.parametrize("mode", ["RGBA", "LA", "P"])
def test_transparent_background_becomes_white_not_black(mode: str) -> None:
    image = _transparent_schematic(mode)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    safe = load_image(buf.getvalue(), max_pixels=40_000_000)
    pixels = list(safe.image.convert("L").getdata())
    mean = sum(pixels) / len(pixels)
    # Prima: `convert("RGB")` rendeva nero tutto lo sfondo (media ≈ 0).
    assert mean > 230, mean
    # Il disegno nero resta visibile.
    assert min(pixels) < 30
    # Controprova: la conversione ingenua dà un'immagine tutta nera.
    naive = list(image.convert("RGB").convert("L").getdata())
    assert sum(naive) / len(naive) < 5


def _pnginfo(key: str, value: str) -> object:
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text(key, value)
    return info


def test_animated_gif_uses_the_first_frame() -> None:
    frames = [Image.new("RGB", (40, 30), color) for color in ("red", "blue", "green")]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
    safe = load_image(buf.getvalue(), max_pixels=40_000_000)
    assert (safe.width, safe.height) == (40, 30)
    r, g, b = safe.image.getpixel((5, 5))
    assert r > 200 and g < 50 and b < 50


def _pdf(pages: int) -> bytes:
    buf = io.BytesIO()
    images = [Image.new("RGB", (50, 50), "white") for _ in range(pages)]
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:])
    return buf.getvalue()


def test_pdf_page_cap() -> None:
    assert pdf_page_count(_pdf(3), max_pages=5) == 3
    with pytest.raises(ImageLimitError) as excinfo:
        pdf_page_count(_pdf(6), max_pages=5)
    assert excinfo.value.code == "too_many_pages"
    with pytest.raises(ImageLimitError) as bad:
        pdf_page_count(b"%PDF-1.4 broken", max_pages=5)
    assert bad.value.code == "unreadable"


def test_crop_v2_encoding_keeps_line_art_lossless_even_from_jpeg() -> None:
    """Ritaglio v2 (doc 18 §22): uno schema arrivato come JPEG diventa PNG;
    una foto pesante da JPEG resta JPEG (q95 4:4:4)."""
    from tests.fixtures.source_figures.native import line_art_image, photo_image

    def as_jpeg(image: Image.Image) -> bytes:
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=92)
        return buf.getvalue()

    art = load_image(as_jpeg(line_art_image()), max_pixels=20_000_000, crop_v2=True)
    assert art.mime == "image/png"
    photo = load_image(as_jpeg(photo_image()), max_pixels=20_000_000, crop_v2=True)
    assert photo.mime == "image/jpeg"
    legacy = load_image(as_jpeg(line_art_image()), max_pixels=20_000_000)
    assert legacy.mime in ("image/png", "image/jpeg")
