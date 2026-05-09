from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel

HEX_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"


def _normalize_hex(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if not v.startswith("#"):
        v = "#" + v
    if len(v) != 7:
        raise ValueError("Colore in formato esadecimale atteso (#RRGGBB).")
    return v.upper() if v[0] == "#" else v


class _TemplateColors(BaseModel):
    text_color: str = Field(default="#1F1F1F", pattern=HEX_COLOR_PATTERN)
    primary_color: str = Field(default="#1976D2", pattern=HEX_COLOR_PATTERN)
    secondary_color: str = Field(default="#9C27B0", pattern=HEX_COLOR_PATTERN)
    font_family: str = Field(default="Roboto", min_length=1, max_length=120)

    @field_validator("text_color", "primary_color", "secondary_color", mode="before")
    @classmethod
    def _norm(cls, v: str | None) -> str | None:
        return _normalize_hex(v)


class SlideTemplateBase(_TemplateColors):
    name: str = Field(min_length=1, max_length=120)
    slide_size: Literal["16:9", "4:3"] = "16:9"
    margin_mm: int = Field(default=20, ge=0, le=60)
    background_opacity_pct: int = Field(default=15, ge=0, le=100)


class SlideTemplateOut(SlideTemplateBase, ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    background_image_path: str | None = None
    logo_left_path: str | None = None
    logo_right_path: str | None = None
    is_default: bool = False
    created_at: datetime
    updated_at: datetime


class PdfTemplateBase(_TemplateColors):
    name: str = Field(min_length=1, max_length=120)
    page_size: Literal["A4", "Letter"] = "A4"
    header_height_mm: int = Field(default=20, ge=0, le=80)
    footer_height_mm: int = Field(default=15, ge=0, le=80)
    margin_mm: int = Field(default=20, ge=0, le=60)
    background_opacity_pct: int = Field(default=15, ge=0, le=100)


class PdfTemplateOut(PdfTemplateBase, ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    background_image_path: str | None = None
    logo_left_path: str | None = None
    logo_right_path: str | None = None
    is_default: bool = False
    created_at: datetime
    updated_at: datetime
