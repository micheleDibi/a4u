"""Fase 3 — riferimenti agli obiettivi formativi: codici nel prompt,
`enum` nello schema, riconciliazione in materializzazione.

Contesto: in produzione una lezione falliva in modo terminale perché la
sezione dichiarava l'obiettivo con una variante del testo di Fase 2
(`lesson_content_unknown_objective`), scartando una dispensa già scritta.
Questi test fissano il nuovo contratto §6.4.
"""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError
from app.models.audit_log import AuditLog
from app.schemas.course_lesson_content import LessonContentOutput
from app.services import course_lesson_content_service as content_svc
from app.services import openai_lesson_content_service as openai_svc
from tests.course_builders import (
    build_course,
    build_lesson_content_output,
    find_lesson,
)

OBJECTIVES = [
    "Lo studente sarà in grado di riconoscere e utilizzare un linguaggio "
    "clinico appropriato nella descrizione del caso",
    "Lo studente sarà in grado di distinguere fra colloquio clinico strutturato e non strutturato",
]
TOPICS = [
    {"topic_id": "T1", "topic": "Il colloquio clinico", "rationale": "x"},
    {"topic_id": "T2", "topic": "I criteri diagnostici", "rationale": "y"},
]
USAGE = {"total": 1, "prompt": 1, "completion": 0, "model": "gpt-5.5"}


async def _lesson_with(db, *, objectives=None, topics=None, status="empty"):
    """Corso di prova con una lezione dotata di struttura Fase 2 reale."""
    course_id, _org, user = await build_course(
        db, modules=1, lessons_per_module=1, content_status=status
    )
    course = await content_svc.load_course_full(db, course_id=course_id)
    lesson = find_lesson(course, "M1.L1")
    lesson.learning_objectives = list(OBJECTIVES if objectives is None else objectives)
    lesson.mandatory_topics = list(TOPICS if topics is None else topics)
    await db.flush()
    return course, lesson, user


async def _materialize(db, course, lesson, output: LessonContentOutput):
    """Come il worker: `raw` è serializzato PRIMA della validazione."""
    await content_svc.materialize_lesson_content(
        db,
        course=course,
        lesson=lesson,
        output=output,
        raw=output.model_dump(),
        usage=USAGE,
    )


# ---------------------------------------------------------------------------
# Prompt e schema
# ---------------------------------------------------------------------------


async def test_user_prompt_lists_objectives_with_their_id(seeded_db):
    course, lesson, _user = await _lesson_with(seeded_db)
    prompt = content_svc.build_user_prompt(course, lesson)
    assert "Obiettivi formativi (con ID):" in prompt
    assert f"- [O1] {OBJECTIVES[0]}" in prompt
    assert f"- [O2] {OBJECTIVES[1]}" in prompt
    assert content_svc.objective_ids_for_lesson(lesson) == ["O1", "O2"]


async def test_multiline_objective_is_collapsed_in_the_prompt(seeded_db):
    """Un a-capo spezzerebbe l'obiettivo in due voci dell'elenco."""
    course, lesson, _user = await _lesson_with(seeded_db, objectives=["Primo\nobiettivo formativo"])
    prompt = content_svc.build_user_prompt(course, lesson)
    assert "- [O1] Primo obiettivo formativo" in prompt


def test_system_prompt_orders_the_code_and_exempts_it_from_translation():
    prompt = openai_svc._system_prompt("en")
    assert "`O1`, `O2`" in prompt
    assert "objectives_addressed" in prompt
    # Senza l'esenzione, la REGOLA TASSATIVA sulla lingua ordinerebbe di
    # tradurre proprio il campo che deve restare un codice.
    assert "i codici di obiettivi (`O1`) e temi" in prompt
    assert "`O1`, `S2`" in prompt  # DIVIETI: mai nella prosa visibile


def test_json_schema_injects_the_enum_without_touching_the_constant():
    snapshot = copy.deepcopy(openai_svc.LESSON_CONTENT_JSON_SCHEMA)
    for _ in range(100):
        schema = openai_svc.build_lesson_content_json_schema(objective_ids=["O1", "O2"])
    props = schema["schema"]["properties"]
    addressed = props["sections"]["items"]["properties"]["objectives_addressed"]
    covered = props["coverage_check"]["properties"]["objectives_covered"]
    expected = ["O1", "O2"]
    assert addressed["items"]["enum"] == expected
    assert covered["items"]["properties"]["objective"]["enum"] == expected
    assert schema["strict"] is True
    assert snapshot == openai_svc.LESSON_CONTENT_JSON_SCHEMA


