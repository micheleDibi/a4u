"""Background worker per l'export PDF delle SLIDE (Fase 4 §7).

Mirror di `course_lesson_pdf_worker` ma scoped a `slides_pdf_*`. Riusa
le settings PDF esistenti (`course_lesson_pdf_max_concurrency` /
`_poll_interval_seconds` / `_auto_retry_max`) per coerenza con il PDF
lezione testo.

Stato per lezione: `course_lesson.slides_pdf_status`:
    empty → pending → processing → ready → failed (solo dopo N retry)
"""
from __future__ import annotations

import asyncio
import time
import uuid

from sqlalchemy import select

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_slides_pdf_service
from app.services import course_lesson_slides_service

log = get_logger("app.course_lesson_slides_pdf.worker")


# State del worker.
_inflight: set[uuid.UUID] = set()
_inflight_lock = asyncio.Lock()
_semaphore: asyncio.Semaphore | None = None
_worker_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None
_active_tasks: set[asyncio.Task] = set()


def _apply_failure(
    lesson: CourseLesson,
    *,
    error: str,
    auto_retry_max: int,
) -> bool:
    """True se schedulato auto-retry, False se terminale."""
    attempts = lesson.slides_pdf_attempts or 0
    if attempts < auto_retry_max:
        lesson.slides_pdf_status = "pending"
        lesson.slides_pdf_error = None
        lesson.slides_pdf_progress = 0
        lesson.slides_pdf_progress_phase = None
        log.info(
            "lesson_slides_pdf_auto_retry",
            lesson_id=str(lesson.id),
            lesson_code=lesson.lesson_code,
            attempts=attempts,
            max_retry=auto_retry_max,
            error=error[:200],
        )
        return True
    lesson.slides_pdf_status = "failed"
    lesson.slides_pdf_error = error[:500]
    lesson.slides_pdf_progress = 0
    lesson.slides_pdf_progress_phase = None
    log.warning(
        "lesson_slides_pdf_failed_terminal",
        lesson_id=str(lesson.id),
        lesson_code=lesson.lesson_code,
        attempts=attempts,
        error=error[:200],
    )
    return False


async def _progress_ticker(
    lesson_id: uuid.UUID,
    *,
    start_pct: int,
    end_pct: int,
    duration_sec: float,
) -> None:
    started = time.monotonic()
    span = max(1, end_pct - start_pct)
    try:
        while True:
            await asyncio.sleep(2.0)
            elapsed = time.monotonic() - started
            ratio = min(1.0, elapsed / duration_sec)
            eased = 1 - (1 - ratio) ** 2
            target = start_pct + int(span * eased)
            target = min(end_pct, target)
            async with async_session_factory() as tdb:
                row = await tdb.get(CourseLesson, lesson_id)
                if row is None or row.slides_pdf_status != "processing":
                    return
                if row.slides_pdf_progress < target:
                    row.slides_pdf_progress = target
                    await tdb.commit()
            if target >= end_pct:
                return
    except asyncio.CancelledError:
        return


async def _set_progress(
    lesson_id: uuid.UUID, *, pct: int, phase: str | None
) -> None:
    async with async_session_factory() as tdb:
        row = await tdb.get(CourseLesson, lesson_id)
        if row is None:
            return
        row.slides_pdf_progress = max(0, min(100, pct))
        row.slides_pdf_progress_phase = phase
        await tdb.commit()


