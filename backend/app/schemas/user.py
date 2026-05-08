from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.security import is_password_strong
from app.schemas.common import ORMModel


class UserOut(ORMModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str
    is_platform_admin: bool
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime


class UserCreateAdmin(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=10, max_length=128)
    is_platform_admin: bool = False

    @field_validator("password")
    @classmethod
    def _validate_password(cls, v: str) -> str:
        if not is_password_strong(v):
            raise ValueError("Password debole: minimo 10 caratteri, una maiuscola e un numero.")
        return v


class UserUpdateAdmin(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    is_platform_admin: bool | None = None
    is_active: bool | None = None


class MeOrganizationOut(ORMModel):
    organization_id: uuid.UUID
    organization_name: str
    role_code: str
    role_name_it: str
    permissions: list[str]


class MeOut(BaseModel):
    user: UserOut
    organizations: list[MeOrganizationOut]
    is_platform_admin: bool
