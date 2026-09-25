"""Ritaglio v2 delle figure di fonte (doc 18 §22, giuria Q4).

- render allineato alla griglia nativa: bit-identico all'oggetto immagine
  su una griglia di ppi, posizioni e orientamenti (ribaltamento, immagine
  ruotata di 90°, pagina ruotata), per PNG, JPEG e Form XObject scalati;
- figure miste (tratti vettoriali sopra il raster): k volte il nativo con
  blocchi k×k esatti; lo strato OCR invisibile e i fondi bianchi non
  rendono mista una scansione;
- niente pavimento a 150 dpi né soffitto a 300; tetto di 12 megapixel e
  anti-bomba; vettoriali come prima;
- misura naturale normalizzata alla pagina;
- codifica per contenuto: PNG senza perdita (grigi e palette esatti), JPEG
  q95 4:4:4 solo per le foto da sorgente con perdita molto pesanti;
- Office: srcRect, ribaltamenti, rotazioni a quarti di giro, EMU e scala
  dei gruppi → ppi nativo e misura naturale;
- figlio end-to-end con `crop_version` 2 (e 1 senza il campo).
"""

from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, JpegImagePlugin

from app.services.document_figures import cropper
from app.services.document_figures.child import DOCX_MIME, PDF_MIME, PPTX_MIME
from app.services.document_figures.geometry import BBox
from app.services.document_figures.office import docx_images, pptx_images
from app.services.document_figures.regions import page_regions
from app.services.source_figure_resolution import page_factor
from tests.fixtures.source_figures import native
from tests.fixtures.source_figures.build import PDF_NAME, build_all
from tests.source_figure_child import run_child

pdfium = pytest.importorskip("pypdfium2")

_T = Image.Transpose
# Trasformazione dell'immagine nativa vista sulla pagina (verificata sul
# prototipo in tutte le 12 combinazioni).
_SEEN = {
    ("none", 0): None,
    ("flip", 0): _T.FLIP_LEFT_RIGHT,
    ("rot90", 0): _T.ROTATE_270,
    ("none", 90): _T.ROTATE_270,
    ("none", 180): _T.ROTATE_180,
    ("none", 270): _T.ROTATE_90,
}


def _open(built: native.BuiltPage) -> tuple[Any, Any]:
    pdf = pdfium.PdfDocument(built.pdf)
    return pdf, pdf[0]


def _page_size(page: Any) -> tuple[float, float]:
    width, height = page.get_size()
    return float(width), float(height)


def _crop(built: native.BuiltPage) -> cropper.NativeCrop:
    _pdf, page = _open(built)
    page_w, page_h = _page_size(page)
    return cropper.render_native_crop(
        page, BBox(*built.raster_rect), page_regions(page), page_w=page_w, page_h=page_h
    )


def _find(crop: Image.Image, target: Image.Image) -> tuple[int, int] | None:
    """Posizione di `target` dentro `crop` (confronto esatto)."""
    big = np.asarray(crop.convert("RGB"), dtype=np.int16)
    small = np.asarray(target.convert("RGB"), dtype=np.int16)
    h, w = small.shape[:2]
    for y in range(0, big.shape[0] - h + 1):
        for x in range(0, big.shape[1] - w + 1):
            if big[y, x, 0] == small[0, 0, 0] and np.array_equal(big[y : y + h, x : x + w], small):
                return x, y
    return None


# --- griglia della bit-identità -------------------------------------------------

_CASES = [
    (ppi, x, y, orientation, rotation, jpeg)
    for ppi, x, y in (
        (72.0, 72.0, 400.0),
        (76.4, 33.3, 123.4),
        (96.0, 50.3, 300.7),
        (130.0, 80.25, 300.5),
        (150.0, 100.25, 500.5),
        (220.0, 61.7, 211.9),
        (300.0, 90.1, 610.3),
    )
    for orientation, rotation in (("none", 0), ("flip", 0), ("rot90", 0), ("none", 90))
    for jpeg in (False,)
] + [
    (150.0, 80.1, 300.3, "none", 180, False),
    (150.0, 80.1, 300.3, "none", 270, False),
    (96.0, 80.1, 300.3, "none", 0, True),
    (220.0, 44.4, 520.2, "none", 0, True),
]


