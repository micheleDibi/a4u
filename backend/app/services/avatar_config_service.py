"""CRUD + reorder dei prompt usati per la generazione clip MiniMax."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import NotFoundError, ValidationAppError
from app.models.avatar_clip_prompt import AvatarClipPrompt
from app.models.avatar_voice_script import AvatarVoiceScript
from app.models.language import Language
from app.schemas.avatar import AvatarClipPromptCreate, AvatarClipPromptUpdate


async def list_prompts(db: AsyncSession) -> list[AvatarClipPrompt]:
    res = await db.execute(
        select(AvatarClipPrompt).order_by(AvatarClipPrompt.position.asc())
    )
    return list(res.scalars().all())


async def get_prompt(db: AsyncSession, prompt_id: uuid.UUID) -> AvatarClipPrompt:
    p = await db.get(AvatarClipPrompt, prompt_id)
    if p is None:
        raise NotFoundError("Prompt non trovato.", code="prompt_not_found")
    return p


async def create_prompt(
    db: AsyncSession, *, payload: AvatarClipPromptCreate, actor_id: uuid.UUID
) -> AvatarClipPrompt:
    # Posiziona in fondo.
    next_pos = (
        await db.execute(select(func.coalesce(func.max(AvatarClipPrompt.position), -1)))
    ).scalar_one()
    p = AvatarClipPrompt(
        position=int(next_pos) + 1,
        prompt=payload.prompt.strip(),
        label_it=(payload.label_it or None),
        is_active=payload.is_active,
    )
    db.add(p)
    await db.flush()
    await db.refresh(p)
    await write_audit(
        db,
        action="avatar.config.prompt.create",
        actor_user_id=actor_id,
        target_type="avatar_clip_prompt",
        target_id=str(p.id),
    )
    return p


async def update_prompt(
    db: AsyncSession,
    *,
    prompt: AvatarClipPrompt,
    payload: AvatarClipPromptUpdate,
    actor_id: uuid.UUID,
) -> AvatarClipPrompt:
    if payload.prompt is not None:
        prompt.prompt = payload.prompt.strip()
    if payload.label_it is not None:
        prompt.label_it = payload.label_it or None
    if payload.is_active is not None:
        prompt.is_active = payload.is_active
    await db.flush()
    await db.refresh(prompt)
    await write_audit(
        db,
        action="avatar.config.prompt.update",
        actor_user_id=actor_id,
        target_type="avatar_clip_prompt",
        target_id=str(prompt.id),
    )
    return prompt


async def delete_prompt(
    db: AsyncSession, *, prompt: AvatarClipPrompt, actor_id: uuid.UUID
) -> None:
    pid = prompt.id
    await db.delete(prompt)
    await db.flush()
    # Compatta le position (decrementa quelle dopo) per mantenere la sequenza.
    rows = list(
        (
            await db.execute(
                select(AvatarClipPrompt).order_by(AvatarClipPrompt.position.asc())
            )
        ).scalars().all()
    )
    for new_pos, row in enumerate(rows):
        if row.position != new_pos:
            row.position = new_pos
    await db.flush()
    await write_audit(
        db,
        action="avatar.config.prompt.delete",
        actor_user_id=actor_id,
        target_type="avatar_clip_prompt",
        target_id=str(pid),
    )


async def reorder_prompts(
    db: AsyncSession, *, ordered_ids: list[uuid.UUID], actor_id: uuid.UUID
) -> list[AvatarClipPrompt]:
    rows = list(
        (
            await db.execute(select(AvatarClipPrompt))
        ).scalars().all()
    )
    by_id = {r.id: r for r in rows}
    if set(by_id.keys()) != set(ordered_ids):
        raise ValidationAppError(
            "L'elenco di id non corrisponde ai prompt esistenti.",
            code="reorder_mismatch",
        )
    # Step 1: assegnazione provvisoria fuori range per evitare il vincolo UNIQUE
    # finché non sono tutti spostati.
    for r in rows:
        r.position = r.position + 10000
    await db.flush()
    for new_pos, pid in enumerate(ordered_ids):
        by_id[pid].position = new_pos
    await db.flush()
    await write_audit(
        db,
        action="avatar.config.prompt.reorder",
        actor_user_id=actor_id,
        target_type="avatar_clip_prompt",
        target_id="*",
        metadata={"order": [str(i) for i in ordered_ids]},
    )
    return await list_prompts(db)


# === Voice scripts ===


async def list_voice_scripts(db: AsyncSession) -> list[AvatarVoiceScript]:
    res = await db.execute(
        select(AvatarVoiceScript).order_by(AvatarVoiceScript.language_code.asc())
    )
    return list(res.scalars().all())


async def get_voice_script(
    db: AsyncSession, language_code: str
) -> AvatarVoiceScript | None:
    res = await db.execute(
        select(AvatarVoiceScript).where(
            AvatarVoiceScript.language_code == language_code
        )
    )
    return res.scalar_one_or_none()


async def get_voice_script_with_fallback(
    db: AsyncSession, language_code: str | None
) -> AvatarVoiceScript | None:
    """Cerca lo script per la lingua data; se manca, usa il default ('it' se
    presente, altrimenti la prima lingua attiva con default flag)."""
    if language_code:
        primary = await get_voice_script(db, language_code)
        if primary is not None:
            return primary
    # fallback alla lingua di default piattaforma
    default_lang = (
        await db.execute(
            select(Language.code)
            .where(Language.is_default.is_(True), Language.is_active.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if default_lang:
        fallback = await get_voice_script(db, default_lang)
        if fallback is not None:
            return fallback
    # ultimo tentativo: qualsiasi script esistente
    res = await db.execute(
        select(AvatarVoiceScript).order_by(AvatarVoiceScript.language_code.asc()).limit(1)
    )
    return res.scalar_one_or_none()


async def upsert_voice_script(
    db: AsyncSession, *, language_code: str, text: str, actor_id: uuid.UUID
) -> AvatarVoiceScript:
    # Verifica che la lingua esista (FK CASCADE).
    lang = (
        await db.execute(select(Language).where(Language.code == language_code))
    ).scalar_one_or_none()
    if lang is None:
        raise ValidationAppError(
            f"Lingua '{language_code}' non esistente.", code="language_not_found"
        )
    existing = await get_voice_script(db, language_code)
    if existing is None:
        existing = AvatarVoiceScript(language_code=language_code, text=text.strip())
        db.add(existing)
    else:
        existing.text = text.strip()
    await db.flush()
    await db.refresh(existing)
    await write_audit(
        db,
        action="avatar.config.voice_script.upsert",
        actor_user_id=actor_id,
        target_type="avatar_voice_script",
        target_id=language_code,
    )
    return existing


async def delete_voice_script(
    db: AsyncSession, *, language_code: str, actor_id: uuid.UUID
) -> None:
    existing = await get_voice_script(db, language_code)
    if existing is None:
        raise NotFoundError("Script vocale non trovato.", code="voice_script_not_found")
    await db.delete(existing)
    await db.flush()
    await write_audit(
        db,
        action="avatar.config.voice_script.delete",
        actor_user_id=actor_id,
        target_type="avatar_voice_script",
        target_id=language_code,
    )
