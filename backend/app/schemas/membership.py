from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel


class MembershipOut(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: EmailStr
    user_full_name: str
    organization_id: uuid.UUID
    role_id: uuid.UUID
    role_code: str
    role_name_it: str
    joined_at: datetime


class EnrollUserRequest(BaseModel):
    user_id: uuid.UUID
    role_code: str = Field(min_length=1, max_length=40)


class ChangeRoleRequest(BaseModel):
    role_code: str = Field(min_length=1, max_length=40)


class InvitationCreateRequest(BaseModel):
    email: EmailStr
    role_code: str


class InvitationOut(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    email: EmailStr
    role_code: str
    expires_at: datetime
    accepted_at: datetime | None = None


class InvitationCreateResponse(BaseModel):
    invitation: InvitationOut
    token: str  # in chiaro, solo nella risposta della creazione
    accept_url: str


class TransferCreatorRequest(BaseModel):
    target_user_id: uuid.UUID


class PermissionOverrideEntry(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    granted: bool


class PermissionOverridesUpdate(BaseModel):
    overrides: list[PermissionOverrideEntry]


class RolePermissionDefaultUpdate(BaseModel):
    role_code: str
    permissions: list[str]
