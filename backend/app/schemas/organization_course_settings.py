from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class OrganizationCourseSettingsBase(BaseModel):
    modules_per_cfu: int = Field(ge=1, le=20)
    lessons_per_module: int = Field(ge=1, le=50)
    lesson_duration_minutes: int = Field(ge=1, le=240)
    assessment_lesson_enabled: bool = True
    multiple_choice_questions_count: int = Field(ge=0, le=200)
    open_questions_count: int = Field(ge=0, le=50)
    # Politica di licenza delle figure di fonte per l'organizzazione
    # (`cite_all` | `open_only`); None = il default di
    # `FIGURE_SOURCE_LICENSE_POLICY`. Letta dal vivo dal catalogo.
    figure_source_license_policy: Literal["cite_all", "open_only"] | None = None


class OrganizationCourseSettingsUpdate(OrganizationCourseSettingsBase):
    pass


class OrganizationCourseSettingsOut(OrganizationCourseSettingsBase, ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
