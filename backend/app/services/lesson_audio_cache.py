"""Cache su disco dell'audio TTS delle lezioni.

L'audio sintetizzato da RunPod (un array float32 mono per segment) viene
salvato come WAV sotto
`{upload_root}/lesson_audio/{course_id}/{lesson_id}/`: un file
`seg_NNN.wav` per segment più un `manifest.json` con la chiave di cache.

Alla rigenerazione del video, se il discorso (testo dei segment), la
lingua e il campione vocale non sono cambiati, l'audio viene ricaricato
dalla cache invece di richiamare RunPod — niente costo GPU, niente
attesa. La chiave di cache è un hash di quegli input: la cache si
invalida da sola appena qualcosa cambia.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("app.lesson_audio_cache")

# Sample rate nativo XTTS — i WAV in cache sono mono a questa frequenza.
SAMPLE_RATE = 24000
_MANIFEST_NAME = "manifest.json"


def _cache_dir(course_id: uuid.UUID, lesson_id: uuid.UUID) -> Path:
    return (
        get_settings().upload_root
        / "lesson_audio"
        / str(course_id)
        / str(lesson_id)
    )


def compute_cache_key(
    *,
    speech_raw: dict[str, Any],
    voice_sample_path: Path,
    language_code: str,
) -> str:
    """Hash SHA-256 che identifica l'audio: testo dei segment (ordinati
    per id), lingua e contenuto del campione vocale. Se uno qualunque di
    questi cambia, la chiave cambia e la cache non è più valida."""
    h = hashlib.sha256()
    pairs = sorted(
        (str(s.get("segment_id") or ""), str(s.get("text") or ""))
        for s in (speech_raw.get("speech_segments") or [])
        if isinstance(s, dict)
    )
    h.update(json.dumps(pairs, ensure_ascii=False).encode("utf-8"))
    h.update((language_code or "").encode("utf-8"))
    try:
        h.update(Path(voice_sample_path).read_bytes())
    except OSError:  # pragma: no cover — campione mancante: chiave parziale
        pass
    return h.hexdigest()


def load(
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    *,
    cache_key: str,
) -> dict[str, np.ndarray] | None:
    """Ricarica l'audio dalla cache se il manifest combacia con
    `cache_key`. Ritorna None se la cache è assente, di una versione
    diversa, incompleta o corrotta — il chiamante risintetizza."""
    cdir = _cache_dir(course_id, lesson_id)
    manifest_path = cdir / _MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    try:
        meta = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover
        return None
    if not isinstance(meta, dict) or meta.get("key") != cache_key:
        return None
    segment_ids = meta.get("segment_ids") or []
    if not segment_ids:
        return None
    audio: dict[str, np.ndarray] = {}
    for i, sid in enumerate(segment_ids):
        wav = cdir / f"seg_{i:03d}.wav"
        if not wav.is_file():
            return None  # cache incompleta → non valida
        try:
            data, _sr = sf.read(str(wav), dtype="float32")
        except Exception:  # pragma: no cover
            return None
        arr = np.asarray(data, dtype=np.float32)
        if arr.ndim > 1:
            arr = arr[:, 0]
        audio[str(sid)] = arr.reshape(-1)
    return audio


def save(
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    *,
    cache_key: str,
    audio_per_segment: dict[str, np.ndarray],
) -> None:
    """Salva l'audio (un WAV PCM_16 per segment) + manifest, sovrascrivendo
    qualunque cache precedente della lezione."""
    cdir = _cache_dir(course_id, lesson_id)
    shutil.rmtree(cdir, ignore_errors=True)
    cdir.mkdir(parents=True, exist_ok=True)
    segment_ids = list(audio_per_segment.keys())
    for i, sid in enumerate(segment_ids):
        sf.write(
            str(cdir / f"seg_{i:03d}.wav"),
            np.asarray(audio_per_segment[sid], dtype=np.float32),
            SAMPLE_RATE,
            subtype="PCM_16",
        )
    (cdir / _MANIFEST_NAME).write_text(
        json.dumps(
            {"key": cache_key, "segment_ids": segment_ids},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    log.info(
        "lesson_audio_cache_saved",
        course_id=str(course_id),
        lesson_id=str(lesson_id),
        segments=len(segment_ids),
    )
