from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.core.deps import CurrentUser, DbSession, PlatformAdmin
from app.schemas.avatar import (
    AvatarClipPromptCreate,
    AvatarClipPromptOut,
    AvatarClipPromptReorder,
    AvatarClipPromptUpdate,
    AvatarVoiceScriptOut,
    AvatarVoiceScriptUpsert,
)
from app.services import avatar_config_service

router = APIRouter(prefix="/admin/avatar-config", tags=["admin-avatar-config"])


@router.get("/prompts", response_model=list[AvatarClipPromptOut])
async def list_prompts(
    db: DbSession, _: PlatformAdmin
) -> list[AvatarClipPromptOut]:
    prompts = await avatar_config_service.list_prompts(db)
    return [AvatarClipPromptOut.model_validate(p) for p in prompts]


@router.post("/prompts", response_model=AvatarClipPromptOut, status_code=201)
async def create_prompt(
    payload: AvatarClipPromptCreate,
    db: DbSession,
    current: CurrentUser,
    _: PlatformAdmin,
) -> AvatarClipPromptOut:
    p = await avatar_config_service.create_prompt(
        db, payload=payload, actor_id=current.id
    )
    return AvatarClipPromptOut.model_validate(p)


@router.put("/prompts/{prompt_id}", response_model=AvatarClipPromptOut)
async def update_prompt(
    prompt_id: uuid.UUID,
    payload: AvatarClipPromptUpdate,
    db: DbSession,
    current: CurrentUser,
    _: PlatformAdmin,
) -> AvatarClipPromptOut:
    prompt = await avatar_config_service.get_prompt(db, prompt_id)
    prompt = await avatar_config_service.update_prompt(
        db, prompt=prompt, payload=payload, actor_id=current.id
    )
    return AvatarClipPromptOut.model_validate(prompt)


@router.delete("/prompts/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt(
    prompt_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _: PlatformAdmin,
) -> Response:
    prompt = await avatar_config_service.get_prompt(db, prompt_id)
    await avatar_config_service.delete_prompt(
        db, prompt=prompt, actor_id=current.id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/prompts/reorder", response_model=list[AvatarClipPromptOut])
async def reorder_prompts(
    payload: AvatarClipPromptReorder,
    db: DbSession,
    current: CurrentUser,
    _: PlatformAdmin,
) -> list[AvatarClipPromptOut]:
    prompts = await avatar_config_service.reorder_prompts(
        db, ordered_ids=payload.ordered_ids, actor_id=current.id
    )
    return [AvatarClipPromptOut.model_validate(p) for p in prompts]


@router.get("/voice-scripts", response_model=list[AvatarVoiceScriptOut])
async def list_voice_scripts(
    db: DbSession, _: PlatformAdmin
) -> list[AvatarVoiceScriptOut]:
    items = await avatar_config_service.list_voice_scripts(db)
    return [AvatarVoiceScriptOut.model_validate(s) for s in items]


@router.put(
    "/voice-scripts/{language_code}", response_model=AvatarVoiceScriptOut
)
async def upsert_voice_script(
    language_code: str,
    payload: AvatarVoiceScriptUpsert,
    db: DbSession,
    current: CurrentUser,
    _: PlatformAdmin,
) -> AvatarVoiceScriptOut:
    script = await avatar_config_service.upsert_voice_script(
        db,
        language_code=language_code,
        text=payload.text,
        actor_id=current.id,
    )
    return AvatarVoiceScriptOut.model_validate(script)


@router.delete(
    "/voice-scripts/{language_code}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_voice_script(
    language_code: str,
    db: DbSession,
    current: CurrentUser,
    _: PlatformAdmin,
) -> Response:
    await avatar_config_service.delete_voice_script(
        db, language_code=language_code, actor_id=current.id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
