"""Background worker per la generazione dei contenuti delle lezioni
(Fase 3 — §6).

Pattern speculare al worker di Fase 2 ma scoped a livello LEZIONE
(non modulo): dispatcha le lezioni `pending` IN PARALLELO con cap di
concorrenza configurabile (default 3, output 5x più grande di Fase 2).

Stato per lezione su `course_lesson.content_status`:
    empty → pending → processing → ready → approved
                                  ↘ failed (solo dopo N retry esauriti)

**Auto-retry trasparente** (`course_lesson_content_auto_retry_max`,
default 5): se la generazione fallisce in modo recuperabile (errore
OpenAI transiente, validazione §6.4, glossary gate, materializzazione),
il worker NON transita a `failed` — riporta lo status a `pending` e il
ticker successivo (4s) ritenta. La UI vede solo "in elaborazione"
finché passa, mai il messaggio di errore. Solo dopo `auto_retry_max`
attempts esauriti la lezione transita a `failed` (terminale, l'utente
può ricliccare manualmente). Errori non recuperabili
(`OpenAINotConfiguredError` — config issue) vanno a `failed` subito.

Pre-step automatico: al primo task della Fase 3 il worker controlla
`course.glossary_status` — se `empty`/`failed`, chiama sync inline
`course_glossary_service.ensure_glossary_ready` (~10-20s). I task
successivi trovano il glossario già pronto.

Il progresso (0-100%) e la fase corrente sono persistiti su
`course_lesson.content_progress` / `content_progress_phase`. La UI
fa polling su `GET /courses/{id}` mentre almeno una lezione è in
`pending|processing` (o `course.glossary_status='processing'`) e mostra
una progress bar live + aggregate progress in header.
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
    course_glossary_service,
    course_lesson_content_service,
    openai_lesson_content_service,
)
from app.services.openai_client import OpenAINotConfiguredError

log = get_logger("app.course_lesson_content.worker")


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
    attempts = lesson.content_attempts or 0
    if recoverable and attempts < auto_retry_max:
        lesson.content_status = "pending"
        lesson.content_error = None
        lesson.content_progress = 0
        lesson.content_progress_phase = None
        log.info(
            "lesson_content_auto_retry",
            lesson_id=str(lesson.id),
            lesson_code=lesson.lesson_code,
            phase=phase,
            attempts=attempts,
            max_retry=auto_retry_max,
            error=error[:200],
        )
        return True
    lesson.content_status = "failed"
    lesson.content_error = error[:500]
    lesson.content_progress = 0
    lesson.content_progress_phase = None
    log.warning(
        "lesson_content_failed_terminal",
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
    """Aggiorna `content_progress` + phase su una sessione propria."""
    async with async_session_factory() as tdb:
        row = await tdb.get(CourseLesson, lesson_id)
        if row is None:
            return
        row.content_progress = max(0, min(100, pct))
        row.content_progress_phase = phase
        await tdb.commit()


async def _progress_ticker(
    lesson_id: uuid.UUID,
    *,
    start_pct: int,
    end_pct: int,
    duration_sec: float,
) -> None:
    """Incrementa gradualmente `content_progress` da `start_pct` verso
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
                if row is None or row.content_status != "processing":
                    return
                if row.content_progress < target:
                    row.content_progress = target
                    await tdb.commit()
            if target >= end_pct:
                return
    except asyncio.CancelledError:
        return


# ---------------------------------------------------------------------------
# Process one lesson (con sessione DB propria)
# ---------------------------------------------------------------------------