async def _process_one(lesson_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        bare = await db.get(CourseLesson, lesson_id)
        if bare is None:
            log.warning(
                "lesson_slides_pdf_lesson_not_found",
                lesson_id=str(lesson_id),
            )
            return
        if bare.slides_pdf_status != "pending":
            log.info(
                "lesson_slides_pdf_skip_not_pending",
                lesson_id=str(lesson_id),
                status=bare.slides_pdf_status,
            )
            return
        course_id = bare.course_id

        course_full = await course_lesson_slides_service.load_course_full(
            db, course_id=course_id
        )
        if course_full is None:
            log.warning(
                "lesson_slides_pdf_course_not_found",
                lesson_id=str(lesson_id),
                course_id=str(course_id),
            )
            return
        try:
            lesson = await course_lesson_slides_service.get_lesson_or_404(
                db, course=course_full, lesson_id=lesson_id
            )
        except Exception:
            return

        # → processing
        lesson.slides_pdf_attempts = (lesson.slides_pdf_attempts or 0) + 1
        lesson.slides_pdf_status = "processing"
        lesson.slides_pdf_error = None
        lesson.slides_pdf_progress = 5
        lesson.slides_pdf_progress_phase = "preparing"
        await db.commit()

        ticker_task = asyncio.create_task(
            _progress_ticker(
                lesson.id, start_pct=10, end_pct=85, duration_sec=20.0
            )
        )
        await _set_progress(lesson.id, pct=10, phase="rendering_html")

        try:
            try:
                await course_lesson_slides_pdf_service.materialize_lesson_slides_pdf(
                    db, course=course_full, lesson=lesson
                )
            except Exception as exc:  # noqa: BLE001
                settings = get_settings()
                terminal = not _apply_failure(
                    lesson,
                    error=str(exc),
                    auto_retry_max=settings.course_lesson_pdf_auto_retry_max,
                )
                if terminal:
                    await write_audit(
                        db,
                        action="course.lesson.slides_pdf.failed",
                        actor_user_id=None,
                        organization_id=course_full.organization_id,
                        target_type="course_lesson",
                        target_id=str(lesson.id),
                        metadata={
                            "course_id": str(course_full.id),
                            "lesson_code": lesson.lesson_code,
                            "error": str(exc)[:500],
                            "attempts": lesson.slides_pdf_attempts,
                        },
                    )
                await db.commit()
                return
        finally:
            ticker_task.cancel()
            try:
                await ticker_task
            except (asyncio.CancelledError, Exception):
                pass

        # Cancel-check post-render
        await db.refresh(lesson, ["slides_pdf_status"])
        if lesson.slides_pdf_status != "processing":
            log.info(
                "lesson_slides_pdf_cancelled_post_render",
                lesson_id=str(lesson.id),
                lesson_code=lesson.lesson_code,
                current_status=lesson.slides_pdf_status,
            )
            return

        lesson.slides_pdf_status = "ready"
        lesson.slides_pdf_progress = 100
        lesson.slides_pdf_progress_phase = None

        await write_audit(
            db,
            action="course.lesson.slides_pdf.generated",
            actor_user_id=None,
            organization_id=course_full.organization_id,
            target_type="course_lesson",
            target_id=str(lesson.id),
            metadata={
                "course_id": str(course_full.id),
                "lesson_code": lesson.lesson_code,
                "pdf_path": lesson.slides_pdf_path,
                "pdf_template_id": (
                    str(lesson.slides_pdf_template_id)
                    if lesson.slides_pdf_template_id
                    else None
                ),
                "attempts": lesson.slides_pdf_attempts,
            },
        )
        await db.commit()
        log.info(
            "lesson_slides_pdf_generated",
            lesson_id=str(lesson.id),
            lesson_code=lesson.lesson_code,
            pdf_path=lesson.slides_pdf_path,
        )


async def _bound_process(lesson_id: uuid.UUID) -> None:
    assert _semaphore is not None
    try:
        async with _semaphore:
            await _process_one(lesson_id)
    except Exception as exc:  # pragma: no cover
        log.error(
            "lesson_slides_pdf_worker_unexpected",
            lesson_id=str(lesson_id),
            error=str(exc),
            exc_info=True,
        )
    finally:
        async with _inflight_lock:
            _inflight.discard(lesson_id)


async def _tick() -> None:
    async with async_session_factory() as db:
        try:
            res = await db.execute(
                select(CourseLesson.id).where(
                    CourseLesson.slides_pdf_status == "pending"
                )
            )
            lesson_ids = [row[0] for row in res.all()]
        except Exception as exc:  # pragma: no cover
            log.warning(
                "lesson_slides_pdf_worker_tick_failed", error=str(exc)
            )
            return

    if not lesson_ids:
        return

    async with _inflight_lock:
        new_ids = [lid for lid in lesson_ids if lid not in _inflight]
        for lid in new_ids:
            _inflight.add(lid)

    for lid in new_ids:
        task = asyncio.create_task(
            _bound_process(lid),
            name=f"lesson_slides_pdf_lesson_{lid}",
        )
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)


async def _run_loop() -> None:
    settings = get_settings()
    interval = max(2, int(settings.course_lesson_pdf_poll_interval_seconds))
    log.info(
        "course_lesson_slides_pdf_worker_started",
        interval=interval,
        max_concurrency=settings.course_lesson_pdf_max_concurrency,
    )
    assert _stop_event is not None
    while not _stop_event.is_set():
        await _tick()
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    log.info("course_lesson_slides_pdf_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event, _semaphore
    if _worker_task is not None and not _worker_task.done():
        return
    settings = get_settings()
    _stop_event = asyncio.Event()
    _semaphore = asyncio.Semaphore(
        max(1, int(settings.course_lesson_pdf_max_concurrency))
    )
    _worker_task = asyncio.create_task(
        _run_loop(), name="course_lesson_slides_pdf_worker"
    )


async def stop_worker() -> None:
    global _worker_task, _stop_event, _semaphore
    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=15)
        except asyncio.TimeoutError:
            _worker_task.cancel()
            await asyncio.gather(_worker_task, return_exceptions=True)
    if _active_tasks:
        log.info(
            "lesson_slides_pdf_worker_waiting_inflight",
            count=len(_active_tasks),
        )
        await asyncio.gather(*list(_active_tasks), return_exceptions=True)
    _worker_task = None
    _stop_event = None
    _semaphore = None
    _inflight.clear()
    _active_tasks.clear()
