"""PR-0 — bug collaterali nella composizione dei prompt P3/P4/P5.

Fotografa i quattro difetti corretti prima dell'intervento sul registro:
1. etichette tassonomia (ruolo/stile/EQF) sempre vuote negli user prompt
   di slide e discorso (`getattr(term, "name")` su un modello senza `name`);
2. segnaposto `{minuti_per_lezione}` / `{livello_eqf}` / `{{ruolo_docente}}`
   lasciati letterali nei system prompt di Fase 4/5;
3. `asset_type` "(?)" nella serializzazione della dispensa precedente;
4. `REGENERATION_SUFFIX` applicato a lezioni mai generate (generate-all
   con hint).
"""

from __future__ import annotations

import uuid

from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_taxonomy import CourseTaxonomyTerm
from app.services import course_lesson_content_service as content_svc
from app.services import course_lesson_slides_service as slides_svc
from app.services import course_lesson_speech_service as speech_svc
from app.services import (
    openai_lesson_slides_service,
    openai_lesson_speech_service,
)
from tests.course_builders import build_course, find_lesson

# asyncio_mode = "auto" (pyproject): i test async non hanno bisogno del mark.

EQF_LABEL = "EQF 6 — Laurea triennale"
RUOLO_LABEL = "Professore ordinario"
STILE_LABEL = "Frontale con esercitazioni"


async def _course_with_labels(db, **kwargs):
    """Corso di prova con i tre termini tassonomia valorizzati (labels it)."""
    course_id, _org, user = await build_course(db, **kwargs)
    course = await db.get(Course, course_id)
    assert course is not None
    suffix = uuid.uuid4().hex[:6]
    eqf = CourseTaxonomyTerm(
        taxonomy_type="eqf_level", slug=f"eqf-{suffix}", labels={"it": EQF_LABEL}
    )
    ruolo = CourseTaxonomyTerm(
        taxonomy_type="teacher_role",
        slug=f"ruolo-{suffix}",
        labels={"it": RUOLO_LABEL},
    )
    stile = CourseTaxonomyTerm(
        taxonomy_type="teaching_style",
        slug=f"stile-{suffix}",
        labels={"it": STILE_LABEL},
    )
    db.add_all([eqf, ruolo, stile])
    await db.flush()
    course.livello_eqf_term_id = eqf.id
    course.ruolo_docente_term_id = ruolo.id
    course.stile_insegnamento_term_id = stile.id
    await db.commit()
    return course_id, user


# ---------------------------------------------------------------------------
# 1. Etichette reali negli user prompt di Fase 4 e Fase 5
# ---------------------------------------------------------------------------


async def test_slides_user_prompt_carries_real_taxonomy_labels(seeded_db):
    course_id, _user = await _course_with_labels(seeded_db, content_status="approved")
    course = await slides_svc.load_course_full(seeded_db, course_id=course_id)
    assert course is not None
    lesson = find_lesson(course, "M1.L1")

    prompt = slides_svc.build_user_prompt(course, lesson)

    assert f"Livello EQF: {EQF_LABEL}" in prompt
    assert f"Ruolo del docente: {RUOLO_LABEL}" in prompt
    assert f"Stile di insegnamento: {STILE_LABEL}" in prompt
    assert "Livello EQF: \n" not in prompt


async def test_speech_user_prompt_carries_real_taxonomy_labels(seeded_db):
    course_id, _user = await _course_with_labels(
        seeded_db, content_status="approved", slides_status="approved"
    )
    course = await speech_svc.load_course_full(seeded_db, course_id=course_id)
    assert course is not None
    lesson = find_lesson(course, "M1.L1")

    prompt = speech_svc.build_user_prompt(course, lesson)

    assert f"Livello EQF: {EQF_LABEL}" in prompt
    assert f"Ruolo del docente: {RUOLO_LABEL}" in prompt
    assert f"Stile di insegnamento: {STILE_LABEL}" in prompt
    assert "Ruolo del docente: \n" not in prompt


async def test_labels_fall_back_to_placeholder_when_terms_missing(seeded_db):
    """Senza termini assegnati l'etichetta è il placeholder di `_term_label`,
    mai una stringa vuota silenziosa."""
    course_id, _org, _user = await build_course(seeded_db, content_status="approved")
    course = await slides_svc.load_course_full(seeded_db, course_id=course_id)
    assert course is not None
    prompt = slides_svc.build_user_prompt(course, find_lesson(course, "M1.L1"))
    assert "Livello EQF: (non specificato)" in prompt


# ---------------------------------------------------------------------------
# 2. Segnaposto interpolati nei system prompt di Fase 4 e Fase 5
# ---------------------------------------------------------------------------