@pytest.mark.parametrize(("ppi", "x", "y", "orientation", "rotation", "jpeg"), _CASES)
def test_aligned_render_is_bit_identical_to_the_native_image(
    ppi: float, x: float, y: float, orientation: str, rotation: int, jpeg: bool
) -> None:
    image = native.noise_image()
    built = native.raster_page(
        image,
        ppi=ppi,
        x=x,
        y=y,
        jpeg=jpeg,
        flip=orientation == "flip",
        rot90=orientation == "rot90",
        page_rotation=rotation,
    )
    crop = _crop(built)
    assert crop.mode == "raster_native" and crop.aligned
    assert crop.native_ppi == pytest.approx(ppi, rel=1e-3)
    assert crop.dpi == pytest.approx(ppi, abs=1.0)
    assert crop.mime == "image/png"
    _pdf, page = _open(built)
    obj = next(page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]))
    reference = obj.get_bitmap(render=False).to_pil().convert("RGB")
    transform = _SEEN[(orientation, rotation)]
    if transform is not None:
        reference = reference.transpose(transform)
    decoded = Image.open(io.BytesIO(crop.data))
    assert _find(decoded, reference) is not None, (ppi, orientation, rotation)


def test_form_xobject_scale_is_composed() -> None:
    built = native.raster_page(native.noise_image(), ppi=130.0, form_scale=0.5)
    crop = _crop(built)
    assert crop.native_ppi == pytest.approx(260.0, rel=1e-3)
    assert crop.aligned and crop.mode == "raster_native"
    assert _find(Image.open(io.BytesIO(crop.data)), native.noise_image()) is not None


def test_cmyk_jpeg_keeps_its_pixel_grid() -> None:
    image = native.noise_image()
    crop = _crop(native.raster_page(image, ppi=150.0, cmyk=True))
    assert crop.aligned and crop.source_lossy
    # Margine di 4 pt su ogni lato attorno ai pixel nativi.
    margin = round(4 * 150.0 / 72.0)
    assert abs(crop.width - (image.width + 2 * margin)) <= 1


# --- figure miste, OCR, fondi ------------------------------------------------------


def test_vector_labels_make_a_mixed_crop_at_k_times_native() -> None:
    image = native.noise_image()
    crop = _crop(native.raster_page(image, ppi=130.0, labels=True))
    assert crop.mode == "mixed" and crop.aligned
    assert crop.dpi == pytest.approx(390.0, abs=2.0)  # k = ceil(300/130) = 3
    assert crop.native_ppi == pytest.approx(130.0, rel=1e-3)
    # Angolo in alto a sinistra (lontano da etichetta e freccia): blocchi 3×3
    # esatti dei pixel nativi.
    corner = image.crop((0, 0, 12, 10))
    upscaled = corner.resize((36, 30), Image.Resampling.NEAREST)
    assert _find(Image.open(io.BytesIO(crop.data)).convert("RGB"), upscaled) is not None


@pytest.mark.parametrize("kind", ["ocr_text", "background"])
def test_invisible_text_and_white_backgrounds_do_not_make_a_scan_mixed(kind: str) -> None:
    crop = _crop(native.raster_page(native.noise_image(), ppi=150.0, **{kind: True}))
    assert crop.mode == "raster_native" and crop.aligned


def test_no_floor_at_150_and_no_ceiling_at_300() -> None:
    low = _crop(native.raster_page(native.noise_image(), ppi=80.0))
    assert low.dpi == pytest.approx(80.0, abs=1.0)
    high_image = native.noise_image(600, 450)
    high = _crop(native.raster_page(high_image, ppi=1200.0))
    assert high.dpi == pytest.approx(1200.0, abs=2.0)
    assert high.aligned and high.width >= 600