def test_json_schema_without_objectives_has_no_empty_enum():
    """`enum: []` non è uno schema strict valido: OpenAI risponderebbe 400
    a ogni tentativo (lezione senza obiettivi di Fase 2)."""
    schema = openai_svc.build_lesson_content_json_schema(objective_ids=[])
    assert schema is openai_svc.LESSON_CONTENT_JSON_SCHEMA
    props = schema["schema"]["properties"]
    assert "enum" not in props["sections"]["items"]["properties"]["objectives_addressed"]["items"]
    assert (
        "enum"
        not in props["coverage_check"]["properties"]["objectives_covered"]["items"]["properties"][
            "objective"
        ]
    )


# ---------------------------------------------------------------------------
# Materializzazione: riconciliazione invece di rifiuto
# ---------------------------------------------------------------------------


async def test_production_failure_is_now_resolved(seeded_db):
    """La variante tipografica + troncata che faceva fallire 14 volte."""
    course, lesson, _user = await _lesson_with(seeded_db)
    mangled = "Lo studente sara' in grado di riconoscere e utilizzare un linguaggio clinico appr"
    output = build_lesson_content_output(
        sections=[
            ("S1", [mangled], ["T1"]),
            ("S2", [OBJECTIVES[1]], ["T2"]),
        ]
    )
    await _materialize(seeded_db, course, lesson, output)

    assert lesson.content_status == "ready"
    assert lesson.content_raw["sections"][0]["objectives_addressed"] == [OBJECTIVES[0]]


async def test_objective_codes_are_resolved_and_never_persisted(seeded_db):
    course, lesson, _user = await _lesson_with(seeded_db)
    output = build_lesson_content_output(
        sections=[("S1", ["O1"], ["T1"]), ("S2", ["O2"], ["T2"])],
        coverage_objectives=["O1", "O2"],
    )
    await _materialize(seeded_db, course, lesson, output)

    raw = lesson.content_raw
    assert raw["sections"][0]["objectives_addressed"] == [OBJECTIVES[0]]
    covered = [o["objective"] for o in raw["coverage_check"]["objectives_covered"]]
    assert covered == OBJECTIVES
    assert "O1" not in str(raw["coverage_check"])


