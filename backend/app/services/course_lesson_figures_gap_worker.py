"""Worker dei buchi di figure di fonte (WP5, letteratura aperta).

Una lezione alla volta, prima della sua Fase 3:

- il `_tick` della Fase 3 (`course_lesson_content_worker`) segna
  `figures_gap_status = pending` sulle lezioni ordinarie in coda mai
  verificate e le lascia aspettare finché la verifica è in coda o in
  corso, al più `FIGURE_WAIT_MAX_MINUTES` dalla richiesta (filtro SQL, mai
  uno sleep);
- qui si prende la prima lezione `pending` (claim condizionale) il cui
  corso non ha estrazioni di figure in corso, e si esegue
  `literature_figures_service.check_lesson`;
- errori recuperabili → di nuovo `pending` con backoff, fino a
  `FIGURE_LITERATURE_AUTO_RETRY_MAX` ripetizioni, poi `failed`; il costo già
  pagato resta in `figures_gap_usage`.

Parte solo con `FIGURE_SOURCE_ENABLED` e `FIGURE_LITERATURE_ENABLED`: con uno
dei due spento la Fase 3 non aspetta (stesso interruttore nel filtro).
All'avvio le verifiche rimaste `processing` (riavvio a metà) tornano
`pending`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, false, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.course_document import CourseDocument
from app.models.course_lesson import CourseLesson
from app.services import figure_plan_service
from app.services import literature_figures_service as gaps

log = get_logger("app.course_lesson_figures_gap_worker")

# Attesa minima prima di un nuovo tentativo, moltiplicata per i tentativi.
RETRY_BASE_SECONDS = 60
# Tempo per giro del worker: più lezioni per giro, poi la pausa.
TICK_BUDGET_SECONDS = 120.0


def _now() -> datetime:
    return datetime.now(UTC)


def literature_active() -> bool:
    settings = get_settings()
    return bool(settings.figure_source_enabled and settings.figure_literature_enabled)


def _extracting_documents() -> Any:
    """EXISTS: estrazioni di figure del corso in coda o in corso, richieste
    da meno di `FIGURE_WAIT_MAX_MINUTES` (la verifica le aspetta)."""
    cutoff = _now() - timedelta(minutes=int(get_settings().figure_wait_max_minutes))
    return (
        select(CourseDocument.id)
        .where(
            CourseDocument.course_id == CourseLesson.course_id,
            CourseDocument.figures_status.in_(gaps.GAP_ACTIVE),
            CourseDocument.figures_requested_at > cutoff,
        )
        .exists()
    )


def _needs_ready() -> Any:
    """Condizione SQL: la lezione ha i fabbisogni pronti e il piano è attivo."""
    if not figure_plan_service.plan_active():
        return false()
    return CourseLesson.figure_needs_status == "ready"


def _needs_not_ready() -> Any:
    """Negazione esplicita sui NULL (NOT su un confronto con NULL escluderebbe
    la riga)."""
    if not figure_plan_service.plan_active():
        return true()
    return CourseLesson.figure_needs_status.is_distinct_from("ready")


async def claim_next(db: AsyncSession) -> CourseLesson | None:
    """Prima lezione `pending` pronta (backoff scaduto, estrazioni finite),
    portata a `processing` con un UPDATE condizionale. Senza il piano delle
    figure una verifica serve solo PRIMA della Fase 3: se la lezione non è
    più in coda per il contenuto, la verifica si chiude `skipped` senza
    costi. Con i fabbisogni pronti la verifica continua anche dopo l'avvio
    della Fase 3: le figure trovate servono alla rigenerazione successiva."""
    now = _now()
    await db.execute(
        update(CourseLesson)
        .where(
            CourseLesson.figures_gap_status == "pending",
            CourseLesson.content_status != "pending",
            _needs_not_ready(),
        )
        .values(
            figures_gap_status="skipped",
            figures_gap_checked_at=now,
            figures_gap_stats={"reason": "phase3_started"},
        )
    )
    await db.commit()
    candidates = (
        (
            await db.execute(
                select(CourseLesson.id, CourseLesson.figures_gap_attempts)
                .where(
                    CourseLesson.figures_gap_status == "pending",
                    or_(CourseLesson.content_status == "pending", _needs_ready()),
                    ~_extracting_documents(),
                )
                # Prima le verifiche che bloccano una Fase 3 in coda; quelle che
                # servono solo alla rigenerazione successiva vengono dopo.
                .order_by(
                    case((CourseLesson.content_status == "pending", 0), else_=1),
                    CourseLesson.figures_gap_requested_at.asc().nulls_first(),
                )
                .limit(20)
            )
        )
        .tuples()
        .all()
    )
    for lesson_id, attempts in candidates:
        wait = timedelta(seconds=RETRY_BASE_SECONDS * int(attempts or 0))
        claimed = await db.execute(
            update(CourseLesson)
            .where(
                CourseLesson.id == lesson_id,
                CourseLesson.figures_gap_status == "pending",
                or_(
                    CourseLesson.figures_gap_checked_at.is_(None),
                    CourseLesson.figures_gap_checked_at <= now - wait,
                ),
            )
            .values(figures_gap_status="processing")
        )
        if getattr(claimed, "rowcount", 0) == 1:
            await db.commit()
            return await db.get(CourseLesson, lesson_id, populate_existing=True)
    await db.rollback()
    return None


async def process_lesson(db: AsyncSession, lesson: CourseLesson) -> None:
    settings = get_settings()
    lesson_id: uuid.UUID = lesson.id
    previous_usage = dict(lesson.figures_gap_usage or {}) or None
    try:
        outcome = await gaps.check_lesson(db, lesson)
    except gaps.GapRetryError as exc:
        await db.rollback()
        fresh = await db.get(CourseLesson, lesson_id, populate_existing=True)
        if fresh is None:
            return
        attempts = int(fresh.figures_gap_attempts or 0) + 1
        terminal = attempts > int(settings.figure_literature_auto_retry_max)
        fresh.figures_gap_attempts = attempts
        fresh.figures_gap_status = "failed" if terminal else "pending"
        fresh.figures_gap_checked_at = _now()
        fresh.figures_gap_usage = _merged(previous_usage, exc.usage)
        # Esiti per fabbisogno fusi anche qui: un «trovato» dei giri
        # precedenti non si perde con un tentativo fallito.
        fresh.figures_gap_stats = merge_need_stats(
            fresh.figures_gap_stats, {**exc.stats, "error": str(exc)[:500]}
        )
        await db.commit()
        log.warning(
            "figures_gap_retry" if not terminal else "figures_gap_failed",
            lesson_id=str(lesson_id),
            attempts=attempts,
            error=str(exc)[:300],
        )
        return
    fresh = await db.get(CourseLesson, lesson_id, populate_existing=True)
    if fresh is None:
        return
    fresh.figures_gap_status = outcome.status
    fresh.figures_gap_checked_at = _now()
    fresh.figures_gap_usage = _merged(previous_usage, outcome.usage)
    fresh.figures_gap_stats = merge_need_stats(fresh.figures_gap_stats, outcome.stats)
    await db.commit()
    log.info(
        "figures_gap_checked",
        lesson_id=str(lesson_id),
        status=outcome.status,
        kept=outcome.stats.get("kept"),
        reason=outcome.stats.get("reason"),
    )


def merge_need_stats(previous: Any, stats: dict[str, Any]) -> dict[str, Any]:
    """Esito per fabbisogno fuso fra i giri della verifica: un fabbisogno
    trovato resta trovato (con la sua figura) anche se un giro successivo
    non lo cerca più; gli altri prendono l'esito più recente."""
    out = dict(stats)
    old = previous.get("needs") if isinstance(previous, dict) else None
    new = stats.get("needs")
    if not isinstance(old, dict):
        return out
    if not isinstance(new, dict):
        # Giro senza esiti per fabbisogno (errore, criterio di prima): si
        # tengono quelli dei giri precedenti.
        out["needs"] = dict(old)
        return out
    merged = dict(old)
    for need_id, value in new.items():
        if (merged.get(need_id) or {}).get("status") == "found" and (
            not isinstance(value, dict) or value.get("status") != "found"
        ):
            continue
        merged[need_id] = value
    out["needs"] = merged
    return out


