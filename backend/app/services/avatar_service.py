"""Service avatar utente (un avatar per utente, globale).

L'avatar è composto da:
  - immagine frontale (PNG/JPEG/WebP, riprocessata da Pillow)
  - audio (mp3/wav/webm/m4a/... — niente transcoding, salva il file as-is)
  - testo dell'audio + lingua (free-form)
  - N clip video generati da MiniMax a partire dall'immagine

Tutti i file vivono sotto `/uploads/avatars/{user_id}/`. Quando l'utente
cambia immagine, i clip vengono ricreati (status pending).
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ValidationAppError
from app.core.logging import get_logger
from app.models.avatar import Avatar
from app.models.avatar_clip import AvatarClip
from app.models.avatar_clip_prompt import AvatarClipPrompt
from app.services import file_service, storage_service

log = get_logger("app.avatar")


def _user_subdir(user_id: uuid.UUID) -> str:
    return f"avatars/{user_id}"


def _clips_subdir(user_id: uuid.UUID) -> str:
    return f"avatars/{user_id}/clips"


async def get_my_avatar(db: AsyncSession, user_id: uuid.UUID) -> Avatar | None:
    """Fetch dell'avatar dell'utente con clip eager-loaded ordinati per position."""
    res = await db.execute(select(Avatar).where(Avatar.user_id == user_id))
    return res.scalar_one_or_none()


async def _load_active_prompts(db: AsyncSession) -> list[AvatarClipPrompt]:
    res = await db.execute(
        select(AvatarClipPrompt)
        .where(AvatarClipPrompt.is_active.is_(True))
        .order_by(AvatarClipPrompt.position.asc())
    )
    return list(res.scalars().all())


async def upsert_my_avatar(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    image_upload: UploadFile | None,
    audio_upload: UploadFile | None,
    audio_lang: str | None,
    actor_id: uuid.UUID,
) -> Avatar:
    """Crea o aggiorna l'avatar dell'utente.

    Se cambia l'immagine o se è la prima creazione, **rigenera tutti i clip**
    (status `pending`). Il worker MiniMax li raccoglie poco dopo.
    """
    existing = await get_my_avatar(db, user_id)
    is_create = existing is None

    if is_create and (image_upload is None or audio_upload is None):
        raise ValidationAppError(
            "Per creare l'avatar servono sia l'immagine sia l'audio.",
            code="image_and_audio_required",
        )

    image_changed = False
    if image_upload is not None:
        new_image_path = await file_service.save_upload_image(
            image_upload,
            subdir=_user_subdir(user_id),
            filename_stem="image",
            square=True,
        )
        if existing and existing.image_path and existing.image_path != new_image_path:
            storage_service.delete(existing.image_path)
        image_changed = True
    else:
        assert existing is not None
        new_image_path = existing.image_path

    if audio_upload is not None:
        new_audio_path = await file_service.save_upload_audio(
            audio_upload,
            subdir=_user_subdir(user_id),
            filename_stem="audio",
        )
        if existing and existing.audio_path and existing.audio_path != new_audio_path:
            storage_service.delete(existing.audio_path)
    else:
        assert existing is not None
        new_audio_path = existing.audio_path

    if existing is None:
        avatar = Avatar(
            user_id=user_id,
            image_path=new_image_path,
            audio_path=new_audio_path,
            audio_lang=audio_lang,
            clips_status="pending",
        )
        db.add(avatar)
    else:
        avatar = existing
        avatar.image_path = new_image_path
        avatar.audio_path = new_audio_path
        if audio_lang is not None:
            avatar.audio_lang = audio_lang or None

    await db.flush()
    await db.refresh(avatar)

    if is_create or image_changed:
        # Rigenera clip da zero. Cancella i video locali esistenti e i record.
        await _reset_clips(db, avatar)
        await _create_pending_clips(db, avatar)
        avatar.clips_status = "pending"
        await db.flush()
        await db.refresh(avatar)

    await write_audit(
        db,
        action="avatar.upsert",
        actor_user_id=actor_id,
        target_type="avatar",
        target_id=str(avatar.id),
        metadata={"image_changed": image_changed, "is_create": is_create},
    )
    return avatar


async def _reset_clips(db: AsyncSession, avatar: Avatar) -> None:
    """Cancella i clip esistenti dell'avatar (record + file video locali)."""
    res = await db.execute(
        select(AvatarClip).where(AvatarClip.avatar_id == avatar.id)
    )
    for clip in res.scalars().all():
        if clip.video_path:
            storage_service.delete(clip.video_path)
        await db.delete(clip)
    await db.flush()


