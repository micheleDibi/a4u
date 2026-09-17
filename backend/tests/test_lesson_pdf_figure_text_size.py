"""Corpo del testo delle figure nel PDF (D10, D11): oracolo WeasyPrint.

Per ogni caso della tabella (SVG fluidi 340×158, 507,8×158, 1200×420,
650×907, 300×1600 con testo a 14 px, più i renderer reali DOT, Vega-Lite e
`function`) la dispensa e le slide vengono rese da WeasyPrint
(`.render()`, nessun Chromium) e la camminata sui box legge la geometria
reale della figura (`div.mermaid-svg` per l'SVG inline, `img` per gli
altri corpi) e `box.style["font_size"]` delle didascalie; la scala è
`min(box_w/vb_w, box_h/vb_h)` e il corpo del testo `base_uu × 0,75 ×
scala`. I casi dichiarati fuori banda (box troppo stretto o troppo basso)
devono uscire davvero fuori banda con `in_band=False` nel report; tutti
gli altri devono cadere fra 8 e 11 pt in dispensa e fra 10 e 14 pt nelle
slide. Prima di D10 il flowchart v11 usciva a 13,3 pt in dispensa e a
19,9 pt nelle slide. I due casi di ripiego (misura Chromium assente:
metriche lette dal `<style>` come un pie e un radar di Mermaid 11) usano
come oracolo il corpo VERO scritto nel caso, non il parser: con la sola
regola radice il pie usciva a 13,36 pt in dispensa con `in_band=True`.
Nelle slide il box è quello della pagina resa (D12: 255 × 86,6 mm con
titolo e didascalia su una riga), non più il cap di 80 mm.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_pdf_service as pdf
from app.services import course_lesson_slides_pdf_service as slides_pdf
from app.services import figure_render_service as frs
from app.services import mermaid_prerender as mp
from app.services import slide_geometry
from app.services.figure_markup import FigureBox
from app.services.figure_scale import READABILITY_BANDS_PT, FigureFitEntry, SvgMetrics
from app.services.svg_normalize import svg_base_font_px, svg_intrinsic_box

_FIXTURES = Path(__file__).parent / "fixtures"
_MM = 96.0 / 25.4
# Tolleranza sulla lettura dei box di WeasyPrint (passo di 0,001 mm) e
# sull'arrotondamento per difetto della larghezza al centesimo.
_PT_TOL = 0.05


def _weasyprint() -> Any:
    try:
        import weasyprint
    except (ImportError, OSError) as exc:  # su macOS serve DYLD_FALLBACK_LIBRARY_PATH
        pytest.skip(f"weasyprint non importabile: {exc}")
    return weasyprint


def _walk(box: Any) -> Iterator[Any]:
    yield box
    children = getattr(box, "all_children", None)
    for child in children() if children else getattr(box, "children", []):
        yield from _walk(child)


def _box_class(box: Any) -> str:
    try:
        return (box.element.get("class") or "") if box.element is not None else ""
    except Exception:  # box anonimi senza elemento
        return ""


def _figure_geometry(weasyprint: Any, html: str, tag: str, css_class: str) -> dict[str, float]:
    """Larghezza e altezza (px CSS) del corpo della figura e corpo del
    carattere (px) della didascalia, letti dai box di WeasyPrint."""
    out: dict[str, float] = {}
    for page in weasyprint.HTML(string=html).render().pages:
        for box in _walk(page._page_box):
            classes = _box_class(box).split()
            if box.element_tag == tag and css_class in classes and "w" not in out:
                out["w"], out["h"] = float(box.width), float(box.height)
            if box.element_tag == "figcaption" and "caption_font_px" not in out:
                out["caption_font_px"] = float(box.style["font_size"])
    assert {"w", "h", "caption_font_px"} <= set(out), out
    return out


def _fluid_svg(vb_w: float, vb_h: float) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 {vb_w} {vb_h}">'
        f'<rect x="0" y="0" width="{vb_w}" height="{vb_h}" fill="#eee"/>'
        '<text x="10" y="20" font-size="14">Nodo</text></svg>'
    )


# Ripiego della misura: il corpo vero (17 e 12 px) sta solo nelle regole di
# classe del `<style>`, come nel pie e nel radar di Mermaid 11.
_PIE_LIKE = (
    '<svg xmlns="http://www.w3.org/2000/svg" id="p" width="100%" viewBox="0 0 400 300">'
    "<style>#p{font-size:14px}#p .slice{font-size:17px}#p .legend text{font-size:17px}</style>"
    '<rect width="400" height="300" fill="#eee"/><text class="slice" x="5" y="40">60%</text>'
    '<g class="legend"><text x="5" y="80">Teoria</text></g></svg>'
)
_RADAR_LIKE = (
    '<svg xmlns="http://www.w3.org/2000/svg" id="r" width="100%" viewBox="0 0 600 200">'
    "<style>#r{font-size:14px}#r .radarAxisLabel{font-size:12px}</style>"
    '<rect width="600" height="200" fill="#eee"/>'
    '<text class="radarAxisLabel" x="5" y="40">Analisi</text><text x="5" y="80">Titolo</text></svg>'
)
_TRUE_BASE_UU = {"ripiego pie-like (classi a 17)": 17.0, "ripiego radar-like (classe a 12)": 12.0}


def _v11_svg() -> str:
    return mp._strip_mermaid_max_width(
        (_FIXTURES / "mermaid11_flowchart.svg").read_text(encoding="utf-8")
    )


# (nome, formato, SVG o None per i renderer reali, fuori banda in dispensa,
#  fuori banda nelle slide)
_CASES: list[tuple[str, str, str | None, bool, bool]] = [
    ("fluido 340x158", "mermaid", _fluid_svg(340, 158), False, False),
    ("fluido 507.8x158", "mermaid", _fluid_svg(507.828125, 158), False, False),
    ("flowchart v11 misurato", "mermaid", None, False, False),
    ("fluido 1200x420 (box troppo stretto)", "mermaid", _fluid_svg(1200, 420), True, True),
    ("fluido 650x907 (slide troppo bassa)", "mermaid", _fluid_svg(650, 907), False, True),
    ("fluido 300x1600 (verticale)", "mermaid", _fluid_svg(300, 1600), True, True),
    ("dot reale", "dot", None, False, False),
    ("vegalite reale (slide: box 86,6 mm)", "vegalite", None, False, True),
    ("function reale (slide: box 86,6 mm)", "function", None, False, True),
    ("ripiego pie-like (classi a 17)", "mermaid", _PIE_LIKE, False, False),
    ("ripiego radar-like (classe a 12)", "mermaid", _RADAR_LIKE, False, False),
]


def _real_figure(fmt: str) -> frs.RenderedFigure | None:
    renderer = frs.REGISTRY[fmt]
    if not renderer.available():
        return None
    if fmt == "dot":
        svg = renderer.render_svg('digraph { rankdir=LR; Ipotesi -> Tesi [label="deduzione"] }')
    elif fmt == "vegalite":
        svg = renderer.render_svg(
            json.dumps(
                {
                    "data": {"values": [{"k": "a", "v": 3}, {"k": "b", "v": 5}]},
                    "mark": "bar",
                    "encoding": {
                        "x": {"field": "k", "type": "nominal"},
                        "y": {"field": "v", "type": "quantitative"},
                    },
                }
            )
        )
    else:
        svg = renderer.render_svg(
            json.dumps(
                {
                    "kind": "function_study",
                    "expressions": [{"expr": "x**2 - 1"}],
                    "domain": [-3, 3],
                    "show": ["zeros"],
                }
            )
        )
    assert svg, fmt
    return frs.RenderedFigure.from_svg(svg)


def _figure_for(fmt: str, svg: str | None) -> frs.RenderedFigure:
    if svg is not None:
        return frs.RenderedFigure.from_svg(svg)
    if fmt == "mermaid":
        return frs.RenderedFigure(_v11_svg(), SvgMetrics(14.0, 14.0, 6, "measured"))
    fig = _real_figure(fmt)
    assert fig is not None, f"renderer `{fmt}` non disponibile"
    return fig


def _content(fmt: str) -> dict[str, Any]:
    return {
        "introduction": "Vedi [FIG:A].",
        "sections": [],
        "summary": "",
        "visual_assets": [
            {
                "asset_id": "A",
                "format": fmt,
                "content": "x",
                "caption": "Didascalia",
                "alt_text": "",
            }
        ],
    }


def _lesson(fmt: str) -> CourseLesson:
    slides_raw = {
        "slides": [
            {
                "slide_id": "s1",
                "type": "concept",
                "title": "Titolo",
                "body": "",
                "bullets": [],
                "references_assets": ["A"],
            }
        ]
    }
    return CourseLesson(
        lesson_code="M1.L1", title="Lezione", content_raw=_content(fmt), slides_raw=slides_raw
    )


def _text_pt(
    fig: frs.RenderedFigure, geometry: dict[str, float], true_base_uu: float | None
) -> float:
    """Corpo del testo più piccolo alla geometria resa: `base_uu × 0,75 ×
    min(w/vb_w, h/vb_h)`, con `base_uu = font_px_min / px_per_unit` o il
    corpo vero del caso quando le metriche sono un ripiego."""
    box = svg_intrinsic_box(fig.svg)
    assert box is not None
    metrics = fig.metrics if fig.metrics is not None else svg_base_font_px(fig.svg)
    assert metrics.font_px_min is not None
    base_uu = metrics.font_px_min / box.px_per_unit
    if true_base_uu is not None:
        base_uu = true_base_uu
    scale = min(geometry["w"] / box.vb_w, geometry["h"] / box.vb_h)
    return base_uu * 0.75 * scale


def _check(
    variant: str,
    fig: frs.RenderedFigure,
    geometry: dict[str, float],
    report: list[FigureFitEntry],
    true_base_uu: float | None,
) -> tuple[float, bool]:
    assert len(report) == 1
    text_pt = _text_pt(fig, geometry, true_base_uu)
    lo, hi = READABILITY_BANDS_PT[variant]
    # Il corpo reso coincide con quello previsto dal fit (a meno del passo).
    assert text_pt == pytest.approx(report[0].text_pt, abs=_PT_TOL)
    return text_pt, lo - _PT_TOL <= text_pt <= hi + _PT_TOL


@pytest.mark.parametrize("case", _CASES, ids=[c[0] for c in _CASES])
def test_figure_text_size_in_the_lesson_pdf(case: tuple[str, str, str | None, bool, bool]) -> None:
    weasyprint = _weasyprint()
    name, fmt, svg, out_lesson, _out_slide = case
    fig = _figure_for(fmt, svg)
    report: list[FigureFitEntry] = []
    html = pdf.render_lesson_html(
        course=Course(title="Corso", language_code="it", cfu=6),
        lesson=_lesson(fmt),
        organization=None,
        pdf_template=None,
        visual_svg_map={"A": fig},
        fit_report=report,
    )
    tag, css_class = ("div", "mermaid-svg") if fmt == "mermaid" else ("img", "figure-svg")
    geometry = _figure_geometry(weasyprint, html, tag, css_class)
    assert geometry["caption_font_px"] == pytest.approx(12.0)  # didascalia 9pt (snake_case)
    text_pt, in_band = _check("lesson", fig, geometry, report, _TRUE_BASE_UU.get(name))
    assert report[0].in_band is (not out_lesson)
    if out_lesson:
        assert text_pt < READABILITY_BANDS_PT["lesson"][0], text_pt
    else:
        assert in_band, text_pt
    # Mai oltre il box della dispensa (170 mm × 242 mm).
    assert geometry["w"] / _MM <= 170.0 + 0.01 and geometry["h"] / _MM <= 242.0 + 0.01


@pytest.mark.parametrize("case", _CASES, ids=[c[0] for c in _CASES])
def test_figure_text_size_in_the_slides_pdf(case: tuple[str, str, str | None, bool, bool]) -> None:
    weasyprint = _weasyprint()
    name, fmt, svg, _out_lesson, out_slide = case
    fig = _figure_for(fmt, svg)
    report: list[FigureFitEntry] = []
    html = slides_pdf.render_slides_html(
        course=Course(title="Corso", language_code="it", cfu=6),
        lesson=_lesson(fmt),
        organization=None,
        slide_template=None,
        enable_split=False,
        visual_svg_map={"A": fig},
        fit_report=report,
    )
    css_class = "mermaid-svg" if fmt == "mermaid" else "figure-svg"
    geometry = _figure_geometry(weasyprint, html, "img", css_class)
    assert geometry["caption_font_px"] == pytest.approx(8 * 4 / 3)  # didascalia 8pt
    text_pt, in_band = _check("slide", fig, geometry, report, _TRUE_BASE_UU.get(name))
    assert report[0].in_band is (not out_slide)
    if out_slide:
        assert text_pt < READABILITY_BANDS_PT["slide"][0], text_pt
    else:
        assert in_band, text_pt
    # Mai oltre il box della pagina (D12): 255 mm × 86,6 mm con il titolo su
    # una riga e la didascalia su una riga (coda `function` compresa).
    box = _slide_box(fig, report)
    assert geometry["w"] / _MM <= box.w_mm + 0.01 and geometry["h"] / _MM <= box.h_mm + 0.01
    assert f'style="{box.style}"' in html


def _intrinsic_svg(w_px: int, h_px: int, font_px: float) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w_px}px" height="{h_px}px" '
        f'viewBox="0 0 {w_px} {h_px}"><rect width="{w_px}" height="{h_px}" fill="#eee"/>'
        f'<text x="5" y="20" font-size="{font_px}">Nodo</text></svg>'
    )


# (nome, larghezza e altezza px, corpo px, corpo atteso in pt o None se il
#  box in altezza ferma la crescita)
_GROWTH_CASES: list[tuple[str, int, int, float, float | None]] = [
    ("4,5 pt: cresce fino a 8 pt", 200, 300, 6.0, 8.0),
    ("4,5 pt: il box di 242 mm ferma la crescita", 200, 800, 6.0, None),
    ("9 pt: in banda, scala 1", 200, 300, 12.0, 9.0),
]


@pytest.mark.parametrize("case", _GROWTH_CASES, ids=[c[0] for c in _GROWTH_CASES])
def test_intrinsic_img_grows_at_most_to_the_band_floor(
    case: tuple[str, int, int, float, float | None],
) -> None:
    """Nella dispensa un `<img>` intrinseco con il testo sotto 8 pt cresce
    al più fino a 8 pt (altezza × 8 / corpo naturale) e mai oltre il box
    di 170 × 242 mm; in banda resta a scala 1. È la crescita che può
    anticipare un salto pagina (`dot_tiny` da 73 a 117 mm: documentata in
    09 e 17 §12)."""
    weasyprint = _weasyprint()
    _name, w_px, h_px, font_px, expected_pt = case
    fig = frs.RenderedFigure.from_svg(_intrinsic_svg(w_px, h_px, font_px))
    report: list[FigureFitEntry] = []
    html = pdf.render_lesson_html(
        course=Course(title="Corso", language_code="it", cfu=6),
        lesson=_lesson("dot"),
        organization=None,
        pdf_template=None,
        visual_svg_map={"A": fig},
        fit_report=report,
    )
    geometry = _figure_geometry(weasyprint, html, "img", "figure-svg")
    growth = geometry["h"] / h_px
    natural_pt = font_px * 0.75
    lo, _hi = READABILITY_BANDS_PT["lesson"]
    assert growth <= max(1.0, lo / natural_pt) + 1e-3, growth
    assert geometry["w"] / _MM <= 170.0 + 0.01 and geometry["h"] / _MM <= 242.0 + 0.01
    text_pt = natural_pt * growth
    assert text_pt == pytest.approx(report[0].text_pt, abs=_PT_TOL)
    if expected_pt is not None:
        assert report[0].in_band and text_pt == pytest.approx(expected_pt, abs=_PT_TOL)
    else:
        assert geometry["h"] / _MM == pytest.approx(242.0, abs=0.01)
        assert not report[0].in_band and text_pt < lo


def _slide_box(fig: frs.RenderedFigure, report: list[FigureFitEntry]) -> FigureBox:
    """Box atteso per la slide di prova: budget della pagina (titolo
    «Titolo», né prosa né bullet, un blocco) meno la didascalia su una
    riga («Figura. Didascalia»; il `content` di prova «x» non ha una coda
    calcolata in cache, quindi nessuna riga in più per `function`)."""
    budget = slide_geometry.page_figure_budget(title="Titolo", body="", bullets=[], n_blocks=1)
    box, squeezed = slide_geometry.image_box(budget, caption_text="Figura. Didascalia")
    assert budget.block_h_mm == 92.866 and not squeezed
    assert box.h_mm == 86.6, (report[0].fmt, fig.metrics)
    return box


def test_the_oracle_sees_the_pre_d10_geometry() -> None:
    """Controprova: la larghezza piena di prima (168 mm in dispensa, 255 mm
    nelle slide) porta il flowchart v11 a 13,3 e 19,9 pt, fuori banda su
    entrambe le superfici. Dimostra che l'oracolo vede la patologia."""
    fig = frs.RenderedFigure(_v11_svg(), SvgMetrics(14.0, 14.0, 6, "measured"))
    box = svg_intrinsic_box(fig.svg)
    assert box is not None
    lesson_pt = 14 * 0.75 * (168 * _MM / box.vb_w)
    slide_pt = 14 * 0.75 * min(255 * _MM / box.vb_w, 80 * _MM / box.vb_h)
    assert lesson_pt == pytest.approx(13.1, abs=0.1) and lesson_pt > 11
    assert slide_pt == pytest.approx(19.9, abs=0.1) and slide_pt > 14
