"""PROMPT 3 con e senza catalogo delle figure di fonte (I1, parte pura).

Senza catalogo lo schema è identico a prima (stessa costante o stesso
deepcopy) e `visual_assets` non cambia mai; con il catalogo compare solo
`source_figures`, obbligatorio, con l'enum degli id. La riga statica del
system prompt resta entro la guardia della variante peggiore (M6).
"""

from __future__ import annotations

import copy
from typing import Any

from app.services import openai_lesson_content_service as content
from app.services.figure_render_service import RENDERABLE_FORMATS

MAX_SYSTEM_P3 = 31_500


def test_schema_without_catalog_is_unchanged() -> None:
    assert content.build_lesson_content_json_schema() is content.LESSON_CONTENT_JSON_SCHEMA
    with_ids = content.build_lesson_content_json_schema(
        objective_ids=["O1"], visual_formats=["mermaid", "dot"]
    )
    assert "source_figures" not in with_ids["schema"]["properties"]
    assert "source_figures" not in with_ids["schema"]["required"]


def test_schema_with_catalog_adds_only_source_figures() -> None:
    base = content.build_lesson_content_json_schema(
        objective_ids=["O1"], visual_formats=list(RENDERABLE_FORMATS)
    )
    schema = content.build_lesson_content_json_schema(
        objective_ids=["O1"],
        visual_formats=list(RENDERABLE_FORMATS),
        source_figure_refs=["SRC-aaaaaaaa", "SRC-bbbbbbbb"],
    )
    props = schema["schema"]["properties"]
    item = props["source_figures"]["items"]
    assert item["properties"]["figure"]["enum"] == ["SRC-aaaaaaaa", "SRC-bbbbbbbb"]
    assert item["required"] == ["figure", "caption", "alt_text"]
    assert item["additionalProperties"] is False
    assert schema["schema"]["required"][-1] == "source_figures"
    stripped = copy.deepcopy(schema)
    del stripped["schema"]["properties"]["source_figures"]
    stripped["schema"]["required"].remove("source_figures")
    assert stripped == base, "a parte source_figures lo schema è identico"
    assert props["visual_assets"] == base["schema"]["properties"]["visual_assets"]
    # La costante condivisa non è stata toccata.
    assert "source_figures" not in content.LESSON_CONTENT_JSON_SCHEMA["schema"]["properties"]


def test_system_prompt_line_and_worst_variant_guard() -> None:
    prompt = content._system_prompt("it")
    assert "FIGURE DI FONTE" in prompt and "`source_figures`" in prompt
    worst = max(
        len(
            content._system_prompt(
                lang,
                ruolo_docente="Ruoli di supporto e Tutoraggio",
                stile_insegnamento=stile,
                livello_eqf="Diploma di licenza conclusiva del I ciclo di istruzione",
                grounding_enabled=grounding,
                visual_formats=formats,
            )
            + content.REGENERATION_SUFFIX
        )
        for lang in ("it", "zh-cn")
        for stile in ("Collaborativo", "", "(non specificato)")
        for grounding in (True, False)
        for formats in (RENDERABLE_FORMATS, ("mermaid",), ())
    )
    assert worst <= MAX_SYSTEM_P3, worst


# --- I1 contro main: riferimenti esterni, non calcolati dal codice in prova -------

# sha256 del messaggio user del PROMPT 3 e dello schema strict prodotti dal
# codice di `main` (commit 00b9e2c) per la lezione fissa qui sotto, senza
# catalogo: la funzione nuova deve lasciarli identici byte per byte.
MAIN_USER_MESSAGE_SHA256 = "709d826b1f58a0ef03e3e4efe81f2abb38291bf478b9e6bb48b75360d0852b23"
MAIN_SCHEMA_SHA256 = {
    "default": "279a9a9eaedaf6d4ec407dedcda06a8023ef01e1ff2defe2ab8a14170d01c4dd",
    "objectives_formats": "2bf217f1bd9323a164a7e249314eab77b2f730f4e705fe98ee4ca4ae476fa6fb",
}


def _fixed_lesson() -> tuple[Any, Any]:
    from app.models.course import Course
    from app.models.course_lesson import CourseLesson
    from app.models.course_module import CourseModule

    course = Course(
        title="Misure meccaniche",
        objectives="Obiettivi del corso.",
        language_code="it",
        cfu=6,
        lesson_duration_minutes=45,
        modules_count=1,
        lessons_per_module=2,
    )
    course.documents = []
    course.glossary_raw = None
    module = CourseModule(module_code="M1", title="Vibrazioni", position=1, description="Modulo")
    lesson = CourseLesson(
        lesson_code="M1.L2",
        title="Vibrometria laser Doppler",
        position=2,
        is_introductory=False,
        is_assessment=False,
        summary="Principio e schema del vibrometro.",
        learning_objectives=["Descrivere lo schema"],
        prerequisites=[],
        mandatory_topics=[{"topic_id": "T1", "title": "Effetto Doppler"}],
        section_outline=[
            {
                "section_id": "S1",
                "title": "Principio",
                "purpose": "Spiegare",
                "covers_topic_ids": ["T1"],
            },
            {
                "section_id": "S2",
                "title": "Schema",
                "purpose": "Descrivere",
                "covers_topic_ids": [],
            },
        ],
        content_raw=None,
    )
    other = CourseLesson(
        lesson_code="M1.L1",
        title="Introduzione",
        position=1,
        is_introductory=True,
        is_assessment=False,
        summary="Intro.",
        learning_objectives=[],
        prerequisites=[],
        mandatory_topics=[],
        section_outline=[],
    )
    module.lessons = [other, lesson]
    lesson.module = module
    other.module = module
    course.modules = [module]
    return course, lesson


def test_i1_message_and_schema_are_those_of_main() -> None:
    import hashlib
    import json as _json

    from app.services import course_lesson_content_service as content_svc

    course, lesson = _fixed_lesson()
    message = content_svc.build_user_prompt(course, lesson)
    assert hashlib.sha256(message.encode()).hexdigest() == MAIN_USER_MESSAGE_SHA256
    for key, kwargs in (
        ("default", {}),
        (
            "objectives_formats",
            {
                "objective_ids": ["O1", "O2"],
                "visual_formats": ["mermaid", "vegalite", "dot", "function"],
            },
        ),
    ):
        schema = content.build_lesson_content_json_schema(**kwargs)
        digest = hashlib.sha256(_json.dumps(schema, sort_keys=True).encode()).hexdigest()
        assert digest == MAIN_SCHEMA_SHA256[key], key


def test_regeneration_block_lists_source_figures_apart() -> None:
    from app.services import course_lesson_content_service as content_svc

    _course, lesson = _fixed_lesson()
    lesson.content_raw = {
        "introduction": "Intro.",
        "sections": [{"section_id": "S1", "title": "Principio", "content": "Testo."}],
        "visual_assets": [
            {"asset_id": "fig_1", "format": "dot", "content": "digraph{}", "caption": "Catena."},
            {
                "asset_id": "SRC-0f3c2a52",
                "format": "source_figure",
                "content": "0f3c2a52-1111-4a4a-9a9a-222233334444",
                "caption": "Schema del vibrometro.",
            },
        ],
    }
    block = content_svc._format_current_lesson_phase3(lesson)
    head, _sep, sources = block.partition("### Figure di fonte della versione attuale")
    assert "- fig_1 [dot]: Catena." in head and "SRC-0f3c2a52" not in head
    assert "- SRC-0f3c2a52: Schema del vibrometro." in sources
    assert "0f3c2a52-1111" not in block