def test_slides_system_prompt_interpolates_duration_and_eqf():
    prompt = openai_lesson_slides_service._system_prompt(
        "it", minuti_per_lezione=45, livello_eqf=EQF_LABEL
    )
    assert "lezione di 45 minuti." in prompt
    assert "Per 45 minuti," in prompt
    assert f"al livello EQF {EQF_LABEL}." in prompt
    assert "{minuti_per_lezione}" not in prompt
    assert "{livello_eqf}" not in prompt
    assert "{{" not in prompt and "}}" not in prompt


def test_slides_system_prompt_defaults_point_to_user_message():
    prompt = openai_lesson_slides_service._system_prompt("it")
    assert "della durata indicata nel messaggio" in prompt
    assert "al livello EQF indicato nel messaggio." in prompt
    assert "{minuti_per_lezione}" not in prompt


def test_speech_system_prompt_interpolates_target_and_role():
    prompt = openai_lesson_speech_service._system_prompt(
        "it", minuti_per_lezione=45, ruolo_docente=RUOLO_LABEL
    )
    assert prompt.count("45 * 60 = 2700 secondi") == 2
    assert f'al ruolo "{RUOLO_LABEL}"' in prompt
    assert "{minuti_per_lezione}" not in prompt
    assert "{{ruolo_docente}}" not in prompt
    assert "{{" not in prompt and "}}" not in prompt


def test_speech_system_prompt_defaults_and_regeneration_suffix():
    prompt = openai_lesson_speech_service._system_prompt("it")
    assert "la durata target indicata nel messaggio" in prompt
    assert 'al ruolo "indicato nel messaggio"' in prompt
    suffix = openai_lesson_speech_service.REGENERATION_SUFFIX
    assert "{{" not in suffix and "{minuti_per_lezione}" not in suffix
    assert "durata target indicata nel messaggio" in suffix


# ---------------------------------------------------------------------------
# 3. Serializzazione della dispensa precedente senza `asset_type` "(?)"
# ---------------------------------------------------------------------------


def test_previous_lesson_serialization_lists_assets_without_placeholder():
    lesson = CourseLesson(
        lesson_code="M1.L1",
        title="Lezione",
        content_raw={
            "introduction": "Intro.",
            "sections": [{"section_id": "S1", "title": "Prima", "content": "Testo."}],
            "summary": "Sintesi.",
            "key_takeaways": ["Uno", "Due", "Tre"],
            "visual_assets": [
                {
                    "asset_id": "fig_1",
                    "format": "mermaid",
                    "content": "graph TD; A-->B",
                    "caption": "Schema del flusso",
                    "alt_text": "",
                }
            ],
        },
    )
    text = content_svc._format_current_lesson_phase3(lesson)
    assert "- fig_1: Schema del flusso" in text
    assert "(?)" not in text


# ---------------------------------------------------------------------------
# 4. L'hint da solo non è una rigenerazione
# ---------------------------------------------------------------------------


def test_hint_alone_is_not_regeneration_for_any_phase():
    lesson = CourseLesson(
        lesson_code="M1.L1",
        title="Lezione",
        content_regeneration_hint="Più esempi",
        slides_regeneration_hint="Meno bullet",
        speech_regeneration_hint="Più lento",
    )
    assert content_svc.is_regeneration_for_lesson(lesson) is False
    assert slides_svc.is_regeneration_for_lesson(lesson) is False
    assert speech_svc.is_regeneration_for_lesson(lesson) is False

    lesson.content_raw = {"sections": []}
    lesson.slides_raw = {"slides": []}
    lesson.speech_raw = {"speech_segments": []}
    assert content_svc.is_regeneration_for_lesson(lesson) is True
    assert slides_svc.is_regeneration_for_lesson(lesson) is True
    assert speech_svc.is_regeneration_for_lesson(lesson) is True


async def test_generate_all_with_hint_keeps_hint_but_no_previous_version(
    seeded_db,
):
    course_id, _org, user = await build_course(seeded_db, status="lessons_structure_approved")
    course = await content_svc.load_course_full(seeded_db, course_id=course_id)
    assert course is not None

    refreshed = await content_svc.request_all_lessons_generation(
        seeded_db, course=course, actor_id=user.id, regeneration_hint="Più esempi"
    )
    lesson = find_lesson(refreshed, "M1.L1")
    assert lesson.content_regeneration_hint == "Più esempi"
    assert content_svc.is_regeneration_for_lesson(lesson) is False

    prompt = content_svc.build_user_prompt(refreshed, lesson)
    assert "## Indicazioni del docente per la rigenerazione" in prompt
    assert "Più esempi" in prompt
    assert "## Versione attuale della lezione (DA RIVEDERE)" not in prompt
