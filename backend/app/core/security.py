from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings
from app.core.errors import AuthenticationError

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd_context.verify(password, password_hash)
    except Exception:
        return False


TokenType = Literal["access", "refresh"]


def _now() -> datetime:
    return datetime.now(tz=UTC)


def create_access_token(*, subject: str, extra: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    payload: dict[str, Any] = {
        "sub": subject,
        "type": "access",
        "iat": int(_now().timestamp()),
        "exp": int((_now() + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(*, subject: str, jti: uuid.UUID | None = None) -> tuple[str, uuid.UUID, datetime]:
    settings = get_settings()
    token_jti = jti or uuid.uuid4()
    expires_at = _now() + timedelta(seconds=settings.refresh_token_ttl_seconds)
    payload = {
        "sub": subject,
        "type": "refresh",
        "jti": str(token_jti),
        "iat": int(_now().timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    encoded = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded, token_jti, expires_at


def decode_token(token: str, *, expected_type: TokenType) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token scaduto.", code="token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Token non valido.", code="token_invalid") from exc
    if payload.get("type") != expected_type:
        raise AuthenticationError("Tipo di token errato.", code="token_invalid")
    return payload


def hash_secret(value: str) -> str:
    """Hash deterministico (sha256) per chiavi opache come refresh token e invitation token."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_url_safe_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


# Policy password: ≥10 caratteri, almeno una maiuscola e una cifra.
def is_password_strong(value: str) -> bool:
    if len(value) < 10:
        return False
    if not any(c.isupper() for c in value):
        return False
    if not any(c.isdigit() for c in value):
        return False
    return True
