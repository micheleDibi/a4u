"""Prestazioni L1 — W1: la pipeline dei tre PDF non blocca l'event loop.

In produzione API e worker condividono un solo event loop (contratto
`docs/contracts/perf-l1-conventions.md` §1). Qui le parti sincrone della
pipeline sono rese lente apposta (storage finto che dorme, collector math
rallentato) e una coroutine sonda, sullo stesso loop, misura il ritardo dei
suoi `sleep`: deve restare sotto 100 ms. Ogni caso controlla anche che il
risultato sia lo stesso del calcolo sincrono di prima.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.course_lesson import CourseLesson
from app.models.pdf_template import PdfTemplate
from app.services import course_lesson_pdf_service as pdf
from app.services import course_lesson_slides_pdf_service as slides_pdf
from app.services import course_lesson_speech_pdf_service as speech_pdf
from app.services import figure_render_service, remote_storage

# Durata di ogni passo sincrono finto: ben oltre la soglia della sonda.
SLOW_SEC = 0.3
# Ritardo massimo ammesso per la sonda sul loop (contratto §1).
MAX_LAG_SEC = 0.1
# Periodo della sonda.
PROBE_SEC = 0.005

# PNG 1×1 trasparente: basta che sia un'immagine valida per il data URL.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


async def _run_with_probe(work: Awaitable[Any]) -> tuple[Any, float]:
    """Esegue `work` con una sonda in parallelo sullo stesso loop; ritorna
    `(risultato o eccezione, ritardo massimo della sonda in secondi)`."""
    stop = asyncio.Event()
    worst = 0.0

    async def probe() -> None:
        nonlocal worst
        while not stop.is_set():
            t0 = time.perf_counter()
            await asyncio.sleep(PROBE_SEC)
            worst = max(worst, time.perf_counter() - t0 - PROBE_SEC)

    task = asyncio.create_task(probe())
    await asyncio.sleep(0)  # la sonda parte prima del lavoro
    try:
        result: Any = await work
    except Exception as exc:  # il chiamante decide quale eccezione attendersi
        result = exc
    finally:
        stop.set()
        await task
    return result, worst


class _SlowStorage:
    """Storage finto: ogni download dorme `SLOW_SEC` (come una connessione
    SFTP nuova) e conta le chiamate."""

    def __init__(self) -> None:
        self.downloads: list[str] = []

    def download_bytes(self, key: str) -> bytes:
        time.sleep(SLOW_SEC)
        self.downloads.append(key)
        return PNG_1PX


@pytest.fixture
def slow_storage(monkeypatch: pytest.MonkeyPatch) -> _SlowStorage:
    fake = _SlowStorage()
    monkeypatch.setattr(remote_storage, "get_storage", lambda: fake)
    return fake


def _template() -> PdfTemplate:
    """Template con sfondo e due loghi: tre download, e l'intestazione con i
    loghi cambia il box rispetto al template di default."""
    return PdfTemplate(
        name="Ateneo",
        background_image_path="/uploads/pdf_templates/bg.png",
        logo_left_path="/uploads/pdf_templates/left.png",
        logo_right_path="/uploads/pdf_templates/right.png",
        text_color="#1F1F1F",
        primary_color="#1976D2",
        secondary_color="#9C27B0",
        font_family="Roboto",
        page_size="A4",
        header_height_mm=40,
        footer_height_mm=15,
        margin_mm=20,
        background_opacity_pct=20,
    )


def _content() -> dict[str, Any]:
    return {
        "introduction": "Come mostra [FIG:iter] vale $x^2 + y^2$.",
        "sections": [
            {
                "section_id": "S1",
                "title": "Metodo",
                "content": "Il passo è $$\\frac{a}{b}$$ e il costo in [TAB:costi].",
            }
        ],
        "summary": "Sintesi con $\\alpha$.",
        "key_takeaways": ["Ricordare $\\beta$."],
        "references": [],
        "equations": [{"equation_id": "e1", "latex": "E = mc^2", "label": "Energia"}],
        "tables": [
            {"table_id": "costi", "markdown": "| a | b |\n|---|---|\n| $z$ | 1 |", "caption": "C"}
        ],
        "visual_assets": [
            {
                "asset_id": "iter",
                "format": "mermaid",
                "content": "flowchart LR\n  A --> B",
                "caption": "Ciclo $\\gamma$",
            }
        ],
    }


def _slides_raw() -> dict[str, Any]:
    return {
        "slides": [
            {
                "slide_id": "s1",
                "slide_number": 1,
                "type": "concept",
                "title": "Ciclo di [FIG:iter]",
                "body": "Formula $a [FIG:iter] b$ in frase.",
                "bullets": ["Punto con $\\delta$."],
                "references_assets": ["iter"],
            }
        ],
        "new_assets": [],
        "new_tables": [],
        "new_equations": [{"equation_id": "n1", "latex": "F = ma", "label": "Forza"}],
        "new_examples": [],
    }


def _speech_raw() -> dict[str, Any]:
    return {
        "speech_segments": [
            {
                "segment_id": "g1",
                "text": "Guardate [FIG:iter]: vale $\\epsilon$.",
                "delivery_notes": "Indicare $\\zeta$ sulla slide.",
                "estimated_duration_seconds": 30,
                "estimated_word_count": 60,
            }
        ],
        "slide_to_segments_map": [
            {"slide_id": "s1", "segment_ids": ["g1"], "slide_total_duration_seconds": 30}
        ],
        "estimated_total_duration_seconds": 30,
        "estimated_total_word_count": 60,
    }


def _lesson() -> CourseLesson:
    return CourseLesson(
        lesson_code="M1.L1",
        title="Lezione",
        content_raw=_content(),
        slides_raw=_slides_raw(),
        speech_raw=_speech_raw(),
    )


@pytest.fixture
def slow_collector(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Collector math rallentato (stesso risultato) e batch MathJax finta che
    registra le chiavi ricevute."""
    original = pdf._collect_math_from_content
    batches: list[tuple[str, str]] = []

    def slow_collect(content: dict[str, Any], *, language: str = "it") -> list[tuple[str, str]]:
        time.sleep(SLOW_SEC)
        return original(content, language=language)

    async def fake_batch(items: list[tuple[str, str]]) -> list[str | None]:
        batches.extend(items)
        return ["<svg/>" for _ in items]

    monkeypatch.setattr(pdf, "_collect_math_from_content", slow_collect)
    monkeypatch.setattr(pdf, "_prerender_math_to_svg_batch", fake_batch)
    return batches