async def _create_pending_clips(db: AsyncSession, avatar: Avatar) -> None:
    """Crea N clip pending in base ai prompt admin attivi."""
    prompts = await _load_active_prompts(db)
    for position, p in enumerate(prompts):
        db.add(
            AvatarClip(
                avatar_id=avatar.id,
                prompt_id=p.id,
                position=position,
                prompt_text=p.prompt,
                status="pending",
            )
        )
    await db.flush()


async def regenerate_clips(
    db: AsyncSession, *, avatar: Avatar, actor_id: uuid.UUID
) -> Avatar:
    """Cancella tutti i clip e li ricrea pending (es. se admin cambia config)."""
    await _reset_clips(db, avatar)
    await _create_pending_clips(db, avatar)
    avatar.clips_status = "pending"
    await db.flush()
    await db.refresh(avatar)
    await write_audit(
        db,
        action="avatar.regenerate_clips",
        actor_user_id=actor_id,
        target_type="avatar",
        target_id=str(avatar.id),
    )
    return avatar


async def update_musetalk_params(
    db: AsyncSession,
    *,
    avatar: Avatar,
    extra_margin: int,
    left_cheek_width: int,
    right_cheek_width: int,
    actor_id: uuid.UUID,
) -> Avatar:
    """Aggiorna i parametri MuseTalk per-avatar usati dalla generazione
    del «Video con Avatar» delle lezioni."""
    avatar.musetalk_extra_margin = extra_margin
    avatar.musetalk_left_cheek_width = left_cheek_width
    avatar.musetalk_right_cheek_width = right_cheek_width
    await db.flush()
    await db.refresh(avatar)
    await write_audit(
        db,
        action="avatar.musetalk_params.update",
        actor_user_id=actor_id,
        target_type="avatar",
        target_id=str(avatar.id),
        metadata={
            "extra_margin": extra_margin,
            "left_cheek_width": left_cheek_width,
            "right_cheek_width": right_cheek_width,
        },
    )
    return avatar


async def delete_my_avatar(
    db: AsyncSession, *, avatar: Avatar, actor_id: uuid.UUID
) -> None:
    user_id = avatar.user_id
    res = await db.execute(
        select(AvatarClip).where(AvatarClip.avatar_id == avatar.id)
    )
    for clip in res.scalars().all():
        if clip.video_path:
            storage_service.delete(clip.video_path)
    storage_service.delete(avatar.image_path)
    storage_service.delete(avatar.audio_path)
    storage_service.delete_directory(_clips_subdir(user_id))
    storage_service.delete_directory(_user_subdir(user_id))
    await db.delete(avatar)
    await db.flush()
    await write_audit(
        db,
        action="avatar.delete",
        actor_user_id=actor_id,
        target_type="avatar",
        target_id=str(avatar.id),
    )


def aggregate_clips_status(statuses: list[str]) -> str:
    """Riassume lo status dei clip in un'unica etichetta per la UI."""
    if not statuses:
        return "pending"
    if all(s == "ready" for s in statuses):
        return "ready"
    if all(s == "failed" for s in statuses):
        return "failed"
    if any(s == "ready" for s in statuses) and any(s == "failed" for s in statuses):
        return "partial"
    if any(s in {"pending", "processing"} for s in statuses):
        return "processing" if any(s == "processing" for s in statuses) else "pending"
    return "partial"


# Helper utile a util/test: path locale assoluto per leggere bytes.
def absolute_path(public_path: str) -> Path:
    from app.core.config import get_settings

    settings = get_settings()
    rel = public_path.removeprefix("/uploads/")
    return settings.upload_root / rel
