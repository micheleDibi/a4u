from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.course import Course
from app.models.course_document import CourseDocument
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.models.organization import Organization
from app.models.user import User
from app.schemas.course_lesson_content import LessonContentOutput


def _now() -> datetime:
    return datetime.now(UTC)


async def build_course(
    db: AsyncSession,
    *,
    status: str = "lessons_structure_approved",
    modules: int = 2,
    lessons_per_module: int = 2,
    module_status: str = "approved",
    with_structure: bool = True,
    with_assessment: bool = False,
    content_status: str = "empty",
    slides_status: str = "empty",
    speech_status: str = "empty",
) -> tuple[uuid.UUID, Organization, User]:
    """Costruisce org + utente assegnatario + corso con moduli e lezioni.

    Pattern analogo a `_setup_user_membership` di test_permissions: dati
    minimi validi, unicità garantita dagli uuid nei campi identificativi.
    Gli stati per-fase vengono applicati uniformemente a tutte le lezioni
    (le assessment restano `empty` su slides/speech, come in produzione).
    Ritorna l'id del corso: ricaricarlo con `load_course_full` del service
    sotto test per avere gli eager-load corretti.
    """
    user = User(
        email=f"c-{uuid.uuid4().hex[:8]}@x.it",
        password_hash=hash_password("Password123!"),
        full_name="Docente Test",
        is_active=True,
    )
    org = Organization(
        name=f"Org-{uuid.uuid4().hex[:6]}",
        email=f"o-{uuid.uuid4().hex[:6]}@x.it",
    )
    db.add_all([user, org])
    await db.flush()

    course = Course(
        organization_id=org.id,
        title="Corso di prova",
        objectives="Obiettivi di prova.",
        language_code="it",
        cfu=6,
        modules_count=modules,
        lessons_per_module=lessons_per_module,
        lesson_duration_minutes=45,
        assessment_lesson_enabled=with_assessment,
        multiple_choice_questions_count=0,
        open_questions_count=0,
        assignee_user_id=user.id,
        created_by_user_id=user.id,
        status=status,
        didactic_setup_confirmed_at=_now(),
    )
    db.add(course)
    await db.flush()

    module_approved_at = _now() if module_status == "approved" else None
    for m_idx in range(1, modules + 1):
        module = CourseModule(
            course_id=course.id,
            position=m_idx,
            module_code=f"M{m_idx}",
            title=f"Modulo {m_idx}",
            lessons_structure_status=module_status,
            lessons_structure_approved_at=module_approved_at,
        )
        db.add(module)
        await db.flush()
        for l_idx in range(1, lessons_per_module + 1):
            is_assessment = (
                with_assessment
                and m_idx == modules
                and l_idx == lessons_per_module
            )
            lesson = CourseLesson(
                module_id=module.id,
                course_id=course.id,
                position=l_idx,
                lesson_code=f"M{m_idx}.L{l_idx}",
                title=f"Lezione {m_idx}.{l_idx}",
                summary="Sommario di prova.",
                is_assessment=is_assessment,
                learning_objectives=(
                    ["Comprendere l'argomento"] if with_structure else []
                ),
                mandatory_topics=(
                    [{"topic_id": "T1", "title": "Argomento 1"}]
                    if with_structure
                    else []
                ),
                prerequisites=[],
                section_outline=(
                    [{"section_id": "S1", "title": "Introduzione"}]
                    if with_structure
                    else []
                ),
                content_status=content_status,
                slides_status="empty" if is_assessment else slides_status,
                speech_status="empty" if is_assessment else speech_status,
            )
            db.add(lesson)
    await db.commit()
    return course.id, org, user


def find_lesson(course: Course, lesson_code: str) -> CourseLesson:
    for module in course.modules:
        for lesson in module.lessons:
            if lesson.lesson_code == lesson_code:
                return lesson
    raise AssertionError(f"Lezione {lesson_code} non trovata nel corso di test")


