"""Formato `tikz` nel registro dei renderer (WP6.2).

Senza TeX (ovunque):
- spento di default: fuori da `available_formats`, un PATCH con un asset
  `tikz` risponde 422 `figure_format_unavailable`;
- validazione statica = lexer (e script dei font);
- traduzione dei testi dei nodi (D7) con escape di TeX, formule intatte;
  una traduzione che il lexer rifiuta torna all'originale;
- dispensa: una figura `tikz` senza SVG è il segnaposto numerato, mai il
  sorgente TeX in pagina.

Con TeX (container `test`, altrimenti `[dep:tex]`):
- acceso: disponibile (autotest), validazione profonda con compilazione;
  i difetti geometrici bloccano in generazione, nel PATCH sono avvisi;
- resa con le metriche del testo dal PDF e cache.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import ValidationAppError
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_pdf_service as pdf
from app.services import figure_render_service as frs
from app.services import tikz_compile_service
from tests.dep_guard import require_binary
from tests.test_tikz_validator import CHAIN

OVERLAP = r"""\begin{tikzpicture}
  \node[draw] (a) at (0,0) {Condizionamento};
  \node[draw] (b) at (0.5,0) {Sensore};
\end{tikzpicture}"""


def _enable(monkeypatch: pytest.MonkeyPatch, enabled: bool = True) -> None:
    patched = frs.get_settings().model_copy(update={"figure_tikz_enabled": enabled})
    monkeypatch.setattr(frs, "get_settings", lambda: patched)
    monkeypatch.setattr(tikz_compile_service, "get_settings", lambda: patched)
    frs.available_formats.cache_clear()
    tikz_compile_service.available.cache_clear()
    frs.clear_svg_cache()


@pytest.fixture(autouse=True)
def _reset_caches() -> Any:
    yield
    frs.available_formats.cache_clear()
    tikz_compile_service.available.cache_clear()
    frs.clear_svg_cache()


def test_off_by_default_and_patch_refuses_it() -> None:
    renderer = frs.REGISTRY["tikz"]
    assert renderer.available() is False
    assert "tikz" not in frs.available_formats()
    import asyncio

    with pytest.raises(ValidationAppError) as excinfo:
        asyncio.run(
            frs.validate_visual_assets_or_raise(
                [{"asset_id": "t1", "format": "tikz", "content": CHAIN}],
                previous=[],
                loc_root="visual_assets",
                code="lesson_content_invalid_visual_asset",
            )
        )
    (error,) = excinfo.value.meta["errors"]
    assert error["type"] == frs.FIGURE_FORMAT_UNAVAILABLE


def test_static_validation_is_the_lexer() -> None:
    renderer = frs.REGISTRY["tikz"]
    assert renderer.validate(CHAIN) == (True, "")
    ok, message = renderer.validate(r"\begin{tikzpicture}\input{x}\end{tikzpicture}")
    assert not ok and message.startswith("tikz_source_invalid: control_word")
    ok, message = renderer.validate("```latex\n" + CHAIN + "\n```")
    assert ok, message


def test_node_texts_are_translated_with_tex_escaping() -> None:
    renderer = frs.REGISTRY["tikz"]
    fields = renderer.extract_translatable(CHAIN)
    assert fields == {"node.0": "Sensore", "node.1": "Condizionamento", "node.2": "ADC"}
    translated = renderer.apply_translations(
        CHAIN, {"node.0": "Sensor", "node.1": "Signal conditioning & filter"}
    )
    assert "{Sensor}" in translated and r"{Signal conditioning \& filter}" in translated
    assert "$v(t)$" in translated  # le formule restano
    assert renderer.validate(translated)[0]


def test_tikz_without_svg_is_a_numbered_placeholder() -> None:
    lesson = CourseLesson(
        lesson_code="M1.L1",
        title="Misure",
        content_raw={
            "introduction": "Vedi [FIG:t1].",
            "sections": [],
            "summary": "",
            "visual_assets": [
                {"asset_id": "t1", "format": "tikz", "content": CHAIN, "caption": "Catena."}
            ],
        },
    )
    html = pdf.render_lesson_html(
        course=Course(title="Misure", language_code="it", cfu=6),
        lesson=lesson,
        organization=None,
        pdf_template=None,
        visual_svg_map={},
    )
    import re

    block = re.search(r'<figure[^>]*data-asset-id="t1".*?</figure>', html, re.S)
    assert block is not None
    assert "Figura non disponibile." in block.group(0) and "Figura 1." in block.group(0)
    assert "tikzpicture" not in html and 'class="figure-fallback' not in block.group(0)


@pytest.fixture
def tex(monkeypatch: pytest.MonkeyPatch) -> None:
    require_binary("tex", "xelatex", "kpsewhich", "pdftocairo")
    _enable(monkeypatch)


def test_enabled_renderer_compiles_and_measures(tex: None) -> None:
    renderer = frs.REGISTRY["tikz"]
    assert renderer.available() and "tikz" in frs.available_formats()
    assert renderer.validate(CHAIN, deep=True) == (True, "")
    figure = renderer.render_figure(CHAIN, asset_id="t1")
    assert figure is not None and figure.svg.startswith("<svg")
    assert figure.metrics is not None and figure.metrics.font_px_min
    assert figure.metrics.defects == ()
    assert renderer.render_figure(CHAIN, asset_id="t1") is figure  # cache


def test_geometry_blocks_generation_but_only_warns_in_patch(tex: None) -> None:
    renderer = frs.REGISTRY["tikz"]
    ok, message = renderer.validate(OVERLAP, deep=True)
    assert not ok and message.startswith("difetti geometrici: labels_overlap")
    assert renderer.validate(OVERLAP, deep=True, strict_geometry=False) == (True, "")
    import asyncio

    asyncio.run(
        frs.validate_visual_assets_or_raise(
            [{"asset_id": "t1", "format": "tikz", "content": OVERLAP}],
            previous=[],
            loc_root="visual_assets",
            code="lesson_content_invalid_visual_asset",
        )
    )


def test_patch_reports_compile_errors(tex: None) -> None:
    import asyncio

    broken = r"\begin{tikzpicture}\draw (a) -- (nessuno);\end{tikzpicture}"
    with pytest.raises(ValidationAppError) as excinfo:
        asyncio.run(
            frs.validate_visual_assets_or_raise(
                [{"asset_id": "t1", "format": "tikz", "content": broken}],
                previous=[],
                loc_root="visual_assets",
                code="lesson_content_invalid_visual_asset",
            )
        )
    (error,) = excinfo.value.meta["errors"]
    assert "tikz_compile_failed" in error["msg"] and error["loc"] == ["visual_assets", 0, "content"]


def test_slides_cannot_create_tikz_new_assets() -> None:
    """Le slide referenziano le `tikz` della dispensa, non ne creano: la
    vista `tikz-view` rende solo sorgenti di `content_raw` (verifica WP6)."""
    from pydantic import ValidationError

    from app.schemas.course_lesson_content import LessonContentVisualAsset
    from app.schemas.course_lesson_slides import LessonSlideNewAsset

    asset = {"asset_id": "n1", "format": "tikz", "content": CHAIN}
    assert LessonContentVisualAsset.model_validate(asset).format == "tikz"
    with pytest.raises(ValidationError):
        LessonSlideNewAsset.model_validate(asset)
