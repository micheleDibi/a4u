"""Gestione utenti lato platform-admin.

Tiene le invarianti di sicurezza (no auto-disattivazione / auto-demozione,
deve restare almeno un admin di piattaforma attivo) e l'audit in
un'unica transazione. Il router resta sottile (precedente:
`membership_service`). NESSUNA eliminazione definitiva: la rimozione di
un account = disattivazione (`is_active=False`), reversibile.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ConflictError, NotFoundError
from app.core.security import hash_password
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.user import UserUpdateAdmin


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def _count_other_active_platform_admins(
    db: AsyncSession, *, exclude_user_id: uuid.UUID
) -> int:
    """Numero di platform-admin ATTIVI diversi da `exclude_user_id`."""
    total = (
        await db.execute(
            select(func.count())
            .select_from(User)
            .where(
                User.id != exclude_user_id,
                User.is_platform_admin.is_(True),
                User.is_active.is_(True),
            )
        )
    ).scalar_one()
    return int(total)


async def update_user_admin(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    payload: UserUpdateAdmin,
    actor: User,
    ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("Utente non trovato.", code="user_not_found")

    # Self-guard: l'admin non può disattivare o demuoversi da solo (rischio
    # di lockout immediato).
    if user.id == actor.id:
        if payload.is_active is False:
            raise ConflictError(
                "Non puoi disattivare il tuo stesso account.",
                code="cannot_deactivate_self",
            )
        if payload.is_platform_admin is False:
            raise ConflictError(
                "Non puoi rimuovere il tuo ruolo di admin di piattaforma.",
                code="cannot_demote_self",
            )

    # Last-active-admin: la piattaforma deve sempre avere almeno un
    # admin attivo. Blocca la demozione/disattivazione dell'unico rimasto.
    removing_admin = payload.is_platform_admin is False and user.is_platform_admin
    deactivating = payload.is_active is False and user.is_active
    if (
        (removing_admin or deactivating)
        and user.is_platform_admin
        and user.is_active
        and await _count_other_active_platform_admins(db, exclude_user_id=user.id) == 0
    ):
        raise ConflictError(
            "Operazione bloccata: deve restare almeno un admin di piattaforma attivo.",
            code="last_active_admin",
        )

    changed: list[str] = []

    if payload.email is not None:
        normalized = payload.email.lower().strip()
        if normalized != user.email.lower():
            clash = (
                await db.execute(
                    select(User).where(
                        func.lower(User.email) == normalized,
                        User.id != user.id,
                    )
                )
            ).scalar_one_or_none()
            if clash is not None:
                raise ConflictError("Email già in uso.", code="email_in_use")
            user.email = normalized
            changed.append("email")

    if payload.full_name is not None and payload.full_name != user.full_name:
        user.full_name = payload.full_name
        changed.append("full_name")
    if payload.is_platform_admin is not None and payload.is_platform_admin != user.is_platform_admin:
        user.is_platform_admin = payload.is_platform_admin
        changed.append("is_platform_admin")
    if payload.is_active is not None and payload.is_active != user.is_active:
        user.is_active = payload.is_active
        changed.append("is_active")

    await db.flush()
    await write_audit(
        db,
        action="user.update",
        actor_user_id=actor.id,
        target_type="user",
        target_id=str(user.id),
        metadata={
            "fields": changed,
            "is_platform_admin": user.is_platform_admin,
            "is_active": user.is_active,
        },
        ip=ip,
        user_agent=user_agent,
    )
    return user


async def set_user_password(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    new_password: str,
    actor: User,
    ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("Utente non trovato.", code="user_not_found")

    user.password_hash = hash_password(new_password)
    # Reset password lato admin = forza il re-login: revoca tutti i
    # refresh token vivi dell'utente target (soft-revoke, come
    # auth_service.rotate_refresh). Gli access token JWT restano validi
    # fino a scadenza TTL (~15 min), coerente con l'architettura.
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    await db.flush()
    await write_audit(
        db,
        action="user.password_reset",
        actor_user_id=actor.id,
        target_type="user",
        target_id=str(user.id),
        ip=ip,
        user_agent=user_agent,
    )
    return user
