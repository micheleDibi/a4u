"""Prestazioni L1 — W2: dispensa e figure non bloccano l'event loop.

Contratto `docs/contracts/perf-l1-conventions.md` §1: storage, PIL, misure
geometriche SVG e latex2mathml girano in un thread. Ogni caso rende lento
apposta il passo sincrono (`time.sleep`) e una sonda sullo stesso loop deve
restare sotto 100 ms; il risultato deve essere quello di prima.
"""

from __future__ import annotations

import base64
import io
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from app.core import config
from app.schemas.course_lesson_content import LessonContentUpdateInput
from app.services import asset_validation_service as avs
from app.services import course_lesson_content_crud as content_crud
from app.services import course_lesson_content_service as content_svc
from app.services import figure_render_service as frs
from app.services import (
    openai_figure_describe_service,
    openai_figure_relevance_service,
    openai_tikz_render_review_service,
    remote_storage,
)
from app.services.openai_figure_review_service import FigureMeasure, ReviewContext
from tests.course_builders import build_course, build_lesson_content_output, find_lesson
from tests.test_perf_l1_offloop_pdf import MAX_LAG_SEC, SLOW_SEC, _run_with_probe

# SVG minimo con testo: abbastanza per la geometria (viewBox e corpo).
_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="240pt" height="120pt" '
    'viewBox="0 0 240 120"><text x="10" y="20" font-size="14">nodo a</text>'
    '<text x="10" y="60" font-size="14">nodo b</text></svg>'
)
_DOT = "digraph G {\n  a -> b;\n  b -> c;\n}"
_DOT_SMALL = "digraph H { x -> y; }"


@pytest.fixture(autouse=True)
def _clean_caches() -> Iterator[None]:
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()
    yield
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()


def _png(w: int = 1600, h: int = 900) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _lag_msg(lag: float) -> str:
    return f"loop fermo per {lag * 1000:.0f} ms"


# ---------------------------------------------------------------------------
# Dispensa: delete degli asset immagine rimossi
# ---------------------------------------------------------------------------


class _SlowDeleteStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, key: str) -> None:
        time.sleep(SLOW_SEC)
        self.deleted.append(key)


async def test_content_patch_deletes_removed_images_off_loop(
    seeded_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    course_id, _org, user = await build_course(
        seeded_db, modules=1, lessons_per_module=1, content_status="ready"
    )
    course = await content_svc.load_course_full(seeded_db, course_id=course_id)
    assert course is not None
    lesson = find_lesson(course, "M1.L1")
    kept = f"/uploads/lesson_assets/{course_id}/kept.png"
    removed = f"/uploads/lesson_assets/{course_id}/removed.png"
    raw = dict(lesson.content_raw or {})
    raw["visual_assets"] = [
        {"asset_id": "img1", "format": "image", "content": kept, "caption": "Tenuta"},
        {"asset_id": "img2", "format": "image", "content": removed, "caption": "Tolta"},
    ]
    lesson.content_raw = raw
    await seeded_db.flush()
    fake = _SlowDeleteStorage()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: fake)

    payload = LessonContentUpdateInput(
        visual_assets=[
            {"asset_id": "img1", "format": "image", "content": kept, "caption": "Tenuta"}
        ]
    )
    result, lag = await _run_with_probe(
        content_crud.update_lesson_content(
            seeded_db, course=course, lesson=lesson, payload=payload, actor_id=user.id
        )
    )

    assert not isinstance(result, Exception), result
    assert lag < MAX_LAG_SEC, _lag_msg(lag)
    # Stesso esito di prima: cancellato solo il file tolto, dopo il commit.
    assert fake.deleted == [remote_storage.uploads_key(removed)]


# ---------------------------------------------------------------------------
# Vision: PIL fuori dal loop nei tre servizi
# ---------------------------------------------------------------------------


class _SentError(Exception):
    """La richiesta è pronta: il test si ferma prima della rete."""

    def __init__(self, body: dict[str, Any]) -> None:
        super().__init__("sent")
        self.body = body


def _image_url(body: dict[str, Any]) -> str:
    for part in body["messages"][1]["content"]:
        if part.get("type") == "image_url":
            return str(part["image_url"]["url"])
    raise AssertionError("nessuna immagine nella richiesta")


