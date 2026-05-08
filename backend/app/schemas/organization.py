from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel


class OrganizationBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=50)
    website: str | None = Field(default=None, max_length=255)
    vat_number: str | None = Field(default=None, max_length=64)
    fiscal_code: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, max_length=100)
    address: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=120)
    province: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)


class OrganizationOut(OrganizationBase, ORMModel):
    id: uuid.UUID
    logo_path: str | None = None
    created_at: datetime
    updated_at: datetime
