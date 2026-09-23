"""Figure di fonte in Fase 4 e 5 (WP4d): I4 e prompt.

- 8c (`FIGURE_SLIDES_COVERAGE_REPAIR_ENABLED`): ogni figura di Fase 3 senza
  slide ne riceve una dedicata, dopo l'ultima slide della sezione che la
  cita (o prima delle slide di chiusura); numerazione 1..N, `total_slides`
  e `references_assets` coerenti; le tabelle no; con il flag spento
  `slides_raw` è quello di prima;
- PROMPT 5 e PROMPT 6: l'UUID della figura non arriva mai al modello (vista
  per il prompt), la regola sulle figure di fonte entra nel messaggio user
  solo se ce ne sono (altrimenti byte-identico); il discorso riceve la
  fonte già pronta da dire a voce, per slide;
- controllo soft della fonte detta a voce.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.core.config import get_settings
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.schemas.course_lesson_slides import LessonSlideItem, LessonSlidesOutput
from app.services import course_lesson_slides_service as slides_svc
from app.services import course_lesson_speech_service as speech_svc
from app.services import figure_provenance

FIG_UUID = str(uuid.UUID("0f3c2a52-1111-4a4a-9a9a-222233334444"))


def _content_raw(with_source: bool = True) -> dict[str, Any]:
    assets = [
        {
            "asset_id": "fig_1",
            "format": "mermaid",
            "content": "flowchart LR\n  A-->B",
            "caption": "Figura 1. Catena di misura.",
            "alt_text": "catena",
        },
        {
            "asset_id": "fig_2",
            "format": "mermaid",
            "content": "flowchart LR\n  C-->D",
            "caption": "Risposta in frequenza.",
            "alt_text": "risposta",
        },
    ]
    if with_source:
        assets.append(
            {
                "asset_id": "SRC-0f3c2a52",
                "format": "source_figure",
                "content": FIG_UUID,
                "caption": "Schema del vibrometro laser Doppler.",
                "alt_text": "schema",
            }
        )
    return {
        "introduction": "Intro.",
        "sections": [
            {"section_id": "S1", "title": "Catena", "content": "Vedi [FIG:fig_1]."},
            {
                "section_id": "S2",
                "title": "Vibrometro",
                "content": "Lo schema [FIG:SRC-0f3c2a52] e [FIG:fig_2].",
            },
        ],
        "summary": "Sintesi.",
        "visual_assets": assets,
        "tables": [{"table_id": "tab_1", "caption": "T", "markdown": "| a |\n|---|\n| 1 |"}],
    }


def _slide(number: int, section: str, refs: list[str] | None = None, **kw: Any) -> LessonSlideItem:
    return LessonSlideItem(
        slide_number=number,
        slide_id=kw.get("slide_id", f"s{number}"),
        type=kw.get("type", "diagram" if refs else "concept"),
        title="Titolo",
        references_assets=refs or [],
        source_section_id=section,
    )


def _course(lesson: CourseLesson) -> Course:
    module = CourseModule(module_code="M1", title="M", position=1)
    module.lessons = [lesson]
    course = Course(title="Corso", language_code="it", cfu=6, lesson_duration_minutes=20)
    course.modules = [module]
    course.documents = []
    return course


async def _materialize(slides: list[LessonSlideItem], content_raw: dict[str, Any]) -> CourseLesson:
    lesson = CourseLesson(lesson_code="M1.L1", title="Vibrometria", content_raw=content_raw)
    output = LessonSlidesOutput(lesson_id="M1.L1", total_slides=len(slides), slides=slides)
    await slides_svc.materialize_lesson_slides(
        None,  # type: ignore[arg-type]  # nessun accesso al DB in questo percorso
        course=_course(lesson),
        lesson=lesson,
        output=output,
        raw=output.model_dump(),
        usage={},
    )
    return lesson


async def test_missing_figures_get_a_dedicated_slide_after_their_section() -> None:
    slides = [
        _slide(1, "", type="title"),
        _slide(2, "S1"),
        _slide(3, "S2", ["SRC-0f3c2a52"]),
        _slide(4, "S2"),
        _slide(5, "", type="summary"),
    ]
    lesson = await _materialize(slides, _content_raw())
    raw = lesson.slides_raw
    ids = [s["slide_id"] for s in raw["slides"]]
    # fig_1 (sezione S1) dopo s2; fig_2 (sezione S2) dopo s4; tab_1 no.
    assert ids == ["s1", "s2", "fig_1", "s3", "s4", "fig_2", "s5"]
    assert [s["slide_number"] for s in raw["slides"]] == list(range(1, 8))
    assert raw["total_slides"] == 7
    added = {s["slide_id"]: s for s in raw["slides"] if s["slide_id"].startswith("fig_")}
    assert added["fig_1"]["references_assets"] == ["fig_1"]
    assert added["fig_1"]["source_section_id"] == "S1"
    assert added["fig_1"]["type"] == "diagram"
    assert added["fig_1"]["title"] == "Catena di misura."
    assert not any("tab_1" in s["references_assets"] for s in raw["slides"])


async def test_without_a_section_the_slide_goes_before_the_closing_slides() -> None:
    content = _content_raw(with_source=False)
    content["sections"] = []
    slides = [
        _slide(1, "", type="title"),
        _slide(2, "", ["fig_2"]),
        _slide(3, "", type="summary"),
        _slide(4, "", type="references"),
    ]
    lesson = await _materialize(slides, content)
    ids = [s["slide_id"] for s in lesson.slides_raw["slides"]]
    assert ids == ["s1", "s2", "fig_1", "s3", "s4"]


async def test_flag_off_keeps_the_output_of_main(monkeypatch: pytest.MonkeyPatch) -> None:
    patched = get_settings().model_copy(update={"figure_slides_coverage_repair_enabled": False})
    monkeypatch.setattr(slides_svc, "get_settings", lambda: patched)
    slides = [
        _slide(1, "", type="title"),
        _slide(2, "S1"),
        _slide(3, "S2", ["SRC-0f3c2a52"]),
        _slide(4, "S2"),
        _slide(5, "", type="summary"),
    ]
    expected = LessonSlidesOutput(
        lesson_id="M1.L1", total_slides=5, slides=[s.model_copy() for s in slides]
    ).model_dump()
    lesson = await _materialize(slides, _content_raw())
    assert lesson.slides_raw == expected


async def test_slide_ids_never_collide() -> None:
    slides = [
        _slide(1, "", type="title", slide_id="fig_1"),
        _slide(2, "S2", ["SRC-0f3c2a52", "fig_2"][:1]),
        _slide(3, "S2", ["fig_2"]),
    ]
    lesson = await _materialize(slides, _content_raw())
    ids = [s["slide_id"] for s in lesson.slides_raw["slides"]]
    assert len(ids) == len(set(ids)) and "fig_2" in ids


def _lesson_for_prompts(with_source: bool = True) -> tuple[Course, CourseLesson]:
    lesson = CourseLesson(
        lesson_code="M1.L1",
        title="Vibrometria",
        content_raw=_content_raw(with_source),
        is_introductory=False,
        slides_raw={
            "lesson_id": "M1.L1",
            "total_slides": 2,
            "slides": [
                {"slide_id": "s1", "references_assets": ["fig_1"]},
                {"slide_id": "s2", "references_assets": ["src-0f3c2a52"]},
            ],
        },
    )
    course = _course(lesson)
    return course, lesson


def test_slides_prompt_hides_the_uuid_and_adds_the_rule() -> None:
    course, lesson = _lesson_for_prompts()
    prompt = slides_svc.build_user_prompt(course, lesson)
    assert FIG_UUID not in prompt
    assert figure_provenance.PROMPT_PLACEHOLDER in prompt
    assert "Le figure con `format: source_figure`" in prompt
    course2, lesson2 = _lesson_for_prompts(with_source=False)
    plain = slides_svc.build_user_prompt(course2, lesson2)
    assert "source_figure" not in plain
    assert json.dumps(lesson2.content_raw, ensure_ascii=False, indent=2) in plain


def test_speech_prompt_carries_the_spoken_source_per_slide() -> None:
    course, lesson = _lesson_for_prompts()
    spoken = {"SRC-0f3c2a52": "tratta da Rossi, «Vibrometria laser», 2021"}
    prompt = speech_svc.build_user_prompt(course, lesson, spoken_sources=spoken)
    assert FIG_UUID not in prompt
    assert "## Fonti delle figure da citare a voce" in prompt
    assert "- slide s2: tratta da Rossi, «Vibrometria laser», 2021" in prompt
    # Senza frasi pronunciabili (o senza figure sulle slide): niente blocco.
    assert "Fonti delle figure" not in speech_svc.build_user_prompt(
        course, lesson, spoken_sources={}
    )
    course2, lesson2 = _lesson_for_prompts(with_source=False)
    assert speech_svc.build_user_prompt(course2, lesson2) == speech_svc.build_user_prompt(
        course2, lesson2, spoken_sources={"x": "y"}
    )


def test_unspoken_sources_is_a_soft_check() -> None:
    _unused, lesson = _lesson_for_prompts()
    keys = {
        "SRC-0f3c2a52": figure_provenance.spoken_keys(
            {"authors": ["Rossi"], "title": "Vibrometria laser", "year": 2021}
        )
    }
    said = [("s1", "Ecco la catena."), ("s2", "Lo schema, tratto dal lavoro di Rossi, mostra…")]
    assert figure_provenance.unspoken_sources(lesson.slides_raw, said, keys) == []
    by_title = [("s2", "La figura viene da Vibrometria laser, del 2021.")]
    assert figure_provenance.unspoken_sources(lesson.slides_raw, by_title, keys) == []
    # Una parola del titolo da sola («laser») non basta.
    silent = [("s1", "Ecco la catena."), ("s2", "Lo schema mostra il laser.")]
    assert figure_provenance.unspoken_sources(lesson.slides_raw, silent, keys) == ["s2"]
