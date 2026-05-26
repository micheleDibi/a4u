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
import traceback
import uuid
from datetime import UTC, datetime

# Cap di durata massima per un singolo job di duplicazione: oltre il quale
# il worker lo termina forzatamente e lo segna come `failed`. Evita job
# "appesi" indefinitamente per timeout silenti di OpenAI o bug di
# materializzazione. Configurabile via
# `settings.course_duplication_job_timeout_minutes` (default 90 min).
def _get_job_total_timeout_seconds() -> int:
    return max(60, int(get_settings().course_duplication_job_timeout_minutes) * 60)

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
    job_id: uuid.UUID,
    *,
    pct: int,
    phase: str | None,
    detail: str | None = None,
) -> None:
    """Aggiorna progress + phase (+ optional detail) su una sessione
    propria (lock breve)."""
    async with async_session_factory() as tdb:
        job = await tdb.get(CourseDuplicationJob, job_id)
        if job is None:
            return
        job.progress = max(0, min(100, pct))
        job.progress_phase = phase
        # `detail` viene SEMPRE riscritto (None per phase brevi che
        # non hanno un sotto-progresso significativo).
        job.progress_detail = detail
        await tdb.commit()


async def _set_progress_detail(
    job_id: uuid.UUID, *, detail: str | None
) -> None:
    """Aggiorna SOLO il sotto-progresso (es. counter lezioni
    completate). Non tocca progress/phase, evitando di ridurre %
    accidentalmente fra due aggiornamenti."""
    async with async_session_factory() as tdb:
        job = await tdb.get(CourseDuplicationJob, job_id)
        if job is None:
            return
        job.progress_detail = detail
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

        # Memorizza il progress raggiunto dall'attempt precedente PRIMA
        # di resettarlo: serve per il resume-from-progress (skip delle
        # phase gia' completate nei retry). Senza questo, ogni retry
        # rifa' architecture + meta + glossary da capo, sprecando 10-20
        # minuti su corsi grandi.
        resume_from_pct = job.progress if (job.attempts or 0) > 0 else 0

        job.status = "processing"
        job.attempts = (job.attempts or 0) + 1
        job.error = None
        job.progress = 2
        job.progress_phase = "loading_source"
        job.started_at = datetime.now(UTC)
        await db.commit()

    if resume_from_pct > 0:
        log.info(
            "course_duplication_resume_from_progress",
            job_id=str(job_id),
            resume_from_pct=resume_from_pct,
            attempts=job.attempts,
        )

    try:
        # --- Phase 1: load source full -----------------------------------
        async with async_session_factory() as db:
            source = await _svc.load_source_full(db, course_id=source_id)
            if source is None:
                raise OpenAINotConfiguredError(
                    "Corso sorgente non trovato dopo il claim."
                )

            # --- Phase 2: clone structure (IDEMPOTENTE su retry) --------
            # Se il job ha già un target_course_id da un retry precedente,
            # NON ri-clonare: riusiamo il target esistente. Altrimenti
            # ogni retry creerebbe un nuovo target stub nel DB.
            await _set_progress(job_id, pct=5, phase="cloning_structure")
            job = await db.get(CourseDuplicationJob, job_id)
            assert job is not None
            target: Course | None = None
            if job.target_course_id is not None:
                target = await db.get(Course, job.target_course_id)
                if target is None:
                    # Il target è stato eliminato manualmente fra un retry
                    # e l'altro: nullifica il riferimento e ricloara.
                    log.warning(
                        "course_duplication_target_missing_reclone",
                        job_id=str(job_id),
                        previous_target_id=str(job.target_course_id),
                    )
                    job.target_course_id = None
                    await db.commit()
                else:
                    log.info(
                        "course_duplication_clone_skipped_idempotent",
                        job_id=str(job_id),
                        target_course_id=str(target.id),
                        attempts=job.attempts,
                    )
            if target is None:
                target = await _svc._clone_course_structure(
                    db,
                    source=source,
                    target_language_code=target_lang_code,
                    job=job,
                )
            await _set_progress(job_id, pct=8, phase="cloning_structure")

        # --- Phase 3: translate architecture + metadata ------------------
        # SKIP se gia' completata in un attempt precedente (resume).
        if resume_from_pct < 20:
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
        else:
            log.info(
                "course_duplication_phase_skipped_resume",
                job_id=str(job_id),
                phase="translating_architecture",
                resume_from_pct=resume_from_pct,
            )
            await _set_progress(
                job_id, pct=20, phase="translating_architecture"
            )

        if await _check_cancelled(job_id):
            log.info("course_duplication_cancelled_post_arch", job_id=str(job_id))
            return

        # --- Phase 4: lessons meta (campi diretti CourseLesson) ---------
        # Wave veloce, separata dal combined per non saturare le sessioni
        # DB con dati troppo grandi tutti in una volta.
        # SKIP se gia' completata in un attempt precedente (resume).
        if resume_from_pct < 35:
            await _set_progress(
                job_id, pct=25, phase="translating_lesson_metadata"
            )
            await _translate_lessons_phase(
                target_id=target.id,
                phase="meta",
                source_lang_code=source_lang_code,
                source_lang_name=source_lang_name,
                target_lang_code=target_lang_code,
                target_lang_name=target_lang_name,
                lesson_cap=lesson_cap,
            )
            await _set_progress(
                job_id, pct=35, phase="translating_lesson_metadata"
            )
        else:
            log.info(
                "course_duplication_phase_skipped_resume",
                job_id=str(job_id),
                phase="translating_lesson_metadata",
                resume_from_pct=resume_from_pct,
            )
            await _set_progress(
                job_id, pct=35, phase="translating_lesson_metadata"
            )
        if await _check_cancelled(job_id):
            log.info(
                "course_duplication_cancelled_post_meta",
                job_id=str(job_id),
            )
            return

        # --- Phase 5: content + slides + speech per lezione (combinato)
        # Per ogni lezione, le 3 phase si eseguono sequenzialmente dentro
        # la sua task (3 chiamate OpenAI sequenziali). Il parallelismo
        # globale è dato dal cap di concorrenza fra lezioni (default 6).
        # Risultato: ~1/3 del tempo rispetto a 3 wave separate.
        await _set_progress(
            job_id, pct=40, phase="translating_lesson_content_slides_speech"
        )
        await _translate_lessons_combined_phase(
            job_id=job_id,
            target_id=target.id,
            source_lang_code=source_lang_code,
            source_lang_name=source_lang_name,
            target_lang_code=target_lang_code,
            target_lang_name=target_lang_name,
            lesson_cap=lesson_cap,
        )
        await _set_progress(
            job_id, pct=85, phase="translating_lesson_content_slides_speech"
        )
        if await _check_cancelled(job_id):
            log.info(
                "course_duplication_cancelled_post_combined",
                job_id=str(job_id),
            )
            return

        # --- Phase 7: glossary + documents --------------------------------
        # SKIP se gia' completata in un attempt precedente (resume).
        if resume_from_pct < 95:
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
        else:
            log.info(
                "course_duplication_phase_skipped_resume",
                job_id=str(job_id),
                phase="translating_glossary_documents",
                resume_from_pct=resume_from_pct,
            )
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
        log.error(
            "course_duplication_unhandled_exception",
            job_id=str(job_id),
            error=str(exc),
            tb=traceback.format_exc()[:2000],
        )
        async with async_session_factory() as db:
            job = await db.get(CourseDuplicationJob, job_id)
            if job is None:
                return
            terminal = not _apply_failure(
                job,
                error=f"{type(exc).__name__}: {str(exc)[:400]}",
                recoverable=True,
                auto_retry_max=retry_max,
            )
            if terminal:
                # Cleanup automatico del target_course su fail
                # terminale: cascade su moduli + lezioni + documenti via
                # ondelete=CASCADE. Niente stub a vuoto nel DB.
                target_course_id_cleaned: uuid.UUID | None = None
                if job.target_course_id is not None:
                    target = await db.get(Course, job.target_course_id)
                    if target is not None:
                        target_course_id_cleaned = target.id
                        await db.delete(target)
                        log.info(
                            "course_duplication_target_cleaned_up",
                            job_id=str(job_id),
                            target_course_id=str(target.id),
                        )
                    job.target_course_id = None
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
                            "target_course_id_cleaned": (
                                str(target_course_id_cleaned)
                                if target_course_id_cleaned
                                else None
                            ),
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

    if not lesson_ids:
        return

    # Gather con resilienza per-lezione: una singola lezione fallita
    # NON deve far esplodere tutto il job (= retry totale = re-clone).
    # Se >50% fallisce alziamo eccezione per attivare il retry del JOB
    # (segno che OpenAI è giù o c'è un problema sistemico).
    results = await asyncio.gather(
        *[_do_one(lid) for lid in lesson_ids],
        return_exceptions=True,
    )
    failures = [
        (lid, exc)
        for lid, exc in zip(lesson_ids, results)
        if isinstance(exc, BaseException)
    ]
    if failures:
        log.warning(
            "course_duplication_lesson_phase_failures",
            phase=phase,
            failed=len(failures),
            total=len(lesson_ids),
            sample=[
                {"lesson_id": str(lid), "error": str(exc)[:200]}
                for lid, exc in failures[:5]
            ],
        )
    if failures and len(failures) > len(lesson_ids) * 0.5:
        raise RuntimeError(
            f"Phase '{phase}': {len(failures)}/{len(lesson_ids)} lezioni "
            f"fallite (>50%). Attivo retry del job."
        )