async def test_unresolvable_reference_is_dropped_with_an_audit(seeded_db):
    course, lesson, _user = await _lesson_with(seeded_db)
    output = build_lesson_content_output(
        sections=[
            ("S1", ["O1", "Obiettivo inventato dal modello"], ["T1"]),
            ("S2", ["O2"], ["T2", "T99"]),
        ],
    )
    await _materialize(seeded_db, course, lesson, output)

    assert lesson.content_status == "ready"
    assert lesson.content_raw["sections"][0]["objectives_addressed"] == [OBJECTIVES[0]]
    assert lesson.content_raw["sections"][1]["topics_addressed"] == ["T2"]
    rows = (
        (
            await seeded_db.execute(
                select(AuditLog).where(
                    AuditLog.action == "course.lesson.content.coverage_refs_dropped"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].payload["topics"] == ["T99"]


async def test_coverage_check_is_derived_not_compared(seeded_db):
    """Prima un `coverage_check` incoerente buttava via la dispensa."""
    course, lesson, _user = await _lesson_with(seeded_db)
    output = build_lesson_content_output(
        sections=[("S1", ["O1"], ["T1"]), ("S2", ["O2"], ["T2"])],
        coverage_objectives=["Qualcosa che il modello si è inventato"],
        coverage_topics=[],
    )
    await _materialize(seeded_db, course, lesson, output)

    coverage = lesson.content_raw["coverage_check"]
    assert [o["objective"] for o in coverage["objectives_covered"]] == OBJECTIVES
    assert coverage["objectives_covered"][0]["covered_in_section_ids"] == ["S1"]
    assert [t["topic_id"] for t in coverage["topics_covered"]] == ["T1", "T2"]


async def test_uncovered_objective_still_fails_and_names_it(seeded_db):
    course, lesson, _user = await _lesson_with(seeded_db)
    output = build_lesson_content_output(sections=[("S1", ["O1"], ["T1", "T2"])])
    with pytest.raises(ConflictError) as exc:
        await _materialize(seeded_db, course, lesson, output)
    assert exc.value.code == "lesson_content_objectives_uncovered"
    assert OBJECTIVES[1][:40] in exc.value.message


async def test_uncovered_topic_still_fails_and_names_it(seeded_db):
    course, lesson, _user = await _lesson_with(seeded_db)
    output = build_lesson_content_output(sections=[("S1", ["O1", "O2"], ["T1"])])
    with pytest.raises(ConflictError) as exc:
        await _materialize(seeded_db, course, lesson, output)
    assert exc.value.code == "lesson_content_topics_uncovered"
    assert "T2" in exc.value.message


async def test_topic_id_case_variants_are_canonicalized(seeded_db):
    course, lesson, _user = await _lesson_with(seeded_db)
    output = build_lesson_content_output(
        sections=[("S1", ["O1"], ["t1"]), ("S2", ["O2"], ["[T2]"])]
    )
    await _materialize(seeded_db, course, lesson, output)
    assert lesson.content_raw["sections"][0]["topics_addressed"] == ["T1"]
    assert lesson.content_raw["sections"][1]["topics_addressed"] == ["T2"]


async def test_lesson_without_phase2_objectives_no_longer_loops(seeded_db):
    """Prima: qualsiasi obiettivo emesso era "sconosciuto" → fallimento
    perpetuo. Ora la dispensa passa con contabilità vuota."""
    course, lesson, _user = await _lesson_with(seeded_db, objectives=[], topics=[])
    output = build_lesson_content_output(sections=[("S1", ["Un obiettivo qualsiasi"], ["T1"])])
    await _materialize(seeded_db, course, lesson, output)
    assert lesson.content_status == "ready"
    assert lesson.content_raw["sections"][0]["objectives_addressed"] == []
    assert lesson.content_raw["coverage_check"]["objectives_covered"] == []


async def test_perfect_output_is_persisted_byte_identical(seeded_db):
    course, lesson, _user = await _lesson_with(seeded_db)
    output = build_lesson_content_output(
        sections=[
            ("S1", [OBJECTIVES[0]], ["T1"]),
            ("S2", [OBJECTIVES[1]], ["T2"]),
        ]
    )
    expected = output.model_dump()
    await _materialize(seeded_db, course, lesson, output)
    assert lesson.content_raw == expected


async def test_structural_validations_are_untouched(seeded_db):
    course, lesson, _user = await _lesson_with(seeded_db)
    output = build_lesson_content_output(lesson_code="M9.L9")
    with pytest.raises(ConflictError) as exc:
        await _materialize(seeded_db, course, lesson, output)
    assert exc.value.code == "lesson_content_id_mismatch"


# ---------------------------------------------------------------------------
# Budget di retry per-richiesta
# ---------------------------------------------------------------------------


async def test_retry_resets_the_attempts_budget(seeded_db):
    """Senza reset, dopo i 5 retry automatici ogni "Riprova" valeva un
    solo tentativo e ricadeva subito in `Fallito`."""
    course, lesson, user = await _lesson_with(seeded_db, status="failed")
    lesson.content_attempts = 9
    await seeded_db.flush()

    await content_svc.request_lesson_generation(
        seeded_db,
        course=course,
        lesson=lesson,
        actor_id=user.id,
        regeneration_hint=None,
    )
    assert lesson.content_attempts == 0
    assert lesson.content_status == "pending"


async def test_generate_all_does_not_reset_a_lesson_in_flight(seeded_db):
    """generate-all non filtra per stato: senza il vincolo, due clic di
    fila azzererebbero il contatore all'infinito."""
    course, lesson, user = await _lesson_with(seeded_db, status="processing")
    lesson.content_attempts = 4
    await seeded_db.flush()

    await content_svc.request_all_lessons_generation(
        seeded_db, course=course, actor_id=user.id, regeneration_hint=None
    )
    assert lesson.content_attempts == 4


async def test_cancel_does_not_reset_the_attempts(seeded_db):
    course, lesson, user = await _lesson_with(seeded_db, status="processing")
    lesson.content_attempts = 3
    await seeded_db.flush()

    await content_svc.cancel_all_lessons_generation(seeded_db, course=course, actor_id=user.id)
    assert lesson.content_attempts == 3
