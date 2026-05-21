"""Schemi Pydantic per la scheda "Video con Avatar" (§9b).

Il "Video con Avatar" prende il video MP4 già generato della lezione
(Fase 6, `video_status='ready'`) e ci sovrappone in basso a destra un
avatar parlante con lip-sync MuseTalk, sincronizzato sull'audio del
video stesso (l'audio viene estratto dal video → sync garantita).

Prodotto dal worker `course_lesson_avatar_video_worker` quando la
lezione passa per `avatar_video_status='pending' → processing → ready`.
Pre-condizioni runtime: `video_status='ready'` AND l'avatar
dell'assegnatario del corso ha almeno una clip MiniMax pronta.

Pipeline (3 fasi):
1. Preparazione (10%): estrazione della traccia audio dal video MP4
   già generato della lezione.
2. Lip-sync MuseTalk (75%): subprocess `synth_random_lipsync` —
   campiona le clip MiniMax dell'avatar, le invia a RunPod insieme
   all'audio, scarica il video di avatar parlante.
3. Overlay ffmpeg (15%): sovrappone l'avatar (quadrato, in basso a
   destra) al video della lezione, conservandone la traccia audio.

Output: `/uploads/lesson_avatar_videos/{course_id}/{lesson_id}.mp4`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel


# ---------------------------------------------------------------------------
# Input dal frontend
# ---------------------------------------------------------------------------


class LessonAvatarVideoGenerateInput(BaseModel):
    """Body opzionale per `POST .../avatar-video/generate` (riservato a
    future opzioni). Attualmente vuoto."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Output verso il frontend (DTO read-only)
# ---------------------------------------------------------------------------


class LessonAvatarVideoStatusOut(ORMModel):
    """Stato del video con avatar di una lezione singola.

    `video_url`: path pubblico (servito da StaticFiles `/uploads/...`)
    quando `status='ready'`, altrimenti `None`.

    `is_stale`: True se il video della lezione è stato rigenerato dopo
    l'ultima generazione del video con avatar
    (`avatar_video_generated_at < video_generated_at`).
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
    # "Genera" con un tooltip mirato.
    lesson_video_ready: bool = False
    avatar_clips_ready: bool = False


class LessonAvatarVideoBatchOut(BaseModel):
    """Snapshot batch a livello corso (`GET .../avatar-video/status`).

    Aggregato pronto per la scheda "Video con Avatar": il FE non deve
    calcolare aggregati lato client.
    """

    items: list[LessonAvatarVideoStatusOut] = Field(default_factory=list)
    total: int = 0
    ready_count: int = 0
    processing_count: int = 0
    pending_count: int = 0
    failed_count: int = 0
    # Lezioni eleggibili = video della lezione `ready` AND avatar con
    # clip pronte. Il bottone "Genera tutti" usa questo count.
    eligible_count: int = 0
    aggregate_progress: int = 0  # 0-100, media pesata su lezioni in flight
    # Stato dell'avatar dell'assegnatario del corso (course-level): se
    # `false` nessuna lezione è eleggibile.
    avatar_clips_ready: bool = False
