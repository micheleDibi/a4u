"""Fascia «Fonte» delle slide e dei frame video (U2, G1): la riga si vede.

La fascia `.slide-attribution` ha altezza fissa (4 righe, `overflow:
hidden`): una riga troppo lunga, o più figure di fonte sulla stessa pagina,
non devono mai finire nella parte tagliata.

- stima pura: `fitted_written_line` sta nel budget in em e tiene sempre
  anno, figura, pagina e licenza; una riga corta resta identica a quella
  della dispensa;
- Chromium (come i frame video, CSS del video compreso) con 1-4 figure di
  fonte dalle bibliografie più lunghe ammesse: `scrollHeight <= clientHeight`
  e ogni licenza visibile, anche con un font più largo (Verdana) come
  prova di stress della stima;
- WeasyPrint (PDF slide): ogni riga di testo della fascia finisce dentro la
  fascia;
- dalla quinta figura di fonte sulla stessa pagina: segnaposto, mai
  un'immagine senza la sua riga.
"""

from __future__ import annotations

import asyncio
import base64
import io
import uuid
from typing import Any

import pytest
from PIL import Image

from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_slides_pdf_service as slides_pdf
from app.services.figure_attribution import (
    AttributionSource,
    attribution_line,
    fitted_written_line,
    license_label,
    text_em,
)
from app.services.slide_geometry import SlideGeometry
from app.services.source_figure_service import ResolvedSourceFigure, band_texts
from tests.test_lesson_pdf_figures import _weasyprint

_LONG_NAME = "Maximilian Alexander Konstantinopoulos-Wetherby"
_TITLE_WORDS = (
    "Laser Doppler vibrometry for non contact measurement of structural vibrations "
    "in rotating machinery and lightweight aerospace components under harsh conditions "
)

SOURCES: dict[str, AttributionSource] = {
    "latin": AttributionSource(
        authors=tuple(f"{_LONG_NAME} {i}" for i in range(5)),
        title=(_TITLE_WORDS * 4)[:500],
        container=("Proceedings of the International Conference on Vibration " * 6)[:300],
        year=2021,
        figure_number="12.4",
        page=1234,
        license="cc_by_nc_sa",
    ),
    "caps": AttributionSource(
        authors=("WOLFGANG MAXIMILIAN WEHRMACHER", "MARGARETHE WILHELMINA MOMMSEN"),
        title=(_TITLE_WORDS.upper() * 4)[:500],
        container="MEASUREMENT SCIENCE AND TECHNOLOGY",
        year=2019,
        figure_number="7",
        page=88,
        license="cc_by_nd",
    ),
    "cjk": AttributionSource(
        authors=("王小明", "李华", "张伟", "陈静"),
        title=("激光多普勒测振仪的原理与应用及其在旋转机械振动测量中的实验研究" * 10)[:300],
        container=("中国机械工程学会振动与冲击学报" * 10)[:120],
        year=2020,
        figure_number="3.2",
        page=45,
        license="cc_by",
    ),
    "fallback": AttributionSource(
        fallback_name=("appunti del corso di misure meccaniche e termiche " * 5)[:200],
        figure_number="1",
        page=3,
        license="cc_by_sa",
    ),
    "long_word": AttributionSource(
        authors=("Rossi",),
        title="Pneumonoultramicroscopicsilicovolcanoconiosis" * 5,
        year=2018,
        page=12,
        license="cc0",
    ),
}


def _tail(src: AttributionSource) -> list[str]:
    parts = [str(src.year)] if src.year else []
    if src.figure_number:
        parts.append(f"fig. {src.figure_number}")
    if src.page:
        parts.append(f"p. {src.page}")
    label = license_label(src.license, language="it")
    if label:
        parts.append(f"({label})")
    return parts


@pytest.mark.parametrize("name", sorted(SOURCES))
@pytest.mark.parametrize("lines", [1, 2])
def test_fitted_line_fits_and_keeps_the_tail(name: str, lines: int) -> None:
    src = SOURCES[name]
    budget = SlideGeometry().attribution_budget_em(lines)
    text = fitted_written_line(src, language="it", max_em=budget)
    assert text_em(text) <= budget
    assert text.startswith("Fonte: ")
    for part in _tail(src):
        assert part in text, (part, text)
    # Il nome della fonte (autore, credito o file) resta riconoscibile.
    who = (src.authors[0] if src.authors else src.fallback_name or "")[:5]
    assert who in text


def test_short_line_is_identical_to_the_lesson_line() -> None:
    src = AttributionSource(
        authors=("Mario Rossi",),
        title="Vibrometria laser",
        container="Dispense di misure",
        year=2021,
        figure_number="2.1",
        page=3,
        license="cc_by",
    )
    full = attribution_line(src, language="it")
    assert band_texts(src, language="it") == (full, full)


# --- resa ------------------------------------------------------------------------------


def _data_url() -> str:
    image = Image.new("RGB", (320, 200), "white")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _resolved(src: AttributionSource) -> ResolvedSourceFigure:
    band, short = band_texts(src, language="it")
    return ResolvedSourceFigure(
        True,
        figure_id=uuid.uuid4(),
        data_url=_data_url(),
        mime_type="image/png",
        width=320,
        height=200,
        attribution_text=attribution_line(src, language="it"),
        band_text=band,
        band_text_short=short,
    )


