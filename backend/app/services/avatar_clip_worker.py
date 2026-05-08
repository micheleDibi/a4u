"""Background worker per la generazione clip video MiniMax.

Loop singolo `asyncio.Task` lanciato a startup in `app.main.lifespan`. Lo
stato è in DB, quindi se il backend si riavvia il worker riprende da dove
aveva lasciato (recovery). Niente Celery, nessun broker esterno.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.avatar import Avatar
from app.models.avatar_clip import AvatarClip
from app.services import minimax_service, storage_service
from app.services.avatar_service import aggregate_clips_status

if TYPE_CHECKING:
    pass

log = get_logger("app.avatar.worker")


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def _start_pending(db: AsyncSession, clip: AvatarClip, avatar: Avatar) -> None:
    image_url = storage_service.public_url(avatar.image_path)
    try:
        task_id = await minimax_service.start_video_generation(
            image_url=image_url,
            prompt=clip.prompt_text,
        )
    except minimax_service.MinimaxNotConfiguredError:
        # Non spammo log: lo stato resta pending finché l'admin configura la key.
        return
    except minimax_service.MinimaxError as exc:
        clip.status = "failed"
        clip.error_message = str(exc)
        clip.completed_at = _now()
        log.warning("clip_start_failed", clip_id=str(clip.id), error=str(exc))
        return
    clip.minimax_task_id = task_id
    clip.status = "processing"
    clip.started_at = _now()


async def _poll_processing(db: AsyncSession, clip: AvatarClip, avatar: Avatar) -> None:
    if not clip.minimax_task_id:
        clip.status = "pending"
        return
    try:
        status = await minimax_service.query_task_status(clip.minimax_task_id)
    except minimax_service.MinimaxError as exc:
        log.warning("clip_poll_failed", clip_id=str(clip.id), error=str(exc))
        return  # ritento al prossimo giro

    if status.status in {"preparing", "processing", "unknown"}:
        return
    if status.status == "failed":
        clip.status = "failed"
        clip.error_message = "MiniMax ha riportato fallimento"
        clip.completed_at = _now()
        return
    if status.status == "success" and status.file_id:
        try:
            data = await minimax_service.download_file(status.file_id)
        except minimax_service.MinimaxError as exc:
            log.warning("clip_download_failed", clip_id=str(clip.id), error=str(exc))
            return
        path = storage_service.save_bytes(
            subdir=f"avatars/{avatar.user_id}/clips",
            filename=f"{clip.id}.mp4",
            data=data,
        )
        clip.video_path = path
        clip.minimax_file_id = status.file_id
        clip.status = "ready"
        clip.completed_at = _now()
        await write_audit(
            db,
            action="avatar.clip.ready",
            actor_user_id=avatar.user_id,
            target_type="avatar_clip",
            target_id=str(clip.id),
            metadata={"avatar_id": str(avatar.id)},
        )


async def _recompute_avatar_status(db: AsyncSession, avatar: Avatar) -> None:
    res = await db.execute(
        select(AvatarClip.status).where(AvatarClip.avatar_id == avatar.id)
    )
    statuses = [str(s) for s in res.scalars().all()]
    new_status = aggregate_clips_status(statuses)
    if avatar.clips_status != new_status:
        avatar.clips_status = new_status


async def _tick() -> None:
    """Una iterazione del loop: processa pending+processing in batch piccoli."""
    async with async_session_factory() as db:
        try:
            res = await db.execute(
                select(AvatarClip).where(
                    AvatarClip.status.in_(["pending", "processing"])
                )
            )
            clips = list(res.scalars().all())
            avatars_touched: dict = {}
            for clip in clips:
                avatar = avatars_touched.get(clip.avatar_id)
                if avatar is None:
                    avatar = await db.get(Avatar, clip.avatar_id)
                    if avatar is None:
                        continue
                    avatars_touched[clip.avatar_id] = avatar
                if clip.status == "pending":
                    await _start_pending(db, clip, avatar)
                elif clip.status == "processing":
                    await _poll_processing(db, clip, avatar)
            for avatar in avatars_touched.values():
                await _recompute_avatar_status(db, avatar)
            await db.commit()
        except Exception as exc:  # pragma: no cover — loop deve sopravvivere
            await db.rollback()
            log.warning("avatar_worker_tick_failed", error=str(exc))


_worker_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


async def _run_loop() -> None:
    settings = get_settings()
    interval = max(2, int(settings.minimax_poll_interval_seconds))
    log.info("avatar_worker_started", interval=interval)
    assert _stop_event is not None
    while not _stop_event.is_set():
        await _tick()
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    log.info("avatar_worker_stopped")


def start_worker() -> None:
    global _worker_task, _stop_event
    if _worker_task is not None and not _worker_task.done():
        return
    _stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(_run_loop(), name="avatar_clip_worker")


async def stop_worker() -> None:
    global _worker_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=15)
        except asyncio.TimeoutError:
            _worker_task.cancel()
            with_suppressed_cancel = asyncio.gather(_worker_task, return_exceptions=True)
            await with_suppressed_cancel
    _worker_task = None
    _stop_event = None