def build_document_summary(
    *,
    title: str = "",
    authors: list[str] | None = None,
    abstract: str = "Un abstract sul tema.",
    **overrides: Any,
) -> dict[str, Any]:
    """Riassunto Appendice A minimo e valido; `overrides` sostituisce i
    campi (es. `key_concepts=[...]`, `examples_or_cases=[...]`)."""
    summary: dict[str, Any] = {
        "source_title": title,
        "detected_language": "it",
        "abstract": abstract,
        "structure_outline": ["Capitolo 1"],
        "key_concepts": [{"name": "Concetto", "explanation": "Spiegazione."}],
        "definitions": [{"term": "Termine", "definition": "Definizione."}],
        "examples_or_cases": [],
        "formulas_or_rules": [],
        "authors_and_references": [
            {"type": "author", "value": a} for a in (authors or [])
        ],
        "didactic_relevance_tags": ["tag1"],
    }
    summary.update(overrides)
    return summary


def build_course_document(
    course_id: uuid.UUID,
    *,
    filename: str,
    policy: str = "citable",
    summary: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> CourseDocument:
    """`CourseDocument` in memoria (non persistito) con riassunto `ready`."""
    doc = CourseDocument(
        course_id=course_id,
        filename_original=filename,
        filename_stored=f"{uuid.uuid4().hex}.pdf",
        file_path=f"/uploads/courses/{course_id}/x.pdf",
        mime_type="application/pdf",
        size_bytes=1,
        summary_status="ready",
        summary=summary if summary is not None else build_document_summary(),
        citation_policy=policy,
    )
    if created_at is not None:
        doc.created_at = created_at
    return doc


def build_lesson_content_output(
    *,
    lesson_code: str = "M1.L1",
    sections: list[tuple[str, list[str], list[str]]] | None = None,
    coverage_objectives: list[str] | None = None,
    coverage_topics: list[str] | None = None,
    **overrides: Any,
) -> LessonContentOutput:
    """Output di Fase 3 minimo e valido (§6.3).

    `sections` è una lista di `(section_id, objectives_addressed,
    topics_addressed)`; il `coverage_check` rispecchia le sezioni se non
    si passano `coverage_objectives`/`coverage_topics` (che servono a
    simulare un modello che dichiara una copertura incoerente).
    """
    rows = sections or [("S1", ["Comprendere l'argomento"], ["T1"])]
    objectives = (
        coverage_objectives
        if coverage_objectives is not None
        else list(dict.fromkeys(o for _sid, objs, _t in rows for o in objs))
    )
    topics = (
        coverage_topics
        if coverage_topics is not None
        else list(dict.fromkeys(t for _sid, _o, tids in rows for t in tids))
    )
    payload: dict[str, Any] = {
        "lesson_id": lesson_code,
        "lesson_title": "Lezione di prova",
        "is_introductory": False,
        "estimated_word_count": 900,
        "introduction": "Introduzione della lezione.",
        "sections": [
            {
                "section_id": sid,
                "title": f"Sezione {sid}",
                "content": "Testo della sezione.",
                "objectives_addressed": list(objs),
                "topics_addressed": list(tids),
            }
            for sid, objs, tids in rows
        ],
        "summary": "Sintesi della lezione.",
        "key_takeaways": ["Primo punto", "Secondo punto", "Terzo punto"],
        "visual_assets": [],
        "tables": [],
        "equations": [],
        "examples": [],
        "references": [],
        "coverage_check": {
            "objectives_covered": [
                {
                    "objective": objective,
                    "covered_in_section_ids": [
                        sid for sid, objs, _t in rows if objective in objs
                    ],
                }
                for objective in objectives
            ],
            "topics_covered": [
                {
                    "topic_id": topic_id,
                    "covered_in_section_ids": [
                        sid for sid, _o, tids in rows if topic_id in tids
                    ],
                }
                for topic_id in topics
            ],
        },
    }
    payload.update(overrides)
    return LessonContentOutput.model_validate(payload)
