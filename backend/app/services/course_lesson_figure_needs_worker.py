"""Worker dei fabbisogni di figure di fonte (PROMPT 22, piano delle figure).

Prende le lezioni con `figure_needs_status = pending` (messe in coda da una
richiesta di Fase 3, `figure_plan_service.request_needs`), fino a
`FIGURE_NEEDS_CONCURRENCY` alla volta, con un UPDATE condizionale a
`processing`; calcola i fabbisogni e li salva con l'impronta dell'input.

- La struttura è cambiata nel frattempo (impronta diversa): si ricalcola
  con l'input attuale, senza richiesta nuova.
- Lezione senza scaletta o verifica: `skipped`, nessuna chiamata.
- Errori recuperabili (rete, 429, 5xx, risposta inutilizzabile) →
  di nuovo `pending` con backoff, fino a `FIGURE_NEEDS_AUTO_RETRY_MAX`
  ripetizioni, poi `failed` (la Fase 3 procede comunque, senza piano); il
  costo pagato resta in `figure_needs_usage`. Un 4xx diverso da 429 o la
  chiave assente → `failed` subito.
- Una nuova richiesta arrivata durante il calcolo (stato di nuovo
  `pending`) si soddisfa con il risultato solo se l'impronta dell'input
  attuale coincide; altrimenti resta in coda e si ricalcola.
- All'avvio i calcoli rimasti `processing` tornano `pending`.

Parte solo con `FIGURE_SOURCE_ENABLED` e `FIGURE_PLAN_ENABLED`.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.services import figure_plan_service as plan
from app.services import openai_figure_needs_service as needs_svc
from app.services.openai_client import OpenAINotConfiguredError

log = get_logger("app.course_lesson_figure_needs_worker")

RETRY_BASE_SECONDS = 30


def _now() -> datetime:
    return datetime.now(UTC)


async def claim_batch(db: AsyncSession, limit: int) -> list[uuid.UUID]:
    """Fino a `limit` lezioni `pending` con il backoff scaduto, portate a
    `processing` una per una con un UPDATE condizionale."""
    now = _now()
    rows = (
        (
            await db.execute(
                select(CourseLesson.id, CourseLesson.figure_needs_attempts)
                .where(CourseLesson.figure_needs_status == "pending")
                .order_by(CourseLesson.figure_needs_requested_at.asc().nulls_first())
                .limit(limit * 4)
            )
        )
        .tuples()
        .all()
    )
    claimed: list[uuid.UUID] = []
    for lesson_id, attempts in rows:
        if len(claimed) >= limit:
            break
        wait = timedelta(seconds=RETRY_BASE_SECONDS * int(attempts or 0))
        done = await db.execute(
            update(CourseLesson)
            .where(
                CourseLesson.id == lesson_id,
                CourseLesson.figure_needs_status == "pending",
                or_(
                    CourseLesson.figure_needs_checked_at.is_(None),
                    CourseLesson.figure_needs_checked_at <= now - wait,
                ),
            )
            .values(figure_needs_status="processing")
        )
        if getattr(done, "rowcount", 0) == 1:
            claimed.append(lesson_id)
    await db.commit()
    return claimed


async def _course_of(db: AsyncSession, lesson: CourseLesson) -> Course | None:
    return (
        await db.execute(
            select(Course)
            .where(Course.id == lesson.course_id)
            .options(selectinload(Course.modules).selectinload(CourseModule.lessons))
        )
    ).scalar_one_or_none()


async def process_lesson(db: AsyncSession, lesson_id: uuid.UUID) -> None:
    settings = get_settings()
    lesson = await db.get(CourseLesson, lesson_id, populate_existing=True)
    if lesson is None or lesson.figure_needs_status != "processing":
        return
    course = await _course_of(db, lesson)
    item = plan.needs_input(course, lesson) if course is not None else None
    if item is None:
        lesson.figure_needs_status = "skipped"
        lesson.figure_needs_checked_at = _now()
        await db.commit()
        return
    max_total = plan.max_needs(lesson)
    fp = plan.fingerprint(item, max_total)
    try:
        result, usage = await needs_svc.generate_needs(item, max_total=max_total)
    except (needs_svc.OpenAIFigureNeedsError, OpenAINotConfiguredError) as exc:
        await db.rollback()
        fresh = await db.get(CourseLesson, lesson_id, populate_existing=True)
        if fresh is None:
            return
        fresh.figure_needs_usage = plan.merge_usage(
            fresh.figure_needs_usage, getattr(exc, "usage", None)
        )
        fresh.figure_needs_checked_at = _now()
        if fresh.figure_needs_status != "processing":
            # Una richiesta nuova ha rimesso in coda la lezione: la si
            # lascia com'è (il costo pagato resta).
            await db.commit()
            return
        attempts = int(fresh.figure_needs_attempts or 0) + 1
        terminal = (
            isinstance(exc, OpenAINotConfiguredError)
            or _client_error(exc)
            or attempts > int(settings.figure_needs_auto_retry_max)
        )
        fresh.figure_needs_attempts = attempts
        fresh.figure_needs_status = "failed" if terminal else "pending"
        await db.commit()
        log.warning(
            "figure_needs_failed" if terminal else "figure_needs_retry",
            lesson_id=str(lesson_id),
            attempts=attempts,
            error=str(exc)[:300],
        )
        return
    fresh = await db.get(CourseLesson, lesson_id, populate_existing=True)
    if fresh is None:
        return
    if fresh.figure_needs_status == "pending":
        # Richiesta nuova durante il calcolo: vale il risultato solo se
        # l'input attuale è lo stesso; altrimenti si ricalcola.
        current = plan.needs_input(course, fresh) if course is not None else None
        if current is None or plan.fingerprint(current, plan.max_needs(fresh)) != fp:
            fresh.figure_needs_usage = plan.merge_usage(fresh.figure_needs_usage, usage)
            fresh.figure_needs_checked_at = _now()
            await db.commit()
            return
    elif fresh.figure_needs_status != "processing":
        # `skipped` o altro stato scritto nel frattempo: si tiene solo il costo.
        fresh.figure_needs_usage = plan.merge_usage(fresh.figure_needs_usage, usage)
        fresh.figure_needs_checked_at = _now()
        await db.commit()
        return
    plan.store_needs(
        fresh,
        result=result,
        fp=fp,
        model=str(settings.openai_figure_needs_model),
        usage=usage,
    )
    await db.commit()
    log.info(
        "figure_needs_ready",
        lesson_id=str(lesson_id),
        needs=len(result.needs),
        must=sum(1 for n in result.needs if n["priority"] == "must"),
        **result.dropped,
    )


def _client_error(exc: BaseException) -> bool:
    """4xx diverso da 429: la richiesta è sbagliata, ripeterla non serve."""
    status = getattr(exc, "status", None)
    return isinstance(status, int) and 400 <= status < 500 and status != 429


async def _process_one(lesson_id: uuid.UUID) -> None:
    try:
        async with async_session_factory() as db:
            try:
                await process_lesson(db, lesson_id)
            except Exception as exc:  # rete di sicurezza: stessa regola dei worker AI
                await db.rollback()
                log.error("figure_needs_unexpected", lesson_id=str(lesson_id), error=str(exc)[:300])
                fresh = await db.get(CourseLesson, lesson_id, populate_existing=True)
                if fresh is not None and fresh.figure_needs_status == "processing":
                    attempts = int(fresh.figure_needs_attempts or 0) + 1
                    limit = int(get_settings().figure_needs_auto_retry_max)
                    fresh.figure_needs_attempts = attempts
                    fresh.figure_needs_status = "failed" if attempts > limit else "pending"
                    fresh.figure_needs_checked_at = _now()
                    await db.commit()
    except Exception as exc:
        # Anche la rete di sicurezza può fallire (DB giù): il worker non deve
        # morire; la lezione resta `processing` e torna `pending` al riavvio.
        log.error("figure_needs_safety_net_failed", lesson_id=str(lesson_id), error=str(exc)[:300])


async def _tick() -> None:
    limit = max(1, int(get_settings().figure_needs_concurrency))
    async with async_session_factory() as db:
        try:
            ids = await claim_batch(db, limit)
        except Exception as exc:  # pragma: no cover
            await db.rollback()
            log.warning("figure_needs_tick_failed", error=str(exc))
            return
    if ids:
        await asyncio.gather(
            *(_process_one(lesson_id) for lesson_id in ids), return_exceptions=True
        )


async def reset_interrupted() -> None:
    """All'avvio: i calcoli rimasti `processing` tornano `pending`."""
    async with async_session_factory() as db:
        await db.execute(
            update(CourseLesson)
            .where(CourseLesson.figure_needs_status == "processing")
            .values(figure_needs_status="pending")
        )
        await db.commit()


_worker_task: asyncio.Task[None] | None = None
_stop_event: asyncio.Event | None = None


async def _run_loop() -> None:
    interval = max(1, int(get_settings().figure_needs_poll_interval_seconds))
    log.info("figure_needs_worker_started", interval=interval)
    try:
        await reset_interrupted()
    except Exception as exc:  # pragma: no cover
        log.warning("figure_needs_reset_failed", error=str(exc))
    assert _stop_event is not None
    while not _stop_event.is_set():
        try:
            await _tick()
        except Exception as exc:  # pragma: no cover - ultima difesa del loop
            log.error("figure_needs_tick_crashed", error=str(exc)[:300])
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
    log.info("figure_needs_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event
    if not plan.plan_active():
        log.info("figure_needs_worker_disabled")
        return
    if _worker_task is not None and not _worker_task.done():
        return
    _stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(_run_loop(), name="course_lesson_figure_needs_worker")


async def stop_worker() -> None:
    global _worker_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=30)
        except TimeoutError:
            _worker_task.cancel()
            await asyncio.gather(_worker_task, return_exceptions=True)
        except Exception as exc:  # task già terminato con un errore
            log.warning("figure_needs_worker_stop_error", error=str(exc)[:300])
    _worker_task = None
    _stop_event = None
