"""Schemi Pydantic per la Fase 4 — Slide della lezione (§7).

Mirror dello schema JSON di `prompt_generazione_corsi.md` §7.3 (output
dell'AI per lezione) + tipi di input per gli endpoint CRUD manuale e di
trigger generazione/approve.

Validazione (§7.4) è in `course_lesson_slides_service.materialize_lesson_slides`:
- `total_slides == len(slides)`
- `slide_number` univoci e sequenziali 1..N
- `total_slides` rientra nel range atteso per `minuti_per_lezione`
  (con tolleranza ±20%)
- ogni `references_assets` punta a un asset esistente in
  `lesson.content_raw.{visual_assets,tables,equations,examples}`
  OPPURE in `output.new_assets`
- ogni `source_section_id` (se non vuoto) referenzia una sezione di
  `lesson.content_raw.sections`
- ogni `section.section_id` di Fase 3 è referenziato da almeno una slide
  (soft warning, non bloccante)
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel
from app.schemas.course_lesson_content import (
    LessonContentEquation,
    LessonContentExample,
    LessonContentTable,
)


# ---------------------------------------------------------------------------
# Enum dei tipi slide (§7.1 punto 6)
# ---------------------------------------------------------------------------


SlideType = Literal[
    "title",
    "agenda",
    "prerequisites",
    "concept",
    "definition",
    "diagram",
    "formula",
    "table",
    "example",
    "case_study",
    "exercise",
    "discussion",
    "summary",
    "takeaways",
    "references",
    "bibliography",
]


# ---------------------------------------------------------------------------
# Output AI (§7.3) — validato dopo la chiamata OpenAI
# ---------------------------------------------------------------------------


class LessonSlideItem(BaseModel):
    """Una slide singola (§7.3 slides[*])."""

    model_config = ConfigDict(extra="forbid")
    slide_number: int = Field(ge=1)
    slide_id: str = Field(min_length=1, max_length=50)
    type: SlideType
    title: str = Field(min_length=1, max_length=300)
    # `body`: prosa breve (1-3 frasi) usata come subtitle/descrizione
    # discorsiva. Pensata per evitare slide tutte-bullet che
    # appesantiscono la lettura. Vuota per slide molto schematiche.
    body: str = Field(default="", max_length=600)
    bullets: list[str] = Field(default_factory=list, max_length=10)
    references_assets: list[str] = Field(default_factory=list, max_length=20)
    # Vuoto per slide strutturali (title/agenda/transition).
    source_section_id: str = Field(default="", max_length=50)


class LessonSlideNewAsset(BaseModel):
    """Asset creato dalla Fase 4 (NON presente nelle Dispense / Fase 3).

    Es. una slide di sintesi che richiede uno schema riepilogativo non
    già prodotto nel testo. asset_id deve avere prefisso che evita
    collisioni con quelli delle Dispense (suggerito: `*_new_*`).

    Allineato a `LessonContentVisualAsset`: stessi `format` (Mermaid +
    immagine caricata + legacy read-only) e `extra="ignore"` per
    tollerare il vecchio campo `asset_type` ancora prodotto dall'AI ma
    non più usato in rendering.
    """

    model_config = ConfigDict(extra="ignore")
    asset_id: str = Field(min_length=1, max_length=50)
    format: Literal[
        "mermaid",
        "image",
        # — legacy, read-only —
        "image_prompt",
        "image_search_query",
        "description",
    ]
    content: str = Field(min_length=1)
    caption: str = Field(default="", max_length=600)
    alt_text: str = Field(default="", max_length=400)


class LessonSlidesOutput(BaseModel):
    """Output AI per una singola lezione (§7.3)."""

    model_config = ConfigDict(extra="forbid")
    lesson_id: str
    total_slides: int = Field(ge=1)
    slides: list[LessonSlideItem] = Field(min_length=1)
    new_assets: list[LessonSlideNewAsset] = Field(default_factory=list)
    # Asset NUOVI non visivi creati in Fase 4 (parità con le Dispense):
    # stessi modelli di LessonContent*. Default vuoti per retro-compat.
    new_tables: list[LessonContentTable] = Field(default_factory=list)
    new_equations: list[LessonContentEquation] = Field(default_factory=list)
    new_examples: list[LessonContentExample] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Input dal frontend
# ---------------------------------------------------------------------------


class LessonSlidesGenerateInput(BaseModel):
    """Body opzionale per `POST /lessons/{lid}/slides/generate`
    e `POST /lessons-slides/generate-all`. Vuoto = prima generazione."""

    regeneration_hint: str | None = Field(default=None, max_length=2000)


class LessonSlidesUpdateInput(BaseModel):
    """Body per `PATCH /lessons/{lid}/slides` (CRUD manuale).

    Tutti i campi sono opzionali. Edit non degrada lo status (`approved`
    resta `approved`). Validazione di consistenza in CRUD service.
    """

    model_config = ConfigDict(extra="forbid")

    slides: list[LessonSlideItem] | None = None
    new_assets: list[LessonSlideNewAsset] | None = None
    new_tables: list[LessonContentTable] | None = None
    new_equations: list[LessonContentEquation] | None = None
    new_examples: list[LessonContentExample] | None = None


# ---------------------------------------------------------------------------
# Output verso il frontend (DTO read-only)
# ---------------------------------------------------------------------------


class LessonSlidesOut(ORMModel):
    """Sub-DTO con il payload Fase 4 di `course_lesson` (parsa
    `slides_raw`). Usato eventualmente come embed in `CourseLessonOut`.
    Tutti i campi opzionali per consentire serializzazione anche con
    slides_raw=None."""

    total_slides: int | None = None
    slides: list[dict[str, Any]] = Field(default_factory=list)
    new_assets: list[dict[str, Any]] = Field(default_factory=list)
    new_tables: list[dict[str, Any]] = Field(default_factory=list)
    new_equations: list[dict[str, Any]] = Field(default_factory=list)
    new_examples: list[dict[str, Any]] = Field(default_factory=list)


class LessonSlidesMetaOut(ORMModel):
    """Meta della lezione per Fase 4 — esposto in `CourseLessonOut`."""

    slides_status: str
    slides_progress: int = 0
    slides_progress_phase: str | None = None
    slides_error: str | None = None
    slides_attempts: int = 0
    slides_generated_at: datetime | None = None
    slides_approved_at: datetime | None = None
    slides_tokens: dict[str, Any] | None = None
    slides_regeneration_hint: str | None = None
    slides_modified_at: datetime | None = None
