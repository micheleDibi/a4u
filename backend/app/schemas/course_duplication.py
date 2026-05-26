"""Schemi Pydantic per la duplicazione corso in altra lingua.

- `CourseDuplicationJobOut`: response completa di un job (usato dagli
  endpoint dedicati).
- `CourseDuplicationJobCompact`: subset embedded nella lista corsi
  (`CourseListItemOut.duplication_job`), per il badge "Duplicazione in
  corso XX%" nel FE.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.common import ORMModel

JobStatus = Literal["pending", "processing", "ready", "failed"]


class CourseDuplicationJobCompact(ORMModel):
    """Compatto per inclusione in `CourseListItemOut.duplication_job`."""

    id: uuid.UUID
    source_course_id: uuid.UUID
    target_course_id: uuid.UUID | None
    target_language_code: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    progress_phase: str | None = None
    progress_detail: str | None = None
    # Esposto nel Compact per consentire al FE di calcolare l'ETA
    # ("~X min rimanenti") nel badge senza richiedere lo schema full.
    started_at: datetime | None = None


class CourseDuplicationJobOut(ORMModel):
    """Response completa di un job di duplicazione (POST /duplicate,
    GET /duplications, POST /cancel)."""

    id: uuid.UUID
    source_course_id: uuid.UUID
    target_course_id: uuid.UUID | None
    target_language_code: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    progress_phase: str | None = None
    progress_detail: str | None = None
    error: str | None = None
    attempts: int
    tokens: dict[str, Any] | None = None
    requested_by_user_id: uuid.UUID | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
