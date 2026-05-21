r"""Schemi Pydantic per la Fase 5 — Discorso temporizzato (§8).

Mirror dello schema JSON di `prompt_generazione_corsi.md` §8.4 (output
dell'AI per lezione) + tipi di input per gli endpoint CRUD manuale e di
trigger generazione/approve.

Validazione (§8.5) è in
`course_lesson_speech_service.materialize_lesson_speech`:
- ogni `slide_id` referenziato esiste in `slides_raw.slides`
- ogni slide di Fase 4 ha almeno un segmento associato
- `segment_id` univoci a livello di lezione
- `sum(estimated_duration_seconds)`: se fuori da ±5% dal target le
  durate vengono riscalate sul target (`target =
  course.lesson_duration_minutes × 60`); hard-fail solo per deriva
  estrema (oltre ±50%)
- word count coerente con duration × wpm (130 it / 150 en) ±15%
- testo TTS-safe: niente `*` `_` `` ` `` `#` `\` `$`, niente
  abbreviazioni note (`es.`, `etc.`, `ca.`, `p.es.`, `i.e.`, `e.g.`),
  niente LaTeX (`\frac`, `\sum`, ...)
- `slide_to_segments_map` coerente con `speech_segments`
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel


# ---------------------------------------------------------------------------
# Output AI (§8.4) — validato dopo la chiamata OpenAI
# ---------------------------------------------------------------------------


class LessonSpeechSegment(BaseModel):
    """Un segmento di parlato sincronizzato a una slide (§8.4)."""

    model_config = ConfigDict(extra="forbid")
    segment_id: str = Field(min_length=1, max_length=50)
    slide_id: str = Field(min_length=1, max_length=50)
    text: str = Field(min_length=1)
    estimated_duration_seconds: int = Field(ge=1, le=600)
    delivery_notes: str = Field(default="", max_length=500)


class LessonSlideSegmentsMapEntry(BaseModel):
    """Mapping slide → segmenti per sincronizzazione (§8.4)."""

    model_config = ConfigDict(extra="forbid")
    slide_id: str = Field(min_length=1, max_length=50)
    segment_ids: list[str] = Field(min_length=1)
    slide_total_duration_seconds: int = Field(ge=1, le=3600)


class LessonSpeechOutput(BaseModel):
    """Output AI per una singola lezione (§8.4)."""

    model_config = ConfigDict(extra="forbid")
    lesson_id: str
    language: str = Field(min_length=2, max_length=10)
    target_duration_seconds: int = Field(ge=1, le=10800)
    estimated_total_duration_seconds: int = Field(ge=1, le=10800)
    estimated_total_word_count: int = Field(ge=1)
    speech_segments: list[LessonSpeechSegment] = Field(min_length=1)
    slide_to_segments_map: list[LessonSlideSegmentsMapEntry] = Field(
        min_length=1
    )


# ---------------------------------------------------------------------------
# Input dal frontend
# ---------------------------------------------------------------------------


class LessonSpeechGenerateInput(BaseModel):
    """Body opzionale per `POST /lessons/{lid}/speech/generate`
    e `POST /lessons-speech/generate-all`. Vuoto = prima generazione."""

    regeneration_hint: str | None = Field(default=None, max_length=2000)


class LessonSpeechUpdateInput(BaseModel):
    """Body per `PATCH /lessons/{lid}/speech` (CRUD manuale).

    Tutti i campi sono opzionali. Edit non degrada lo status (`approved`
    resta `approved`). Validazione di consistenza in CRUD service.
    """

    model_config = ConfigDict(extra="forbid")

    speech_segments: list[LessonSpeechSegment] | None = None
    slide_to_segments_map: list[LessonSlideSegmentsMapEntry] | None = None


# ---------------------------------------------------------------------------
# Output verso il frontend (DTO read-only)
# ---------------------------------------------------------------------------


class LessonSpeechOut(ORMModel):
    """Sub-DTO con il payload Fase 5 di `course_lesson` (parsa
    `speech_raw`). Tutti i campi opzionali per consentire serializzazione
    anche con speech_raw=None."""

    lesson_id: str | None = None
    language: str | None = None
    target_duration_seconds: int | None = None
    estimated_total_duration_seconds: int | None = None
    estimated_total_word_count: int | None = None
    speech_segments: list[dict[str, Any]] = Field(default_factory=list)
    slide_to_segments_map: list[dict[str, Any]] = Field(default_factory=list)


class LessonSpeechMetaOut(ORMModel):
    """Meta della lezione per Fase 5 — esposto in `CourseLessonOut`."""

    speech_status: str
    speech_progress: int = 0
    speech_progress_phase: str | None = None
    speech_error: str | None = None
    speech_attempts: int = 0
    speech_generated_at: datetime | None = None
    speech_approved_at: datetime | None = None
    speech_tokens: dict[str, Any] | None = None
    speech_regeneration_hint: str | None = None
    speech_modified_at: datetime | None = None