@pytest.fixture
def slow_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    """`vision_image` rallentata (stesso risultato) in ogni modulo che la
    usa, e trasporto OpenAI finto che solleva con il body pronto."""
    original = openai_figure_describe_service.vision_image

    def slow(data: bytes, long_side: int | None = None) -> bytes:
        time.sleep(SLOW_SEC)
        return original(data, long_side)

    async def fake_post(body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        raise _SentError(body)

    for module in (
        openai_figure_describe_service,
        openai_figure_relevance_service,
        openai_tikz_render_review_service,
    ):
        monkeypatch.setattr(module, "vision_image", slow)
        monkeypatch.setattr(module, "post_chat_with_retry", fake_post)


def _expected_url(png: bytes) -> str:
    jpeg = openai_figure_describe_service.vision_image(png)
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


async def test_describe_figure_encodes_the_image_off_loop(slow_vision: None) -> None:
    png = _png()
    item = openai_figure_describe_service.DescribeInput(
        image=png, caption="Figura", context="Testo", document_title="Doc", language_code="it"
    )
    result, lag = await _run_with_probe(openai_figure_describe_service.describe_figure(item))
    assert isinstance(result, _SentError), result
    assert lag < MAX_LAG_SEC, _lag_msg(lag)
    assert _image_url(result.body) == _expected_url(png)


async def test_relevance_encodes_the_candidate_off_loop(slow_vision: None) -> None:
    png = _png()
    lesson = openai_figure_relevance_service.LessonContext(
        title="Lezione", topics=("Grafi",), objectives=("Capire",), language_code="it"
    )
    result, lag = await _run_with_probe(
        openai_figure_relevance_service.assess_candidate(
            png, lesson, source_title="Fonte", source_text="Testo"
        )
    )
    assert isinstance(result, _SentError), result
    assert lag < MAX_LAG_SEC, _lag_msg(lag)
    assert _image_url(result.body) == _expected_url(png)


async def test_tikz_review_encodes_the_render_off_loop(slow_vision: None) -> None:
    png = _png()
    result, lag = await _run_with_probe(
        openai_tikz_render_review_service.review_render(
            png, caption="Figura", citing_text="Testo", labels=["a"], language_code="it"
        )
    )
    assert isinstance(result, _SentError), result
    assert lag < MAX_LAG_SEC, _lag_msg(lag)
    assert _image_url(result.body) == _expected_url(png)


# ---------------------------------------------------------------------------
# Mappa di resa: misura degli SVG stringa fuori dal loop
# ---------------------------------------------------------------------------


class _StrRenderer:
    """Renderer che restituisce SVG stringa (come DOT, Vega-Lite, function)."""

    fmt = "dot"

    def available(self) -> bool:
        return True

    def sanitize(self, content: str) -> str:
        return content.strip()

    def render_svg_batch(self, contents: list[str], *, asset_ids: list[str]) -> list[str]:
        return [_SVG.replace("nodo a", c[:20]) for c in contents]


async def test_render_figure_map_measures_string_svgs_off_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = _StrRenderer()
    monkeypatch.setitem(frs.REGISTRY, "dot", renderer)
    monkeypatch.setattr(frs, "available_formats", lambda: ("mermaid", "dot"))
    original = frs._figure_from_svg

    def slow(r: object, svg: str, *, asset_id: str) -> frs.RenderedFigure:
        time.sleep(SLOW_SEC)
        return original(r, svg, asset_id=asset_id)

    monkeypatch.setattr(frs, "_figure_from_svg", slow)
    assets = [
        {"asset_id": "g1", "format": "dot", "content": _DOT},
        {"asset_id": "g2", "format": "dot", "content": _DOT_SMALL},
    ]
    figures, lag = await _run_with_probe(frs.render_figure_map(assets, language="it"))

    assert lag < MAX_LAG_SEC, _lag_msg(lag)
    contents = [a["content"].strip() for a in assets]
    expected = {
        a["asset_id"]: original(renderer, svg, asset_id=a["asset_id"])
        for a, svg in zip(assets, renderer.render_svg_batch(contents, asset_ids=[]), strict=True)
    }
    assert figures == expected
    # La cache positiva è scritta (sul loop) come prima.
    key = frs._renderer_key(renderer, "dot", contents[0])
    assert frs._cache_get_figure(key) == expected["g1"]


# ---------------------------------------------------------------------------
# Validazione: latex2mathml e misure del revisore fuori dal loop
# ---------------------------------------------------------------------------


async def test_validate_slots_runs_latex2mathml_off_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = avs.validate_latex_mathml

    def slow(latex: str) -> tuple[bool, str]:
        time.sleep(SLOW_SEC)
        return original(latex)

    async def no_js(items: list[tuple[str, str]]) -> None:
        return None  # CDN assente: il LaTeX resta gated dal solo latex2mathml

    monkeypatch.setattr(avs, "validate_latex_mathml", slow)
    monkeypatch.setattr(avs, "_validate_js_batch", no_js)
    sources = ["x^2 + y^2", "\\left( x", "", "\\alpha"]
    slots = [
        avs._Slot(id=f"eq:{i}", kind="latex", current=src, context="", commit=lambda v: None)
        for i, src in enumerate(sources)
    ]
    checks, lag = await _run_with_probe(avs._validate_slots(slots))

    assert lag < MAX_LAG_SEC, _lag_msg(lag)
    expected = []
    for slot in slots:
        ok, err = original(slot.current)
        message = "" if ok else f"latex2mathml: {err}"
        expected.append(avs.AssetCheck(slot.id, "latex", ok, message))
    assert checks == expected
    assert [c.ok for c in checks] == [True, False, False, True]


def _review_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = config.get_settings().model_copy(
        update={
            "openai_api_key": "sk-test-finta",
            "figure_review_enabled": True,
            "figure_review_max_attempts": 1,
        }
    )
    monkeypatch.setattr(avs, "get_settings", lambda: settings)


def _slow_measure(monkeypatch: pytest.MonkeyPatch) -> Any:
    original = avs._figure_measure

    def slow(fmt: str, source: str, fig: frs.RenderedFigure | None) -> FigureMeasure:
        time.sleep(SLOW_SEC)
        return original(fmt, source, fig)

    monkeypatch.setattr(avs, "_figure_measure", slow)
    return original


async def test_review_figures_measures_the_originals_off_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _review_settings(monkeypatch)
    original_measure = _slow_measure(monkeypatch)
    figure = frs.RenderedFigure.from_svg(_SVG)

    async def fake_map(assets: Any, **kwargs: Any) -> dict[str, frs.RenderedFigure]:
        return {a["asset_id"]: figure for a in assets}

    async def passthrough(assets: Any, figures: Any, **kwargs: Any) -> Any:
        return dict(figures)

    seen: list[Any] = []

    async def stop_round(pending: list[Any], **kwargs: Any) -> list[Any]:
        seen.extend(pending)
        return []

    monkeypatch.setattr(avs, "render_figure_map", fake_map)
    monkeypatch.setattr(avs, "render_chain_variants", passthrough)
    monkeypatch.setattr(avs, "_review_round", stop_round)
    output = build_lesson_content_output(
        visual_assets=[
            {"asset_id": "g1", "format": "dot", "content": _DOT, "caption": "C1"},
            {"asset_id": "g2", "format": "dot", "content": _DOT_SMALL, "caption": "C2"},
        ],
    )
    result, lag = await _run_with_probe(
        avs._review_figures(output, language_code="it", usage_sink=[])
    )

    assert result == 0
    assert lag < MAX_LAG_SEC, _lag_msg(lag)
    assert [it.key for it in seen] == ["asset:g1", "asset:g2"]
    assert [it.measure for it in seen] == [
        original_measure(a.format, a.content, figure) for a in output.visual_assets
    ]


async def test_judge_candidates_measures_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    original_measure = _slow_measure(monkeypatch)
    figure = frs.RenderedFigure.from_svg(_SVG)
    rewritten = frs.RenderedFigure.from_svg(_SVG.replace("nodo b", "nodo c"))

    async def all_valid(slots: list[Any]) -> list[avs.AssetCheck]:
        return [avs.AssetCheck(s.id, s.kind, True, "") for s in slots]

    async def fake_map(entries: Any, **kwargs: Any) -> dict[str, frs.RenderedFigure]:
        return {
            e["asset_id"]: (rewritten if e["asset_id"].endswith(avs._REVIEW_SUFFIX) else figure)
            for e in entries
        }

    async def passthrough(assets: Any, figures: Any, **kwargs: Any) -> Any:
        return dict(figures)

    monkeypatch.setattr(avs, "_validate_slots", all_valid)
    monkeypatch.setattr(avs, "render_figure_map", fake_map)
    monkeypatch.setattr(avs, "render_chain_variants", passthrough)
    candidate = "digraph G {\n  a -> b;\n  b -> c;\n  a -> c;\n}"
    item = avs._ReviewItem(
        asset=SimpleNamespace(asset_id="g1"),
        key="asset:g1",
        fmt="dot",
        original=_DOT,
        context=ReviewContext(title="", text="", cited=False),
        measure=original_measure("dot", _DOT, figure),
    )
    judgements, lag = await _run_with_probe(
        avs._judge_candidates([(item, candidate)], language_code="it")
    )

    assert lag < MAX_LAG_SEC, _lag_msg(lag)
    before = original_measure("dot", _DOT, figure)
    after = original_measure("dot", candidate, rewritten)
    ok, reason = avs.review_acceptance("dot", before, after)
    assert judgements == [avs._Judgement(ok, reason, before.crossings, after.crossings)]
