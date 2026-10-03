"""Stato leggero del corso per il polling dell'editor (Prestazioni L1).

`GET /orgs/{org_id}/courses/{course_id}/status` espone solo stati, avanzamento e timestamp:
nessun JSONB grande (`*_raw`, `*_tokens`, riassunti, bibliografie). Contratto vincolante:
`docs/contracts/perf-l1-course-status.md`. Nomi, tipi e default dei campi sono gli stessi di
`CourseOut`, `CourseDocumentOut`, `CourseModuleOut` e `CourseLessonOut`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.common import ORMModel
from app.schemas.course import CourseStatus, DocumentSummaryStatus


class CourseStatusDocumentOut(ORMModel):
    id: uuid.UUID
    summary_status: DocumentSummaryStatus
    summary_generated_at: datetime | None = None
    summary_error: str | None = None
    summary_attempts: int = 0
    summary_coverage: str | None = None
    summary_chunks_total: int | None = None
    summary_chunks_done: int | None = None
    figures_status: str | None = None
    figures_error_code: str | None = None
    figures_count: int | None = None
    figures_coverage: str | None = None
    figures_pages_total: int | None = None
    figures_pages_done: int | None = None
    figures_progress: dict[str, object] | None = None
    figures_requested_at: datetime | None = None


class CourseStatusLessonOut(ORMModel):
    id: uuid.UUID
    lesson_structure_modified_at: datetime | None = None

    # Fase 3 — dispense
    content_status: str = "empty"
    content_progress: int = 0
    content_progress_phase: str | None = None
    content_error: str | None = None
    content_attempts: int = 0
    content_generated_at: datetime | None = None
    content_approved_at: datetime | None = None
    content_modified_at: datetime | None = None

    # Fase 4 — slide
    slides_status: str = "empty"
    slides_progress: int = 0
    slides_progress_phase: str | None = None
    slides_error: str | None = None
    slides_attempts: int = 0
    slides_generated_at: datetime | None = None
    slides_approved_at: datetime | None = None
    slides_modified_at: datetime | None = None

    # Fase 5 — discorso
    speech_status: str = "empty"
    speech_progress: int = 0
    speech_progress_phase: str | None = None
    speech_error: str | None = None
    speech_attempts: int = 0
    speech_generated_at: datetime | None = None
    speech_approved_at: datetime | None = None
    speech_modified_at: datetime | None = None

    # Export PDF (dispense, slide, discorso)
    pdf_status: str = "empty"
    pdf_progress: int = 0
    pdf_progress_phase: str | None = None
    pdf_error: str | None = None
    pdf_attempts: int = 0
    pdf_generated_at: datetime | None = None

    slides_pdf_status: str = "empty"
    slides_pdf_progress: int = 0
    slides_pdf_progress_phase: str | None = None
    slides_pdf_error: str | None = None
    slides_pdf_attempts: int = 0
    slides_pdf_generated_at: datetime | None = None

    speech_pdf_status: str = "empty"
    speech_pdf_progress: int = 0
    speech_pdf_progress_phase: str | None = None
    speech_pdf_error: str | None = None
    speech_pdf_attempts: int = 0
    speech_pdf_generated_at: datetime | None = None

    # Fase 6 / 6b — video
    video_status: str
    avatar_video_status: str


class CourseStatusModuleOut(ORMModel):
    id: uuid.UUID
    lessons_structure_status: str = "empty"
    lessons_structure_progress: int = 0
    lessons_structure_progress_phase: str | None = None
    lessons_structure_error: str | None = None
    lessons_structure_attempts: int = 0
    lessons_structure_generated_at: datetime | None = None
    lessons_structure_approved_at: datetime | None = None
    architecture_modified_at: datetime | None = None
    lessons: list[CourseStatusLessonOut] = Field(default_factory=list)


class CourseStatusOut(ORMModel):
    course_id: uuid.UUID
    status: CourseStatus
    updated_at: datetime
    architecture_progress: int = 0
    architecture_progress_phase: str | None = None
    architecture_error: str | None = None
    architecture_attempts: int = 0
    architecture_generated_at: datetime | None = None
    glossary_status: str = "empty"
    glossary_generated_at: datetime | None = None
    glossary_error: str | None = None
    documents: list[CourseStatusDocumentOut] = Field(default_factory=list)
    modules: list[CourseStatusModuleOut] = Field(default_factory=list)
