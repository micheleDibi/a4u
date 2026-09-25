"""Risoluzione effettiva delle figure di fonte (doc 18 §22, WP2).

- griglia della regola di stampa: mai sotto 100 ppi; good ≥ 200,
  acceptable ≥ 150; mai oltre la colonna né oltre 1,25 volte la misura
  naturale; senza dati la figura non è mai `unusable`;
- slide e frame: mai oltre 1,25 pixel del frame per pixel d'informazione,
  quindi ≥ 135 ppi nel PDF delle slide; la figura resta nel riquadro;
- regressioni sui dati del docente (M-A2b): il raster a 150 dpi non esce
  più a 120 ppi; le pagine delle slide 16:9 si normalizzano;
- resa: con gli ingressi della risoluzione la dispensa e le slide ricevono
  la larghezza della regola; senza (interruttore spento) resta la v1;
- PDF WeasyPrint vero: i ppi misurati nel PDF sono quelli della regola;
- resolver (DB): ingressi presenti con la regola attiva, assenti spenta.
"""

from __future__ import annotations

import base64
import io
import itertools
import math
import uuid
from typing import Any

import pytest
from PIL import Image, ImageDraw
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_pdf_service as pdf
from app.services import course_lesson_slides_pdf_service as slides_pdf
from app.services import remote_storage
from app.services.source_figure_resolution import (
    ACCEPTABLE_PPI,
    FRAME_PX_PER_MM,
    GOOD_PPI,
    LOW_PPI,
    NATURAL_WIDTH_SCALE,
    REFERENCE_COLUMN_MM,
    SLIDE_MAX_UPSCALE,
    ResolutionInputs,
    assess,
    class_for_ppi,
    information_width_px,
    natural_width,
    page_factor,
    plan_print,
    plan_slide,
    selection_class,
)
from app.services.source_figure_service import ResolvedSourceFigure, resolve_source_figures
from tests.course_builders import build_course, build_course_document
from tests.source_figure_builders import build_document_figure

LINE = "Fonte: Rossi, «Vibrometria laser», Dispense di misure, 2021, fig. 2.1, p. 3 (CC BY 4.0)"


def _inputs(
    width: int,
    *,
    height: int | None = None,
    dpi: float | None = 150,
    native: float | None = None,
    natural: float | None = None,
    vector: bool = False,
    bbox_w_pt: float | None = None,
    page_w_pt: float | None = None,
    source_kind: str = "uploaded",
) -> ResolutionInputs:
    return ResolutionInputs(
        width_px=width,
        height_px=height if height is not None else max(1, int(width * 0.6)),
        dpi=dpi,
        native_ppi=native,
        natural_width_mm=natural,
        is_vector=vector,
        bbox_w_pt=bbox_w_pt,
        page_w_pt=page_w_pt,
        source_kind=source_kind,
    )


# --- costanti ----------------------------------------------------------------


def test_thresholds_and_classes() -> None:
    assert (GOOD_PPI, ACCEPTABLE_PPI, LOW_PPI) == (200.0, 150.0, 100.0)
    assert class_for_ppi(200) == "good" and class_for_ppi(199.9) == "acceptable"
    assert class_for_ppi(150) == "acceptable" and class_for_ppi(149.9) == "low"
    assert class_for_ppi(100) == "low" and class_for_ppi(99.9) == "unusable"
    assert math.isclose(FRAME_PX_PER_MM, 1980 / 297)
    assert page_factor(595) == 1.0 and page_factor(612) == 1.0
    assert math.isclose(page_factor(960), 612 / 960)


# --- griglia della regola di stampa ------------------------------------------

_WIDTHS = (120, 300, 481, 710, 900, 1330, 2400, 4000)
_DPIS = (None, 72, 150, 220, 300, 600)
_NATIVES = (None, 51.0, 76.4, 96.0, 150.0, 300.0)
_NATURALS = (None, 20.0, 60.0, 101.6, 152.4, 250.0)
_COLUMNS = (170.0, 150.0)


def _grid() -> list[tuple[Any, ...]]:
    return list(itertools.product(_WIDTHS, _DPIS, _NATIVES, _NATURALS, (False, True), _COLUMNS))


