from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.errors import AuthenticationError, RateLimitedError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_secret,
    verify_password,
)
from app.models.login_attempt import LoginAttempt
from app.models.refresh_token import RefreshToken
from app.models.user import User

log = get_logger("app.auth")


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def _record_login_attempt(
    db: AsyncSession, *, email: str, ip: str | None, success: bool
) -> None:
    db.add(LoginAttempt(email=email.lower(), ip=ip, success=success))
    await db.flush()


async def login(
    db: AsyncSession, *, email: str, password: str, ip: str | None, user_agent: str | None
) -> tuple[User, str, str]:
    settings = get_settings()
    email_norm = email.lower().strip()

    user_q = await db.execute(select(User).where(func.lower(User.email) == email_norm))
    user = user_q.scalar_one_or_none()

    if user and user.locked_until and user.locked_until > _now():
        raise RateLimitedError(
            "Account temporaneamente bloccato per troppi tentativi falliti.",
            code="account_locked",
        )

    if not user or not user.is_active or not verify_password(password, user.password_hash):
        await _record_login_attempt(db, email=email_norm, ip=ip, success=False)
        if user is not None:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            if user.failed_login_count >= settings.login_lockout_threshold:
                user.locked_until = _now() + timedelta(minutes=settings.login_lockout_minutes)
                user.failed_login_count = 0
                await write_audit(
                    db,
                    action="auth.login.locked",
                    actor_user_id=user.id,
                    metadata={"email": email_norm},
                    ip=ip,
                    user_agent=user_agent,
                )
            await db.flush()
        await write_audit(
            db,
            action="auth.login.failure",
            metadata={"email": email_norm},
            ip=ip,
            user_agent=user_agent,
        )
        # Commit prima di rilanciare: i tentativi/lockout devono persistere anche se la richiesta fallisce.
        await db.commit()
        raise AuthenticationError("Credenziali non valide.", code="invalid_credentials")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = _now()
    await _record_login_attempt(db, email=email_norm, ip=ip, success=True)

    access_token = create_access_token(subject=str(user.id))
    refresh_raw, jti, expires_at = create_refresh_token(subject=str(user.id))
    db.add(
        RefreshToken(
            id=jti,
            user_id=user.id,
            token_hash=hash_secret(refresh_raw),
            expires_at=expires_at,
            user_agent=user_agent,
            ip=ip,
        )
    )
    await db.flush()

    await write_audit(
        db,
        action="auth.login.success",
        actor_user_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )

    return user, access_token, refresh_raw


async def rotate_refresh(
    db: AsyncSession, *, refresh_token: str, ip: str | None, user_agent: str | None
) -> tuple[User, str, str]:
    payload = decode_token(refresh_token, expected_type="refresh")
    try:
        user_id = uuid.UUID(payload["sub"])
        jti = uuid.UUID(payload["jti"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Refresh token non valido.", code="token_invalid") from exc

    rt = await db.get(RefreshToken, jti)
    if rt is None or rt.user_id != user_id:
        raise AuthenticationError("Refresh token sconosciuto.", code="token_unknown")

    if rt.token_hash != hash_secret(refresh_token):
        raise AuthenticationError("Refresh token non valido.", code="token_invalid")

    if rt.revoked_at is not None:
        # Reuse detection: revoca tutti i refresh dell'utente
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        await write_audit(
            db,
            action="auth.refresh.reuse_detected",
            actor_user_id=user_id,
            ip=ip,
            user_agent=user_agent,
        )
        raise AuthenticationError(
            "Refresh token già usato. Sessioni invalidate per sicurezza.",
            code="token_reused",
        )

    if rt.expires_at <= _now():
        raise AuthenticationError("Refresh token scaduto.", code="token_expired")

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Utente non valido.", code="user_inactive")

    access_token = create_access_token(subject=str(user.id))
    new_raw, new_jti, expires_at = create_refresh_token(subject=str(user.id))
    new_rt = RefreshToken(
        id=new_jti,
        user_id=user.id,
        token_hash=hash_secret(new_raw),
        expires_at=expires_at,
        user_agent=user_agent,
        ip=ip,
    )
    # Inserisco prima il nuovo token, poi aggiorno il vecchio referenziandolo:
    # il FK self-referential `replaced_by_id` richiede che la riga puntata esista
    # già al momento dell'UPDATE, e l'UoW di SQLAlchemy non riordina sempre
    # correttamente le operazioni su FK auto-referenziali.
    db.add(new_rt)
    await db.flush()
    rt.revoked_at = _now()
    rt.replaced_by_id = new_jti
    await db.flush()

    await write_audit(
        db,
        action="auth.refresh.success",
        actor_user_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )

    return user, access_token, new_raw


async def revoke_refresh_token(
    db: AsyncSession, *, refresh_token: str | None, ip: str | None
) -> None:
    if not refresh_token:
        return
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
        jti = uuid.UUID(payload["jti"])
    except (AuthenticationError, KeyError, ValueError):
        return
    rt = await db.get(RefreshToken, jti)
    if rt and rt.revoked_at is None:
        rt.revoked_at = _now()
        await write_audit(
            db, action="auth.logout", actor_user_id=rt.user_id, ip=ip, user_agent=None
        )
