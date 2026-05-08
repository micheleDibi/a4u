from __future__ import annotations

import uuid
from typing import Annotated, AsyncIterator

from fastapi import Cookie, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.logging import user_id_ctx
from app.core.security import decode_token
from app.db.session import async_session_factory
from app.models.user import User


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    request: Request,
    db: DbSession,
    access_token: Annotated[str | None, Cookie(alias="access_token")] = None,
) -> User:
    if not access_token:
        # supporto opzionale Bearer per CLI/tests
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            access_token = auth_header.removeprefix("Bearer ").strip()
    if not access_token:
        raise AuthenticationError("Autenticazione richiesta.", code="not_authenticated")

    payload = decode_token(access_token, expected_type="access")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Token non valido.", code="token_invalid") from exc

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise AuthenticationError("Utente non valido o disattivato.", code="user_inactive")

    user_id_ctx.set(str(user.id))
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_platform_admin(user: CurrentUser) -> User:
    if not user.is_platform_admin:
        raise PermissionDeniedError("Richiesto ruolo admin di piattaforma.", code="platform_admin_required")
    return user


PlatformAdmin = Annotated[User, Depends(require_platform_admin)]