@pytest.mark.parametrize("max_height", [None, 60.0])
def test_print_rule_invariants_on_a_grid(max_height: float | None) -> None:
    checked = 0
    for width, dpi, native, natural, vector, column in _grid():
        inputs = _inputs(width, dpi=dpi, native=native, natural=natural, vector=vector)
        plan = plan_print(inputs, column_mm=column, max_height_mm=max_height)
        assessment = assess(inputs, column_mm=column)
        assert plan is not None and assessment is not None
        info = information_width_px(inputs)
        assert info is not None
        ppi = info / (plan.width_mm / 25.4)
        case = (width, dpi, native, natural, vector, column, plan)
        # Mai sotto 100 ppi, in qualunque classe.
        assert ppi >= LOW_PPI - 1e-6, case
        assert plan.width_mm <= column + 1e-9, case
        nat, _basis = natural_width(inputs)
        assert plan.width_mm <= NATURAL_WIDTH_SCALE * nat + 0.05, case
        if max_height is not None and inputs.height_px and inputs.width_px:
            assert plan.width_mm * inputs.height_px / inputs.width_px <= max_height + 0.1, case
        if plan.resolution_class == "good":
            assert ppi >= GOOD_PPI - 1e-6, case
        if plan.resolution_class == "acceptable":
            assert ppi >= ACCEPTABLE_PPI - 1e-6, case
        checked += 1
    assert checked == len(_grid())


def test_missing_data_is_never_unusable() -> None:
    """Righe storiche senza dpi né misura (DOCX/PPTX v1, OpenAlex v1): la
    misura è una stima, quindi la figura non si esclude."""
    tiny = _inputs(120, dpi=None, native=None, natural=None)
    assert natural_width(tiny) == (90.0, "estimate")
    assert selection_class(tiny) == "low"
    plan = plan_print(tiny)
    assert plan is not None and 120 / (plan.width_mm / 25.4) >= LOW_PPI
    # Commons raster: la misura convenzionale vale, quindi può essere unusable.
    commons = _inputs(300, dpi=None, source_kind="wikimedia")
    assert natural_width(commons) == (90.0, "convention")
    assert selection_class(commons) == "unusable"
    # Commons SVG reso a 1920 px: sempre good.
    assert selection_class(_inputs(1920, dpi=None, vector=True, source_kind="wikimedia")) == "good"
    # Senza pixel: classe ignota (la figura non si rende comunque).
    assert selection_class(_inputs(0)) is None


def test_teacher_raster_at_150_dpi_is_acceptable_at_its_natural_size() -> None:
    """M-A2: 50 collocazioni uscivano a 120 ppi (ritaglio a 150 dpi
    ingrandito ×1,25). Con la regola nuova escono alla misura naturale."""
    inputs = _inputs(710, dpi=150, native=150.0, bbox_w_pt=710 / 150 * 72, page_w_pt=439.37)
    plan = plan_print(inputs)
    assert plan is not None and plan.resolution_class == "acceptable"
    assert math.isclose(plan.width_mm, 120.2, abs_tol=0.1)
    assert 710 / (plan.width_mm / 25.4) >= ACCEPTABLE_PPI


def test_slide_pages_are_normalized_and_information_pixels_count() -> None:
    """M-A2b: «LE VIBRAZIONI» è una presentazione esportata (960 pt). Il
    ritaglio v1 a 150 dpi di un raster a 76 ppi ha metà dei pixel
    d'informazione: classe low, stampa sempre ≥ 100 ppi."""
    inputs = _inputs(1330, height=700, dpi=150, native=76.4, bbox_w_pt=1330 / 150 * 72)
    wide = _inputs(1330, height=700, dpi=150, native=76.4, bbox_w_pt=638.4, page_w_pt=960)
    assert information_width_px(inputs) == pytest.approx(1330 * 76.4 / 150)
    assert natural_width(wide)[0] == pytest.approx(638.4 / 72 * 25.4 * 612 / 960)
    plan = plan_print(wide)
    assert plan is not None and plan.resolution_class == "low"
    assert plan.ppi >= LOW_PPI
    # La 1330 px di prima usciva a 199 ppi solo in apparenza (pixel ripetuti).
    assert plan.width_mm < 1330 / 150 * 25.4


def test_placed_unusable_figure_prints_at_100_ppi() -> None:
    """U1: una figura già collocata sotto soglia resta, a 100 ppi."""
    inputs = _inputs(407, dpi=150, native=51.1, bbox_w_pt=407 / 150 * 72, page_w_pt=595)
    assert selection_class(inputs) == "unusable"
    plan = plan_print(inputs)
    info = 407 * 51.1 / 150
    assert plan is not None and plan.width_mm == math.floor(info * 0.254 * 10) / 10
    assert plan.ppi >= LOW_PPI


def test_good_figures_are_enlarged_at_most_to_the_column_and_125_percent() -> None:
    vector = _inputs(2400, dpi=400, vector=True, bbox_w_pt=432, page_w_pt=595)
    plan = plan_print(vector)
    assert plan is not None and plan.resolution_class == "good"
    assert plan.width_mm == REFERENCE_COLUMN_MM  # 1,25 · 152,4 > 170
    small = _inputs(1181, dpi=600, vector=True, natural=50.0)
    plan_small = plan_print(small)
    assert plan_small is not None and plan_small.width_mm == 62.5


