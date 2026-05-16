"""Servizio per il pre-training silenzioso dei conditioning latents XTTS.

Workflow:
1. Utente carica/aggiorna `Avatar.audio_path` dalla UI.
2. L'endpoint upload audio chiama `mark_latents_pending(avatar)` per
   resettare lo stato + cancellare i vecchi `.pt` (se presenti).
3. Il worker `avatar_tts_latents_worker` (poll periodico) trova avatar
   con `tts_latents_status='pending'` AND `audio_path IS NOT NULL`, e
   chiama `extract_latents_for_avatar(avatar)` che:
   - Risolve il path filesystem del campione audio.
   - Carica XTTSService (singleton, modello già in memoria dopo primo run).
   - Estrae `gpt_cond_latent + speaker_embedding` con i parametri di
     config del modello (vedi `XTTSService.extract_latents`).
   - Salva in `<upload_root>/avatars/{user_id}/tts_latents.pt`.
   - Aggiorna lo stato avatar a `ready` + path + timestamp.

Su failure: status `failed` + `tts_latents_error`. Niente auto-retry —
l'estrazione è deterministica: se fallisce, fallisce ogni volta (l'utente
deve ri-caricare un audio diverso). Il worker video bloccherà la
generazione con errore esplicito `voice_latents_not_ready`.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.avatar import Avatar
from app.services import xtts_voice_clone_service

log = get_logger("app.avatar_tts_latents")


# Subdir relativa sotto `upload_root`. La radice `avatars` è già in
# `_ALLOWED_SUBDIR_ROOTS` di `file_service.py`.
def _latents_relative_path(user_id: str) -> str:
    return f"avatars/{user_id}/tts_latents.pt"


def latents_absolute_path(user_id: str) -> Path:
    settings = get_settings()
    return (settings.upload_root / _latents_relative_path(user_id)).resolve()


def _public_audio_to_filesystem(audio_path_public: str) -> Path | None:
    """Convert `/uploads/avatars/foo.webm` → Path filesystem assoluto.
    Ritorna None se il path non è in /uploads/ o il file non esiste."""
    if not audio_path_public:
        return None
    settings = get_settings()
    rel = audio_path_public
    if rel.startswith("/uploads/"):
        rel = rel.removeprefix("/uploads/")
    target = (settings.upload_root / rel).resolve()
    try:
        target.relative_to(settings.upload_root.resolve())
    except ValueError:  # pragma: no cover
        return None
    return target if target.is_file() else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def mark_latents_pending(avatar: Avatar) -> None:
    """Reset dello stato latents quando l'utente carica/cambia l'audio.

    Non committa — il caller deve fare `await db.commit()`. Best-effort
    `os.unlink` del file .pt precedente (se esisteva).
    """
    invalidate_latents(avatar)
    avatar.tts_latents_status = "pending"
    avatar.tts_latents_path = None
    avatar.tts_latents_generated_at = None
    avatar.tts_latents_error = None


def invalidate_latents(avatar: Avatar) -> None:
    """Cancella il file `.pt` (se presente) e resetta path/status.

    Idempotente: no-op se il file non esiste. Path safety: controlla che
    sia sotto `upload_root/avatars/`.
    """
    if avatar.tts_latents_path:
        settings = get_settings()
        rel = avatar.tts_latents_path
        if rel.startswith("/uploads/"):
            rel = rel.removeprefix("/uploads/")
        target = (settings.upload_root / rel).resolve()
        try:
            target.relative_to(
                (settings.upload_root / "avatars").resolve()
            )
        except ValueError:
            log.warning(
                "avatar_latents_path_outside_avatars",
                path=avatar.tts_latents_path,
            )
            return
        try:
            if target.is_file():
                target.unlink()
                log.info("avatar_latents_unlinked", path=str(target))
        except OSError as exc:  # pragma: no cover
            log.warning(
                "avatar_latents_unlink_failed",
                path=str(target),
                error=str(exc),
            )


async def extract_latents_for_avatar(avatar: Avatar) -> str:
    """Estrae e salva i latents per un avatar. Ritorna il path relativo
    `avatars/{user_id}/tts_latents.pt` (da assegnare a
    `avatar.tts_latents_path`).

    Pre-condizioni (caller responsability):
    - `avatar.audio_path` non None
    - file fisico esiste su disco
    - durata audio >= 6s (validato all'upload via `file_service`)
    """
    if not avatar.audio_path:
        raise xtts_voice_clone_service.XTTSVoiceSampleError(
            "Avatar senza audio_path — impossibile estrarre latents."
        )
    voice_sample_path = _public_audio_to_filesystem(avatar.audio_path)
    if voice_sample_path is None:
        raise xtts_voice_clone_service.XTTSVoiceSampleError(
            f"File audio non trovato sul filesystem: {avatar.audio_path}"
        )
    rel = _latents_relative_path(str(avatar.user_id))
    abs_path = latents_absolute_path(str(avatar.user_id))
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    await xtts_voice_clone_service.extract_latents_to_file(
        voice_sample_path, abs_path
    )
    return rel


def mark_latents_ready(avatar: Avatar, *, relative_path: str) -> None:
    """Set finale post-extract success. Caller fa commit."""
    avatar.tts_latents_path = relative_path
    avatar.tts_latents_status = "ready"
    avatar.tts_latents_generated_at = datetime.now(UTC)
    avatar.tts_latents_error = None


def mark_latents_failed(avatar: Avatar, *, error: str) -> None:
    """Set finale post-extract failure. Caller fa commit."""
    avatar.tts_latents_status = "failed"
    avatar.tts_latents_error = error[:500]
    avatar.tts_latents_path = None
    avatar.tts_latents_generated_at = None
