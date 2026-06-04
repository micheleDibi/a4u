from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.security import is_password_strong


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class InvitationAcceptRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    password: str | None = Field(default=None, min_length=10, max_length=128)


# --- Self-service profilo personale ---


class ProfileUpdate(BaseModel):
    """PATCH /auth/me — modifica del proprio nome (nessuna re-auth)."""

    full_name: str = Field(min_length=1, max_length=255)


class ChangeEmailRequest(BaseModel):
    """POST /auth/me/change-email — richiede la password attuale."""

    current_password: str = Field(min_length=1, max_length=200)
    new_email: EmailStr


class ChangePasswordRequest(BaseModel):
    """POST /auth/me/change-password — richiede la password attuale."""

    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=10, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _validate_new_password(cls, v: str) -> str:
        if not is_password_strong(v):
            raise ValueError("Password debole: minimo 10 caratteri, una maiuscola e un numero.")
        return v
