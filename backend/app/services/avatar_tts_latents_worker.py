"""Background worker per il pre-training dei conditioning latents XTTS.

Pattern speculare a `avatar_clip_worker`:
- loop singolo `asyncio.Task` lanciato a startup in `app.main.lifespan`
- query periodica avatar con `tts_latents_status='pending'` AND
  `audio_path IS NOT NULL`
- per ogni avatar: claim atomico (set `processing`), estrai latents,
  salva `.pt` su disco, set `ready`
- niente auto-retry: l'estrazione è deterministica, se fallisce serve
  un audio diverso → status `failed` + `tts_latents_error`

L'utente vede tutto via UI (badge stato in AvatarPage) — il processo è
"silenzioso" lato utente.

Cap concorrenza: 1 (XTTSService usa thread-pool singolo, niente
parallelismo intrinseco).
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.avatar import Avatar
from app.services import avatar_tts_latents_service

log = get_logger("app.avatar_tts_latents.worker")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_inflight: set[uuid.UUID] = set()
_inflight_lock = asyncio.Lock()

_worker_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


# ---------------------------------------------------------------------------
# Process one avatar
# ---------------------------------------------------------------------------


async def _process_one(avatar_id: uuid.UUID) -> None:
    """Estrae e salva i latents per un singolo avatar. Sessione DB propria."""
    async with async_session_factory() as db:
        avatar = await db.get(Avatar, avatar_id)
        if avatar is None:
            log.warning("avatar_latents_avatar_not_found", id=str(avatar_id))
            return
        if avatar.tts_latents_status != "pending":
            log.info(
                "avatar_latents_skip_not_pending",
                id=str(avatar_id),
                status=avatar.tts_latents_status,
            )
            return
        if not avatar.audio_path:
            # No audio → si rimuove dal pending (transizione neutra:
            # `pending` resta finché un audio non è caricato).
            log.info(
                "avatar_latents_skip_no_audio",
                id=str(avatar_id),
                user_id=str(avatar.user_id),
            )
            return

        # Claim → processing
        avatar.tts_latents_status = "processing"
        avatar.tts_latents_error = None
        await db.commit()

        try:
            rel = await avatar_tts_latents_service.extract_latents_for_avatar(
                avatar
            )
        except Exception as exc:  # noqa: BLE001
            avatar_tts_latents_service.mark_latents_failed(
                avatar, error=str(exc)
            )
            await write_audit(
                db,
                action="avatar.tts_latents.failed",
                actor_user_id=avatar.user_id,
                target_type="avatar",
                target_id=str(avatar.id),
                metadata={
                    "error": str(exc)[:500],
                    "user_id": str(avatar.user_id),
                },
            )
            await db.commit()
            log.warning(
                "avatar_latents_failed",
                avatar_id=str(avatar.id),
                error=str(exc)[:200],
            )
            return

        avatar_tts_latents_service.mark_latents_ready(
            avatar, relative_path=rel
        )
        await write_audit(
            db,
            action="avatar.tts_latents.ready",
            actor_user_id=avatar.user_id,
            target_type="avatar",
            target_id=str(avatar.id),
            metadata={
                "path": rel,
                "user_id": str(avatar.user_id),
            },
        )
        await db.commit()
        log.info(
            "avatar_latents_ready",
            avatar_id=str(avatar.id),
            user_id=str(avatar.user_id),
            path=rel,
        )


# ---------------------------------------------------------------------------
# Bound + tick + run loop
# ---------------------------------------------------------------------------


async def _bound_process(avatar_id: uuid.UUID) -> None:
    """Sequenziale (cap=1 implicito) — no semaphore necessaria perché
    XTTSService è single-threaded sul device."""
    try:
        await _process_one(avatar_id)
    except Exception as exc:  # pragma: no cover
        log.error(
            "avatar_latents_worker_unexpected",
            avatar_id=str(avatar_id),
            error=str(exc),
            exc_info=True,
        )
    finally:
        async with _inflight_lock:
            _inflight.discard(avatar_id)


async def _tick() -> None:
    """Pickup avatar pending. Serializza il processing (uno alla volta)."""
    async with async_session_factory() as db:
        try:
            res = await db.execute(
                select(Avatar.id).where(
                    Avatar.tts_latents_status == "pending",
                    Avatar.audio_path.isnot(None),
                )
            )
            avatar_ids = [row[0] for row in res.all()]
        except Exception as exc:  # pragma: no cover
            log.warning(
                "avatar_latents_worker_tick_failed", error=str(exc)
            )
            return

    if not avatar_ids:
        return

    # Filtra quelli già in flight.
    async with _inflight_lock:
        new_ids = [aid for aid in avatar_ids if aid not in _inflight]
        for aid in new_ids:
            _inflight.add(aid)

    # Esegui in sequenza (uno alla volta), per non saturare XTTS thread-pool.
    for aid in new_ids:
        if _stop_event is not None and _stop_event.is_set():
            async with _inflight_lock:
                _inflight.discard(aid)
            return
        await _bound_process(aid)


async def _run_loop() -> None:
    settings = get_settings()
    # Riusiamo il poll interval del worker video (default 4s) — stessa
    # cadenza del flusso video pipeline.
    interval = max(
        2, int(settings.course_lesson_video_poll_interval_seconds)
    )
    log.info("avatar_tts_latents_worker_started", interval=interval)
    assert _stop_event is not None
    while not _stop_event.is_set():
        await _tick()
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    log.info("avatar_tts_latents_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event
    if _worker_task is not None and not _worker_task.done():
        return
    _stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(
        _run_loop(), name="avatar_tts_latents_worker"
    )


async def stop_worker() -> None:
    global _worker_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=15)
        except asyncio.TimeoutError:
            _worker_task.cancel()
            await asyncio.gather(_worker_task, return_exceptions=True)
    _worker_task = None
    _stop_event = None
    _inflight.clear()
