"""Lingue supportate da XTTS-v2 — costanti pure, nessuna dipendenza pesante.

Estratto da `xtts_voice_clone_service.py` (rimosso con la migrazione del
TTS su RunPod): qui restano solo la lista delle lingue e la
normalizzazione del codice, usate per validare `course.video_language_code`
lato API. Il modello XTTS-v2 gira su RunPod ma il set di lingue supportate
e' lo stesso (dipende dai pesi del modello, non da dove gira).

Lista TASSATIVAMENTE allineata a `clone_voice.py` dello script di
riferimento (16 lingue, NO `hi`).
"""
from __future__ import annotations

XTTS_SUPPORTED_LANGUAGES: frozenset[str] = frozenset(
    {
        "it", "en", "es", "fr", "de", "pt", "pl", "tr",
        "ru", "nl", "cs", "ar", "zh-cn", "ja", "hu", "ko",
    }
)


def normalize_language_code(language_code: str) -> str:
    """Normalizza un codice lingua per XTTS-v2.

    - lowercase
    - rimuove il country code (`it-IT` -> `it`)
    - speciale `zh*` -> `zh-cn`
    - se non in `XTTS_SUPPORTED_LANGUAGES`, fallback a `it`
    """
    code = (language_code or "it").strip().lower()
    if code.startswith("zh"):
        return "zh-cn"
    if "-" in code:
        code = code.split("-")[0]
    if code not in XTTS_SUPPORTED_LANGUAGES:
        return "it"
    return code


def is_language_supported(code: str | None) -> bool:
    """True se `code` (post-normalize) e' in `XTTS_SUPPORTED_LANGUAGES`."""
    if not code:
        return False
    return normalize_language_code(code) in XTTS_SUPPORTED_LANGUAGES
