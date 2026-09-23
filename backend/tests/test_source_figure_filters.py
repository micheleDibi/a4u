"""Moduli puri dell'estrazione delle figure (G12): filtri, didascalie,
risoluzione adattiva (D21), pHash e codifica dei ritagli."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.services.document_figures import cropper, filters
from app.services.document_figures.captions import caption_label, is_table_caption
from app.services.document_figures.geometry import BBox
from app.services.document_figures.page_text import Line, caption_near, context_excerpt
from app.services.document_figures.phash import hamming, phash

A4 = (595.0, 842.0)


@pytest.mark.parametrize(
    ("bbox", "is_vector", "confidence", "expected"),
    [
        (BBox(100, 200, 500, 500), True, None, None),
        (BBox(40, 24, 64, 48), False, None, "too_small"),
        (BBox(100, 200, 170, 260), True, None, "too_small"),  # 70×60 pt ma < 1,2% della pagina
        (BBox(40, 400, 560, 460), True, None, "bad_aspect"),
        (BBox(100, 10, 400, 60), True, None, "header_footer"),
        (BBox(100, 780, 500, 840), True, None, "header_footer"),
        (BBox(0, 0, 595, 842), False, None, "too_large"),
        (BBox(0, 0, 595, 842), True, None, None),  # tavola vettoriale a piena pagina
        (BBox(100, 200, 500, 500), True, 0.2, "detector_noise"),
    ],
)
def test_geometry_filters(
    bbox: BBox, is_vector: bool, confidence: float | None, expected: str | None
) -> None:
    assert (
        filters.geometry_reject_reason(
            bbox, page_w=A4[0], page_h=A4[1], is_vector=is_vector, confidence=confidence
        )
        == expected
    )


def test_repeated_and_duplicates() -> None:
    logo = "f0f0f0f0f0f0f0f0"
    figs = [
        filters.HashedFigure("logo1", 1, logo),
        filters.HashedFigure("logo2", 2, logo),
        filters.HashedFigure("logo3", 3, "f0f0f0f0f0f0f0f1"),
        filters.HashedFigure("schema", 4, "0123456789abcdef"),
        filters.HashedFigure("schema-copia", 7, "0123456789abcdee"),
        filters.HashedFigure("altro", 5, "fedcba9876543210"),
    ]
    repeated, duplicates = filters.repeated_and_duplicates(figs)
    assert repeated == {"logo1", "logo2", "logo3"}
    assert duplicates == {"schema-copia": "schema"}


@pytest.mark.parametrize(
    ("caption", "label"),
    [
        ("Figura 2.1. Schema", "Figura 2.1"),
        ("Fig. 3: vista", "Fig. 3"),
        ("FIGURE 12a – test", "FIGURE 12a"),
        ("Tabella 1. Dati", None),
        ("La figura mostra", None),
        (None, None),
    ],
)
def test_caption_label(caption: str | None, label: str | None) -> None:
    assert caption_label(caption) == label


def test_table_caption_is_recognised() -> None:
    assert is_table_caption("Tabella 2.1. Caratteristiche")
    assert not is_table_caption("Figura 2.1. Schema")


def _line(text: str, x0: float, top: float, x1: float, bottom: float) -> Line:
    return Line(text, BBox(x0, top, x1, bottom))


def test_caption_below_with_continuation_lines() -> None:
    bbox = BBox(100, 200, 500, 400)
    lines = [
        _line("Paragrafo prima della figura.", 60, 150, 540, 162),
        _line("Figura 3. Schema del banco di misura con", 100, 410, 480, 421),
        _line("lo shaker e la testa vibrometrica.", 100, 423, 400, 434),
        _line("Testo successivo che non fa parte della didascalia.", 60, 460, 540, 471),
    ]
    assert caption_near(lines, bbox) == (
        "Figura 3. Schema del banco di misura con lo shaker e la testa vibrometrica."
    )


def test_caption_above_when_nothing_below() -> None:
    bbox = BBox(100, 200, 500, 400)
    lines = [_line("Figure 2: Setup.", 100, 180, 300, 191)]
    assert caption_near(lines, bbox) == "Figure 2: Setup."


def test_caption_beside_the_figure() -> None:
    bbox = BBox(283, 342, 552, 691)
    lines = [_line("Fig. 1. The melting temperature", 72, 636, 252, 647)]
    assert caption_near(lines, bbox) == "Fig. 1. The melting temperature"


def test_stacked_figures_with_captions_above_get_their_own_caption() -> None:
    # «Figura 1», figura 1, «Figura 2», figura 2: la riga sotto la figura 1
    # è più vicina alla figura 2, quindi è la sua didascalia.
    fig1 = BBox(100, 120, 500, 300)
    fig2 = BBox(100, 340, 500, 520)
    lines = [
        _line("Figura 1. Schema a blocchi.", 100, 104, 300, 115),
        _line("Figura 2. Banco di misura.", 100, 325, 300, 336),
    ]
    others = [fig1, fig2]
    assert caption_near(lines, fig1, others=others) == "Figura 1. Schema a blocchi."
    assert caption_near(lines, fig2, others=others) == "Figura 2. Banco di misura."
    # Didascalie sotto: ognuna resta alla figura che la precede.
    lines_below = [
        _line("Figura 1. Schema a blocchi.", 100, 305, 300, 316),
        _line("Figura 2. Banco di misura.", 100, 525, 300, 536),
    ]
    assert caption_near(lines_below, fig1, others=others) == "Figura 1. Schema a blocchi."
    assert caption_near(lines_below, fig2, others=others) == "Figura 2. Banco di misura."


def test_two_column_caption_keeps_its_continuation_line() -> None:
    bbox = BBox(60, 200, 290, 380)
    lines = [
        _line("Figure 3: Architecture of the proposed", 60, 390, 290, 401),
        # Riga dell'altra colonna, fra le due righe della didascalia.
        _line("which is measured with the reference", 310, 396, 540, 407),
        _line("vibrometer.", 60, 403, 120, 414),
    ]
    assert caption_near(lines, bbox) == "Figure 3: Architecture of the proposed vibrometer."


def test_far_or_unlabelled_lines_are_not_captions() -> None:
    bbox = BBox(100, 200, 500, 400)
    lines = [
        _line("Figura 9. Troppo lontana", 100, 500, 400, 511),
        _line("Schema del sensore", 100, 410, 400, 421),
    ]
    assert caption_near(lines, bbox) is None


def test_context_skips_figure_caption_and_page_furniture() -> None:
    bbox = BBox(100, 200, 500, 400)
    lines = [
        _line("Università di Prova — testata", 60, 30, 400, 42),
        _line("Il sensore converte la grandezza.", 60, 150, 540, 162),
        _line("etichetta interna", 200, 300, 300, 310),
        _line("Figura 3. Schema", 100, 410, 300, 421),
        _line("Il segnale viene poi campionato.", 60, 440, 540, 451),
        _line("7", 290, 810, 300, 820),
    ]
    text = context_excerpt(lines, bbox, caption="Figura 3. Schema", page_h=842)
    assert text == "Il sensore converte la grandezza. Il segnale viene poi campionato."


@pytest.mark.parametrize(
    ("bbox", "is_vector", "native", "expected"),
    [
        (BBox(0, 0, 432, 288), True, None, 400),  # 6 in → 2400/6
        (BBox(0, 0, 144, 72), True, None, 600),  # piccolo: tetto 600
        (BBox(0, 0, 595, 842), True, None, 300),  # A4 intera: 300 dpi, 8,7 MP
        (BBox(0, 0, 842, 1191), True, None, 0),  # A3: sotto 300 per il tetto di 12 MP
        (BBox(0, 0, 360, 240), False, 96.0, 150),  # raster a bassa risoluzione: minimo 150
        (BBox(0, 0, 360, 240), False, 220.0, 220),  # ppi nativo rispettato
        (BBox(0, 0, 360, 240), False, 1200.0, 300),  # mai oltre 300 per i raster
    ],
)
def test_adaptive_dpi(bbox: BBox, is_vector: bool, native: float | None, expected: int) -> None:
    dpi = cropper.choose_dpi(bbox, is_vector=is_vector, native_ppi=native)
    if expected == 0:
        pixels = (bbox.width / 72 * dpi) * (bbox.height / 72 * dpi)
        assert pixels <= cropper.MAX_PIXELS and 200 < dpi < 300
    else:
        assert dpi == expected


def _drawing(seed: int) -> Image.Image:
    image = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(image)
    rng = np.random.default_rng(seed)
    for _ in range(12):
        x0, y0 = rng.integers(0, 300), rng.integers(0, 200)
        draw.rectangle((x0, y0, x0 + 80, y0 + 60), outline="black", width=3)
    return image


def test_phash_is_stable_under_resize_and_separates_different_images() -> None:
    base = _drawing(1)
    resized = base.resize((800, 600))
    assert hamming(phash(base), phash(resized)) <= 4
    assert hamming(phash(base), phash(_drawing(2))) > 10
    assert len(phash(base)) == 16


def test_encoding_choices() -> None:
    gray, mime = cropper.encode_image(_drawing(1), photo=False)
    assert mime == "image/png"
    assert Image.open(io.BytesIO(gray)).mode == "L"
    rng = np.random.default_rng(0)
    photo = Image.fromarray(rng.integers(0, 255, (300, 400, 3), dtype=np.uint8))
    data, mime = cropper.encode_image(photo, photo=True)
    assert mime == "image/jpeg" and data[:2] == b"\xff\xd8"
    assert cropper.looks_like_photo(photo) and not cropper.looks_like_photo(_drawing(1))


def test_blank_detection() -> None:
    assert cropper.is_blank(Image.new("RGB", (200, 200), "white"))
    assert not cropper.is_blank(_drawing(3))


def test_storage_helpers_only_touch_figure_crops(monkeypatch: pytest.MonkeyPatch) -> None:
    import uuid

    from app.services import remote_storage
    from app.services.document_figures import storage

    calls: list[tuple[str, str]] = []

    class _Fake:
        def upload_bytes(self, key: str, data: bytes) -> None:
            calls.append(("upload", key))

        def download_bytes(self, key: str) -> bytes:
            calls.append(("read", key))
            return b""

        def delete(self, key: str) -> None:
            calls.append(("delete", key))

    monkeypatch.setattr(remote_storage, "get_storage", lambda: _Fake())
    course, doc = uuid.uuid4(), uuid.uuid4()
    good = storage.figure_path(course, doc, "p0001-f01-0123456789ab.png")
    storage.upload(good, b"x")
    storage.read(good)
    storage.delete(good)
    assert [c[0] for c in calls] == ["upload", "read", "delete"]
    assert storage.belongs_to_course(good, course)
    assert not storage.belongs_to_course(good, uuid.uuid4())
    bad_paths = [
        f"/uploads/courses/{course}/documents/lezione.pdf",
        f"/uploads/courses/{course}/document_figures/{doc}/../../documents/x.png",
        f"/uploads/courses/{course}/document_figures/{doc}/sub/x.png",
        f"/uploads/avatars/{doc}/p0001.png",
        f"/uploads/courses/{course}/document_figures/{doc}/P0001.PNG",
    ]
    for bad in bad_paths:
        for helper in (storage.read, storage.delete):
            with pytest.raises(storage.FigureStoragePathError):
                helper(bad)
        with pytest.raises(storage.FigureStoragePathError):
            storage.upload(bad, b"x")
        assert not storage.belongs_to_course(bad, course)
    assert len(calls) == 3
