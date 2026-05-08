"""CRUD manuale dei campi Fase 2 (struttura formativa) di una lezione.

L'AI produce la struttura via worker (`course_lesson_structure_worker`),
ma il docente può raffinare manualmente i 4 campi JSONB di
`course_lesson` finché il modulo è in stato `ready` o `approved`.

Edit non degrada lo stato (resta `approved` se era `approved`): è una
scelta esplicita del docente.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.schemas.course_lesson_structure import LessonStructureUpdateInput

log = get_logger("app.course_lesson_structure_crud")


# Stati del modulo da cui è ammesso l'edit manuale dei 4 campi JSONB.
EDITABLE_MODULE_STATUSES = ("ready", "approved")


def _ensure_editable(lesson: CourseLesson) -> None:
    """Solleva ConflictError se il modulo della lezione non è in
    `ready`/`approved`."""
    module_status = lesson.module.lessons_structure_status
    if module_status not in EDITABLE_MODULE_STATUSES:
        raise ConflictError(
            f"Struttura lezione non editabile: il modulo è in stato "
            f"`{module_status}` (richiesto `ready` o `approved`).",
            code="lessons_structure_not_editable",
        )


def _validate_consistency(
    *,
    learning_objectives: list[str] | None,
    mandatory_topics: list[Any] | None,
    section_outline: list[Any] | None,
    current: CourseLesson,
) -> None:
    """Valida la consistenza dei 4 campi (topic_id univoci, section_id
    univoci, covers_topic_ids referenziati, coverage completa).

    Per i campi non passati nel patch, usa i valori attuali della
    lezione per la validazione cross-field.
    """
    # Effettivi dopo il merge
    effective_topics = (
        [t.model_dump() if hasattr(t, "model_dump") else t for t in mandatory_topics]
        if mandatory_topics is not None
        else (current.mandatory_topics or [])
    )
    effective_sections = (
        [
            s.model_dump() if hasattr(s, "model_dump") else s
            for s in section_outline
        ]
        if section_outline is not None
        else (current.section_outline or [])
    )

    # 1. topic_id univoci
    topic_ids = [t.get("topic_id") if isinstance(t, dict) else None for t in effective_topics]
    if any(tid is None or not str(tid).strip() for tid in topic_ids):
        raise ConflictError(
            "Ogni tema obbligatorio deve avere un `topic_id` non vuoto.",
            code="lessons_structure_topic_id_required",
        )
    if len(set(topic_ids)) != len(topic_ids):
        raise ConflictError(
            "I `topic_id` devono essere univoci all'interno della lezione.",
            code="lessons_structure_duplicate_topic_id",
        )

    # 2. section_id univoci
    section_ids = [
        s.get("section_id") if isinstance(s, dict) else None
        for s in effective_sections
    ]
    if any(sid is None or not str(sid).strip() for sid in section_ids):
        raise ConflictError(
            "Ogni sezione deve avere un `section_id` non vuoto.",
            code="lessons_structure_section_id_required",
        )
    if len(set(section_ids)) != len(section_ids):
        raise ConflictError(
            "I `section_id` devono essere univoci all'interno della lezione.",
            code="lessons_structure_duplicate_section_id",
        )

    # 3. covers_topic_ids referenziano topic_id esistenti
    valid_topic_ids = set(topic_ids)
    for s in effective_sections:
        if not isinstance(s, dict):
            continue
        covers = s.get("covers_topic_ids") or []
        for cid in covers:
            if cid not in valid_topic_ids:
                raise ConflictError(
                    f"La sezione `{s.get('section_id')}` referenzia "
                    f"topic_id `{cid}` non presente in `mandatory_topics`.",
                    code="lessons_structure_invalid_covers_reference",
                )


async def update_lesson_structure(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    payload: LessonStructureUpdateInput,
    actor_id: uuid.UUID,
) -> Course:
    """Patch dei 4 campi JSONB della lezione, con validazione di
    consistenza e gating sullo stato del modulo padre."""
    _ensure_editable(lesson)
    _validate_consistency(
        learning_objectives=payload.learning_objectives,
        mandatory_topics=payload.mandatory_topics,
        section_outline=payload.section_outline,
        current=lesson,
    )

    changed: dict[str, Any] = {}
    if payload.learning_objectives is not None:
        lesson.learning_objectives = list(payload.learning_objectives)
        changed["learning_objectives"] = len(lesson.learning_objectives)
    if payload.mandatory_topics is not None:
        lesson.mandatory_topics = [t.model_dump() for t in payload.mandatory_topics]
        changed["mandatory_topics"] = len(lesson.mandatory_topics)
    if payload.prerequisites is not None:
        lesson.prerequisites = list(payload.prerequisites)
        changed["prerequisites"] = len(lesson.prerequisites)
    if payload.section_outline is not None:
        lesson.section_outline = [s.model_dump() for s in payload.section_outline]
        changed["section_outline"] = len(lesson.section_outline)

    if not changed:
        # Nessun campo passato → no-op silenzioso (ritorna lo stato attuale).
        return course

    await write_audit(
        db,
        action="course.lesson.structure.updated",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
            "module_code": lesson.module.module_code,
            "fields": changed,
        },
    )
    await db.commit()
    # Reload con eager-load per evitare lazy-load durante la serializzazione.
    from app.services import course_lesson_structure_service

    return await course_lesson_structure_service._refresh_full(db, course)
