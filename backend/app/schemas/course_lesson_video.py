"""Schemi Pydantic per la Fase 6 — Generazione video MP4 (§9).

Il video è prodotto dal worker `course_lesson_video_worker` quando la
lezione passa per `video_status='pending' → processing → ready`. Pre-
condizioni runtime: `speech_status='approved'` AND
`slides_status='approved'` AND voice sample
(`Avatar.audio_path` dell'assegnatario corso) presente sul filesystem.

Pipeline (3 fasi):
1. TTS XTTS-v2 (60% del progress): sintesi audio per ciascun
   `LessonSpeechSegment`, voce clonata da `Avatar.audio_path`.
2. Slide PNG via Playwright (20%): viewport 1920×1080, riusa
   pre-render Mermaid SVG di Fase 4.
3. Encoding ffmpeg (20%): per ciascuna slide `-loop 1 -i slide.png -i
   audio.wav -shortest -tune stillimage -c:v libx264 -c:a aac`, poi
   concat finale `-f concat -c copy`.

Output: `/uploads/lesson_videos/{course_id}/{lesson_id}.mp4` 1080p30
H.264 + AAC 192 kbps, servito da `StaticFiles` con HTTP Range nativo.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel


# ---------------------------------------------------------------------------
# Input dal frontend
# ---------------------------------------------------------------------------


class LessonVideoGenerateInput(BaseModel):
    """Body opzionale per `POST /lessons/{lid}/video/generate` (riservato
    a future opzioni come override risoluzione/preset). Attualmente vuoto.
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Output verso il frontend (DTO read-only)
# ---------------------------------------------------------------------------


class LessonVideoStatusOut(ORMModel):
    """Stato video di una lezione singola.

    `video_url`: path pubblico (servito da StaticFiles `/uploads/...`)
    quando `video_status='ready'`, altrimenti `None`.

    `is_stale`: True se `video_generated_at < speech_modified_at` OR
    `< slides_modified_at` OR `< speech_approved_at` (cambi successivi
    all'ultima generazione). Allineato a `isSpeechPdfStale` lato FE.

    `tokens`: metadata della run (durate, device, num_*). Esposti per
    debug performance e per popolare la card UI con info contestuali.
    """

    lesson_id: str
    lesson_code: str
    status: str  # empty|pending|processing|ready|failed|cancelled
    progress: int = 0
    progress_phase: str | None = None
    video_url: str | None = None
    error: str | None = None
    attempts: int = 0
    generated_at: datetime | None = None
    tokens: dict[str, Any] | None = None
    is_stale: bool = False
    # Pre-requisiti runtime — il FE li usa per disabilitare il bottone
    # "Genera" con tooltip mirato.
    speech_approved: bool = False
    slides_approved: bool = False
    voice_sample_available: bool = False
    # Stato del pre-training latents dell'assegnatario (Fase 6 §9
    # rifinitura). True quando `Avatar.tts_latents_status == 'ready'`.
    voice_latents_ready: bool = False
    # Status raw per la UI: 'pending'|'processing'|'ready'|'failed'|None.
    voice_latents_status: str | None = None


class LessonVideoMetaOut(ORMModel):
    """Meta video di lezione esposto in `CourseLessonOut` per indici."""

    video_status: str
    video_progress: int = 0
    video_progress_phase: str | None = None
    video_path: str | None = None
    video_error: str | None = None
    video_attempts: int = 0
    video_generated_at: datetime | None = None
    video_tokens: dict[str, Any] | None = None


class LessonVideoBatchOut(BaseModel):
    """Snapshot batch a livello corso (`GET /courses/{cid}/video/status`).

    Aggregato pronto per la pagina indice: il FE non deve calcolare
    aggregati lato client.
    """

    items: list[LessonVideoStatusOut] = Field(default_factory=list)
    total: int = 0
    ready_count: int = 0
    processing_count: int = 0
    pending_count: int = 0
    failed_count: int = 0
    # Lezioni eleggibili = speech_approved AND slides_approved AND
    # voice_sample_available. Il bottone "Genera tutti" usa questo count.
    eligible_count: int = 0
    aggregate_progress: int = 0  # 0-100, media pesata su lezioni in flight