# --- slide e frame -------------------------------------------------------------


@pytest.mark.parametrize(("box_w", "box_h"), [(255.0, 86.6), (255.0, 40.0), (120.0, 100.0)])
def test_slide_rule_never_upscales_beyond_125_percent(box_w: float, box_h: float) -> None:
    for width, dpi, native, _natural, vector, _column in _grid():
        inputs = _inputs(width, dpi=dpi, native=native, vector=vector)
        plan = plan_slide(inputs, box_w_mm=box_w, box_h_mm=box_h)
        info = information_width_px(inputs)
        assert plan is not None and info is not None
        case = (width, dpi, native, vector, plan)
        assert plan.width_mm * FRAME_PX_PER_MM / info <= SLIDE_MAX_UPSCALE + 1e-9, case
        assert plan.upscale <= SLIDE_MAX_UPSCALE + 1e-9, case
        # Nel PDF delle slide: ≥ 135 ppi (25,4 · 6,667 / 1,25).
        assert info / (plan.width_mm / 25.4) >= 135.0, case
        assert plan.width_mm <= box_w + 1e-9, case
        assert inputs.height_px is not None and inputs.width_px is not None
        assert plan.width_mm * inputs.height_px / inputs.width_px <= box_h + 0.1, case


# --- resa ---------------------------------------------------------------------------


def _png(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 7):
        draw.line([(x, 0), (x, height)], fill="navy", width=1)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _resolved(inputs: ResolutionInputs | None, *, display: float | None = None) -> Any:
    width = inputs.width_px if inputs is not None and inputs.width_px else 320
    height = inputs.height_px if inputs is not None and inputs.height_px else 200
    return ResolvedSourceFigure(
        True,
        figure_id=uuid.uuid4(),
        data_url="data:image/png;base64," + base64.b64encode(_png(width, height)).decode("ascii"),
        mime_type="image/png",
        width=width,
        height=height,
        attribution_text=LINE,
        display_width_mm=display,
        resolution=inputs,
    )


def _asset(asset_id: str = "src-1") -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "format": "source_figure",
        "content": str(uuid.uuid4()),
        "caption": "Schema del vibrometro.",
        "alt_text": "schema",
    }


def test_lesson_block_uses_the_print_rule_on_the_template_column() -> None:
    inputs = _inputs(900, height=600, dpi=150, native=150.0, bbox_w_pt=432, page_w_pt=612)
    block = pdf._render_visual_asset_block(
        _asset(),
        variant="lesson",
        source_figures={"src-1": _resolved(inputs, display=999.0)},
        figure_box_mm=(150.0, 240.0),
    )
    expected = plan_print(inputs, column_mm=150.0, max_height_mm=210.0)
    assert expected is not None
    assert f'style="width: {expected.width_mm}mm"' in block
    assert "999.0mm" not in block


def test_kill_switch_keeps_the_v1_width_in_the_lesson_only() -> None:
    blocks = {
        variant: pdf._render_visual_asset_block(
            _asset(), variant=variant, source_figures={"src-1": _resolved(None, display=62.5)}
        )
        for variant in ("lesson", "slide")
    }
    assert "width: 62.5mm" in blocks["lesson"]
    assert "width: 62.5mm" not in blocks["slide"]


def _slides_html(resolved: Any) -> str:
    lesson = CourseLesson(
        lesson_code="M1.L2",
        title="Vibrometria",
        content_raw={
            "introduction": "Intro.",
            "sections": [],
            "summary": "Sintesi.",
            "visual_assets": [_asset()],
        },
        slides_raw={
            "slides": [
                {
                    "slide_id": "s1",
                    "type": "diagram",
                    "title": "Schema",
                    "body": "Lo schema.",
                    "references_assets": ["src-1"],
                }
            ]
        },
    )
    return slides_pdf.render_slides_html(
        course=Course(title="Misure", language_code="it", cfu=6),
        lesson=lesson,
        organization=None,
        slide_template=None,
        visual_svg_map={},
        enable_split=True,
        source_figures={"src-1": resolved},
    )


def test_slide_block_width_never_upscales_the_frame_beyond_125_percent() -> None:
    import re

    inputs = _inputs(400, height=250, dpi=150, native=150.0, bbox_w_pt=192, page_w_pt=595)
    html = _slides_html(_resolved(inputs))
    match = re.search(r'<img class="source-figure" src="[^"]+" style="width: ([0-9.]+)mm"', html)
    assert match, html[:2000]
    width = float(match.group(1))
    assert width * FRAME_PX_PER_MM / 400 <= SLIDE_MAX_UPSCALE + 1e-9
    assert width == math.floor(1.25 * 400 / FRAME_PX_PER_MM * 10) / 10
    # Interruttore spento: nessuna larghezza, resta il riquadro del CSS.
    off = _slides_html(_resolved(None, display=62.5))
    assert not re.search(r'class="source-figure" src="[^"]+" style="width', off)


