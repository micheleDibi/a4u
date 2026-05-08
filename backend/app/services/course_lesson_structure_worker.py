"""Background worker per la generazione della struttura delle lezioni
(Fase 2 — §5).

A differenza del worker di Fase 1 che processa i corsi sequenzialmente,
questo worker dispatcha i moduli `pending` IN PARALLELO con un cap di
concorrenza configurabile (`asyncio.Semaphore`). Ogni task ha la sua
`AsyncSession` propria — necessario perché le sessioni SQLAlchemy
async non sono task-safe.

Stato per modulo su `course_module.lessons_structure_status`:
    empty → pending → processing → ready → approved
                                  ↘ failed (solo dopo N retry esauriti)

**Auto-retry trasparente**
(`course_lesson_structure_auto_retry_max`, default 5): se la
generazione fallisce in modo recuperabile (errore OpenAI transiente,
materializzazione), il worker NON transita a `failed` — riporta
status='pending' e ritenta al ticker successivo. La UI vede solo "in
elaborazione". Solo dopo `auto_retry_max` esauriti diventa terminale
`failed`. `OpenAINotConfiguredError` (config issue) è terminale subito.

Il progresso (0-100%) e la fase corrente sono persistiti su
`course_module.lessons_structure_progress` /
`lessons_structure_progress_phase`. La UI fa polling su
`GET /courses/{id}` mentre almeno un modulo è in `pending|processing`
e mostra una progress bar live + aggregate progress in header.
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
from app.models.course import Course
from app.models.course_module import CourseModule
from app.services import (
    course_lesson_structure_service,
    openai_lesson_structure_service,
)
from app.services.openai_client import OpenAINotConfiguredError

log = get_logger("app.course_lesson_structure.worker")


# ---------------------------------------------------------------------------
# State del worker (modulo-scope)
# ---------------------------------------------------------------------------

# Set degli ID modulo attualmente in lavorazione. Evita doppio dispatch
# di un modulo che è in `pending` ma il task è già stato spawnato.
_inflight: set[uuid.UUID] = set()
_inflight_lock = asyncio.Lock()

# Semaforo cap concorrenza. Inizializzato a `start_worker()` quando
# settings sono disponibili.
_semaphore: asyncio.Semaphore | None = None

_worker_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None
_active_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Auto-retry helper
# ---------------------------------------------------------------------------


def _apply_failure(
    module: CourseModule,
    *,
    error: str,
    phase: str,
    recoverable: bool,
    auto_retry_max: int,
) -> bool:
    """Decide se ritentare automaticamente o passare a `failed`.

    Scrive sui campi del modulo SENZA committare. Il caller fa il
    commit + audit log dopo aver visto il return value.

    Returns:
        True se è stato schedulato un auto-retry (status='pending').
        False se è transizione terminale (status='failed').
    """
    attempts = module.lessons_structure_attempts or 0
    if recoverable and attempts < auto_retry_max:
        module.lessons_structure_status = "pending"
        module.lessons_structure_error = None
        module.lessons_structure_progress = 0
        module.lessons_structure_progress_phase = None
        log.info(
            "lesson_structure_auto_retry",
            module_id=str(module.id),
            module_code=module.module_code,
            phase=phase,
            attempts=attempts,
            max_retry=auto_retry_max,
            error=error[:200],
        )
        return True
    module.lessons_structure_status = "failed"
    module.lessons_structure_error = error[:500]
    module.lessons_structure_progress = 0
    module.lessons_structure_progress_phase = None
    log.warning(
        "lesson_structure_failed_terminal",
        module_id=str(module.id),
        module_code=module.module_code,
        phase=phase,
        attempts=attempts,
        error=error[:200],
    )
    return False


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


async def _set_progress(module_id: uuid.UUID, *, pct: int, phase: str | None) -> None:
    """Aggiorna `lessons_structure_progress` + phase su una sessione propria."""
    async with async_session_factory() as tdb:
        row = await tdb.get(CourseModule, module_id)
        if row is None:
            return
        row.lessons_structure_progress = max(0, min(100, pct))
        row.lessons_structure_progress_phase = phase
        await tdb.commit()


async def _progress_ticker(
    module_id: uuid.UUID,
    *,
    start_pct: int,
    end_pct: int,
    duration_sec: float,
) -> None:
    """Incrementa gradualmente `lessons_structure_progress` da
    `start_pct` verso `end_pct` su `duration_sec` secondi (ease-out).

    Si ferma se cancellato o se lo status non è più `processing`.
    """
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
                row = await tdb.get(CourseModule, module_id)
                if row is None or row.lessons_structure_status != "processing":
                    return
                if row.lessons_structure_progress < target:
                    row.lessons_structure_progress = target
                    await tdb.commit()
            if target >= end_pct:
                return
    except asyncio.CancelledError:
        return


# ---------------------------------------------------------------------------
# Process one module (con sessione DB propria)
# ---------------------------------------------------------------------------


async def _process_one(module_id: uuid.UUID) -> None:
    """Genera la struttura di Fase 2 per un singolo modulo.

    La sessione DB è interna a questa funzione e non condivisa con altre
    task → niente contention.
    """
    async with async_session_factory() as db:
        # Lookup leggero per ottenere course_id e fare il check di stato
        # senza eager-load di tutta la gerarchia (evita un round-trip
        # superfluo se il modulo non è più pending).
        bare = await db.get(CourseModule, module_id)
        if bare is None:
            log.warning("lesson_structure_module_not_found", module_id=str(module_id))
            return
        if bare.lessons_structure_status != "pending":
            log.info(
                "lesson_structure_skip_not_pending",
                module_id=str(module_id),
                status=bare.lessons_structure_status,
            )
            return
        course_id = bare.course_id

        # Carica il corso completo con eager-load + risolvi l'istanza del
        # modulo a partire da course.modules (stessa sessione DB).
        course_full = await course_lesson_structure_service.load_course_full(
            db, course_id=course_id
        )
        if course_full is None:
            log.warning(
                "lesson_structure_course_not_found",
                module_id=str(module_id),
                course_id=str(course_id),
            )
            return

        try:
            module = await course_lesson_structure_service.get_module_or_404(
                db, course=course_full, module_id=module_id
            )
        except Exception:
            return

        # Transizione → processing
        module.lessons_structure_attempts = (
            module.lessons_structure_attempts or 0
        ) + 1
        module.lessons_structure_status = "processing"
        module.lessons_structure_error = None
        module.lessons_structure_progress = 5
        module.lessons_structure_progress_phase = "preparing_prompt"
        await db.commit()

        regen = course_lesson_structure_service.is_regeneration(module)
        user_prompt = course_lesson_structure_service.build_user_prompt(
            course_full, module
        )

        # Aggiorna progresso → calling_openai e avvia ticker
        module.lessons_structure_progress = 15
        module.lessons_structure_progress_phase = "calling_openai"
        await db.commit()

        ticker_task = asyncio.create_task(
            _progress_ticker(
                module.id, start_pct=15, end_pct=85, duration_sec=40.0
            )
        )

        try:
            try:
                structure, usage = (
                    await openai_lesson_structure_service.generate_lesson_structure(
                        user_prompt=user_prompt,
                        language_code=course_full.language_code,
                        is_regeneration=regen,
                    )
                )
            except OpenAINotConfiguredError:
                settings = get_settings()
                _apply_failure(
                    module,
                    error=(
                        "OpenAI non configurato: l'amministratore deve impostare "
                        "OPENAI_API_KEY nel file .env del backend."
                    ),
                    phase="openai_call",
                    recoverable=False,
                    auto_retry_max=settings.course_lesson_structure_auto_retry_max,
                )
                course_lesson_structure_service._recompute_course_lessons_structure_status(
                    course_full
                )
                await write_audit(
                    db,
                    action="course.module.lessons_structure.failed",
                    actor_user_id=None,
                    organization_id=course_full.organization_id,
                    target_type="course_module",
                    target_id=str(module.id),
                    metadata={
                        "course_id": str(course_full.id),
                        "module_code": module.module_code,
                        "phase": "openai_call",
                        "error": "openai_not_configured",
                        "attempts": module.lessons_structure_attempts,
                    },
                )
                await db.commit()
                return
            except (
                openai_lesson_structure_service.OpenAILessonStructureError
            ) as exc:
                settings = get_settings()
                terminal = not _apply_failure(
                    module,
                    error=str(exc),
                    phase="openai_call",
                    recoverable=True,
                    auto_retry_max=settings.course_lesson_structure_auto_retry_max,
                )
                if terminal:
                    course_lesson_structure_service._recompute_course_lessons_structure_status(
                        course_full
                    )
                    await write_audit(
                        db,
                        action="course.module.lessons_structure.failed",
                        actor_user_id=None,
                        organization_id=course_full.organization_id,
                        target_type="course_module",
                        target_id=str(module.id),
                        metadata={
                            "course_id": str(course_full.id),
                            "module_code": module.module_code,
                            "phase": "openai_call",
                            "error": str(exc)[:500],
                            "attempts": module.lessons_structure_attempts,
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

        # Materializzazione
        module.lessons_structure_progress = 90
        module.lessons_structure_progress_phase = "materializing"
        await db.commit()

        try:
            await course_lesson_structure_service.materialize_module_structure(
                db,
                course=course_full,
                module=module,
                output=structure,
                raw=structure.model_dump(),
                usage=usage,
            )
        except Exception as exc:
            settings = get_settings()
            terminal = not _apply_failure(
                module,
                error=f"Materializzazione fallita: {exc}",
                phase="materialize",
                recoverable=True,
                auto_retry_max=settings.course_lesson_structure_auto_retry_max,
            )
            if terminal:
                course_lesson_structure_service._recompute_course_lessons_structure_status(
                    course_full
                )
                await write_audit(
                    db,
                    action="course.module.lessons_structure.failed",
                    actor_user_id=None,
                    organization_id=course_full.organization_id,
                    target_type="course_module",
                    target_id=str(module.id),
                    metadata={
                        "course_id": str(course_full.id),
                        "module_code": module.module_code,
                        "phase": "materialize",
                        "error": str(exc)[:500],
                        "attempts": module.lessons_structure_attempts,
                    },
                )
            await db.commit()
            return

        await write_audit(
            db,
            action="course.module.lessons_structure.generated",
            actor_user_id=None,
            organization_id=course_full.organization_id,
            target_type="course_module",
            target_id=str(module.id),
            metadata={
                "course_id": str(course_full.id),
                "module_code": module.module_code,
                "lessons": len(structure.lessons),
                "tokens_total": usage.get("total"),
                "tokens_prompt": usage.get("prompt"),
                "tokens_completion": usage.get("completion"),
                "model": usage.get("model"),
                "attempts": module.lessons_structure_attempts,
                "regeneration": regen,
            },
        )
        await db.commit()
        log.info(
            "lesson_structure_generated",
            module_id=str(module.id),
            module_code=module.module_code,
            lessons=len(structure.lessons),
            tokens=usage.get("total"),
        )


# ---------------------------------------------------------------------------
# Bound task (semaforo + inflight tracking)
# ---------------------------------------------------------------------------


async def _bound_process(module_id: uuid.UUID) -> None:
    """Wrap `_process_one` con cap concorrenza e inflight tracking."""
    assert _semaphore is not None
    async with _semaphore:
        async with _inflight_lock:
            _inflight.add(module_id)
        try:
            await _process_one(module_id)
        except Exception as exc:  # pragma: no cover
            log.error(
                "lesson_structure_worker_unexpected",
                module_id=str(module_id),
                error=str(exc),
                exc_info=True,
            )
        finally:
            async with _inflight_lock:
                _inflight.discard(module_id)


# ---------------------------------------------------------------------------
# Tick: discovery + dispatch
# ---------------------------------------------------------------------------


async def _tick() -> None:
    """Cerca moduli `pending` non già in flight e li dispatcha come task
    paralleli (fire-and-forget). Il `_tick` stesso ritorna subito senza
    aspettare la fine dei task.
    """
    async with async_session_factory() as db:
        try:
            res = await db.execute(
                select(CourseModule.id).where(
                    CourseModule.lessons_structure_status == "pending"
                )
            )
            module_ids = [row[0] for row in res.all()]
        except Exception as exc:  # pragma: no cover
            log.warning(
                "lesson_structure_worker_tick_failed", error=str(exc)
            )
            return

    if not module_ids:
        return

    async with _inflight_lock:
        new_ids = [mid for mid in module_ids if mid not in _inflight]

    for mid in new_ids:
        task = asyncio.create_task(
            _bound_process(mid),
            name=f"lesson_structure_module_{mid}",
        )
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)


# ---------------------------------------------------------------------------
# Run loop + lifecycle
# ---------------------------------------------------------------------------


async def _run_loop() -> None:
    settings = get_settings()
    interval = max(2, int(settings.course_lesson_structure_poll_interval_seconds))
    log.info(
        "course_lesson_structure_worker_started",
        interval=interval,
        max_concurrency=settings.course_lesson_structure_max_concurrency,
    )
    assert _stop_event is not None
    while not _stop_event.is_set():
        await _tick()
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    log.info("course_lesson_structure_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event, _semaphore
    if _worker_task is not None and not _worker_task.done():
        return
    settings = get_settings()
    _stop_event = asyncio.Event()
    _semaphore = asyncio.Semaphore(
        max(1, int(settings.course_lesson_structure_max_concurrency))
    )
    _worker_task = asyncio.create_task(
        _run_loop(), name="course_lesson_structure_worker"
    )


async def stop_worker() -> None:
    global _worker_task, _stop_event, _semaphore
    if _stop_event is not None:
        _stop_event.set()
    # Attendi la fine del run_loop
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=15)
        except asyncio.TimeoutError:
            _worker_task.cancel()
            await asyncio.gather(_worker_task, return_exceptions=True)
    # Attendi la fine dei task in flight (con timeout aggressivo, sono I/O)
    if _active_tasks:
        log.info(
            "lesson_structure_worker_waiting_inflight",
            count=len(_active_tasks),
        )
        await asyncio.gather(*list(_active_tasks), return_exceptions=True)
    _worker_task = None
    _stop_event = None
    _semaphore = None
    _inflight.clear()
    _active_tasks.clear()