def test_pixel_cap_and_bomb_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    built = native.raster_page(native.noise_image(400, 300), ppi=150.0)
    monkeypatch.setattr(cropper, "MAX_PIXELS", 40_000)
    capped = _crop(built)
    assert capped.width * capped.height <= 40_000 and not capped.aligned
    monkeypatch.setattr(cropper, "MAX_RENDER_PIXELS", 1_000)
    guarded = _crop(built)
    assert guarded.width * guarded.height <= 40_000 and not guarded.aligned


def test_vector_only_figure_keeps_the_vector_dpi() -> None:
    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(595, 842)
    import ctypes

    raw = pdfium.raw
    for i in range(6):
        path = raw.FPDFPageObj_CreateNewPath(ctypes.c_float(100 + i * 30), ctypes.c_float(300))
        raw.FPDFPath_LineTo(path, ctypes.c_float(120 + i * 30), ctypes.c_float(420))
        raw.FPDFPageObj_SetStrokeColor(path, 0, 0, 0, 255)
        raw.FPDFPath_SetDrawMode(path, 0, 1)
        raw.FPDFPage_InsertObject(page.raw, path)
    page.gen_content()
    buf = io.BytesIO()
    pdf.save(buf)
    doc = pdfium.PdfDocument(buf.getvalue())
    crop = cropper.render_native_crop(
        doc[0], BBox(90, 410, 290, 550), page_regions(doc[0]), page_w=595, page_h=842
    )
    assert crop.mode == "vector" and crop.is_vector and crop.native_ppi is None
    assert 300 <= crop.dpi <= 600


def test_natural_width_is_normalized_to_the_page() -> None:
    built = native.raster_page(native.noise_image(), ppi=96.0, page_size=(960.0, 540.0))
    crop = _crop(built)
    box = BBox(*built.raster_rect).expand(cropper.CROP_MARGIN_PT)
    expected = box.width / 72.0 * 25.4 * page_factor(960.0)
    assert crop.natural_width_mm == pytest.approx(expected, abs=0.05)
    assert page_factor(960.0) == pytest.approx(0.6375)


# --- codifica ------------------------------------------------------------------------


def test_line_art_is_always_png_even_from_a_lossy_source() -> None:
    art = native.line_art_image()
    assert cropper.is_line_art(art)
    data, mime = cropper.encode_figure(art, source_lossy=True)
    assert mime == "image/png"
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(data)).convert("RGB")), np.asarray(art))


def test_heavy_photo_from_a_lossy_source_is_jpeg_444() -> None:
    photo = native.photo_image()
    assert not cropper.is_line_art(photo)
    data, mime = cropper.encode_figure(photo, source_lossy=True)
    assert mime == "image/jpeg"
    decoded = Image.open(io.BytesIO(data))
    assert isinstance(decoded, JpegImagePlugin.JpegImageFile)
    assert JpegImagePlugin.get_sampling(decoded) == 0  # 4:4:4
    lossless, mime_lossless = cropper.encode_figure(photo, source_lossy=False)
    assert mime_lossless == "image/png"
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(lossless))), np.asarray(photo))


