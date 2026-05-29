"""Background worker per la generazione delle slide delle lezioni
(Fase 4 — §7).

Pattern speculare al worker di Fase 3 (contenuti) ma scoped a Fase 4:
dispatcha le lezioni con `slides_status='pending'` IN PARALLELO con cap
di concorrenza configurabile (default 3).

Stato per lezione su `course_lesson.slides_status`:
    empty → pending → processing → ready → approved
                                  ↘ failed (solo dopo N retry esauriti)

**Auto-retry trasparente** (`course_lesson_slides_auto_retry_max`,
default 5): se la generazione fallisce in modo recuperabile (errore
OpenAI transiente, validazione §7.4, materializzazione), il worker NON
transita a `failed` — riporta lo status a `pending` e il ticker
successivo (4s) ritenta. La UI vede solo "in elaborazione" finché passa.

Pre-condizione: la lezione deve avere `content_status ∈ (ready, approved)`
(servono `content_raw` come input). Se non lo è, transizione a
`failed` con error chiaro (non recuperabile).

Il progresso (0-100%) e la fase corrente sono persistiti su
`course_lesson.slides_progress` / `slides_progress_phase`. Phases:
`preparing_prompt → calling_openai → materializing`.
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
from app.services import (
    asset_validation_service,
    course_lesson_slides_service,
    openai_lesson_slides_service,
)
from app.services.openai_client import OpenAINotConfiguredError

log = get_logger("app.course_lesson_slides.worker")


# ---------------------------------------------------------------------------
# State del worker (lesson-scope)
# ---------------------------------------------------------------------------

_inflight: set[uuid.UUID] = set()
_inflight_lock = asyncio.Lock()

_semaphore: asyncio.Semaphore | None = None
_worker_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None
_active_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Auto-retry helper
# ---------------------------------------------------------------------------


def _apply_failure(
    lesson: CourseLesson,
    *,
    error: str,
    phase: str,
    recoverable: bool,
    auto_retry_max: int,
) -> bool:
    """Decide se ritentare automaticamente o passare a `failed`.

    Scrive sui campi della lezione SENZA committare. Il caller fa il
    commit + audit log dopo aver visto il return value.

    Returns:
        True se è stato schedulato un auto-retry (status='pending').
        False se è transizione terminale (status='failed').
    """
    attempts = lesson.slides_attempts or 0
    if recoverable and attempts < auto_retry_max:
        lesson.slides_status = "pending"
        lesson.slides_error = None
        lesson.slides_progress = 0
        lesson.slides_progress_phase = None
        log.info(
            "lesson_slides_auto_retry",
            lesson_id=str(lesson.id),
            lesson_code=lesson.lesson_code,
            phase=phase,
            attempts=attempts,
            max_retry=auto_retry_max,
            error=error[:200],
        )
        return True
    lesson.slides_status = "failed"
    lesson.slides_error = error[:500]
    lesson.slides_progress = 0
    lesson.slides_progress_phase = None
    log.warning(
        "lesson_slides_failed_terminal",
        lesson_id=str(lesson.id),
        lesson_code=lesson.lesson_code,
        phase=phase,
        attempts=attempts,
        error=error[:200],
    )
    return False


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


async def _set_progress(
    lesson_id: uuid.UUID, *, pct: int, phase: str | None
) -> None:
    """Aggiorna `slides_progress` + phase su una sessione propria."""
    async with async_session_factory() as tdb:
        row = await tdb.get(CourseLesson, lesson_id)
        if row is None:
            return
        row.slides_progress = max(0, min(100, pct))
        row.slides_progress_phase = phase
        await tdb.commit()


async def _progress_ticker(
    lesson_id: uuid.UUID,
    *,
    start_pct: int,
    end_pct: int,
    duration_sec: float,
) -> None:
    """Incrementa gradualmente `slides_progress` da `start_pct` verso
    `end_pct` su `duration_sec` secondi (ease-out).

    Si ferma se cancellato o se lo status non è più `processing`.
    """
    started = time.monotonic()
    span = max(1, end_pct - start_pct)
    try:
        while True:
            await asyncio.sleep(3.0)
            elapsed = time.monotonic() - started
            ratio = min(1.0, elapsed / duration_sec)
            eased = 1 - (1 - ratio) ** 2
            target = start_pct + int(span * eased)
            target = min(end_pct, target)
            async with async_session_factory() as tdb:
                row = await tdb.get(CourseLesson, lesson_id)
                if row is None or row.slides_status != "processing":
                    return
                if row.slides_progress < target:
                    row.slides_progress = target
                    await tdb.commit()
            if target >= end_pct:
                return
    except asyncio.CancelledError:
        return


# ---------------------------------------------------------------------------
# Process one lesson (con sessione DB propria)
# ---------------------------------------------------------------------------


async def _process_one(lesson_id: uuid.UUID) -> None:
    """Genera le slide di Fase 4 per una singola lezione.

    Sessione DB propria, niente contention con altre task.
    """
    async with async_session_factory() as db:
        bare = await db.get(CourseLesson, lesson_id)
        if bare is None:
            log.warning(
                "lesson_slides_lesson_not_found", lesson_id=str(lesson_id)
            )
            return
        if bare.slides_status != "pending":
            log.info(
                "lesson_slides_skip_not_pending",
                lesson_id=str(lesson_id),
                status=bare.slides_status,
            )
            return
        course_id = bare.course_id

        course_full = await course_lesson_slides_service.load_course_full(
            db, course_id=course_id
        )
        if course_full is None:
            log.warning(
                "lesson_slides_course_not_found",
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

        # Pre-check: content_raw deve essere presente. Se la lezione non
        # ha content (status != ready/approved), failure immediata
        # (non recuperabile — l'utente ha triggerato fuori contesto).
        if lesson.content_status not in ("ready", "approved") or not lesson.content_raw:
            settings = get_settings()
            _apply_failure(
                lesson,
                error=(
                    f"Impossibile generare slide: il contenuto della lezione "
                    f"deve essere `ready` o `approved` (attuale: "
                    f"`{lesson.content_status}`). Genera prima il contenuto."
                ),
                phase="precheck_content",
                recoverable=False,
                auto_retry_max=settings.course_lesson_slides_auto_retry_max,
            )
            course_lesson_slides_service._recompute_course_slides_status(
                course_full
            )
            await write_audit(
                db,
                action="course.lesson.slides.failed",
                actor_user_id=None,
                organization_id=course_full.organization_id,
                target_type="course_lesson",
                target_id=str(lesson.id),
                metadata={
                    "course_id": str(course_full.id),
                    "lesson_code": lesson.lesson_code,
                    "phase": "precheck_content",
                    "error": "content_not_ready",
                    "attempts": lesson.slides_attempts,
                },
            )
            await db.commit()
            return

        # Transizione → processing
        lesson.slides_attempts = (lesson.slides_attempts or 0) + 1
        lesson.slides_status = "processing"
        lesson.slides_error = None
        lesson.slides_progress = 5
        lesson.slides_progress_phase = "preparing_prompt"
        await db.commit()

        regen = course_lesson_slides_service.is_regeneration_for_lesson(lesson)
        user_prompt = course_lesson_slides_service.build_user_prompt(
            course_full, lesson
        )

        # Aggiorna progresso → calling_openai e avvia ticker
        lesson.slides_progress = 15
        lesson.slides_progress_phase = "calling_openai"
        await db.commit()

        # Ticker ease-out: slide ~30-90s.
        ticker_task = asyncio.create_task(
            _progress_ticker(
                lesson.id, start_pct=15, end_pct=85, duration_sec=60.0
            )
        )

        try:
            try:
                slides_output, usage = (
                    await openai_lesson_slides_service.generate_lesson_slides(
                        user_prompt=user_prompt,
                        language_code=course_full.language_code,
                        is_regeneration=regen,
                    )
                )
            except OpenAINotConfiguredError:
                # NON recuperabile (config issue) → terminal subito.
                settings = get_settings()
                _apply_failure(
                    lesson,
                    error=(
                        "OpenAI non configurato: l'amministratore deve impostare "
                        "OPENAI_API_KEY nel file .env del backend."
                    ),
                    phase="openai_call",
                    recoverable=False,
                    auto_retry_max=settings.course_lesson_slides_auto_retry_max,
                )
                course_lesson_slides_service._recompute_course_slides_status(
                    course_full
                )
                await write_audit(
                    db,
                    action="course.lesson.slides.failed",
                    actor_user_id=None,
                    organization_id=course_full.organization_id,
                    target_type="course_lesson",
                    target_id=str(lesson.id),
                    metadata={
                        "course_id": str(course_full.id),
                        "lesson_code": lesson.lesson_code,
                        "phase": "openai_call",
                        "error": "openai_not_configured",
                        "attempts": lesson.slides_attempts,
                    },
                )
                await db.commit()
                return
            except (
                openai_lesson_slides_service.OpenAILessonSlidesError
            ) as exc:
                settings = get_settings()
                terminal = not _apply_failure(
                    lesson,
                    error=str(exc),
                    phase="openai_call",
                    recoverable=True,
                    auto_retry_max=settings.course_lesson_slides_auto_retry_max,
                )
                if terminal:
                    course_lesson_slides_service._recompute_course_slides_status(
                        course_full
                    )
                    await write_audit(
                        db,
                        action="course.lesson.slides.failed",
                        actor_user_id=None,
                        organization_id=course_full.organization_id,
                        target_type="course_lesson",
                        target_id=str(lesson.id),
                        metadata={
                            "course_id": str(course_full.id),
                            "lesson_code": lesson.lesson_code,
                            "phase": "openai_call",
                            "error": str(exc)[:500],
                            "attempts": lesson.slides_attempts,
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

        # Cancel-check: se nel frattempo l'utente ha annullato, lo status
        # DB è stato spostato a `failed`. Scarta il risultato OpenAI.
        await db.refresh(lesson, ["slides_status"])
        if lesson.slides_status != "processing":
            log.info(
                "lesson_slides_cancelled_post_openai",
                lesson_id=str(lesson.id),
                lesson_code=lesson.lesson_code,
                current_status=lesson.slides_status,
            )
            return

        # Validazione + auto-fix degli asset "fragili" (new_assets Mermaid +
        # math inline) PRIMA di materializzare: ripara con AI ogni asset
        # invalido cosi' che nessuno raggiunga `ready` rotto.
        lesson.slides_progress = 88
        lesson.slides_progress_phase = "validating_assets"
        await db.commit()
        try:
            slides_output = (
                await asset_validation_service.validate_and_fix_slides_assets(
                    slides_output, language_code=course_full.language_code
                )
            )
        # Cattura ampia (incl. AssetFixUnresolvedError): qualunque errore in
        # validazione/fix e' recuperabile -> auto-retry rigenera la lezione.
        except Exception as exc:  # noqa: BLE001
            settings = get_settings()
            terminal = not _apply_failure(
                lesson,
                error=f"Asset non validabili: {exc}",
                phase="asset_validation",
                recoverable=True,
                auto_retry_max=settings.course_lesson_slides_auto_retry_max,
            )
            if terminal:
                course_lesson_slides_service._recompute_course_slides_status(
                    course_full
                )
                await write_audit(
                    db,
                    action="course.lesson.slides.failed",
                    actor_user_id=None,
                    organization_id=course_full.organization_id,
                    target_type="course_lesson",
                    target_id=str(lesson.id),
                    metadata={
                        "course_id": str(course_full.id),
                        "lesson_code": lesson.lesson_code,
                        "phase": "asset_validation",
                        "error": str(exc)[:500],
                        "attempts": lesson.slides_attempts,
                    },
                )
            await db.commit()
            return

        # Materializzazione + validazioni §7.4
        lesson.slides_progress = 90
        lesson.slides_progress_phase = "materializing"
        await db.commit()

        try:
            await course_lesson_slides_service.materialize_lesson_slides(
                db,
                course=course_full,
                lesson=lesson,
                output=slides_output,
                raw=slides_output.model_dump(),
                usage=usage,
            )
        except Exception as exc:
            settings = get_settings()
            terminal = not _apply_failure(
                lesson,
                error=f"Materializzazione fallita: {exc}",
                phase="materialize",
                recoverable=True,
                auto_retry_max=settings.course_lesson_slides_auto_retry_max,
            )
            if terminal:
                course_lesson_slides_service._recompute_course_slides_status(
                    course_full
                )
                await write_audit(
                    db,
                    action="course.lesson.slides.failed",
                    actor_user_id=None,
                    organization_id=course_full.organization_id,
                    target_type="course_lesson",
                    target_id=str(lesson.id),
                    metadata={
                        "course_id": str(course_full.id),
                        "lesson_code": lesson.lesson_code,
                        "phase": "materialize",
                        "error": str(exc)[:500],
                        "attempts": lesson.slides_attempts,
                    },
                )
            await db.commit()
            return

        await write_audit(
            db,
            action="course.lesson.slides.generated",
            actor_user_id=None,
            organization_id=course_full.organization_id,
            target_type="course_lesson",
            target_id=str(lesson.id),
            metadata={
                "course_id": str(course_full.id),
                "lesson_code": lesson.lesson_code,
                "total_slides": slides_output.total_slides,
                "new_assets": len(slides_output.new_assets),
                "tokens_total": usage.get("total"),
                "tokens_prompt": usage.get("prompt"),
                "tokens_completion": usage.get("completion"),
                "model": usage.get("model"),
                "attempts": lesson.slides_attempts,
                "regeneration": regen,
            },
        )
        await db.commit()
        log.info(
            "lesson_slides_generated",
            lesson_id=str(lesson.id),
            lesson_code=lesson.lesson_code,
            total_slides=slides_output.total_slides,
            tokens=usage.get("total"),
        )


# ---------------------------------------------------------------------------
# Bound task (semaforo + inflight tracking)
# ---------------------------------------------------------------------------


async def _bound_process(lesson_id: uuid.UUID) -> None:
    """Wrap `_process_one` con cap concorrenza.

    NB: il task viene aggiunto a `_inflight` da `_tick` PRIMA del dispatch
    (non qui) per evitare race con i tick successivi mentre la task è in
    coda dietro al semaforo. Qui ci occupiamo solo del discard finale.
    """
    assert _semaphore is not None
    try:
        async with _semaphore:
            await _process_one(lesson_id)
    except Exception as exc:  # pragma: no cover
        log.error(
            "lesson_slides_worker_unexpected",
            lesson_id=str(lesson_id),
            error=str(exc),
            exc_info=True,
        )
    finally:
        async with _inflight_lock:
            _inflight.discard(lesson_id)


# ---------------------------------------------------------------------------
# Tick: discovery + dispatch
# ---------------------------------------------------------------------------


async def _tick() -> None:
    """Cerca lezioni `pending` non già in flight e le dispatcha come task
    paralleli (fire-and-forget). Il `_tick` ritorna subito.
    """
    async with async_session_factory() as db:
        try:
            res = await db.execute(
                select(CourseLesson.id).where(
                    CourseLesson.slides_status == "pending"
                )
            )
            lesson_ids = [row[0] for row in res.all()]
        except Exception as exc:  # pragma: no cover
            log.warning("lesson_slides_worker_tick_failed", error=str(exc))
            return

    if not lesson_ids:
        return

    # Dedup + claim ATOMICO (vedi commento in content_worker._tick).
    async with _inflight_lock:
        new_ids = [lid for lid in lesson_ids if lid not in _inflight]
        for lid in new_ids:
            _inflight.add(lid)

    for lid in new_ids:
        task = asyncio.create_task(
            _bound_process(lid),
            name=f"lesson_slides_lesson_{lid}",
        )
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)


# ---------------------------------------------------------------------------
# Run loop + lifecycle
# ---------------------------------------------------------------------------


async def _run_loop() -> None:
    settings = get_settings()
    interval = max(2, int(settings.course_lesson_slides_poll_interval_seconds))
    log.info(
        "course_lesson_slides_worker_started",
        interval=interval,
        max_concurrency=settings.course_lesson_slides_max_concurrency,
    )
    assert _stop_event is not None
    while not _stop_event.is_set():
        await _tick()
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    log.info("course_lesson_slides_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event, _semaphore
    if _worker_task is not None and not _worker_task.done():
        return
    settings = get_settings()
    _stop_event = asyncio.Event()
    _semaphore = asyncio.Semaphore(
        max(1, int(settings.course_lesson_slides_max_concurrency))
    )
    _worker_task = asyncio.create_task(
        _run_loop(), name="course_lesson_slides_worker"
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
            "lesson_slides_worker_waiting_inflight",
            count=len(_active_tasks),
        )
        await asyncio.gather(*list(_active_tasks), return_exceptions=True)
    _worker_task = None
    _stop_event = None
    _semaphore = None
    _inflight.clear()
    _active_tasks.clear()
