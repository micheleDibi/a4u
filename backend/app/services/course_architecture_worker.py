"""Background worker per la generazione architettura corso (Fase 1).

Loop singolo `asyncio.Task` lanciato a startup in `app.main.lifespan`.
Stato in DB su `course.status`: passa a `architecture_pending` quando
l'utente richiede la generazione, il worker legge le righe in pending,
costruisce il prompt, chiama OpenAI, materializza modules + lessons.

Il progresso (0-100%) e la fase corrente sono persistiti su
`course.architecture_progress` / `architecture_progress_phase`. La UI
fa polling su `GET /courses/{id}` mentre lo status è pending e mostra
una barra di avanzamento. Durante la chiamata OpenAI (la fase più lunga)
un task di sfondo incrementa la percentuale gradualmente per evitare
che la UI sembri ferma.

In caso di errore: status torna a `draft` con `architecture_error` valorizzato.
"""
from __future__ import annotations

import asyncio
import time

from sqlalchemy import select

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.course import Course
from app.services import (
    course_architecture_service,
    openai_architecture_service,
)
from app.services.openai_client import OpenAINotConfiguredError

log = get_logger("app.course_architecture.worker")


async def _set_progress(db, course: Course, *, pct: int, phase: str | None) -> None:
    """Aggiorna progresso + fase e fa commit immediato (visibile alla UI)."""
    course.architecture_progress = max(0, min(100, pct))
    course.architecture_progress_phase = phase
    await db.commit()
    await db.refresh(course)


async def _progress_ticker(
    course_id, *, start_pct: int, end_pct: int, duration_sec: float
) -> None:
    """Incrementa gradualmente `architecture_progress` da `start_pct` verso
    `end_pct` su `duration_sec` secondi. Si ferma se cancellato.

    Usa una sessione DB indipendente per non interferire con la transazione
    principale del worker (che potrebbe essere in attesa della risposta HTTP).
    """
    from app.models.course import Course as CourseModel

    started = time.monotonic()
    span = max(1, end_pct - start_pct)
    try:
        while True:
            await asyncio.sleep(2.0)
            elapsed = time.monotonic() - started
            ratio = min(1.0, elapsed / duration_sec)
            # Curva ease-out: avanza veloce all'inizio e rallenta verso end_pct.
            eased = 1 - (1 - ratio) ** 2
            target = start_pct + int(span * eased)
            target = min(end_pct, target)
            async with async_session_factory() as tdb:
                row = await tdb.get(CourseModel, course_id)
                if row is None or row.status != "architecture_pending":
                    return
                # Non sovrascrivere se il worker ha già scritto un valore più alto.
                if row.architecture_progress < target:
                    row.architecture_progress = target
                    await tdb.commit()
            if target >= end_pct:
                return
    except asyncio.CancelledError:
        return