def test_png_is_exactly_lossless_for_gray_and_palette_images() -> None:
    gray = Image.fromarray(np.tile(np.arange(256, dtype=np.uint8), (40, 1))).convert("RGB")
    data, _mime = cropper.encode_figure(gray, source_lossy=False)
    decoded = Image.open(io.BytesIO(data))
    assert decoded.mode == "L"
    assert np.array_equal(np.asarray(decoded.convert("RGB")), np.asarray(gray))
    # Quasi grigio (canali diversi di 1): resta RGB, senza perdita.
    arr = np.asarray(gray).copy()
    arr[0, 0, 2] = arr[0, 0, 2] ^ 1
    near = Image.fromarray(arr)
    data, _mime = cropper.encode_figure(near, source_lossy=False)
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(data)).convert("RGB")), arr)
    rng = np.random.default_rng(5)
    palette = rng.integers(0, 256, (200, 3), dtype=np.uint8)
    indexed = palette[rng.integers(0, 200, (60, 80))]
    data, _mime = cropper.encode_figure(Image.fromarray(indexed), source_lossy=False)
    decoded = Image.open(io.BytesIO(data))
    assert decoded.mode == "P"
    assert np.array_equal(np.asarray(decoded.convert("RGB")), indexed)


# --- Office ----------------------------------------------------------------------------

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_IMG = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


