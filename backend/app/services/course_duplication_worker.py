"""Worker async per la duplicazione corso in altra lingua.

Pattern speculare al `course_lesson_content_worker.py` (loop +
semaforo + auto-retry trasparente), ma scoped a livello JOB
(non lezione). Cap globale 1 perché ogni job consuma molti token
OpenAI e di solito è scatenato manualmente dall'utente; dentro al
job, le lezioni vengono tradotte in parallelo cap 3 per fase.

Stato per job su `course_duplication_job.status`:
    pending → processing → ready
                          ↘ failed (dopo `auto_retry_max` retry esauriti)

Pipeline `_process_one(job_id)`:
  Phase 1 — loading_source                  (progress 2%)
  Phase 2 — cloning_structure               (progress 5% → 8%)
  Phase 3 — translating_architecture        (progress 10% → 20%)
  Phase 4 — translating_content             (progress 25% → 50%)
  Phase 5 — translating_slides              (progress 55% → 70%)
  Phase 6 — translating_speech              (progress 75% → 85%)
  Phase 7 — translating_glossary_documents  (progress 88% → 95%)
  Phase 8 — finalizing                      (progress 95% → 100%)

Su `OpenAIError` transient: il job viene riportato a `pending` con
`attempts+1`, fino a `course_duplication_auto_retry_max=5`. Su errore
terminale o cap esaurito → `status=failed`.

La cancel da UI mette `failed` con `error='Annullata dall'utente'`.
Il worker, prima di scrivere `ready`, verifica `job.status` per
evitare race window.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.course import Course
from app.models.course_duplication_job import CourseDuplicationJob
from app.models.course_lesson import CourseLesson
from app.models.language import Language
from app.services import course_duplication_service as _svc
from app.services.openai_client import OpenAINotConfiguredError

log = get_logger("app.course_duplication.worker")


# ---------------------------------------------------------------------------
# State del worker
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
    job: CourseDuplicationJob,
    *,
    error: str,
    recoverable: bool,
    auto_retry_max: int,
) -> bool:
    """True se è stato schedulato un retry (job torna in `pending`).
    False se l'errore è terminale (`status=failed`).
    """
    attempts = job.attempts or 0
    if recoverable and attempts < auto_retry_max:
        job.status = "pending"
        job.error = None
        log.info(
            "course_duplication_auto_retry",
            job_id=str(job.id),
            attempts=attempts,
            max_retry=auto_retry_max,
            error=error[:200],
        )
        return True
    job.status = "failed"
    job.error = error[:500]
    job.finished_at = datetime.now(UTC)
    log.warning(
        "course_duplication_failed_terminal",
        job_id=str(job.id),
        attempts=attempts,
        error=error[:200],
    )
    return False


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


async def _set_progress(
    job_id: uuid.UUID, *, pct: int, phase: str | None
) -> None:
    """Aggiorna progress + phase su una sessione propria (lock breve)."""
    async with async_session_factory() as tdb:
        job = await tdb.get(CourseDuplicationJob, job_id)
        if job is None:
            return
        job.progress = max(0, min(100, pct))
        job.progress_phase = phase
        await tdb.commit()


async def _check_cancelled(job_id: uuid.UUID) -> bool:
    """True se il job è stato cancellato (status uscito da `processing`)."""
    async with async_session_factory() as tdb:
        job = await tdb.get(CourseDuplicationJob, job_id)
        if job is None:
            return True
        return job.status != "processing"


# ---------------------------------------------------------------------------
# Process one job
# ---------------------------------------------------------------------------


async def _process_one(job_id: uuid.UUID) -> None:
    """Esegue la pipeline completa di duplicazione per un singolo job.
    Sessione DB propria per la transizione `pending → processing`,
    poi sessioni proprie per ogni fase (per ridurre lock contention).
    """
    settings = get_settings()
    retry_max = settings.course_duplication_auto_retry_max
    lesson_cap = max(
        1, int(settings.course_duplication_lesson_translate_concurrency)
    )

    started = time.monotonic()
    # --- Claim → processing ---------------------------------------------
    async with async_session_factory() as db:
        job = await db.get(CourseDuplicationJob, job_id)
        if job is None or job.status != "pending":
            return
        source_id = job.source_course_id
        target_lang_code = job.target_language_code
        organization_id = None
        source_lang_code = None
        source_lang_name = None
        target_lang_name = None

        source = await db.get(Course, source_id)
        if source is None:
            _apply_failure(
                job,
                error="Corso sorgente non trovato.",
                recoverable=False,
                auto_retry_max=retry_max,
            )
            await db.commit()
            return
        organization_id = source.organization_id
        source_lang_code = source.language_code

        source_lang_row = await db.get(Language, source_lang_code)
        target_lang_row = await db.get(Language, target_lang_code)
        if source_lang_row is None or target_lang_row is None:
            _apply_failure(
                job,
                error="Lingua sorgente o target non trovata in DB.",
                recoverable=False,
                auto_retry_max=retry_max,
            )
            await db.commit()
            return
        source_lang_name = source_lang_row.name_native
        target_lang_name = target_lang_row.name_native

        job.status = "processing"
        job.attempts = (job.attempts or 0) + 1
        job.error = None
        job.progress = 2
        job.progress_phase = "loading_source"
        job.started_at = datetime.now(UTC)
        await db.commit()

    try:
        # --- Phase 1: load source full -----------------------------------
        async with async_session_factory() as db:
            source = await _svc.load_source_full(db, course_id=source_id)
            if source is None:
                raise OpenAINotConfiguredError(
                    "Corso sorgente non trovato dopo il claim."
                )

            # --- Phase 2: clone structure -------------------------------
            await _set_progress(job_id, pct=5, phase="cloning_structure")
            job = await db.get(CourseDuplicationJob, job_id)
            assert job is not None
            target = await _svc._clone_course_structure(
                db,
                source=source,
                target_language_code=target_lang_code,
                job=job,
            )
            await _set_progress(job_id, pct=8, phase="cloning_structure")

        # --- Phase 3: translate architecture + metadata ------------------
        async with async_session_factory() as db:
            target = await _svc.load_target_full(db, course_id=target.id)
            assert target is not None
            await _set_progress(
                job_id, pct=12, phase="translating_architecture"
            )
            await _svc._translate_course_metadata(
                db,
                target=target,
                source_lang_code=source_lang_code,
                source_lang_name=source_lang_name,
                target_lang_code=target_lang_code,
                target_lang_name=target_lang_name,
            )
            await _svc._translate_architecture(
                db,
                target=target,
                source_lang_code=source_lang_code,
                source_lang_name=source_lang_name,
                target_lang_code=target_lang_code,
                target_lang_name=target_lang_name,
            )
            await db.commit()
            await _set_progress(
                job_id, pct=20, phase="translating_architecture"
            )

        if await _check_cancelled(job_id):
            log.info("course_duplication_cancelled_post_arch", job_id=str(job_id))
            return

        # --- Phase 4-6: lezioni in parallelo per fase --------------------
        for phase, pct_start, pct_end, label in [
            ("meta", 22, 28, "translating_lesson_metadata"),
            ("content", 30, 50, "translating_content"),
            ("slides", 55, 70, "translating_slides"),
            ("speech", 75, 85, "translating_speech"),
        ]:
            await _set_progress(job_id, pct=pct_start, phase=label)
            await _translate_lessons_phase(
                target_id=target.id,
                phase=phase,
                source_lang_code=source_lang_code,
                source_lang_name=source_lang_name,
                target_lang_code=target_lang_code,
                target_lang_name=target_lang_name,
                lesson_cap=lesson_cap,
            )
            await _set_progress(job_id, pct=pct_end, phase=label)
            if await _check_cancelled(job_id):
                log.info(
                    "course_duplication_cancelled_mid_phase",
                    job_id=str(job_id),
                    phase=label,
                )
                return

        # --- Phase 7: glossary + documents --------------------------------
        async with async_session_factory() as db:
            target = await _svc.load_target_full(db, course_id=target.id)
            assert target is not None
            await _set_progress(
                job_id, pct=88, phase="translating_glossary_documents"
            )
            await _svc._translate_glossary(
                db,
                target=target,
                source_lang_code=source_lang_code,
                source_lang_name=source_lang_name,
                target_lang_code=target_lang_code,
                target_lang_name=target_lang_name,
            )
            await _svc._translate_document_summaries(
                db,
                target=target,
                source_lang_code=source_lang_code,
                source_lang_name=source_lang_name,
                target_lang_code=target_lang_code,
                target_lang_name=target_lang_name,
            )
            await db.commit()
            await _set_progress(
                job_id, pct=95, phase="translating_glossary_documents"
            )

        if await _check_cancelled(job_id):
            return

        # --- Phase 8: finalize -------------------------------------------
        async with async_session_factory() as db:
            source = await db.get(Course, source_id)
            target = await db.get(Course, target.id)
            job = await db.get(CourseDuplicationJob, job_id)
            if source is None or target is None or job is None:
                return
            # Verifica anti-race: il job potrebbe essere stato cancellato.
            if job.status != "processing":
                return
            await _svc._finalize(db, source=source, target=target)
            job.status = "ready"
            job.progress = 100
            job.progress_phase = None
            job.finished_at = datetime.now(UTC)
            job.tokens = {
                "wall_clock_seconds": int(time.monotonic() - started),
            }
            await write_audit(
                db,
                action="course.duplicate.completed",
                actor_user_id=job.requested_by_user_id,
                organization_id=organization_id,
                target_type="course_duplication_job",
                target_id=str(job.id),
                metadata={
                    "source_course_id": str(source.id),
                    "target_course_id": str(target.id),
                    "target_language_code": target_lang_code,
                },
            )
            await db.commit()

        log.info(
            "course_duplication_completed",
            job_id=str(job_id),
            source_course_id=str(source_id),
            target_course_id=str(target.id),
            wall_clock_seconds=int(time.monotonic() - started),
        )

    except OpenAINotConfiguredError as exc:
        async with async_session_factory() as db:
            job = await db.get(CourseDuplicationJob, job_id)
            if job is None:
                return
            _apply_failure(
                job,
                error=str(exc),
                recoverable=False,
                auto_retry_max=retry_max,
            )
            if organization_id is not None:
                await write_audit(
                    db,
                    action="course.duplicate.failed",
                    actor_user_id=job.requested_by_user_id,
                    organization_id=organization_id,
                    target_type="course_duplication_job",
                    target_id=str(job_id),
                    metadata={
                        "error": str(exc)[:500],
                        "attempts": job.attempts,
                    },
                )
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        async with async_session_factory() as db:
            job = await db.get(CourseDuplicationJob, job_id)
            if job is None:
                return
            terminal = not _apply_failure(
                job,
                error=str(exc),
                recoverable=True,
                auto_retry_max=retry_max,
            )
            if terminal and organization_id is not None:
                await write_audit(
                    db,
                    action="course.duplicate.failed",
                    actor_user_id=job.requested_by_user_id,
                    organization_id=organization_id,
                    target_type="course_duplication_job",
                    target_id=str(job_id),
                    metadata={
                        "error": str(exc)[:500],
                        "attempts": job.attempts,
                    },
                )
            await db.commit()


async def _translate_lessons_phase(
    *,
    target_id: uuid.UUID,
    phase: str,
    source_lang_code: str,
    source_lang_name: str,
    target_lang_code: str,
    target_lang_name: str,
    lesson_cap: int,
) -> None:
    """Traduce tutte le lezioni del target per la fase indicata, in
    parallelo cap `lesson_cap`. Carica una sessione fresh per ogni
    lezione per evitare di tenere troppe lezioni in memoria.
    """
    sem = asyncio.Semaphore(lesson_cap)
    async with async_session_factory() as db:
        # Solo lettura degli ID — la traduzione vera apre una sessione
        # per lezione.
        res = await db.execute(
            select(CourseLesson.id).where(CourseLesson.course_id == target_id)
        )
        lesson_ids: list[uuid.UUID] = [row[0] for row in res.all()]

    async def _do_one(lesson_id: uuid.UUID) -> None:
        async with sem:
            async with async_session_factory() as ldb:
                lesson = await ldb.get(CourseLesson, lesson_id)
                if lesson is None:
                    return
                await _svc._translate_lesson(
                    ldb,
                    lesson=lesson,
                    source_lang_code=source_lang_code,
                    source_lang_name=source_lang_name,
                    target_lang_code=target_lang_code,
                    target_lang_name=target_lang_name,
                    phase=phase,
                )
                await ldb.commit()

    if lesson_ids:
        await asyncio.gather(
            *[_do_one(lid) for lid in lesson_ids], return_exceptions=False
        )


# ---------------------------------------------------------------------------
# Bound + tick + run loop
# ---------------------------------------------------------------------------


async def _bound_process(job_id: uuid.UUID) -> None:
    """Wrap `_process_one` con cap globale di concorrenza."""
    assert _semaphore is not None
    try:
        async with _semaphore:
            await _process_one(job_id)
    except Exception as exc:  # pragma: no cover
        log.error(
            "course_duplication_worker_unexpected",
            job_id=str(job_id),
            error=str(exc),
        )
    finally:
        async with _inflight_lock:
            _inflight.discard(job_id)


async def _tick() -> None:
    """Cerca job `pending` e li dispatcha come task paralleli."""
    async with async_session_factory() as db:
        try:
            res = await db.execute(
                select(CourseDuplicationJob.id).where(
                    CourseDuplicationJob.status == "pending"
                )
            )
            job_ids = [row[0] for row in res.all()]
        except Exception as exc:  # pragma: no cover
            log.warning(
                "course_duplication_worker_tick_failed", error=str(exc)
            )
            return

    if not job_ids:
        return

    async with _inflight_lock:
        new_ids = [jid for jid in job_ids if jid not in _inflight]
        for jid in new_ids:
            _inflight.add(jid)

    for jid in new_ids:
        task = asyncio.create_task(
            _bound_process(jid),
            name=f"course_duplication_job_{jid}",
        )
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)


async def _run_loop() -> None:
    settings = get_settings()
    interval = max(2, int(settings.course_duplication_poll_interval_seconds))
    log.info(
        "course_duplication_worker_started",
        interval=interval,
        max_concurrent_jobs=settings.course_duplication_max_concurrent_jobs,
    )
    assert _stop_event is not None
    while not _stop_event.is_set():
        await _tick()
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    log.info("course_duplication_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event, _semaphore
    if _worker_task is not None and not _worker_task.done():
        return
    settings = get_settings()
    _stop_event = asyncio.Event()
    _semaphore = asyncio.Semaphore(
        max(1, int(settings.course_duplication_max_concurrent_jobs))
    )
    _worker_task = asyncio.create_task(
        _run_loop(), name="course_duplication_worker"
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
            "course_duplication_worker_waiting_inflight",
            count=len(_active_tasks),
        )
        await asyncio.gather(*list(_active_tasks), return_exceptions=True)
    _worker_task = None
    _stop_event = None
