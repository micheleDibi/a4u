"""Schemi Pydantic per la Fase 2 — Struttura delle lezioni (§5).

Mirror dello schema JSON di `prompt_generazione_corsi.md` §5.3 (output
dell'AI per modulo) + tipi di input per gli endpoint CRUD manuale e di
trigger generazione/approve.

Validazione (§5.4):
- numero lezioni in output == numero lezioni del modulo in input
- tutti gli `objective` iniziano con "Lo studente sarà in grado di..."
  (IT) / "The student will be able to..." (EN); enforcement morbido qui
  (warning) — controllo stretto in `course_lesson_structure_service`
- l'unione di `covers_topic_ids` su tutte le sezioni copre tutti i
  `topic_id` di `mandatory_topics` (per ogni lezione)
- tutti i `covers_topic_ids` referenziano `topic_id` esistenti
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import ORMModel


# ---------------------------------------------------------------------------
# Output AI (§5.3) — validato dopo la chiamata OpenAI
# ---------------------------------------------------------------------------


class LessonStructureMandatoryTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic_id: str = Field(min_length=1, max_length=20)
    topic: str = Field(min_length=1, max_length=400)
    rationale: str = Field(min_length=1, max_length=1000)


class LessonStructureSectionOutline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_id: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=1000)
    covers_topic_ids: list[str] = Field(default_factory=list, max_length=20)


class LessonStructureLessonOutput(BaseModel):
    """Output AI per una singola lezione (§5.3 lessons[*])."""

    model_config = ConfigDict(extra="forbid")
    lesson_id: str
    title: str
    is_introductory: bool
    learning_objectives: list[str] = Field(min_length=3, max_length=6)
    mandatory_topics: list[LessonStructureMandatoryTopic] = Field(
        min_length=3, max_length=7
    )
    prerequisites: list[str] = Field(default_factory=list, max_length=20)
    section_outline: list[LessonStructureSectionOutline] = Field(
        min_length=3, max_length=7
    )

    @field_validator("learning_objectives")
    @classmethod
    def _strip_objectives(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]

    @field_validator("prerequisites")
    @classmethod
    def _strip_prerequisites(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]


class LessonStructureModuleOutput(BaseModel):
    """Output AI per un singolo modulo (§5.3)."""

    model_config = ConfigDict(extra="forbid")
    module_id: str
    lessons: list[LessonStructureLessonOutput]


# ---------------------------------------------------------------------------
# Input dal frontend
# ---------------------------------------------------------------------------


class LessonsStructureGenerateInput(BaseModel):
    """Body opzionale per `POST /modules/{id}/lessons-structure/generate`
    e `POST /lessons-structure/generate-all`. Se vuoto, il worker
    procede senza hint (prima generazione)."""

    regeneration_hint: str | None = Field(default=None, max_length=2000)


class LessonStructureUpdateInput(BaseModel):
    """Body per `PATCH /lessons/{id}/structure` (CRUD manuale).

    Tutti i campi sono opzionali: l'utente può aggiornare solo le
    sezioni che gli interessano. La validazione di consistenza
    (covers_topic_ids referenziati, ID univoci) è in service.
    """

    model_config = ConfigDict(extra="forbid")

    learning_objectives: list[str] | None = Field(default=None, max_length=10)
    mandatory_topics: list[LessonStructureMandatoryTopic] | None = Field(
        default=None, max_length=10
    )
    prerequisites: list[str] | None = Field(default=None, max_length=20)
    section_outline: list[LessonStructureSectionOutline] | None = Field(
        default=None, max_length=10
    )

    @field_validator("learning_objectives")
    @classmethod
    def _validate_objectives(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        cleaned = [s.strip() for s in v if s and s.strip()]
        if not cleaned:
            raise ValueError("Almeno un obiettivo formativo è richiesto")
        return cleaned

    @field_validator("prerequisites")
    @classmethod
    def _strip_prerequisites(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return [s.strip() for s in v if s and s.strip()]


# ---------------------------------------------------------------------------
# Output verso il frontend (DTO read-only)
# ---------------------------------------------------------------------------


class LessonStructureLessonOut(ORMModel):
    """Sub-DTO con i 4 campi JSONB di `course_lesson` per Fase 2.
    Usato come embed in `CourseLessonOut`."""

    learning_objectives: list[str] = Field(default_factory=list)
    mandatory_topics: list[dict[str, Any]] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    section_outline: list[dict[str, Any]] = Field(default_factory=list)


class LessonStructureModuleMetaOut(ORMModel):
    """Meta del modulo per Fase 2 — esposto in `CourseModuleOut`."""

    lessons_structure_status: str
    lessons_structure_progress: int = 0
    lessons_structure_progress_phase: str | None = None
    lessons_structure_error: str | None = None
    lessons_structure_attempts: int = 0
    lessons_structure_generated_at: datetime | None = None
    lessons_structure_approved_at: datetime | None = None
    lessons_structure_tokens: dict[str, Any] | None = None
    lessons_structure_regeneration_hint: str | None = None