async def _process_one(db, course: Course) -> None:
    """Genera l'architettura per un singolo corso pending."""
    course.architecture_attempts = (course.architecture_attempts or 0) + 1
    course.architecture_error = None
    await _set_progress(db, course, pct=5, phase="preparing_prompt")

    is_regeneration = (
        bool(course.architecture_raw)
        or bool(course.modules)
        or bool(course.architecture_regeneration_hint)
    )
    user_prompt = course_architecture_service.build_user_prompt(course)

    await _set_progress(db, course, pct=15, phase="calling_openai")

    # Ticker di sfondo: incrementa il progresso da 15 a 85 in ~75s.
    # Se la chiamata OpenAI è più rapida, ci fermiamo prima.
    ticker_task = asyncio.create_task(
        _progress_ticker(course.id, start_pct=15, end_pct=85, duration_sec=75.0)
    )

    try:
        try:
            architecture, usage = (
                await openai_architecture_service.generate_architecture(
                    user_prompt=user_prompt,
                    language_code=course.language_code,
                    is_regeneration=is_regeneration,
                )
            )
        except OpenAINotConfiguredError:
            course.architecture_error = (
                "OpenAI non configurato: l'amministratore deve impostare "
                "OPENAI_API_KEY nel file .env del backend."
            )
            course.status = "draft"
            course.architecture_progress = 0
            course.architecture_progress_phase = None
            await write_audit(
                db,
                action="course.architecture.generation.failed",
                actor_user_id=None,
                organization_id=course.organization_id,
                target_type="course",
                target_id=str(course.id),
                metadata={
                    "phase": "openai_call",
                    "error": "openai_not_configured",
                    "attempts": course.architecture_attempts,
                },
            )
            await db.commit()
            log.warning(
                "course_architecture_openai_not_configured",
                course_id=str(course.id),
            )
            return
        except openai_architecture_service.OpenAIArchitectureError as exc:
            course.architecture_error = str(exc)[:500]
            course.status = "draft"
            course.architecture_progress = 0
            course.architecture_progress_phase = None
            await write_audit(
                db,
                action="course.architecture.generation.failed",
                actor_user_id=None,
                organization_id=course.organization_id,
                target_type="course",
                target_id=str(course.id),
                metadata={
                    "phase": "openai_call",
                    "error": str(exc)[:500],
                    "attempts": course.architecture_attempts,
                },
            )
            await db.commit()
            log.warning(
                "course_architecture_openai_error",
                course_id=str(course.id),
                error=str(exc),
            )
            return
    finally:
        ticker_task.cancel()
        try:
            await ticker_task
        except (asyncio.CancelledError, Exception):
            pass

    await db.refresh(course)
    await _set_progress(db, course, pct=90, phase="materializing")

    try:
        await course_architecture_service.materialize_architecture(
            db,
            course=course,
            architecture=architecture,
            raw=architecture.model_dump(),
            usage=usage,
        )
    except Exception as exc:
        course.architecture_error = f"Materializzazione fallita: {exc}"[:500]
        course.status = "draft"
        course.architecture_progress = 0
        course.architecture_progress_phase = None
        await write_audit(
            db,
            action="course.architecture.generation.failed",
            actor_user_id=None,
            organization_id=course.organization_id,
            target_type="course",
            target_id=str(course.id),
            metadata={
                "phase": "materialize",
                "error": str(exc)[:500],
                "attempts": course.architecture_attempts,
            },
        )
        await db.commit()
        log.warning(
            "course_architecture_materialize_failed",
            course_id=str(course.id),
            error=str(exc),
        )
        return

    course.architecture_progress = 100
    course.architecture_progress_phase = None
    await write_audit(
        db,
        action="course.architecture.generated",
        actor_user_id=None,
        organization_id=course.organization_id,
        target_type="course",
        target_id=str(course.id),
        metadata={
            "modules": len(architecture.modules),
            "tokens_total": usage.get("total"),
            "tokens_prompt": usage.get("prompt"),
            "tokens_completion": usage.get("completion"),
            "model": usage.get("model"),
            "attempts": course.architecture_attempts,
            "regeneration": is_regeneration,
        },
    )
    await db.commit()
    log.info(
        "course_architecture_generated",
        course_id=str(course.id),
        modules=len(architecture.modules),
        tokens=usage.get("total"),
    )


async def _tick() -> None:
    async with async_session_factory() as db:
        try:
            res = await db.execute(
                select(Course)
                .where(Course.status == "architecture_pending")
                .options(
                    *course_architecture_service._eager_full_options()
                )
            )
            courses = list(res.scalars().all())
            for course in courses:
                try:
                    await _process_one(db, course)
                except Exception as exc:  # pragma: no cover
                    await db.rollback()
                    log.error(
                        "course_architecture_worker_unexpected",
                        course_id=str(course.id),
                        error=str(exc),
                        exc_info=True,
                    )
        except Exception as exc:  # pragma: no cover
            await db.rollback()
            log.warning("course_architecture_worker_tick_failed", error=str(exc))


_worker_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


async def _run_loop() -> None:
    settings = get_settings()
    interval = max(2, int(settings.course_architecture_poll_interval_seconds))
    log.info("course_architecture_worker_started", interval=interval)
    assert _stop_event is not None
    while not _stop_event.is_set():
        await _tick()
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    log.info("course_architecture_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event
    if _worker_task is not None and not _worker_task.done():
        return
    _stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(_run_loop(), name="course_architecture_worker")


async def stop_worker() -> None:
    global _worker_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=15)
        except asyncio.TimeoutError:
            _worker_task.cancel()
            with_suppressed_cancel = asyncio.gather(
                _worker_task, return_exceptions=True
            )
            await with_suppressed_cancel
    _worker_task = None
    _stop_event = None
