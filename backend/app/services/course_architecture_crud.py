"""CRUD manuale su moduli e lezioni del corso.

L'utente può modificare l'architettura dopo la generazione AI:
- creare/modificare/eliminare moduli
- creare/modificare/eliminare lezioni
- riordinare moduli e lezioni

Le operazioni sono permesse solo finché lo status del corso è
`architecture_ready` o `architecture_approved` (oltre, le fasi successive
della pipeline AI dipendono dall'architettura e modificarla creerebbe
inconsistenze).

I codici (`module_code`, `lesson_code`) sono ricalcolati a ogni operazione
che cambia le posizioni: `M{N}` per i moduli e `M{K}.L{N}` per le lezioni.

Lo status non viene toccato — gli edit manuali sono ortogonali al ciclo
draft → pending → ready → approved.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import write_audit
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.schemas.course_architecture import (
    LessonCreateInput,
    LessonUpdateInput,
    ModuleCreateInput,
    ModuleUpdateInput,
)
from app.services import openai_module_lessons_service
from app.services.openai_client import OpenAINotConfiguredError

log = get_logger("app.course_architecture.crud")

# Stati in cui l'utente può modificare manualmente l'architettura.
# Includiamo tutti gli stati "stabili" downstream (ready/approved delle
# fasi successive) così l'utente può tornare indietro a correggere un
# titolo modulo / aggiungere una lezione anche dopo aver generato Fase 2
# o Fase 3. Lo stale-detection (cf. lib/staleness.ts) segnala quando
# downstream è da rigenerare.
#
# Esclusi:
# - `*_pending`: i worker AI stanno attivamente scrivendo, race condition.
# - `published`/`archived`: il corso è in stato terminale, non si tocca.
EDITABLE_STATUSES = {
    "architecture_ready",
    "architecture_approved",
    "lessons_structure_ready",
    "lessons_structure_approved",
    "content_ready",
    "content_approved",
    "slides_ready",
    "speech_ready",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _touch_module(module: CourseModule) -> None:
    """Marca il modulo come modificato per la stale-detection downstream.

    Set di `architecture_modified_at` al timestamp corrente. Il frontend
    confronta con `lessons_structure_generated_at` per decidere se
    suggerire una rigenerazione della struttura. NON va chiamato dai
    worker AI.
    """
    module.architecture_modified_at = _now()


async def _touch_module_by_id(
    db: AsyncSession, module_id: uuid.UUID
) -> None:
    module = await db.get(CourseModule, module_id)
    if module is not None:
        _touch_module(module)


def _ensure_editable(course: Course) -> None:
    if course.status not in EDITABLE_STATUSES:
        raise ConflictError(
            f"L'architettura è modificabile solo negli stati "
            f"{sorted(EDITABLE_STATUSES)}, attuale: {course.status}.",
            code="architecture_not_editable",
        )


async def _refresh_full(db: AsyncSession, course: Course) -> Course:
    """Ricarica il corso con tutti gli eager-loads usati dal CourseOut."""
    res = await db.execute(
        select(Course)
        .where(Course.id == course.id)
        .options(
            selectinload(Course.documents),
            selectinload(Course.modules).selectinload(CourseModule.lessons),
            selectinload(Course.categoria),
            selectinload(Course.stile_insegnamento),
            selectinload(Course.profondita_contenuto),
            selectinload(Course.ruolo_docente),
            selectinload(Course.dimensione_pubblico),
            selectinload(Course.livello_conoscenza),
            selectinload(Course.destinatari),
            selectinload(Course.livello_eqf),
            selectinload(Course.assignee),
            selectinload(Course.created_by),
        )
    )
    return res.scalar_one()


async def _renumber_modules(db: AsyncSession, course_id: uuid.UUID) -> None:
    """Ricalcola position e module_code dei moduli del corso, e di
    conseguenza i lesson_code di tutte le loro lezioni.

    Per evitare violazioni del constraint `uq_course_module_position`
    durante lo swap, prima sposta tutto fuori range, poi assegna i valori
    finali.
    """
    res = await db.execute(
        select(CourseModule)
        .where(CourseModule.course_id == course_id)
        .order_by(CourseModule.position)
        .options(selectinload(CourseModule.lessons))
    )
    modules = list(res.scalars().all())

    # Step 1: bump positions out of range per evitare collisioni di unique.
    for offset, m in enumerate(modules, start=1):
        m.position = 1000 + offset
        m.module_code = f"_M{offset}_tmp"
    await db.flush()

    # Step 2: assegna i valori definitivi.
    for new_pos, m in enumerate(modules, start=1):
        m.position = new_pos
        m.module_code = f"M{new_pos}"
    await db.flush()

    # Step 3: ricalcola i lesson_code in base ai nuovi module_code.
    for m in modules:
        await _renumber_lessons(db, m.id, _module_code=m.module_code)


async def _renumber_lessons(
    db: AsyncSession, module_id: uuid.UUID, *, _module_code: str | None = None
) -> None:
    """Ricalcola position e lesson_code delle lezioni di un modulo."""
    if _module_code is None:
        m = await db.get(CourseModule, module_id)
        if m is None:
            return
        _module_code = m.module_code

    res = await db.execute(
        select(CourseLesson)
        .where(CourseLesson.module_id == module_id)
        .order_by(CourseLesson.position)
    )
    lessons = list(res.scalars().all())

    for offset, l in enumerate(lessons, start=1):
        l.position = 1000 + offset
        l.lesson_code = f"_{_module_code}.L{offset}_tmp"
    await db.flush()

    for new_pos, l in enumerate(lessons, start=1):
        l.position = new_pos
        l.lesson_code = f"{_module_code}.L{new_pos}"
    await db.flush()


# ---------------------------------------------------------------------------
# Module CRUD
# ---------------------------------------------------------------------------


async def create_module(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    payload: ModuleCreateInput,
) -> Course:
    _ensure_editable(course)
    next_pos = (
        max((m.position for m in course.modules), default=0) + 1
        if course.modules
        else 1
    )
    module = CourseModule(
        course_id=course.id,
        position=next_pos,
        module_code=f"M{next_pos}",
        title=payload.title.strip(),
        description=payload.description.strip(),
    )
    db.add(module)
    await db.flush()
    _touch_module(module)
    await write_audit(
        db,
        action="course.module.created",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_module",
        target_id=str(module.id),
        metadata={"course_id": str(course.id), "module_code": module.module_code},
    )
    await db.commit()
    return await _refresh_full(db, course)


async def update_module(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    module_id: uuid.UUID,
    payload: ModuleUpdateInput,
) -> Course:
    _ensure_editable(course)
    module = await db.get(CourseModule, module_id)
    if module is None or module.course_id != course.id:
        raise NotFoundError("Modulo non trovato.", code="module_not_found")
    if payload.title is not None:
        module.title = payload.title.strip()
    if payload.description is not None:
        module.description = payload.description.strip()
    _touch_module(module)
    await write_audit(
        db,
        action="course.module.updated",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_module",
        target_id=str(module.id),
        metadata={"course_id": str(course.id), "module_code": module.module_code},
    )
    await db.commit()
    return await _refresh_full(db, course)


async def delete_module(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    module_id: uuid.UUID,
) -> Course:
    _ensure_editable(course)
    module = await db.get(CourseModule, module_id)
    if module is None or module.course_id != course.id:
        raise NotFoundError("Modulo non trovato.", code="module_not_found")
    code_snapshot = module.module_code
    await db.delete(module)
    await db.flush()
    await _renumber_modules(db, course.id)
    # Renumber ha cambiato i `module_code` dei moduli superstiti: marca
    # tutti come modificati così la struttura/contenuto downstream sa
    # che i riferimenti potrebbero essere stale.
    res = await db.execute(
        select(CourseModule).where(CourseModule.course_id == course.id)
    )
    for surviving in res.scalars().all():
        _touch_module(surviving)
    await write_audit(
        db,
        action="course.module.deleted",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_module",
        target_id=str(module_id),
        metadata={"course_id": str(course.id), "module_code": code_snapshot},
    )
    await db.commit()
    return await _refresh_full(db, course)


async def reorder_modules(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    new_order: list[uuid.UUID],
) -> Course:
    _ensure_editable(course)
    current_ids = {m.id for m in course.modules}
    if set(new_order) != current_ids or len(new_order) != len(current_ids):
        raise ValidationAppError(
            "L'elenco di IDs deve contenere esattamente i moduli del corso.",
            code="invalid_reorder",
        )
    by_id = {m.id: m for m in course.modules}

    # Carica tutte le lezioni di ogni modulo prima di iniziare il rename.
    lessons_by_mid: dict[uuid.UUID, list[CourseLesson]] = {}
    for mid in new_order:
        res = await db.execute(
            select(CourseLesson)
            .where(CourseLesson.module_id == mid)
            .order_by(CourseLesson.position)
        )
        lessons_by_mid[mid] = list(res.scalars().all())

    # Step 1: bump TUTTI i moduli a codici/posizioni temporanei.
    for offset, mid in enumerate(new_order, start=1):
        m = by_id[mid]
        m.position = 1000 + offset
        m.module_code = f"_M{offset}_tmp"

    # Step 2: bump TUTTE le lezioni a lesson_code globalmente univoci nel
    # corso. Senza questo, l'assegnazione finale dei lesson_code di un
    # modulo (es. M5.L1) può collidere con i codici "vecchi" di un altro
    # modulo non ancora processato (constraint uq_course_lesson_code è su
    # (course_id, lesson_code)).
    counter = 0
    for mid in new_order:
        for pos, lesson in enumerate(lessons_by_mid[mid], start=1):
            counter += 1
            lesson.position = 1000 + pos
            lesson.lesson_code = f"_tmp_{counter}"
    await db.flush()

    # Step 3: assegna posizioni e codici finali ai moduli.
    for new_pos, mid in enumerate(new_order, start=1):
        m = by_id[mid]
        m.position = new_pos
        m.module_code = f"M{new_pos}"
    await db.flush()

    # Step 4: assegna posizioni e codici finali alle lezioni.
    for mid in new_order:
        m = by_id[mid]
        for new_pos, lesson in enumerate(lessons_by_mid[mid], start=1):
            lesson.position = new_pos
            lesson.lesson_code = f"{m.module_code}.L{new_pos}"
    await db.flush()

    # Tutti i moduli hanno nuovi codici/posizioni: marca tutti come
    # modificati per la stale-detection downstream.
    for m in by_id.values():
        _touch_module(m)

    await write_audit(
        db,
        action="course.modules.reordered",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={"order": [str(i) for i in new_order]},
    )
    await db.commit()
    return await _refresh_full(db, course)


# ---------------------------------------------------------------------------
# Lesson CRUD
# ---------------------------------------------------------------------------


async def create_lesson(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    module_id: uuid.UUID,
    payload: LessonCreateInput,
) -> Course:
    _ensure_editable(course)
    module = await db.get(CourseModule, module_id)
    if module is None or module.course_id != course.id:
        raise NotFoundError("Modulo non trovato.", code="module_not_found")
    res = await db.execute(
        select(CourseLesson).where(CourseLesson.module_id == module_id)
    )
    existing = list(res.scalars().all())
    next_pos = (
        max((l.position for l in existing), default=0) + 1 if existing else 1
    )
    lesson = CourseLesson(
        module_id=module.id,
        course_id=course.id,
        position=next_pos,
        lesson_code=f"{module.module_code}.L{next_pos}",
        title=payload.title.strip(),
        summary=payload.summary.strip(),
        is_introductory=payload.is_introductory,
        recommended_bibliography=[
            b.model_dump() for b in payload.recommended_bibliography
        ],
    )
    db.add(lesson)
    await db.flush()
    _touch_module(module)
    await write_audit(
        db,
        action="course.lesson.created",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "module_id": str(module.id),
            "lesson_code": lesson.lesson_code,
        },
    )
    await db.commit()
    return await _refresh_full(db, course)


async def update_lesson(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonUpdateInput,
) -> Course:
    _ensure_editable(course)
    lesson = await db.get(CourseLesson, lesson_id)
    if lesson is None or lesson.course_id != course.id:
        raise NotFoundError("Lezione non trovata.", code="lesson_not_found")
    if payload.title is not None:
        lesson.title = payload.title.strip()
    if payload.summary is not None:
        lesson.summary = payload.summary.strip()
    if payload.is_introductory is not None:
        lesson.is_introductory = payload.is_introductory
    if payload.recommended_bibliography is not None:
        lesson.recommended_bibliography = [
            b.model_dump() for b in payload.recommended_bibliography
        ]
    await _touch_module_by_id(db, lesson.module_id)
    await write_audit(
        db,
        action="course.lesson.updated",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson.id),
        metadata={
            "course_id": str(course.id),
            "lesson_code": lesson.lesson_code,
        },
    )
    await db.commit()
    return await _refresh_full(db, course)


async def delete_lesson(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    lesson_id: uuid.UUID,
) -> Course:
    _ensure_editable(course)
    lesson = await db.get(CourseLesson, lesson_id)
    if lesson is None or lesson.course_id != course.id:
        raise NotFoundError("Lezione non trovata.", code="lesson_not_found")
    module_id = lesson.module_id
    code_snapshot = lesson.lesson_code
    await db.delete(lesson)
    await db.flush()
    await _renumber_lessons(db, module_id)
    await _touch_module_by_id(db, module_id)
    await write_audit(
        db,
        action="course.lesson.deleted",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_lesson",
        target_id=str(lesson_id),
        metadata={
            "course_id": str(course.id),
            "module_id": str(module_id),
            "lesson_code": code_snapshot,
        },
    )
    await db.commit()
    return await _refresh_full(db, course)


async def reorder_lessons(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    module_id: uuid.UUID,
    new_order: list[uuid.UUID],
) -> Course:
    _ensure_editable(course)
    module = await db.get(CourseModule, module_id)
    if module is None or module.course_id != course.id:
        raise NotFoundError("Modulo non trovato.", code="module_not_found")
    res = await db.execute(
        select(CourseLesson).where(CourseLesson.module_id == module_id)
    )
    lessons = list(res.scalars().all())
    current_ids = {l.id for l in lessons}
    if set(new_order) != current_ids or len(new_order) != len(current_ids):
        raise ValidationAppError(
            "L'elenco di IDs deve contenere esattamente le lezioni del modulo.",
            code="invalid_reorder",
        )
    by_id = {l.id: l for l in lessons}
    for offset, lid in enumerate(new_order, start=1):
        l = by_id[lid]
        l.position = 1000 + offset
        l.lesson_code = f"_{module.module_code}.L{offset}_tmp"
    await db.flush()
    for new_pos, lid in enumerate(new_order, start=1):
        l = by_id[lid]
        l.position = new_pos
        l.lesson_code = f"{module.module_code}.L{new_pos}"
    await db.flush()
    _touch_module(module)
    await write_audit(
        db,
        action="course.lessons.reordered",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_module",
        target_id=str(module_id),
        metadata={
            "course_id": str(course.id),
            "order": [str(i) for i in new_order],
        },
    )
    await db.commit()
    return await _refresh_full(db, course)


# ---------------------------------------------------------------------------
# AI lesson generation per modulo
# ---------------------------------------------------------------------------


def _build_module_lessons_user_prompt(
    *, course: Course, target: CourseModule, expected_count: int
) -> str:
    """Costruisce il prompt utente per la generazione lezioni di un modulo."""
    lines: list[str] = []
    lines.append("**Corso**")
    lines.append(f"- Titolo: {course.title}")
    if course.objectives:
        lines.append(f"- Obiettivi: {course.objectives}")
    if course.argomenti_chiave:
        lines.append(
            "- Argomenti chiave: " + ", ".join(course.argomenti_chiave)
        )
    if course.course_overview:
        lines.append(f"- Panoramica: {course.course_overview}")
    if course.pedagogical_rationale:
        lines.append(f"- Razionale didattico: {course.pedagogical_rationale}")
    lines.append("")

    lines.append("**Altri moduli del corso (contesto)**")
    other = [m for m in course.modules if m.id != target.id]
    if other:
        for m in sorted(other, key=lambda x: x.position):
            lines.append(f"- {m.module_code}: {m.title}")
            if m.description:
                lines.append(f"    {m.description}")
            if m.lessons:
                for l in sorted(m.lessons, key=lambda x: x.position):
                    lines.append(f"    • {l.lesson_code}: {l.title}")
    else:
        lines.append("(nessun altro modulo definito)")
    lines.append("")

    lines.append("**Modulo target**")
    lines.append(f"- Codice: {target.module_code}")
    lines.append(f"- Titolo: {target.title}")
    if target.description:
        lines.append(f"- Descrizione: {target.description}")
    lines.append("")

    lines.append("**Compito**")
    lines.append(
        f"Genera esattamente {expected_count} lezioni per il modulo target. "
        "Ogni lezione deve avere title (conciso) e summary (1-3 frasi)."
    )
    return "\n".join(lines)


async def regenerate_module_lessons(
    db: AsyncSession,
    *,
    course: Course,
    actor_id: uuid.UUID,
    module_id: uuid.UUID,
) -> Course:
    """Genera le lezioni di un modulo via AI e le salva, sostituendo
    quelle eventualmente già presenti.

    Output: corso aggiornato (eager-loaded) con le nuove lezioni.
    """
    _ensure_editable(course)
    module = await db.get(CourseModule, module_id)
    if module is None or module.course_id != course.id:
        raise NotFoundError("Modulo non trovato.", code="module_not_found")

    expected = course.lessons_per_module or 3
    user_prompt = _build_module_lessons_user_prompt(
        course=course, target=module, expected_count=expected
    )

    try:
        lessons, usage = await openai_module_lessons_service.generate_module_lessons(
            user_prompt=user_prompt,
            language_code=course.language_code,
            expected_count=expected,
        )
    except OpenAINotConfiguredError:
        raise ValidationAppError(
            "OpenAI non configurato: l'amministratore deve impostare "
            "OPENAI_API_KEY nel file .env del backend.",
            code="openai_not_configured",
        )
    except openai_module_lessons_service.OpenAIModuleLessonsError as exc:
        log.warning(
            "module_lessons_generation_failed",
            course_id=str(course.id),
            module_id=str(module.id),
            error=str(exc),
        )
        raise ValidationAppError(
            f"Generazione lezioni fallita: {exc}",
            code="module_lessons_generation_failed",
        )

    # Pulisce le lezioni esistenti e ne crea di nuove (1-based positions).
    res = await db.execute(
        select(CourseLesson).where(CourseLesson.module_id == module.id)
    )
    for old in res.scalars().all():
        await db.delete(old)
    await db.flush()

    for idx, item in enumerate(lessons, start=1):
        db.add(
            CourseLesson(
                module_id=module.id,
                course_id=course.id,
                position=idx,
                lesson_code=f"{module.module_code}.L{idx}",
                title=item["title"][:300],
                summary=item.get("summary", "")[:4000],
                is_introductory=False,
                recommended_bibliography=[],
            )
        )
    await db.flush()
    _touch_module(module)

    await write_audit(
        db,
        action="course.module.lessons.generated",
        actor_user_id=actor_id,
        organization_id=course.organization_id,
        target_type="course_module",
        target_id=str(module.id),
        metadata={
            "course_id": str(course.id),
            "module_code": module.module_code,
            "count": len(lessons),
            "tokens_total": usage.get("total"),
            "model": usage.get("model"),
        },
    )
    await db.commit()
    return await _refresh_full(db, course)


__all__ = [
    "create_module",
    "update_module",
    "delete_module",
    "reorder_modules",
    "create_lesson",
    "update_lesson",
    "delete_lesson",
    "reorder_lessons",
    "regenerate_module_lessons",
]