async def _translate_lessons_combined_phase(
    *,
    job_id: uuid.UUID,
    target_id: uuid.UUID,
    source_lang_code: str,
    source_lang_name: str,
    target_lang_code: str,
    target_lang_name: str,
    lesson_cap: int,
) -> None:
    """Traduce content + slides + speech di ogni lezione IN PARALLELO
    (3 sessioni DB separate, 3 chiamate OpenAI concorrenti per
    lezione). Parallelismo globale dato dal cap `lesson_cap` fra
    lezioni (default 10).

    Stima tempo per lezione: max(content_time, slides_time, speech_time)
    = ~30s invece di ~75s sequenziali. Totale corso 80 lezioni con
    cap 10 ≈ 4 min (vs ~17 min sequenziale).

    Resilienza: ogni phase di ogni lezione è in try/except dedicato
    con la sua sessione (un fallimento isolato non rompe le altre).
    Soglia 50% di fail TOTALE per attivare retry del job.
    """
    sem = asyncio.Semaphore(lesson_cap)
    async with async_session_factory() as db:
        res = await db.execute(
            select(CourseLesson.id).where(CourseLesson.course_id == target_id)
        )
        lesson_ids: list[uuid.UUID] = [row[0] for row in res.all()]

    if not lesson_ids:
        return

    total_lessons = len(lesson_ids)
    # Counter atomic per il sotto-progresso "X/Y lezioni completate".
    # Incrementato a fine di ogni `_do_one` (= dopo che le 3 phase di
    # quella lezione hanno terminato, ok o errore che sia).
    completed_counter = 0
    completed_lock = asyncio.Lock()
    # Push iniziale del detail cosi il FE vede subito "0/N lezioni"
    # appena la combined phase parte.
    await _set_progress_detail(
        job_id, detail=f"0/{total_lessons} lezioni completate"
    )

    # Multi-pass persistente con fallback model.
    # Lista di lavoro: (lesson_id, phase) per ogni lezione e ognuna
    # delle 3 phase. Ad ogni pass, tutte le coppie ancora pending
    # vengono ritentate. Le pass 5-6 usano fallback model (gpt-4o)
    # piu' stabile per le residue, cosi' alla fine TUTTE le phase
    # sono tradotte (o quasi -- log.error se ne restano).
    pending_work: set[tuple[uuid.UUID, str]] = set()
    for lid in lesson_ids:
        for phase in ("content", "slides", "speech"):
            pending_work.add((lid, phase))
    pending_lock = asyncio.Lock()

    async def _translate_one_phase(
        lesson_id: uuid.UUID,
        phase: str,
        *,
        model_override: str | None = None,
    ) -> bool:
        """Carica la lezione in una sessione propria, traduce la phase
        indicata, commit. Ritorna True se ok, False se errore."""
        try:
            async with async_session_factory() as ldb:
                lesson = await ldb.get(CourseLesson, lesson_id)
                if lesson is None:
                    return False
                await _svc._translate_lesson(
                    ldb,
                    lesson=lesson,
                    source_lang_code=source_lang_code,
                    source_lang_name=source_lang_name,
                    target_lang_code=target_lang_code,
                    target_lang_name=target_lang_name,
                    phase=phase,
                    model_override=model_override,
                )
                await ldb.commit()
                return True
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "course_duplication_lesson_phase_inner_error",
                lesson_id=str(lesson_id),
                phase=phase,
                error=str(exc)[:300],
                model=model_override or "default",
            )
            return False

    async def _attempt_one(
        lesson_id: uuid.UUID,
        phase: str,
        *,
        model_override: str | None = None,
        is_first_completion: bool = False,
    ) -> None:
        """Esegue un tentativo di traduzione phase. Se ok, rimuove da
        pending_work. Aggiorna il counter sotto-progresso solo al
        PRIMO successo di una lezione (cioe' quando tutte e 3 le phase
        di quella lezione hanno avuto almeno un tentativo riuscito o
        terminale nel pass corrente)."""
        nonlocal completed_counter
        async with sem:
            ok = await _translate_one_phase(
                lesson_id, phase, model_override=model_override
            )
        if ok:
            async with pending_lock:
                pending_work.discard((lesson_id, phase))

    # Pass plan: ogni tupla e' (sleep_before_seconds, model_override).
    # Il modello None usa il default (gpt-4o-mini). I pass 5-6 usano
    # il fallback model (gpt-4o) per ultime phase pending.
    settings_local = get_settings()
    default_model = settings_local.openai_model
    fallback_model = settings_local.openai_model_fallback
    pass_plan: list[tuple[int, str | None]] = [
        (0, None),               # Pass 1: default
        (30, None),              # Pass 2: default + 30s wait
        (90, None),              # Pass 3: default + 90s wait
        (180, None),             # Pass 4: default + 3 min wait
        (30, fallback_model),    # Pass 5: fallback gpt-4o + 30s wait
        (60, fallback_model),    # Pass 6: fallback gpt-4o + 1 min wait
    ]

    initial_count = len(pending_work)
    # Tracking per FE sub-progress: contiamo lezioni "completamente
    # processate" intese come "tutte e 3 le phase tentate e o riuscite
    # o non piu' pending nel pass corrente". Per semplicita' qui
    # contiamo lezioni che NON hanno piu' phase pending.
    async def _update_completion_counter() -> None:
        nonlocal completed_counter
        async with pending_lock:
            pending_lessons = {lid for lid, _ in pending_work}
        done = total_lessons - len(pending_lessons)
        if done != completed_counter:
            completed_counter = done
            await _set_progress_detail(
                job_id,
                detail=f"{done}/{total_lessons} lezioni completate",
            )

    for pass_idx, (sleep_s, model_override) in enumerate(pass_plan, start=1):
        if not pending_work:
            break
        if sleep_s > 0:
            log.info(
                "course_duplication_combined_pass_wait",
                pass_num=pass_idx,
                wait_seconds=sleep_s,
                pending=len(pending_work),
                model=model_override or default_model,
            )
            await _set_progress_detail(
                job_id,
                detail=(
                    f"Attesa {sleep_s}s prima del pass {pass_idx}/6 "
                    f"({len(pending_work)} phase da tradurre)…"
                ),
            )
            await asyncio.sleep(sleep_s)

        snapshot = list(pending_work)
        log.info(
            "course_duplication_combined_pass_start",
            pass_num=pass_idx,
            pending=len(snapshot),
            initial=initial_count,
            model=model_override or default_model,
        )
        await asyncio.gather(
            *[
                _attempt_one(lid, phase, model_override=model_override)
                for lid, phase in snapshot
            ],
            return_exceptions=True,
        )
        await _update_completion_counter()
        log.info(
            "course_duplication_combined_pass_done",
            pass_num=pass_idx,
            recovered=len(snapshot) - len(pending_work),
            still_pending=len(pending_work),
            model=model_override or default_model,
        )

    # Logging finale: se restano phase pending dopo tutti i 6 pass +
    # fallback model, l'utente deve esserne informato. Il job procede
    # comunque al finalize (no retry totale): l'utente potra' rigenerare
    # manualmente dal tab specifico della lezione.
    if pending_work:
        residual = sorted(pending_work)
        log.error(
            "course_duplication_combined_residual_failures",
            count=len(residual),
            sample=[
                {"lesson_id": str(lid), "phase": phase}
                for lid, phase in residual[:20]
            ],
        )
    else:
        log.info(
            "course_duplication_combined_phase_fully_converged",
            initial=initial_count,
        )
    # NESSUN raise RuntimeError: anche se restano phase pending, NON
    # scateniamo retry totale del job. La phase 5 ha gia' fatto 6 pass
    # con backoff progressivo + fallback model, rifare TUTTO da capo
    # non risolverebbe nulla.