def _slides_html(names: list[str], *, enable_split: bool) -> tuple[str, dict[str, Any]]:
    ids = [f"src-{i}" for i in range(len(names))]
    lesson = CourseLesson(
        lesson_code="M1.L2",
        title="Vibrometria",
        content_raw={
            "introduction": "Intro.",
            "sections": [],
            "summary": "Sintesi.",
            "visual_assets": [
                {
                    "asset_id": asset_id,
                    "format": "source_figure",
                    "content": str(uuid.uuid4()),
                    "caption": f"Figura di fonte {i}.",
                    "alt_text": "schema",
                }
                for i, asset_id in enumerate(ids)
            ],
        },
        slides_raw={
            "slides": [
                {
                    "slide_id": "s1",
                    "type": "diagram",
                    "title": "Schemi",
                    "body": "Gli schemi a confronto.",
                    "references_assets": ids,
                }
            ]
        },
    )
    resolved = {asset_id: _resolved(SOURCES[n]) for asset_id, n in zip(ids, names, strict=True)}
    html = slides_pdf.render_slides_html(
        course=Course(title="Misure", language_code="it", cfu=6),
        lesson=lesson,
        organization=None,
        slide_template=None,
        visual_svg_map={},
        enable_split=enable_split,
        source_figures=resolved,
    )
    return html, resolved


_CASES = [
    ["latin"],
    ["cjk"],
    ["latin", "caps"],
    ["cjk", "fallback"],
    ["latin", "caps", "cjk"],
    ["latin", "caps", "cjk", "long_word"],
]
_STRESS_FONT = "<style>.slide-attribution{font-family:Verdana,'DejaVu Sans',sans-serif}</style>"


async def _measure(html: str) -> list[dict[str, Any]]:
    from playwright.async_api import async_playwright  # type: ignore

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        try:
            ctx = await browser.new_context(
                viewport={"width": 1980, "height": 1400}, java_script_enabled=False
            )
            page = await ctx.new_page()
            await page.set_content(html)
            result: list[dict[str, Any]] = await page.evaluate(
                """() => [...document.querySelectorAll('.slide-attribution')].map(e => ({
                    scroll: e.scrollHeight, client: e.clientHeight, text: e.innerText}))"""
            )
            return result
        finally:
            await browser.close()


@pytest.mark.parametrize("stress", [False, True], ids=["theme-font", "verdana"])
@pytest.mark.parametrize("names", _CASES, ids=["+".join(c) for c in _CASES])
def test_video_frame_band_shows_every_line(names: list[str], stress: bool) -> None:
    from tests.dep_guard import require_module

    require_module("playwright", "playwright")
    from app.services import lesson_slides_video_render_service as video

    html, _resolved_map = _slides_html(names, enable_split=False)
    extra = video._VIDEO_OVERRIDE_CSS + (_STRESS_FONT if stress else "")
    html = html.replace("</head>", extra + "</head>", 1)
    try:
        bands = asyncio.run(_measure(html))
    except Exception as exc:  # Chromium di Playwright non installato
        pytest.skip(f"[dep:chromium] {exc}")
    assert len(bands) == 1
    band = bands[0]
    assert band["scroll"] <= band["client"], band
    text = " ".join(band["text"].split())
    for name in names:
        label = license_label(SOURCES[name].license, language="it")
        assert f"p. {SOURCES[name].page}" in text
        if label:
            assert f"({label})" in text


def _band_overflow_pt(html: str) -> list[float]:
    """Per ogni fascia del PDF WeasyPrint: di quanto la riga più bassa
    sporge oltre il fondo della fascia (≤ 0 se tutto è visibile)."""
    weasyprint = _weasyprint()
    from weasyprint.formatting_structure.boxes import LineBox

    document = weasyprint.HTML(string=html).render()
    out: list[float] = []
    for page in document.pages:
        stack = [page._page_box]
        while stack:
            box = stack.pop()
            element = getattr(box, "element", None)
            classes = (element.get("class") or "") if element is not None else ""
            if "slide-attribution" in classes.split() and hasattr(box, "children"):
                bottom = box.content_box_y() + box.height
                lines = [d for d in box.descendants() if isinstance(d, LineBox)]
                assert lines
                out.append(max(d.position_y + d.height for d in lines) - bottom)
                continue
            stack.extend(getattr(box, "children", []) or [])
    return out


@pytest.mark.parametrize("names", _CASES, ids=["+".join(c) for c in _CASES])
def test_slide_pdf_band_shows_every_line(names: list[str]) -> None:
    html, _resolved_map = _slides_html(names, enable_split=True)
    overflows = _band_overflow_pt(html)
    assert overflows and max(overflows) <= 0.5, overflows


def test_slide_pdf_band_counterproof_detects_a_clipped_line() -> None:
    """Controprova: con la riga intera (non accorciata) la misura vede il
    taglio, quindi l'oracolo WeasyPrint funziona davvero."""
    html, resolved = _slides_html(["latin", "caps"], enable_split=True)
    for item in resolved.values():
        html = html.replace(
            item.band_text.replace("«", "&laquo;").replace("»", "&raquo;"), item.attribution_text
        ).replace(item.band_text, item.attribution_text)
    assert max(_band_overflow_pt(html)) > 0.5


def test_fifth_source_figure_on_a_page_is_a_placeholder() -> None:
    html, _resolved_map = _slides_html(
        ["latin", "caps", "cjk", "fallback", "long_word"], enable_split=False
    )
    page = html.split('<div class="slide">')[1]
    assert page.count('class="source-figure"') == 4
    assert page.count('<span class="source-line">') == 4
    assert page.count("Figura non disponibile.") == 1