def _asymmetric(width: int = 200, height: int = 100) -> Image.Image:
    """Quattro quadranti di colori diversi: ogni ritaglio, ribaltamento e
    rotazione si riconosce."""
    image = Image.new("RGB", (width, height))
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    arr[: height // 2, : width // 2] = (255, 0, 0)
    arr[: height // 2, width // 2 :] = (0, 255, 0)
    arr[height // 2 :, : width // 2] = (0, 0, 255)
    arr[height // 2 :, width // 2 :] = (255, 255, 0)
    image = Image.fromarray(arr)
    return image


def _png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _docx(path: Path, *, cx: int, cy: int, src_l: int, flip_h: bool, rot: int) -> None:
    xfrm_attrs = (' flipH="1"' if flip_h else "") + (f' rot="{rot}"' if rot else "")
    body = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{_W}" xmlns:a="{_A}" xmlns:r="{_R}" xmlns:wp="{_WP}" xmlns:pic="{_PIC}">
<w:body>
<w:p><w:r><w:t>Il vibrometro laser Doppler.</w:t></w:r></w:p>
<w:p><w:r><w:drawing><wp:inline><wp:extent cx="{cx}" cy="{cy}"/>
<a:graphic><a:graphicData><pic:pic>
<pic:blipFill><a:blip r:embed="rId1"/><a:srcRect l="{src_l}"/></pic:blipFill>
<pic:spPr><a:xfrm{xfrm_attrs}><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm></pic:spPr>
</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>
<w:p><w:r><w:t>Figura 1. Schema.</w:t></w:r></w:p>
<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>
</w:body></w:document>"""
    rels = (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_REL}">'
        f'<Relationship Id="rId1" Type="{_IMG}" Target="media/image1.png"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", body)
        zf.writestr("word/_rels/document.xml.rels", rels)
        zf.writestr("word/media/image1.png", _png_bytes(_asymmetric()))


def test_docx_geometry_crop_flip_rotation_and_ppi(tmp_path: Path) -> None:
    path = tmp_path / "appunti.docx"
    # 200×100 px, srcRect sinistra 25% (restano 150 px: rosso/blu 50, verde/
    # giallo 100), riquadro 1 × 1 pollice, ribaltato e ruotato di 90°.
    _docx(path, cx=914_400, cy=914_400, src_l=25_000, flip_h=True, rot=5_400_000)
    (item,) = docx_images(str(path), geometry=True)
    assert item.image is not None
    assert item.image.size == (100, 150)
    assert item.native_ppi == pytest.approx(150.0)
    # Ruotata di 90°: la misura visibile è l'altezza del riquadro.
    assert item.natural_width_mm == pytest.approx(25.4)
    # Ribaltata e poi ruotata in senso orario: in alto verde a destra e
    # giallo a sinistra, in basso rosso a destra e blu a sinistra (senza il
    # ribaltamento, in alto a destra ci sarebbe il rosso).
    assert item.image.getpixel((75, 20)) == (0, 255, 0)
    assert item.image.getpixel((25, 20)) == (255, 255, 0)
    assert item.image.getpixel((75, 130)) == (255, 0, 0)
    assert item.image.getpixel((25, 130)) == (0, 0, 255)
    assert item.source_lossy is False
    # Senza geometria (ritaglio v1): l'immagine come sta nel pacchetto.
    (plain,) = docx_images(str(path))
    assert plain.image is not None and plain.image.size == (200, 100)
    assert plain.native_ppi is None and plain.natural_width_mm is None


def _pptx(path: Path) -> None:
    presentation = (
        f'<?xml version="1.0" encoding="UTF-8"?><p:presentation xmlns:p="{_P}" xmlns:r="{_R}">'
        '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>'
        '<p:sldSz cx="12192000" cy="6858000"/></p:presentation>'
    )
    pres_rels = (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_REL}">'
        '<Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/></Relationships>'
    )
    slide = f"""<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="{_P}" xmlns:a="{_A}" xmlns:r="{_R}"><p:cSld><p:spTree>
<p:grpSp><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1828800" cy="914400"/>
<a:chOff x="0" y="0"/><a:chExt cx="914400" cy="457200"/></a:xfrm></p:grpSpPr>
<p:pic><p:blipFill><a:blip r:embed="rId2"/></p:blipFill>
<p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="457200"/></a:xfrm></p:spPr></p:pic>
</p:grpSp></p:spTree></p:cSld></p:sld>"""
    slide_rels = (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_REL}">'
        f'<Relationship Id="rId2" Type="{_IMG}" Target="../media/image1.png"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ppt/presentation.xml", presentation)
        zf.writestr("ppt/_rels/presentation.xml.rels", pres_rels)
        zf.writestr("ppt/slides/slide1.xml", slide)
        zf.writestr("ppt/slides/_rels/slide1.xml.rels", slide_rels)
        zf.writestr("ppt/media/image1.png", _png_bytes(_asymmetric()))


def test_pptx_group_scale_and_wide_slide_normalization(tmp_path: Path) -> None:
    path = tmp_path / "lezione.pptx"
    _pptx(path)
    (item,) = pptx_images(str(path), geometry=True)
    # Il gruppo raddoppia: la figura è larga 2 pollici → 200 px / 2 in.
    assert item.native_ppi == pytest.approx(100.0)
    slide_w_pt = 12_192_000 / 12_700
    assert item.natural_width_mm == pytest.approx(50.8 * page_factor(slide_w_pt), abs=0.01)


# --- figlio end-to-end ---------------------------------------------------------------


@pytest.fixture(scope="module")
def fixtures_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("fixtures_native")
    build_all(directory)
    return directory


@pytest.mark.parametrize("crop_version", [None, 2])
def test_child_emits_the_crop_inputs(
    tmp_path: Path, fixtures_dir: Path, crop_version: int | None
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    shutil.copyfile(fixtures_dir / PDF_NAME, work / PDF_NAME)
    code, events, stderr = run_child(
        work,
        source=PDF_NAME,
        mime=PDF_MIME,
        engine="heuristic",
        blocks=[[1, 5]],
        crop_version=crop_version,
    )
    assert code == 0, stderr[-2000:]
    figures = [e for e in events if e["event"] == "figure" and e["reject_reason"] is None]
    assert figures
    for event in figures:
        if crop_version is None:
            assert event["crop_version"] == 1 and "crop_mode" not in event
            continue
        assert event["crop_version"] == 2
        assert event["crop_mode"] in cropper.CROP_MODES
        assert event["natural_width_mm"] > 0
        assert event["mime"] == "image/png"  # nessun JPEG per schemi e foto senza perdita
        if event["crop_mode"] != "vector":
            assert event["native_ppi"] and event["dpi"] >= round(event["native_ppi"]) - 1


def test_child_office_geometry(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    _docx(work / "appunti.docx", cx=914_400, cy=914_400, src_l=50_000, flip_h=False, rot=0)
    code, events, stderr = run_child(
        work,
        source="appunti.docx",
        mime=DOCX_MIME,
        engine="heuristic",
        blocks=[[1, 1]],
        crop_version=2,
    )
    assert code == 0, stderr[-2000:]
    (figure,) = [e for e in events if e["event"] == "figure"]
    assert figure["crop_version"] == 2 and figure["crop_mode"] == "office"
    assert figure["native_ppi"] == pytest.approx(100.0)
    assert figure["natural_width_mm"] == pytest.approx(25.4)
    assert (figure["width"], figure["height"]) == (100, 100)
    _pptx(work / "lezione.pptx")
    code, events, stderr = run_child(
        work,
        source="lezione.pptx",
        mime=PPTX_MIME,
        engine="heuristic",
        blocks=[[1, 1]],
        crop_version=2,
    )
    assert code == 0, stderr[-2000:]
    (slide_figure,) = [e for e in events if e["event"] == "figure"]
    assert slide_figure["crop_mode"] == "office" and slide_figure["native_ppi"] == pytest.approx(
        100.0
    )


def test_a_mixed_crop_of_a_heavy_jpeg_photo_stays_png() -> None:
    """Rilievo della verifica WP2: con etichette vettoriali sopra una foto
    JPEG pesante il ritaglio misto resta PNG (le etichette non si
    ricomprimono con perdita)."""
    crop = _crop(
        native.raster_page(native.photo_image(900, 650), ppi=150.0, jpeg=True, labels=True)
    )
    assert crop.mode == "mixed" and crop.source_lossy
    assert crop.mime == "image/png"
    plain = _crop(native.raster_page(native.photo_image(900, 650), ppi=150.0, jpeg=True))
    assert plain.mode == "raster_native"


@pytest.mark.parametrize(
    ("size", "ppi"), [((12, 10), 3.0), ((256, 1), None)], ids=["celle-3ppi", "striscia"]
)
def test_low_density_background_under_vector_content_renders_as_vector(
    size: tuple[int, int], ppi: float | None
) -> None:
    """Rilievo della verifica WP2: un fondo raster rado (mappa a celle) o a
    striscia sotto etichette e frecce non decide la classe della figura, e
    i tratti non scendono sotto i dpi vettoriali."""
    width, height = size
    image = Image.fromarray(
        np.linspace(0, 255, width * height * 3).astype(np.uint8).reshape(height, width, 3)
    )
    density = ppi if ppi is not None else width / 4.0  # striscia: 256 px su 4 pollici
    built = native.raster_page(image, ppi=density, labels=True)
    crop = _crop(built)
    assert crop.mode == "vector" and crop.native_ppi is None
    assert crop.dpi >= 300


def test_mosaic_of_panels_at_different_ppi_is_not_aligned_to_one_grid() -> None:
    """Pannelli a 300 e 72 ppi nella stessa figura: niente griglia unica
    (il pannello denso non si sottocampiona), ppi nativo del peggiore."""
    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(595, 842)
    for img, ppi, x in (
        (native.noise_image(500, 400), 300.0, 60.0),
        (native.noise_image(160, 128), 72.0, 240.0),
    ):
        obj = pdfium.PdfImage.new(pdf)
        obj.set_bitmap(pdfium.PdfBitmap.from_pil(img))
        w, h = img.width / ppi * 72, img.height / ppi * 72
        obj.set_matrix(pdfium.PdfMatrix().scale(w, h).translate(x, 400))
        page.insert_obj(obj)
    page.gen_content()
    buf = io.BytesIO()
    pdf.save(buf)
    doc = pdfium.PdfDocument(buf.getvalue())
    box = BBox(55, 842 - 400 - 130, 405, 842 - 395)
    crop = cropper.render_native_crop(doc[0], box, page_regions(doc[0]), page_w=595, page_h=842)
    assert not crop.aligned and crop.mode == "raster_native"
    assert crop.native_ppi == pytest.approx(72.0, rel=1e-3)
    assert crop.dpi == pytest.approx(300.0, abs=1.0)