def _merged(previous: dict[str, Any] | None, usage: dict[str, Any] | None) -> Any:
    """Somma dell'usage di questa verifica a quello delle precedenti."""
    if not usage:
        return previous
    if not previous:
        return usage
    merged = dict(previous)
    for key in ("calls", "prompt", "completion", "total", "cached_tokens", "reasoning_tokens"):
        merged[key] = int(merged.get(key) or 0) + int(usage.get(key) or 0)
    if usage.get("cost_usd") is not None:
        merged["cost_usd"] = round(
            float(merged.get("cost_usd") or 0.0) + float(usage["cost_usd"]), 8
        )
    merged["model"] = usage.get("model") or merged.get("model")
    merged["last"] = usage.get("last")
    return merged


async def _tick() -> None:
    """Verifica le lezioni pronte una dopo l'altra, entro un tempo per giro:
    le verifiche senza ricerca (figure già sufficienti) durano millisecondi e
    le lezioni di un corso grande non devono aspettare un giro ciascuna."""
    budget = time.monotonic() + TICK_BUDGET_SECONDS
    while time.monotonic() < budget:
        if not await _process_next():
            return


async def _process_next() -> bool:
    async with async_session_factory() as db:
        try:
            lesson = await claim_next(db)
            if lesson is None:
                return False
            lesson_id = lesson.id
            try:
                await process_lesson(db, lesson)
            except Exception as exc:  # rete di sicurezza
                # Errore inatteso (anche un errore DB transitorio): di nuovo
                # `pending` con backoff come i recuperabili, `failed` solo
                # oltre il tetto dei tentativi (regola dei worker AI, Fase D).
                await db.rollback()
                log.error("figures_gap_unexpected", lesson_id=str(lesson_id), error=str(exc))
                fresh = await db.get(CourseLesson, lesson_id, populate_existing=True)
                if fresh is not None and fresh.figures_gap_status == "processing":
                    attempts = int(fresh.figures_gap_attempts or 0) + 1
                    limit = int(get_settings().figure_literature_auto_retry_max)
                    fresh.figures_gap_attempts = attempts
                    fresh.figures_gap_status = "failed" if attempts > limit else "pending"
                    fresh.figures_gap_checked_at = _now()
                    fresh.figures_gap_stats = merge_need_stats(
                        fresh.figures_gap_stats, {"error": str(exc)[:500]}
                    )
                    await db.commit()
            return True
        except Exception as exc:  # pragma: no cover
            await db.rollback()
            log.warning("figures_gap_tick_failed", error=str(exc))
            return False


