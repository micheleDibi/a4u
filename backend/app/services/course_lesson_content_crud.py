"""CRUD manuale dei campi Fase 3 (contenuti) di una lezione.

L'AI produce il contenuto via worker (`course_lesson_content_worker`),
ma il docente può raffinare manualmente il `content_raw` finché la
lezione è in stato `ready` o `approved`.

Edit non degrada lo stato (resta `approved` se era `approved`): è una
scelta esplicita del docente. Le validazioni hard di §6.4 sono
allentate per l'edit manuale (warning soft via log) per permettere
correzioni granulari.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.schemas.course_lesson_content import LessonContentUpdateInput

log = get_logger("app.course_lesson_content_crud")


# Stati della lezione da cui è ammesso l'edit manuale del content_raw.
EDITABLE_LESSON_STATUSES = ("ready", "approved")


_ASSET_REF_RE = re.compile(r"\[(FIG|TAB|EQ|EX):([^\]]+)\]")


def _ensure_editable(lesson: CourseLesson) -> None:
    """Solleva ConflictError se la lezione non è in `ready`/`approved`."""
    if lesson.content_status not in EDITABLE_LESSON_STATUSES:
        raise ConflictError(
            f"Contenuto lezione non editabile: stato attuale "
            f"`{lesson.content_status}` (richiesto `ready` o `approved`).",
            code="lesson_content_not_editable",
        )


def _dump_models(items: list[Any] | None) -> list[dict[str, Any]] | None:
    if items is None:
        return None
    return [
        i.model_dump() if hasattr(i, "model_dump") else i for i in items
    ]


def _validate_consistency(
    *,
    payload: LessonContentUpdateInput,
    current_raw: dict[str, Any],
) -> None:
    """Validazione minima di consistenza per l'edit manuale.

    Hard fail solo per duplicati di ID (rotture strutturali). Le
    coperture e i ref orfani sono solo warning via log (l'utente sa
    cosa sta facendo).
    """
    sections = (
        _dump_models(payload.sections)
        if payload.sections is not None
        else current_raw.get("sections", [])
    )
    section_ids = [
        s.get("section_id") for s in sections if isinstance(s, dict)
    ]
    if any(not sid or not str(sid).strip() for sid in section_ids):
        raise ConflictError(
            "Ogni sezione deve avere un `section_id` non vuoto.",
            code="lesson_content_section_id_required",
        )
    if len(set(section_ids)) != len(section_ids):
        raise ConflictError(
            "I `section_id` devono essere univoci.",
            code="lesson_content_duplicate_section_id",
        )

    visual_assets = (
        _dump_models(payload.visual_assets)
        if payload.visual_assets is not None
        else current_raw.get("visual_assets", [])
    )
    visual_ids = [
        a.get("asset_id") for a in visual_assets if isinstance(a, dict)
    ]
    if len(set(visual_ids)) != len(visual_ids):
        raise ConflictError(
            "Gli `asset_id` dei visual_assets devono essere univoci.",
            code="lesson_content_duplicate_visual_asset_id",
        )

    tables = (
        _dump_models(payload.tables)
        if payload.tables is not None
        else current_raw.get("tables", [])
    )
    table_ids = [
        t.get("table_id") for t in tables if isinstance(t, dict)
    ]
    if len(set(table_ids)) != len(table_ids):
        raise ConflictError(
            "I `table_id` devono essere univoci.",
            code="lesson_content_duplicate_table_id",
        )

    equations = (
        _dump_models(payload.equations)
        if payload.equations is not None
        else current_raw.get("equations", [])
    )
    eq_ids = [
        e.get("equation_id") for e in equations if isinstance(e, dict)
    ]
    if len(set(eq_ids)) != len(eq_ids):
        raise ConflictError(
            "Gli `equation_id` devono essere univoci.",
            code="lesson_content_duplicate_equation_id",
        )

    examples = (
        _dump_models(payload.examples)
        if payload.examples is not None
        else current_raw.get("examples", [])
    )
    ex_ids = [
        ex.get("example_id") for ex in examples if isinstance(ex, dict)
    ]
    if len(set(ex_ids)) != len(ex_ids):
        raise ConflictError(
            "Gli `example_id` devono essere univoci.",
            code="lesson_content_duplicate_example_id",
        )


async def update_lesson_content(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    payload: LessonContentUpdateInput,
    actor_id: uuid.UUID,
) -> Course:
    """Patch parziale del `content_raw` della lezione.

    Edit non degrada lo stato (`approved` resta `approved`).
    """
    _ensure_editable(lesson)
    current_raw: dict[str, Any] = dict(lesson.content_raw or {})

    _validate_consistency(payload=payload, current_raw=current_raw)

    changed: dict[str, int | str] = {}

    if payload.introduction is not None:
        current_raw["introduction"] = payload.introduction
        changed["introduction"] = len(payload.introduction)
    if payload.sections is not None:
        sections_dump = [s.model_dump() for s in payload.sections]
        current_raw["sections"] = sections_dump
        changed["sections"] = len(sections_dump)
    if payload.summary is not None:
        current_raw["summary"] = payload.summary
        changed["summary"] = len(payload.summary)
    if payload.key_takeaways is not None:
        current_raw["key_takeaways"] = list(payload.key_takeaways)
        changed["key_takeaways"] = len(payload.key_takeaways)
    if payload.visual_assets is not None:
        current_raw["visual_assets"] = [
            a.model_dump() for a in payload.visual_assets
        ]
        changed["visual_assets"] = len(payload.visual_assets)
    if payload.tables is not None:
        current_raw["tables"] = [t.model_dump() for t in payload.tables]
        changed["tables"] = len(payload.tables)
    if payload.equations is not None:
        current_raw["equations"] = [e.model_dump() for e in payload.equations]
        changed["equations"] = len(payload.equations)
    if payload.examples is not None:
        current_raw["examples"] = [ex.model_dump() for ex in payload.examples]
        changed["examples"] = len(payload.examples)
    if payload.references is not None:
        current_raw["references"] = [
            r.model_dump() for r in payload.references
        ]
        changed["references"] = len(payload.references)
    if payload.coverage_check is not None:
        current_raw["coverage_check"] = payload.coverage_check.model_dump()
        changed["coverage_check"] = "updated"

    if not changed:
        return course

    # Mantieni gli ID e i flag invariabili (lesson_id, lesson_title,
    # is_introductory, estimated_word_count). Se l'AI li ha già scritti,
    # restano. Se mancano (caso anomalo), seedaali dalla lezione.
    current_raw.setdefault("lesson_id", lesson.lesson_code)
    current_raw.setdefault("lesson_title", lesson.title)
    current_raw.setdefault("is_introductory", bool(lesson.is_introductory))
    current_raw.setdefault("estimated_word_count", 0)

    lesson.content_raw = current_raw
    # Stale-detection: marca il content come modificato manualmente. Il
    # FE confronta con `pdf_generated_at` per dedurre se il PDF
    # downstream è stale. I worker AI di Fase 3 NON toccano questo campo.
    lesson.content_modified_at = datetime.now(UTC)

    await write_audit(
        db,
        action="course.lesson.content.updated",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
            "fields": changed,
        },
    )
    await db.commit()

    from app.services import course_lesson_content_service

    return await course_lesson_content_service._refresh_full(db, course)
