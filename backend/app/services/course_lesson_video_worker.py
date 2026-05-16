"""Background worker per la generazione del video MP4 delle lezioni (§9).

Pattern speculare ai worker delle altre fasi (`course_lesson_speech_worker`,
`course_lesson_pdf_worker`):
- semaphore + `_inflight` set + claim atomico via UPDATE WHERE
- ticker ease-out di progress
- auto-retry trasparente (`course_lesson_video_auto_retry_max`, default 3)
- cancel-check tra fasi

Pre-condizioni runtime (verificate dopo il claim):
- `lesson.speech_status == 'approved'`
- `lesson.slides_status == 'approved'`
- `Avatar.audio_path` dell'assegnatario presente su filesystem

Pipeline (3 fasi con cancel-check tra una e l'altra):
1. TTS XTTS-v2 (0→60%): un segment alla volta, voice cloning dal
   campione dell'assegnatario.
2. Slides PNG (60→80%): Playwright headless 1920×1080.
3. Encoding ffmpeg (80→100%): per-slide MP4 + concat finale.

Output: `/uploads/lesson_videos/{course_id}/{lesson_id}.mp4` (servito da
StaticFiles con HTTP Range nativo).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.course_lesson import CourseLesson
from app.services import (
    course_lesson_video_service,
    lesson_slides_video_render_service,
    lesson_video_compose_service,
    xtts_voice_clone_service,
)

log = get_logger("app.course_lesson_video.worker")


# ---------------------------------------------------------------------------
# State
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
    """Auto-retry come negli altri worker. True se schedulato retry,
    False se transizione terminale a `failed`."""
    attempts = lesson.video_attempts or 0
    if recoverable and attempts < auto_retry_max:
        lesson.video_status = "pending"
        lesson.video_error = None
        lesson.video_progress = 0
        lesson.video_progress_phase = None
        log.info(
            "lesson_video_auto_retry",
            lesson_id=str(lesson.id),
            lesson_code=lesson.lesson_code,
            phase=phase,
            attempts=attempts,
            max_retry=auto_retry_max,
            error=error[:200],
        )
        return True
    lesson.video_status = "failed"
    lesson.video_error = error[:500]
    lesson.video_progress = 0
    lesson.video_progress_phase = None
    log.warning(
        "lesson_video_failed_terminal",
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
    """Aggiorna `video_progress` + phase su sessione propria."""
    async with async_session_factory() as tdb:
        row = await tdb.get(CourseLesson, lesson_id)
        if row is None:
            return
        row.video_progress = max(0, min(100, pct))
        row.video_progress_phase = phase
        await tdb.commit()


async def _check_cancelled(lesson_id: uuid.UUID) -> bool:
    """Ritorna True se nel frattempo lo status è stato spostato a
    `cancelled` (o `failed`/altro non-processing). Usato dal worker tra
    le fasi per abortire pulitamente."""
    async with async_session_factory() as tdb:
        row = await tdb.get(CourseLesson, lesson_id)
        if row is None:
            return True
        return row.video_status != "processing"


# ---------------------------------------------------------------------------
# Fase 1 — TTS
# ---------------------------------------------------------------------------


async def _run_tts_phase(
    lesson_id: uuid.UUID,
    *,
    speech_raw: dict[str, Any],
    precomputed_latents: tuple[Any, Any] | None,
    voice_sample_path: Path | None,
    language_code: str,
) -> tuple[dict[str, np.ndarray], int, float]:
    """Sintetizza l'audio per ogni segment via XTTS. Aggiorna il progress
    (0→60%) man mano che procede.

    Preferisce `precomputed_latents` (pre-trainati al momento dell'upload
    audio nell'avatar) per saltare ~5-15s di estrazione inline a ogni job.
    Fallback su `voice_sample_path` se i latents non sono ancora pronti.

    Returns:
        (audio_per_segment, sample_rate, tts_duration_seconds)
    """
    segments = speech_raw.get("speech_segments") or []
    total = max(1, len(segments))
    lang = xtts_voice_clone_service.normalize_language_code(language_code)
    started = time.monotonic()

    audio_per_segment: dict[str, np.ndarray] = {}
    sample_rate = get_settings().xtts_sample_rate

    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        sid = str(seg.get("segment_id") or "")
        text = (seg.get("text") or "").strip()
        if not sid or not text:
            continue

        # Cancel-check prima di ogni segment (ognuno è 1-30s, vale la pena
        # rispondere rapidamente).
        if await _check_cancelled(lesson_id):
            raise asyncio.CancelledError("Generazione annullata")

        audio, sr = await xtts_voice_clone_service.synthesize_text(
            text=text,
            language=lang,
            precomputed_latents=precomputed_latents,
            voice_sample_path=voice_sample_path,
        )
        audio_per_segment[sid] = audio
        sample_rate = sr

        # Mapping progress lineare 0→60% sul segment index.
        pct = int(((i + 1) / total) * 60)
        await _set_progress(lesson_id, pct=pct, phase="tts")

    tts_seconds = time.monotonic() - started
    return audio_per_segment, sample_rate, tts_seconds


# ---------------------------------------------------------------------------
# Fase 2 — Slides PNG
# ---------------------------------------------------------------------------


async def _run_slides_phase(
    lesson_id: uuid.UUID,
    *,
    course_id: uuid.UUID,
    work_dir: Path,
) -> tuple[list[Path], list[str]]:
    """Renderizza tutti i PNG slide. Progress 60→80%.

    Tipicamente è single-pass (un solo browser launch), quindi il
    progress va da 60 a 80 a "fasi" coarse: 65 (loading), 78 (done).
    """
    await _set_progress(lesson_id, pct=65, phase="rendering_slides")

    # Apre una nuova sessione DB per i lookup (template + organization).
    async with async_session_factory() as tdb:
        course = await course_lesson_video_service.load_course_full(
            tdb, course_id=course_id
        )
        if course is None:
            raise RuntimeError(f"Corso {course_id} non trovato per slides.")
        lesson = await course_lesson_video_service.get_lesson_or_404(
            course=course, lesson_id=lesson_id
        )
        settings = get_settings()
        png_paths, slide_id_order = (
            await lesson_slides_video_render_service.render_slides_to_png(
                tdb,
                course=course,
                lesson=lesson,
                output_dir=work_dir / "slides_png",
                public_base_url=settings.public_base_url,
            )
        )

    await _set_progress(lesson_id, pct=78, phase="rendering_slides")
    return png_paths, slide_id_order


# ---------------------------------------------------------------------------
# Fase 3 — Encoding ffmpeg
# ---------------------------------------------------------------------------


async def _run_encode_phase(
    lesson_id: uuid.UUID,
    *,
    speech_raw: dict[str, Any],
    png_paths: list[Path],
    slide_id_order: list[str],
    audio_per_segment: dict[str, np.ndarray],
    sample_rate: int,
    output_path: Path,
) -> dict[str, Any]:
    """Compose MP4 con ffmpeg. Progress 80→100%.

    Il compose service gira in un thread con loop dedicato per supporto
    `subprocess_exec` su Windows (analogo Playwright in slides_pdf).
    """
    total = max(1, len(png_paths))
    last_pct: list[int] = [80]

    def _on_progress(done: int, _total: int) -> None:
        # Mapping 80→97 sui segment encodati (3% riservati al concat finale).
        pct = 80 + int((done / total) * 17)
        last_pct[0] = pct
        # Aggiornamento progress async-from-sync: schedula su loop principale
        # se possibile, altrimenti skip (best effort).
        try:
            loop = asyncio.get_event_loop()
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    _set_progress(lesson_id, pct=pct, phase="encoding"),
                    loop,
                )
        except Exception:  # pragma: no cover
            pass

    await _set_progress(lesson_id, pct=80, phase="encoding")

    # Esecuzione in thread (con loop dedicato per subprocess Windows).
    tokens = await asyncio.to_thread(
        lesson_video_compose_service.compose_lesson_video_sync,
        lesson_speech_raw=speech_raw,
        png_paths=png_paths,
        slide_id_order=slide_id_order,
        audio_per_segment=audio_per_segment,
        audio_sample_rate=sample_rate,
        output_path=output_path,
        on_progress=_on_progress,
    )
    await _set_progress(lesson_id, pct=99, phase="encoding")
    return tokens


# ---------------------------------------------------------------------------
# Process one lesson
# ---------------------------------------------------------------------------


async def _process_one(lesson_id: uuid.UUID) -> None:
    """Genera il video per una singola lezione. Sessione DB propria."""
    settings = get_settings()
    async with async_session_factory() as db:
        bare = await db.get(CourseLesson, lesson_id)
        if bare is None:
            log.warning("lesson_video_lesson_not_found", lesson_id=str(lesson_id))
            return
        if bare.video_status != "pending":
            log.info(
                "lesson_video_skip_not_pending",
                lesson_id=str(lesson_id),
                status=bare.video_status,
            )
            return
        course_id = bare.course_id
        course = await course_lesson_video_service.load_course_full(
            db, course_id=course_id
        )
        if course is None:
            log.warning(
                "lesson_video_course_not_found",
                lesson_id=str(lesson_id),
                course_id=str(course_id),
            )
            return
        try:
            lesson = await course_lesson_video_service.get_lesson_or_404(
                course=course, lesson_id=lesson_id
            )
        except Exception:
            return

        # === Pre-condition checks ====================================
        if lesson.speech_status != "approved":
            _apply_failure(
                lesson,
                error=(
                    f"Discorso non approvato (attuale: {lesson.speech_status}). "
                    f"Approva il discorso prima di generare il video."
                ),
                phase="precheck",
                recoverable=False,
                auto_retry_max=settings.course_lesson_video_auto_retry_max,
            )
            await db.commit()
            return
        if lesson.slides_status != "approved":
            _apply_failure(
                lesson,
                error=(
                    f"Slide non approvate (attuale: {lesson.slides_status}). "
                    f"Approva le slide prima di generare il video."
                ),
                phase="precheck",
                recoverable=False,
                auto_retry_max=settings.course_lesson_video_auto_retry_max,
            )
            await db.commit()
            return

        # Risolvi avatar dell'assegnatario per voce + latents cache.
        assignee_avatar = (
            await course_lesson_video_service.resolve_assignee_avatar(
                db, assignee_user_id=course.assignee_user_id
            )
        )
        if assignee_avatar is None or not assignee_avatar.audio_path:
            _apply_failure(
                lesson,
                error=(
                    "L'assegnatario del corso non ha caricato un campione "
                    "vocale (Avatar.audio_path mancante)."
                ),
                phase="precheck",
                recoverable=False,
                auto_retry_max=settings.course_lesson_video_auto_retry_max,
            )
            await db.commit()
            return
        if assignee_avatar.tts_latents_status != "ready":
            _apply_failure(
                lesson,
                error=(
                    f"L'addestramento della voce dell'assegnatario non è "
                    f"ancora completato (stato: "
                    f"{assignee_avatar.tts_latents_status}). Attendi il "
                    f"completamento o ri-carica l'audio."
                ),
                phase="precheck",
                recoverable=False,
                auto_retry_max=settings.course_lesson_video_auto_retry_max,
            )
            await db.commit()
            return

        # === Claim → processing ======================================
        lesson.video_attempts = (lesson.video_attempts or 0) + 1
        lesson.video_status = "processing"
        lesson.video_error = None
        lesson.video_progress = 1
        lesson.video_progress_phase = "preparing"
        await db.commit()

        speech_raw = lesson_video_compose_service.parse_speech_raw(
            lesson.speech_raw
        )
        # Override per-corso (Fase 6 §9 rifinitura): lingua TTS configurabile
        # dal tab Video. NULL → fallback su `course.language_code`.
        language_code = (
            course.video_language_code or course.language_code or "it"
        )
        # Path filesystem latents pre-trainati.
        latents_abs_path = (
            course_lesson_video_service.resolve_assignee_latents_path(
                assignee_avatar
            )
        )
        # Fallback path audio (usato solo se latents non caricabili).
        voice_sample_path = (
            await course_lesson_video_service.resolve_voice_sample_path(
                db, assignee_user_id=course.assignee_user_id
            )
        )

    # Esecuzione fuori dalla sessione DB iniziale: le fasi aprono sessioni
    # proprie quando serve (per ridurre lock contention).
    output_rel = course_lesson_video_service.video_relative_path(
        course_id=course_id, lesson_id=lesson_id
    )
    output_path = course_lesson_video_service.video_absolute_path(output_rel)
    work_dir = output_path.parent / f".tmp_work_{lesson_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        # --- Phase 1: TTS ---
        # Carica i latents pre-trainati da disco (fast path: ~10ms vs
        # ~5-15s di re-estrazione). Se il file manca per qualche motivo
        # (eliminato manualmente, race con re-upload), fallback su
        # voice_sample_path che farà l'estrazione inline.
        precomputed_latents = None
        if latents_abs_path is not None and latents_abs_path.is_file():
            try:
                precomputed_latents = (
                    await xtts_voice_clone_service.load_latents_from_file(
                        latents_abs_path
                    )
                )
                log.info(
                    "lesson_video_latents_loaded",
                    lesson_id=str(lesson_id),
                    path=str(latents_abs_path),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "lesson_video_latents_load_failed",
                    lesson_id=str(lesson_id),
                    path=str(latents_abs_path),
                    error=str(exc),
                )

        audio_per_segment, sample_rate, tts_seconds = await _run_tts_phase(
            lesson_id,
            speech_raw=speech_raw,
            precomputed_latents=precomputed_latents,
            voice_sample_path=voice_sample_path,
            language_code=language_code,
        )

        if await _check_cancelled(lesson_id):
            log.info(
                "lesson_video_cancelled_post_tts",
                lesson_id=str(lesson_id),
            )
            return

        # --- Phase 2: Slides PNG ---
        png_paths, slide_id_order = await _run_slides_phase(
            lesson_id, course_id=course_id, work_dir=work_dir
        )

        if await _check_cancelled(lesson_id):
            log.info(
                "lesson_video_cancelled_post_slides",
                lesson_id=str(lesson_id),
            )
            return

        # --- Phase 3: Encoding ---
        encode_tokens = await _run_encode_phase(
            lesson_id,
            speech_raw=speech_raw,
            png_paths=png_paths,
            slide_id_order=slide_id_order,
            audio_per_segment=audio_per_segment,
            sample_rate=sample_rate,
            output_path=output_path,
        )

        if await _check_cancelled(lesson_id):
            log.info(
                "lesson_video_cancelled_post_encode",
                lesson_id=str(lesson_id),
            )
            return

        # --- Save metadata ---
        from app.services.xtts_voice_clone_service import XTTSService

        try:
            xtts_device = (await XTTSService.get()).device
        except Exception:  # pragma: no cover
            xtts_device = "unknown"

        tokens = {
            **encode_tokens,
            "tts_duration_ms": int(tts_seconds * 1000),
            "device": xtts_device,
            "model_xtts": settings.xtts_model_name,
            "num_segments": len(audio_per_segment),
            "num_slides": len(png_paths),
        }

        async with async_session_factory() as db:
            lesson_db = await db.get(CourseLesson, lesson_id)
            if lesson_db is None or lesson_db.video_status != "processing":
                # Annullato durante save: niente da fare.
                return
            course_lesson_video_service.save_video_metadata(
                lesson_db,
                video_rel_path=output_rel,
                tokens=tokens,
            )
            await write_audit(
                db,
                action="course.lesson.video.generated",
                actor_user_id=None,
                organization_id=course.organization_id,
                target_type="course_lesson",
                target_id=str(lesson_db.id),
                metadata={
                    "course_id": str(course_id),
                    "lesson_code": lesson_db.lesson_code,
                    "video_path": output_rel,
                    **tokens,
                },
            )
            await db.commit()

        log.info(
            "lesson_video_generated",
            lesson_id=str(lesson_id),
            video_path=output_rel,
            audio_duration_s=encode_tokens.get("audio_duration_s"),
            file_size=encode_tokens.get("file_size_bytes"),
        )

    except asyncio.CancelledError:
        log.info("lesson_video_cancelled", lesson_id=str(lesson_id))
        # Lo status è già stato spostato a `cancelled` dall'API; niente fix.
        return
    except Exception as exc:  # noqa: BLE001
        async with async_session_factory() as db:
            lesson_db = await db.get(CourseLesson, lesson_id)
            if lesson_db is None:
                return
            terminal = not _apply_failure(
                lesson_db,
                error=str(exc),
                phase=lesson_db.video_progress_phase or "unknown",
                recoverable=True,
                auto_retry_max=settings.course_lesson_video_auto_retry_max,
            )
            if terminal:
                await write_audit(
                    db,
                    action="course.lesson.video.failed",
                    actor_user_id=None,
                    organization_id=course.organization_id,
                    target_type="course_lesson",
                    target_id=str(lesson_id),
                    metadata={
                        "course_id": str(course_id),
                        "lesson_code": lesson_db.lesson_code,
                        "error": str(exc)[:500],
                        "attempts": lesson_db.video_attempts,
                    },
                )
            await db.commit()
    finally:
        try:
            import shutil

            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:  # pragma: no cover
            pass


# ---------------------------------------------------------------------------
# Bound + tick + run loop
# ---------------------------------------------------------------------------


async def _bound_process(lesson_id: uuid.UUID) -> None:
    """Wrap `_process_one` con cap concorrenza."""
    assert _semaphore is not None
    try:
        async with _semaphore:
            await _process_one(lesson_id)
    except Exception as exc:  # pragma: no cover
        log.error(
            "lesson_video_worker_unexpected",
            lesson_id=str(lesson_id),
            error=str(exc),
            exc_info=True,
        )
    finally:
        async with _inflight_lock:
            _inflight.discard(lesson_id)


async def _tick() -> None:
    """Discovery + dispatch lezioni pending."""
    async with async_session_factory() as db:
        try:
            res = await db.execute(
                select(CourseLesson.id).where(
                    CourseLesson.video_status == "pending"
                )
            )
            lesson_ids = [row[0] for row in res.all()]
        except Exception as exc:  # pragma: no cover
            log.warning("lesson_video_worker_tick_failed", error=str(exc))
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
            name=f"lesson_video_lesson_{lid}",
        )
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)


async def _run_loop() -> None:
    settings = get_settings()
    interval = max(2, int(settings.course_lesson_video_poll_interval_seconds))
    log.info(
        "course_lesson_video_worker_started",
        interval=interval,
        max_concurrency=settings.course_lesson_video_max_concurrency,
    )
    assert _stop_event is not None
    while not _stop_event.is_set():
        await _tick()
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    log.info("course_lesson_video_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event, _semaphore
    if _worker_task is not None and not _worker_task.done():
        return
    settings = get_settings()
    _stop_event = asyncio.Event()
    _semaphore = asyncio.Semaphore(
        max(1, int(settings.course_lesson_video_max_concurrency))
    )
    _worker_task = asyncio.create_task(
        _run_loop(), name="course_lesson_video_worker"
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
            "lesson_video_worker_waiting_inflight",
            count=len(_active_tasks),
        )
        await asyncio.gather(*list(_active_tasks), return_exceptions=True)
    _worker_task = None
    _stop_event = None
    _semaphore = None
    _inflight.clear()
    _active_tasks.clear()
