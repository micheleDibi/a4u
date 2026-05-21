from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status

from app.core.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError
from app.schemas.avatar import (
    AvatarMusetalkParamsUpdate,
    AvatarOut,
    AvatarVoiceScriptOut,
)
from app.services import avatar_config_service, avatar_service

router = APIRouter(prefix="/me/avatar", tags=["me-avatar"])


@router.get("", response_model=AvatarOut | None)
async def get_my_avatar(db: DbSession, current: CurrentUser) -> AvatarOut | None:
    avatar = await avatar_service.get_my_avatar(db, current.id)
    if avatar is None:
        return None
    return AvatarOut.model_validate(avatar)


@router.put("", response_model=AvatarOut)
async def upsert_my_avatar(
    db: DbSession,
    current: CurrentUser,
    audio_lang: Annotated[str | None, Form(max_length=10)] = None,
    image: Annotated[UploadFile | None, File()] = None,
    audio: Annotated[UploadFile | None, File()] = None,
) -> AvatarOut:
    avatar = await avatar_service.upsert_my_avatar(
        db,
        user_id=current.id,
        image_upload=image,
        audio_upload=audio,
        audio_lang=audio_lang,
        actor_id=current.id,
    )
    return AvatarOut.model_validate(avatar)


@router.get("/voice-script", response_model=AvatarVoiceScriptOut | None)
async def get_voice_script(
    db: DbSession,
    _: CurrentUser,
    lang: Annotated[str | None, Query(max_length=10)] = None,
) -> AvatarVoiceScriptOut | None:
    """Restituisce il testo che l'utente deve leggere durante la registrazione.

    Fallback: se per `lang` non è configurato, ritorna lo script della
    lingua di default piattaforma; se manca pure quello, qualsiasi script
    disponibile; null se nessuno è configurato.
    """
    script = await avatar_config_service.get_voice_script_with_fallback(db, lang)
    if script is None:
        return None
    return AvatarVoiceScriptOut.model_validate(script)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_avatar(db: DbSession, current: CurrentUser) -> Response:
    avatar = await avatar_service.get_my_avatar(db, current.id)
    if avatar is None:
        raise NotFoundError("Nessun avatar da eliminare.", code="avatar_not_found")
    await avatar_service.delete_my_avatar(db, avatar=avatar, actor_id=current.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/clips/regenerate", response_model=AvatarOut, status_code=status.HTTP_202_ACCEPTED)
async def regenerate_clips(db: DbSession, current: CurrentUser) -> AvatarOut:
    avatar = await avatar_service.get_my_avatar(db, current.id)
    if avatar is None:
        raise NotFoundError("Nessun avatar.", code="avatar_not_found")
    avatar = await avatar_service.regenerate_clips(db, avatar=avatar, actor_id=current.id)
    return AvatarOut.model_validate(avatar)


@router.patch("/musetalk-params", response_model=AvatarOut)
async def update_musetalk_params(
    payload: AvatarMusetalkParamsUpdate,
    db: DbSession,
    current: CurrentUser,
) -> AvatarOut:
    """Aggiorna i parametri MuseTalk per-avatar usati dal «Video con
    Avatar» delle lezioni (lip-sync dell'avatar sovrapposto al video)."""
    avatar = await avatar_service.get_my_avatar(db, current.id)
    if avatar is None:
        raise NotFoundError("Nessun avatar.", code="avatar_not_found")
    avatar = await avatar_service.update_musetalk_params(
        db,
        avatar=avatar,
        extra_margin=payload.musetalk_extra_margin,
        left_cheek_width=payload.musetalk_left_cheek_width,
        right_cheek_width=payload.musetalk_right_cheek_width,
        actor_id=current.id,
    )
    return AvatarOut.model_validate(avatar)