# ---------------------------------------------------------------------------
# La sonda vede davvero un blocco sul loop
# ---------------------------------------------------------------------------


async def test_probe_detects_a_blocking_call_on_the_loop() -> None:
    """Controllo della sonda: lo stesso passo lento eseguito SUL loop la fa
    ritardare oltre la soglia (altrimenti i test sotto non proverebbero
    nulla)."""

    async def blocking() -> None:
        time.sleep(SLOW_SEC)

    _result, lag = await _run_with_probe(blocking())
    assert lag >= MAX_LAG_SEC


# ---------------------------------------------------------------------------
# Dispensa: box della figura (download del template) fuori dal loop
# ---------------------------------------------------------------------------


class _StopError(Exception):
    pass


async def test_lesson_pdf_box_downloads_template_assets_off_loop(
    monkeypatch: pytest.MonkeyPatch, slow_storage: _SlowStorage
) -> None:
    template = _template()
    captured: dict[str, Any] = {}

    async def fake_org(db: Any, organization_id: Any) -> None:
        return None

    async def fake_template(db: Any, **kwargs: Any) -> PdfTemplate:
        return template

    async def fake_prerender(content: Any, **kwargs: Any) -> dict[str, Any]:
        return {}

    async def stop_at_chain(assets: Any, figures: Any, *, box_mm: Any, **kw: Any) -> Any:
        captured["box_mm"] = box_mm
        raise _StopError

    class _Db:
        async def get(self, *args: Any, **kwargs: Any) -> None:
            return None

    monkeypatch.setattr(pdf, "_get_organization", fake_org)
    monkeypatch.setattr(pdf, "_resolve_pdf_template_for_lesson", fake_template)
    monkeypatch.setattr(pdf, "_prerender_visual_assets_for_lesson", fake_prerender)
    monkeypatch.setattr(figure_render_service, "render_chain_variants", stop_at_chain)
    course = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        assignee_user_id=uuid.uuid4(),
        language_code="it",
    )
    lesson = SimpleNamespace(content_raw=_content(), lesson_code="M1.L1")

    result, lag = await _run_with_probe(
        pdf.materialize_lesson_pdf(_Db(), course=course, lesson=lesson)  # type: ignore[arg-type]
    )

    assert isinstance(result, _StopError)
    assert len(slow_storage.downloads) == 3  # sfondo + due loghi
    assert lag < MAX_LAG_SEC, f"loop fermo per {lag * 1000:.0f} ms"
    # Stesso box del calcolo sincrono di prima, e diverso da quello senza
    # loghi: il risultato dipende davvero dai download.
    assert captured["box_mm"] == pdf.lesson_mermaid_box_mm(template, language="it")
    assert captured["box_mm"] != pdf.lesson_mermaid_box_mm(None, language="it")