# ---------------------------------------------------------------------------
# Bound + tick + run loop
# ---------------------------------------------------------------------------


async def _bound_process(job_id: uuid.UUID) -> None:
    """Wrap `_process_one` con cap globale di concorrenza + timeout totale.

    Se `_process_one` non termina entro
    `settings.course_duplication_job_timeout_minutes` (default 90 min),
    il job viene cancellato (CancelledError) e marcato `failed` con
    error "job_total_timeout". Evita stati appesi indefinitamente.
    """
    assert _semaphore is not None
    timeout_seconds = _get_job_total_timeout_seconds()
    try:
        async with _semaphore:
            try:
                await asyncio.wait_for(
                    _process_one(job_id),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                log.error(
                    "course_duplication_job_total_timeout",
                    job_id=str(job_id),
                    timeout_seconds=timeout_seconds,
                )
                async with async_session_factory() as db:
                    job = await db.get(CourseDuplicationJob, job_id)
                    if job is not None and job.status in (
                        "pending",
                        "processing",
                    ):
                        job.status = "failed"
                        job.error = (
                            f"Timeout totale del job ({timeout_seconds // 60} min)."
                        )
                        job.finished_at = datetime.now(UTC)
                        # Cleanup target su timeout — stesso comportamento
                        # del fail terminale (vedi `except Exception`).
                        if job.target_course_id is not None:
                            target = await db.get(Course, job.target_course_id)
                            if target is not None:
                                log.info(
                                    "course_duplication_target_cleaned_up",
                                    job_id=str(job_id),
                                    target_course_id=str(target.id),
                                    reason="job_total_timeout",
                                )
                                await db.delete(target)
                            job.target_course_id = None
                        await db.commit()
    except Exception as exc:  # pragma: no cover
        log.error(
            "course_duplication_worker_unexpected",
            job_id=str(job_id),
            error=str(exc),
            tb=traceback.format_exc()[:1000],
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