# --- PDF WeasyPrint vero: ppi misurati -------------------------------------------


def _pdf_image_widths_pt(data: bytes) -> list[tuple[int, float]]:
    """(larghezza in pixel, larghezza disegnata in pt) di ogni immagine."""
    pdfium = pytest.importorskip("pypdfium2")
    raw = pytest.importorskip("pypdfium2.raw")
    doc = pdfium.PdfDocument(data)
    out: list[tuple[int, float]] = []
    for page in doc:
        for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=15):
            matrix = obj.get_matrix()
            container = obj.container
            while container is not None:
                matrix = matrix.multiply(container.get_matrix())
                container = container.container
            a, b, _c, _d, _e, _f = matrix.get()
            px_w, _px_h = obj.get_px_size()
            out.append((int(px_w), math.hypot(a, b)))
    return out


def test_printed_ppi_measured_in_the_weasyprint_pdf() -> None:
    from tests.test_lesson_pdf_figures import _weasyprint

    weasyprint = _weasyprint()
    cases = {
        # acceptable: 150 ppi alla misura naturale (152,4 mm).
        "acc": _inputs(900, height=600, dpi=150, native=150.0, bbox_w_pt=432, page_w_pt=612),
        # low: rimpicciolita verso 150 ppi (pavimento 0,8·N).
        "low": _inputs(600, height=400, dpi=150, native=120.0, bbox_w_pt=288, page_w_pt=612),
        # unusable già collocata: 100 ppi.
        "bad": _inputs(300, height=200, dpi=150, native=60.0, bbox_w_pt=144, page_w_pt=612),
    }
    assets = [_asset(key) for key in cases]
    lesson = CourseLesson(
        lesson_code="M1.L2",
        title="Vibrometria",
        content_raw={
            "introduction": "Introduzione.",
            "sections": [
                {
                    "section_id": "S1",
                    "title": "Schemi",
                    "content": "Primo [FIG:acc].\n\nSecondo [FIG:low].\n\nTerzo [FIG:bad].",
                }
            ],
            "summary": "Sintesi.",
            "visual_assets": assets,
        },
    )
    html = pdf.render_lesson_html(
        course=Course(title="Misure", language_code="it", cfu=6),
        lesson=lesson,
        organization=None,
        pdf_template=None,
        visual_svg_map={},
        source_figures={key: _resolved(inputs) for key, inputs in cases.items()},
    )
    data = weasyprint.HTML(string=html).write_pdf()
    drawn = dict(_pdf_image_widths_pt(data))
    for key, inputs in cases.items():
        plan = plan_print(inputs, column_mm=170.0)
        assert plan is not None
        assert inputs.width_px in drawn, (key, drawn)
        width_mm = drawn[inputs.width_px] / 72 * 25.4
        info = information_width_px(inputs)
        assert info is not None
        assert width_mm == pytest.approx(plan.width_mm, abs=0.3), key
        assert info / (width_mm / 25.4) >= LOW_PPI - 0.5, key
    acc_mm = drawn[900] / 72 * 25.4
    assert 900 / (acc_mm / 25.4) >= ACCEPTABLE_PPI - 0.5


# --- resolver (DB) ------------------------------------------------------------------


class _Storage:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def download_bytes(self, key: str) -> bytes:
        return self.files[key]


@pytest.mark.parametrize("enabled", [True, False])
async def test_resolver_carries_resolution_inputs_only_with_the_rule(
    seeded_db: AsyncSession, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    db = seeded_db
    storage = _Storage()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: storage)
    monkeypatch.setattr(get_settings(), "figure_resolution_rules_enabled", enabled)
    course_id, _org, _user = await build_course(db, modules=1, lessons_per_module=1)
    doc = build_course_document(course_id, filename="ldv.pdf")
    db.add(doc)
    await db.flush()
    fig = build_document_figure(
        course_id, doc.id, license="cc_by", native_ppi=150.0, natural_width_mm=120.0
    )
    db.add(fig)
    await db.commit()
    storage.files[remote_storage.uploads_key(str(fig.storage_path))] = _png(64, 48)
    out = await resolve_source_figures(
        db,
        course_id=course_id,
        assets=[{"asset_id": "a", "format": "source_figure", "content": str(fig.id)}],
        language="it",
    )
    resolved = out["a"]
    assert resolved.renderable
    if enabled:
        assert resolved.resolution == ResolutionInputs.from_figure(fig)
        assert resolved.resolution.natural_width_mm == 120.0
    else:
        assert resolved.resolution is None