async def reset_interrupted() -> None:
    """All'avvio (un solo processo): le verifiche rimaste `processing`
    tornano `pending` e la ripresa conta come un tentativo (un contenuto che
    fa cadere il processo non riparte all'infinito); oltre il tetto,
    `failed`."""
    limit = int(get_settings().figure_literature_auto_retry_max)
    async with async_session_factory() as db:
        await db.execute(
            update(CourseLesson)
            .where(
                CourseLesson.figures_gap_status == "processing",
                CourseLesson.figures_gap_attempts >= limit,
            )
            .values(
                figures_gap_status="failed",
                figures_gap_attempts=CourseLesson.figures_gap_attempts + 1,
                figures_gap_checked_at=_now(),
            )
        )
        await db.execute(
            update(CourseLesson)
            .where(CourseLesson.figures_gap_status == "processing")
            .values(
                figures_gap_status="pending",
                figures_gap_attempts=CourseLesson.figures_gap_attempts + 1,
                figures_gap_checked_at=_now(),
            )
        )
        await db.commit()


_worker_task: asyncio.Task[None] | None = None
_stop_event: asyncio.Event | None = None


async def _run_loop() -> None:
    interval = max(2, int(get_settings().figure_literature_poll_interval_seconds))
    log.info("figures_gap_worker_started", interval=interval)
    assert _stop_event is not None
    with contextlib.suppress(Exception):
        await reset_interrupted()
    while not _stop_event.is_set():
        await _tick()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
    log.info("figures_gap_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event
    if not literature_active():
        log.info("figures_gap_worker_disabled")
        return
    if not (get_settings().papers_polite_email or "").strip():
        # La policy di Wikimedia chiede un contatto nello User-Agent: senza,
        # le richieste possono essere bloccate (403).
        log.warning("figures_gap_no_contact_email", setting="PAPERS_POLITE_EMAIL")
    if _worker_task is not None and not _worker_task.done():
        return
    _stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(_run_loop(), name="course_lesson_figures_gap_worker")


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
    _worker_task = None
    _stop_event = None