async def _process_one(lesson_id: uuid.UUID) -> None:
    """Genera il contenuto di Fase 3 per una singola lezione.

    Sessione DB propria, niente contention con altre task.
    """
    async with async_session_factory() as db:
        bare = await db.get(CourseLesson, lesson_id)
        if bare is None:
            log.warning("lesson_content_lesson_not_found", lesson_id=str(lesson_id))
            return
        if bare.content_status != "pending":
            log.info(
                "lesson_content_skip_not_pending",
                lesson_id=str(lesson_id),
                status=bare.content_status,
            )
            return
        course_id = bare.course_id

        course_full = await course_lesson_content_service.load_course_full(
            db, course_id=course_id
        )
        if course_full is None:
            log.warning(
                "lesson_content_course_not_found",
                lesson_id=str(lesson_id),
                course_id=str(course_id),
            )
            return

        try:
            lesson = await course_lesson_content_service.get_lesson_or_404(
                db, course=course_full, lesson_id=lesson_id
            )
        except Exception:
            return

        # Transizione → processing
        lesson.content_attempts = (lesson.content_attempts or 0) + 1
        lesson.content_status = "processing"
        lesson.content_error = None
        lesson.content_progress = 5
        lesson.content_progress_phase = "preparing_prompt"
        await db.commit()

        # Glossary gate: se non disponibile, genera sync inline. La
        # lezione-verifica non usa il glossario → salta il gate.
        if not lesson.is_assessment and course_full.glossary_status not in (
            "ready",
            "approved",
        ):
            try:
                course_full = await course_glossary_service.ensure_glossary_ready(
                    db, course=course_full, actor_id=None
                )
                # Ricarica la lezione dalla nuova istanza ricaricata.
                lesson = await course_lesson_content_service.get_lesson_or_404(
                    db, course=course_full, lesson_id=lesson_id
                )
            except Exception as exc:
                settings = get_settings()
                terminal = not _apply_failure(
                    lesson,
                    error=f"Glossario non disponibile: {exc}",
                    phase="glossary_gate",
                    recoverable=True,
                    auto_retry_max=settings.course_lesson_content_auto_retry_max,
                )
                if terminal:
                    course_lesson_content_service._recompute_course_content_status(
                        course_full
                    )
                    await write_audit(
                        db,
                        action="course.lesson.content.failed",
                        actor_user_id=None,
                        organization_id=course_full.organization_id,
                        target_type="course_lesson",
                        target_id=str(lesson.id),
                        metadata={
                            "course_id": str(course_full.id),
                            "lesson_code": lesson.lesson_code,
                            "phase": "glossary_gate",
                            "error": str(exc)[:500],
                            "attempts": lesson.content_attempts,
                        },
                    )
                await db.commit()
                return

        regen = course_lesson_content_service.is_regeneration_for_lesson(lesson)
        if lesson.is_assessment:
            user_prompt = (
                course_lesson_content_service.build_assessment_user_prompt(
                    course_full, lesson
                )
            )
        else:
            user_prompt = course_lesson_content_service.build_user_prompt(
                course_full, lesson
            )

        # Aggiorna progresso → calling_openai e avvia ticker
        lesson.content_progress = 15
        lesson.content_progress_phase = "calling_openai"
        await db.commit()

        # Ticker ease-out più lento di Fase 2: lezione completa ~60-120s.
        ticker_task = asyncio.create_task(
            _progress_ticker(
                lesson.id, start_pct=15, end_pct=85, duration_sec=90.0
            )
        )

        try:
            try:
                if lesson.is_assessment:
                    content_output, usage = (
                        await openai_lesson_content_service.generate_lesson_assessment(
                            user_prompt=user_prompt,
                            language_code=course_full.language_code,
                            is_regeneration=regen,
                        )
                    )
                else:
                    content_output, usage = (
                        await openai_lesson_content_service.generate_lesson_content(
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
                    auto_retry_max=settings.course_lesson_content_auto_retry_max,
                )
                course_lesson_content_service._recompute_course_content_status(
                    course_full
                )
                await write_audit(
                    db,
                    action="course.lesson.content.failed",
                    actor_user_id=None,
                    organization_id=course_full.organization_id,
                    target_type="course_lesson",
                    target_id=str(lesson.id),
                    metadata={
                        "course_id": str(course_full.id),
                        "lesson_code": lesson.lesson_code,
                        "phase": "openai_call",
                        "error": "openai_not_configured",
                        "attempts": lesson.content_attempts,
                    },
                )
                await db.commit()
                return
            except (
                openai_lesson_content_service.OpenAILessonContentError
            ) as exc:
                settings = get_settings()
                terminal = not _apply_failure(
                    lesson,
                    error=str(exc),
                    phase="openai_call",
                    recoverable=True,
                    auto_retry_max=settings.course_lesson_content_auto_retry_max,
                )
                if terminal:
                    course_lesson_content_service._recompute_course_content_status(
                        course_full
                    )
                    await write_audit(
                        db,
                        action="course.lesson.content.failed",
                        actor_user_id=None,
                        organization_id=course_full.organization_id,
                        target_type="course_lesson",
                        target_id=str(lesson.id),
                        metadata={
                            "course_id": str(course_full.id),
                            "lesson_code": lesson.lesson_code,
                            "phase": "openai_call",
                            "error": str(exc)[:500],
                            "attempts": lesson.content_attempts,
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

        # Cancel-check: se nel frattempo l'utente ha annullato la
        # generazione, lo status DB è stato spostato a `failed`. Scarta
        # il risultato OpenAI (lo abbiamo pagato ma non lo materializziamo).
        await db.refresh(lesson, ["content_status"])
        if lesson.content_status != "processing":
            log.info(
                "lesson_content_cancelled_post_openai",
                lesson_id=str(lesson.id),
                lesson_code=lesson.lesson_code,
                current_status=lesson.content_status,
            )
            return

        # Materializzazione + validazioni §6.4
        lesson.content_progress = 90
        lesson.content_progress_phase = "materializing"
        await db.commit()

        try:
            if lesson.is_assessment:
                await course_lesson_content_service.materialize_lesson_assessment(
                    db,
                    course=course_full,
                    lesson=lesson,
                    output=content_output,
                    raw=content_output.model_dump(),
                    usage=usage,
                )
            else:
                await course_lesson_content_service.materialize_lesson_content(
                    db,
                    course=course_full,
                    lesson=lesson,
                    output=content_output,
                    raw=content_output.model_dump(),
                    usage=usage,
                )
        except Exception as exc:
            settings = get_settings()
            terminal = not _apply_failure(
                lesson,
                error=f"Materializzazione fallita: {exc}",
                phase="materialize",
                recoverable=True,
                auto_retry_max=settings.course_lesson_content_auto_retry_max,
            )
            if terminal:
                course_lesson_content_service._recompute_course_content_status(
                    course_full
                )
                await write_audit(
                    db,
                    action="course.lesson.content.failed",
                    actor_user_id=None,
                    organization_id=course_full.organization_id,
                    target_type="course_lesson",
                    target_id=str(lesson.id),
                    metadata={
                        "course_id": str(course_full.id),
                        "lesson_code": lesson.lesson_code,
                        "phase": "materialize",
                        "error": str(exc)[:500],
                        "attempts": lesson.content_attempts,
                    },
                )
            await db.commit()
            return

        if lesson.is_assessment:
            phase_metadata: dict = {
                "is_assessment": True,
                "mc_questions": len(content_output.multiple_choice_questions),
                "open_questions": len(content_output.open_questions),
            }
        else:
            phase_metadata = {
                "sections": len(content_output.sections),
                "visual_assets": len(content_output.visual_assets),
                "tables": len(content_output.tables),
                "equations": len(content_output.equations),
                "examples": len(content_output.examples),
                "estimated_word_count": content_output.estimated_word_count,
            }
        await write_audit(
            db,
            action="course.lesson.content.generated",
            actor_user_id=None,
            organization_id=course_full.organization_id,
            target_type="course_lesson",
            target_id=str(lesson.id),
            metadata={
                "course_id": str(course_full.id),
                "lesson_code": lesson.lesson_code,
                **phase_metadata,
                "tokens_total": usage.get("total"),
                "tokens_prompt": usage.get("prompt"),
                "tokens_completion": usage.get("completion"),
                "model": usage.get("model"),
                "attempts": lesson.content_attempts,
                "regeneration": regen,
            },
        )
        await db.commit()
        log.info(
            "lesson_content_generated",
            lesson_id=str(lesson.id),
            lesson_code=lesson.lesson_code,
            is_assessment=lesson.is_assessment,
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
            "lesson_content_worker_unexpected",
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
                    CourseLesson.content_status == "pending"
                )
            )
            lesson_ids = [row[0] for row in res.all()]
        except Exception as exc:  # pragma: no cover
            log.warning(
                "lesson_content_worker_tick_failed", error=str(exc)
            )
            return

    if not lesson_ids:
        return

    # Dedup + claim ATOMICO: filtriamo le lezioni nuove e le marchiamo come
    # inflight nello stesso lock. Senza il claim qui, le task accodate dietro
    # il semaforo non risultano in `_inflight` finché non lo acquisiscono →
    # il tick successivo (4s dopo) le ridispatcherebbe duplicate, generando
    # uno storm di `lesson_content_skip_not_pending` quando finalmente
    # eseguono e trovano `status=processing` settato dal vincitore.
    async with _inflight_lock:
        new_ids = [lid for lid in lesson_ids if lid not in _inflight]
        for lid in new_ids:
            _inflight.add(lid)

    for lid in new_ids:
        task = asyncio.create_task(
            _bound_process(lid),
            name=f"lesson_content_lesson_{lid}",
        )
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)


# ---------------------------------------------------------------------------
# Run loop + lifecycle
# ---------------------------------------------------------------------------


async def _run_loop() -> None:
    settings = get_settings()
    interval = max(2, int(settings.course_lesson_content_poll_interval_seconds))
    log.info(
        "course_lesson_content_worker_started",
        interval=interval,
        max_concurrency=settings.course_lesson_content_max_concurrency,
    )
    assert _stop_event is not None
    while not _stop_event.is_set():
        await _tick()
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    log.info("course_lesson_content_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event, _semaphore
    if _worker_task is not None and not _worker_task.done():
        return
    settings = get_settings()
    _stop_event = asyncio.Event()
    _semaphore = asyncio.Semaphore(
        max(1, int(settings.course_lesson_content_max_concurrency))
    )
    _worker_task = asyncio.create_task(
        _run_loop(), name="course_lesson_content_worker"
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
            "lesson_content_worker_waiting_inflight",
            count=len(_active_tasks),
        )
        await asyncio.gather(*list(_active_tasks), return_exceptions=True)
    _worker_task = None
    _stop_event = None
    _semaphore = None
    _inflight.clear()
    _active_tasks.clear()
