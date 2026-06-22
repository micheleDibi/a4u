"""CRUD manuale dei campi Fase 4 (slide) di una lezione.

L'AI produce le slide via worker (`course_lesson_slides_worker`), ma il
docente può raffinare manualmente il `slides_raw` finché la lezione è
in stato `ready` o `approved`.

Edit non degrada lo stato (resta `approved` se era `approved`): è una
scelta esplicita del docente. La validazione di consistenza qui è
allentata rispetto a `materialize_lesson_slides` (che valida l'output
fresh dell'AI): hard fail solo per duplicati di ID e per ref orfani
(che renderebbero la slide inutilizzabile a render).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.schemas.course_lesson_slides import LessonSlidesUpdateInput

log = get_logger("app.course_lesson_slides_crud")


# Stati della lezione da cui è ammesso l'edit manuale del slides_raw.
EDITABLE_LESSON_STATUSES = ("ready", "approved")


def _ensure_editable(lesson: CourseLesson) -> None:
    """Solleva ConflictError se la lezione non è in `ready`/`approved`."""
    if lesson.slides_status not in EDITABLE_LESSON_STATUSES:
        raise ConflictError(
            f"Slide lezione non editabili: stato attuale "
            f"`{lesson.slides_status}` (richiesto `ready` o `approved`).",
            code="lesson_slides_not_editable",
        )


def _dump_models(items: list[Any] | None) -> list[dict[str, Any]] | None:
    if items is None:
        return None
    return [
        i.model_dump() if hasattr(i, "model_dump") else i for i in items
    ]


def _validate_consistency(
    *,
    payload: LessonSlidesUpdateInput,
    current_raw: dict[str, Any],
    content_raw: dict[str, Any] | None,
) -> None:
    """Validazione minima di consistenza per l'edit manuale.

    Hard fail per: slide_id duplicati, slide_number non sequenziali,
    new_assets asset_id duplicati, references_assets verso ID
    inesistenti (non risolvibili in content_raw + new_assets effettivi).
    """
    slides = (
        _dump_models(payload.slides)
        if payload.slides is not None
        else current_raw.get("slides", [])
    ) or []
    new_assets = (
        _dump_models(payload.new_assets)
        if payload.new_assets is not None
        else current_raw.get("new_assets", [])
    ) or []
    new_tables = (
        _dump_models(payload.new_tables)
        if payload.new_tables is not None
        else current_raw.get("new_tables", [])
    ) or []
    new_equations = (
        _dump_models(payload.new_equations)
        if payload.new_equations is not None
        else current_raw.get("new_equations", [])
    ) or []
    new_examples = (
        _dump_models(payload.new_examples)
        if payload.new_examples is not None
        else current_raw.get("new_examples", [])
    ) or []

    # 1. slide_id univoci
    slide_ids = [
        s.get("slide_id") for s in slides if isinstance(s, dict)
    ]
    if any(not sid or not str(sid).strip() for sid in slide_ids):
        raise ConflictError(
            "Ogni slide deve avere uno `slide_id` non vuoto.",
            code="lesson_slides_slide_id_required",
        )
    if len(set(slide_ids)) != len(slide_ids):
        raise ConflictError(
            "Gli `slide_id` devono essere univoci.",
            code="lesson_slides_duplicate_slide_id",
        )

    # 2. slide_number sequenziali 1..N
    nums = [
        s.get("slide_number")
        for s in slides
        if isinstance(s, dict) and s.get("slide_number") is not None
    ]
    if len(nums) != len(slides):
        raise ConflictError(
            "Ogni slide deve avere un `slide_number` numerico.",
            code="lesson_slides_slide_number_required",
        )
    if sorted(nums) != list(range(1, len(nums) + 1)):
        raise ConflictError(
            "Gli `slide_number` devono essere sequenziali 1..N "
            f"(trovati: {sorted(nums)}).",
            code="lesson_slides_nonsequential",
        )

    # 3. id dei nuovi asset (visivi + tabelle + equazioni + esempi) non
    #    vuoti e univoci nello spazio-id piatto delle references_assets.
    new_ids: list[str] = []
    for a in new_assets:
        if isinstance(a, dict):
            new_ids.append(str(a.get("asset_id") or "").strip())
    for tbl in new_tables:
        if isinstance(tbl, dict):
            new_ids.append(str(tbl.get("table_id") or "").strip())
    for eq in new_equations:
        if isinstance(eq, dict):
            new_ids.append(str(eq.get("equation_id") or "").strip())
    for ex in new_examples:
        if isinstance(ex, dict):
            new_ids.append(str(ex.get("example_id") or "").strip())
    if any(not nid for nid in new_ids):
        raise ConflictError(
            "Ogni nuovo asset deve avere un id non vuoto.",
            code="lesson_slides_new_asset_id_required",
        )
    if len(set(new_ids)) != len(new_ids):
        raise ConflictError(
            "Gli id dei nuovi asset (tabelle/equazioni/esempi inclusi) "
            "devono essere univoci.",
            code="lesson_slides_duplicate_new_asset_id",
        )

    # 4. references_assets risolvibili (in content_raw + nuovi asset).
    #    Match case-insensitive: il riferimento della slide e l'id
    #    dichiarato dell'asset sono generati dall'AI con case non sempre
    #    coerente (es. asset `TAB_x` referenziato come `tab_x`).
    valid_asset_ids: set[str] = {nid.lower() for nid in new_ids}
    if content_raw:
        for key in ("visual_assets", "tables", "equations", "examples"):
            for a in content_raw.get(key, []) or []:
                if isinstance(a, dict):
                    for id_key in (
                        "asset_id",
                        "table_id",
                        "equation_id",
                        "example_id",
                    ):
                        if id_key in a and a[id_key]:
                            valid_asset_ids.add(str(a[id_key]).lower())

    for s in slides:
        if not isinstance(s, dict):
            continue
        refs = s.get("references_assets") or []
        for aid in refs:
            if str(aid).strip().lower() not in valid_asset_ids:
                raise ConflictError(
                    f"Slide {s.get('slide_id')}: references_assets contiene "
                    f"`{aid}` non presente nelle Dispense né tra i nuovi asset.",
                    code="lesson_slides_unknown_asset_ref",
                )

    # 5. source_section_id (se non vuoto) deve referenziare una sezione
    valid_section_ids: set[str] = set()
    if content_raw:
        for s in content_raw.get("sections") or []:
            if isinstance(s, dict) and s.get("section_id"):
                valid_section_ids.add(str(s["section_id"]))
    for s in slides:
        if not isinstance(s, dict):
            continue
        ssid = s.get("source_section_id") or ""
        if ssid and ssid not in valid_section_ids:
            raise ConflictError(
                f"Slide {s.get('slide_id')}: source_section_id "
                f"`{ssid}` non esiste nelle sezioni di Fase 3.",
                code="lesson_slides_unknown_source_section",
            )


async def update_lesson_slides(
    db: AsyncSession,
    *,
    course: Course,
    lesson: CourseLesson,
    payload: LessonSlidesUpdateInput,
    actor_id: uuid.UUID,
) -> Course:
    """Patch parziale del `slides_raw` della lezione.

    Edit non degrada lo stato (`approved` resta `approved`). Setta
    `slides_modified_at` per la stale-detection downstream (PDF slide).
    """
    _ensure_editable(lesson)
    current_raw: dict[str, Any] = dict(lesson.slides_raw or {})

    _validate_consistency(
        payload=payload,
        current_raw=current_raw,
        content_raw=lesson.content_raw,
    )

    changed: dict[str, int | str] = {}

    if payload.slides is not None:
        slides_dump = [s.model_dump() for s in payload.slides]
        current_raw["slides"] = slides_dump
        current_raw["total_slides"] = len(slides_dump)
        changed["slides"] = len(slides_dump)
    if payload.new_assets is not None:
        current_raw["new_assets"] = [a.model_dump() for a in payload.new_assets]
        changed["new_assets"] = len(payload.new_assets)
    if payload.new_tables is not None:
        current_raw["new_tables"] = [tbl.model_dump() for tbl in payload.new_tables]
        changed["new_tables"] = len(payload.new_tables)
    if payload.new_equations is not None:
        current_raw["new_equations"] = [
            eq.model_dump() for eq in payload.new_equations
        ]
        changed["new_equations"] = len(payload.new_equations)
    if payload.new_examples is not None:
        current_raw["new_examples"] = [
            ex.model_dump() for ex in payload.new_examples
        ]
        changed["new_examples"] = len(payload.new_examples)

    if not changed:
        return course

    # Mantieni `lesson_id` invariato (è il lesson_code).
    current_raw.setdefault("lesson_id", lesson.lesson_code)
    if "total_slides" not in current_raw:
        current_raw["total_slides"] = len(current_raw.get("slides", []))

    lesson.slides_raw = current_raw
    # Stale-detection: marca le slide come modificate manualmente. Il FE
    # confronta con `slides_pdf_generated_at` (Step 7 PDF) per dedurre
    # se il PDF slide downstream è stale. I worker AI di Fase 4 NON
    # toccano questo campo.
    lesson.slides_modified_at = datetime.now(UTC)

    await write_audit(
        db,
        action="course.lesson.slides.updated",
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

    from app.services import course_lesson_slides_service

    return await course_lesson_slides_service._refresh_full(db, course)