# ---------------------------------------------------------------------------
# Raccolta delle formule fuori dal loop (dispensa, slide, discorso)
# ---------------------------------------------------------------------------


async def test_lesson_math_collect_runs_off_loop(
    slow_collector: list[tuple[str, str]],
) -> None:
    content = _content()
    svg_map, lag = await _run_with_probe(pdf._prerender_math_for_lesson(content, language="en"))

    assert lag < MAX_LAG_SEC, f"loop fermo per {lag * 1000:.0f} ms"
    expected = pdf._collect_math_from_content(content, language="en")
    assert expected and slow_collector == expected
    assert isinstance(svg_map, pdf.MathSvgMap)
    assert svg_map.requested == len(expected) and set(svg_map) == set(expected)


async def test_slides_math_collect_runs_off_loop(
    slow_collector: list[tuple[str, str]],
) -> None:
    content, slides = _content(), _slides_raw()
    svg_map, lag = await _run_with_probe(
        slides_pdf._prerender_math_for_slides(content, slides, language="en")
    )

    assert lag < MAX_LAG_SEC, f"loop fermo per {lag * 1000:.0f} ms"
    # La composizione di prima: collector (lingua di default) sul contenuto
    # fuso delle slide.
    expected = pdf._collect_math_from_content(
        slides_pdf._math_content_for_slides(content, slides, language="en")
    )
    assert ("F = ma", "block") in expected and ("\\delta", "inline") in expected
    assert slow_collector == expected
    assert svg_map.requested == len(expected)


async def test_speech_math_collect_runs_off_loop(
    slow_collector: list[tuple[str, str]],
) -> None:
    lesson = _lesson()
    svg_map, lag = await _run_with_probe(
        speech_pdf._prerender_math_for_speech(lesson, language="en")
    )

    assert lag < MAX_LAG_SEC, f"loop fermo per {lag * 1000:.0f} ms"
    expected = pdf._collect_math_from_content(
        speech_pdf._math_content_for_speech(lesson, language="en")
    )
    assert ("\\epsilon", "inline") in expected and ("\\zeta", "inline") in expected
    assert slow_collector == expected
    assert svg_map.requested == len(expected)


def test_speech_math_content_from_values_matches_the_lesson_form() -> None:
    """La forma per valori (quella che va nel thread) è la stessa del
    wrapper sulla lezione."""
    lesson = _lesson()
    by_lesson = speech_pdf._math_content_for_speech(lesson, language="it")
    by_values = speech_pdf._speech_math_content(
        lesson.speech_raw, lesson.slides_raw, lesson.content_raw, language="it"
    )
    assert by_values["inline_texts"] == by_lesson["inline_texts"]
    assert by_values["asset_refs"].numbers == by_lesson["asset_refs"].numbers
