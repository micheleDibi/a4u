"""Le figure di Fase 3 arrivano davvero a una slide — misura, non gate.

Contraltare di `lesson_content_figure_mix` sul lato Fase 4. La
validazione di `materialize_lesson_slides` controlla che ogni
`references_assets` esista (hard) e che una slide non porti più di un
asset visivo (hard), ma NON che ogni figura di Fase 3 abbia la sua
slide: sull'export reale del docente (18 settembre 2026) la lezione
M4.L1 aveva quattro figure e quattordici slide, e `fig_markets_
intermediaries` non compariva in nessun `references_assets`. Una figura
scartata sparisce dal deck e dal video — la Fase 5 parla le slide che
esistono — e resta solo in coda alla dispensa.

Con la numerosità portata a 4-8 figure per lezione la perdita cresce
con il numero: qui si prova che la materializzazione la DICE, come
warning, senza rifiutare nulla.
"""

from __future__ import annotations

from typing import Any

import structlog.testing

from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.schemas.course_lesson_slides import LessonSlideItem, LessonSlidesOutput
from app.services import course_lesson_slides_service as slides_svc


def _visual(asset_id: str) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "format": "mermaid",
        "content": "flowchart LR\n  A[Passo uno] --> B[Passo due]",
        "caption": "Didascalia",
    }


def _content_raw(figures: int, tables: int = 0) -> dict[str, Any]:
    return {
        "sections": [],
        "visual_assets": [_visual(f"fig_{i}") for i in range(1, figures + 1)],
        "tables": [
            {"table_id": f"tab_{i}", "caption": "T", "markdown": "| a |\n|---|\n| 1 |"}
            for i in range(1, tables + 1)
        ],
    }


def _slide(number: int, refs: list[str] | None = None) -> LessonSlideItem:
    return LessonSlideItem(
        slide_number=number,
        slide_id=f"s{number}",
        type="diagram" if refs else "concept",
        title="Titolo",
        references_assets=refs or [],
    )


def _course(lesson: CourseLesson) -> Course:
    module = CourseModule(module_code="M1", title="M", position=1)
    module.lessons = [lesson]
    course = Course(title="Corso", language_code="it", cfu=6, lesson_duration_minutes=1)
    course.modules = [module]
    return course


async def _materialize(
    content_raw: dict[str, Any], slides: list[LessonSlideItem]
) -> tuple[CourseLesson, list[dict[str, Any]]]:
    lesson = CourseLesson(lesson_code="M1.L1", title="L", content_raw=content_raw)
    output = LessonSlidesOutput(lesson_id="M1.L1", total_slides=len(slides), slides=slides)
    with structlog.testing.capture_logs() as logs:
        await slides_svc.materialize_lesson_slides(
            None,  # type: ignore[arg-type]  # nessun accesso al DB in questo percorso
            course=_course(lesson),
            lesson=lesson,
            output=output,
            raw=output.model_dump(),
            usage={},
        )
    return lesson, logs


def _events(logs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in logs if e["event"] == name]


async def test_a_figure_without_its_slide_is_reported_and_the_lesson_is_ready() -> None:
    """La forma misurata sull'export: quattro figure, tre referenziate."""
    lesson, logs = await _materialize(
        _content_raw(figures=4),
        [
            _slide(1),
            _slide(2, ["fig_1"]),
            _slide(3, ["fig_2"]),
            _slide(4, ["fig_3"]),
        ],
    )
    assert lesson.slides_status == "ready"
    (warning,) = _events(logs, "lesson_slides_unreferenced_assets")
    assert warning["log_level"] == "warning"
    assert warning["lesson_code"] == "M1.L1"
    assert warning["unreferenced_assets"] == ["fig_4"]
    assert (warning["phase3_assets"], warning["total_slides"]) == (4, 4)


async def test_eight_figures_each_on_its_slide_do_not_warn() -> None:
    """Il caso che la nuova numerosità rende ordinario: otto figure e due
    tabelle, una slide ciascuna. Nessun warning, e nessun fallimento per
    conteggio (il range cresce con gli asset)."""
    content_raw = _content_raw(figures=8, tables=2)
    slides = [_slide(1)]
    slides += [_slide(i + 2, [f"fig_{i + 1}"]) for i in range(8)]
    slides += [_slide(i + 10, [f"tab_{i + 1}"]) for i in range(2)]
    lesson, logs = await _materialize(content_raw, slides)
    assert lesson.slides_status == "ready"
    assert _events(logs, "lesson_slides_unreferenced_assets") == []


async def test_the_match_ignores_case_and_spaces_like_the_rest_of_the_validation() -> None:
    """Stessa chiave del punto 6b e del CRUD: minuscolo, senza spazi ai
    bordi. Un riferimento con un'altra grafia è lo stesso asset, e non
    deve far scattare un warning che il PDF smentirebbe."""
    _lesson, logs = await _materialize(
        _content_raw(figures=2),
        [_slide(1, [" FIG_1 "]), _slide(2, ["fig_2"])],
    )
    assert _events(logs, "lesson_slides_unreferenced_assets") == []


async def test_a_new_asset_of_phase4_is_not_counted_as_a_lost_figure() -> None:
    """Il confronto è con le figure di Fase 3: un `new_asset` nasce già
    referenziato dalla slide che lo crea e non entra nel conto."""
    lesson = CourseLesson(lesson_code="M1.L1", title="L", content_raw=_content_raw(figures=1))
    output = LessonSlidesOutput(
        lesson_id="M1.L1",
        total_slides=2,
        slides=[_slide(1, ["fig_1"]), _slide(2, ["fig_new_1"])],
        new_assets=[
            {
                "asset_id": "fig_new_1",
                "format": "mermaid",
                "content": "flowchart LR\n  A --> B",
                "caption": "Sintesi",
            }
        ],
    )
    with structlog.testing.capture_logs() as logs:
        await slides_svc.materialize_lesson_slides(
            None,  # type: ignore[arg-type]
            course=_course(lesson),
            lesson=lesson,
            output=output,
            raw=output.model_dump(),
            usage={},
        )
    assert lesson.slides_status == "ready"
    assert _events(logs, "lesson_slides_unreferenced_assets") == []


async def test_tables_are_counted_too_because_they_share_the_dedicated_slide_rule() -> None:
    _lesson, logs = await _materialize(
        _content_raw(figures=1, tables=1),
        [_slide(1, ["fig_1"]), _slide(2)],
    )
    (warning,) = _events(logs, "lesson_slides_unreferenced_assets")
    assert warning["unreferenced_assets"] == ["tab_1"]
