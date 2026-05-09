"""Schema Pydantic per l'architettura corso (Fase 1, §4).

Mirror dello schema JSON di prompt_generazione_corsi.md §4.3. Validato
dopo la chiamata a OpenAI prima di materializzare le righe in
`course_module` e `course_lesson`.

Validazione (§4.4):
- `len(modules) == numero_moduli`
- per ogni modulo: `len(lessons) == numero_lezioni_per_modulo`
- M1.L1 ha `is_introductory == true` e `len(recommended_bibliography) >= 1`
- tutte le altre lezioni hanno `is_introductory == false` e
  `recommended_bibliography == []`
- voci con `source = "general_knowledge_suggestion"` devono avere
  `confidence = "to_verify"`
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel


class RecommendedBibliographyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authors: str
    title: str
    publisher: str
    year: str
    note: str
    source: Literal["from_uploaded_documents", "general_knowledge_suggestion"]
    confidence: Literal["confirmed", "to_verify"]


class ArchitectureLesson(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lesson_id: str
    title: str
    summary: str
    is_introductory: bool
    recommended_bibliography: list[RecommendedBibliographyItem] = Field(
        default_factory=list
    )


class ArchitectureModule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module_id: str
    title: str
    description: str
    lessons: list[ArchitectureLesson]


class ArchitectureOutput(BaseModel):
    """Output della Fase 1, conforme al JSON Schema §4.3."""

    model_config = ConfigDict(extra="forbid")
    course_overview: str
    pedagogical_rationale: str
    modules: list[ArchitectureModule]


# ---------------------------------------------------------------------------
# Output verso il frontend
# ---------------------------------------------------------------------------


class CourseLessonOut(ORMModel):
    id: uuid.UUID
    module_id: uuid.UUID
    course_id: uuid.UUID
    position: int
    lesson_code: str
    title: str
    summary: str
    is_introductory: bool
    recommended_bibliography: list[dict[str, Any]] = Field(default_factory=list)

    # Fase 2 — struttura formativa (§5)
    learning_objectives: list[str] = Field(default_factory=list)
    mandatory_topics: list[dict[str, Any]] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    section_outline: list[dict[str, Any]] = Field(default_factory=list)

    # Fase 3 — contenuti (§6) — meta + payload AI
    content_status: str = "empty"
    content_progress: int = 0
    content_progress_phase: str | None = None
    content_error: str | None = None
    content_attempts: int = 0
    content_generated_at: datetime | None = None
    content_approved_at: datetime | None = None
    content_tokens: dict[str, Any] | None = None
    content_regeneration_hint: str | None = None
    # Stale-detection: timestamp dell'ultima modifica manuale (NON i
    # worker AI). Usati lato FE per calcolare se un downstream è
    # disallineato. Vedi `lib/staleness.ts` nel frontend.
    lesson_structure_modified_at: datetime | None = None
    content_modified_at: datetime | None = None
    # Output AI completo (verbatim §6.3) — nullable finché non generato.
    content_raw: dict[str, Any] | None = None

    # §7 — Export PDF della lezione
    pdf_status: str = "empty"
    pdf_progress: int = 0
    pdf_progress_phase: str | None = None
    pdf_error: str | None = None
    pdf_attempts: int = 0
    pdf_generated_at: datetime | None = None
    pdf_template_id: uuid.UUID | None = None
    # Path relativo (es. "{org}/{course}/{lesson}.pdf"). La UI usa
    # l'endpoint dedicato per il download anziché esporre il path raw.
    pdf_path: str | None = None

    # Fase 4 — Slide della lezione (§7) — meta + payload AI
    slides_status: str = "empty"
    slides_progress: int = 0
    slides_progress_phase: str | None = None
    slides_error: str | None = None
    slides_attempts: int = 0
    slides_generated_at: datetime | None = None
    slides_approved_at: datetime | None = None
    slides_tokens: dict[str, Any] | None = None
    slides_regeneration_hint: str | None = None
    # Stale-detection: timestamp dell'ultima modifica manuale al
    # `slides_raw`. Set da CRUD; worker AI NON lo tocca.
    slides_modified_at: datetime | None = None
    # Output AI completo (verbatim §7.3) — nullable finché non generato.
    slides_raw: dict[str, Any] | None = None

    # §7 — Export PDF delle slide
    slides_pdf_status: str = "empty"
    slides_pdf_progress: int = 0
    slides_pdf_progress_phase: str | None = None
    slides_pdf_error: str | None = None
    slides_pdf_attempts: int = 0
    slides_pdf_generated_at: datetime | None = None
    slides_pdf_template_id: uuid.UUID | None = None
    slides_pdf_path: str | None = None


class CourseModuleOut(ORMModel):
    id: uuid.UUID
    course_id: uuid.UUID
    position: int
    module_code: str
    title: str
    description: str
    lessons: list[CourseLessonOut] = Field(default_factory=list)

    # Fase 2 — meta della struttura lezioni (§5)
    lessons_structure_status: str = "empty"
    lessons_structure_progress: int = 0
    lessons_structure_progress_phase: str | None = None
    lessons_structure_error: str | None = None
    lessons_structure_attempts: int = 0
    lessons_structure_generated_at: datetime | None = None
    lessons_structure_approved_at: datetime | None = None
    lessons_structure_tokens: dict[str, Any] | None = None
    lessons_structure_regeneration_hint: str | None = None
    # Stale-detection: timestamp dell'ultima modifica manuale al modulo
    # o alle sue lezioni di architettura. Vedi `lib/staleness.ts` FE.
    architecture_modified_at: datetime | None = None


class CourseArchitectureOut(BaseModel):
    """Vista completa dell'architettura per la UI."""

    course_overview: str | None = None
    pedagogical_rationale: str | None = None
    modules: list[CourseModuleOut] = Field(default_factory=list)
    architecture_attempts: int = 0
    architecture_tokens: dict[str, Any] | None = None
    architecture_error: str | None = None
    architecture_generated_at: datetime | None = None
    architecture_regeneration_hint: str | None = None


# ---------------------------------------------------------------------------
# Input dal frontend
# ---------------------------------------------------------------------------


class CourseArchitectureGenerateInput(BaseModel):
    """Body opzionale per `POST /architecture/generate`. Se vuoto, il
    worker procede senza hint (prima generazione)."""

    regeneration_hint: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# CRUD manuale moduli/lezioni
# ---------------------------------------------------------------------------


class ModuleCreateInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)


class ModuleUpdateInput(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)


class LessonCreateInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(default="", max_length=4000)
    is_introductory: bool = False
    recommended_bibliography: list[RecommendedBibliographyItem] = Field(
        default_factory=list, max_length=20
    )


class LessonUpdateInput(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    summary: str | None = Field(default=None, max_length=4000)
    is_introductory: bool | None = None
    recommended_bibliography: list[RecommendedBibliographyItem] | None = Field(
        default=None, max_length=20
    )


class ReorderInput(BaseModel):
    """Lista di UUID nell'ordine desiderato. La lunghezza deve corrispondere
    al numero attuale di moduli/lezioni e l'insieme dev'essere identico."""

    ids: list[uuid.UUID] = Field(min_length=1)
