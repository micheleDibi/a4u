from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.models.organization import Organization
from app.models.user import User


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
