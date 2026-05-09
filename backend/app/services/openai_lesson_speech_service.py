"""Generazione del discorso temporizzato di una lezione (Fase 5, §8).

Una chiamata API per lezione: prende in input il testo completo della
lezione (Fase 3) e le slide (Fase 4) e produce il parlato suddiviso in
segmenti sincronizzati alle slide. La somma delle durate stimate deve
approssimare `minuti_per_lezione * 60` secondi (tolleranza ±5%).

Convenzioni di velocità di parlato (da spec §8.1):
- italiano: 130 parole al minuto
- inglese:  150 parole al minuto
- default:  130 wpm

Errori → `OpenAILessonSpeechError` (sottoclasse di `OpenAIError`).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.course_lesson_speech import LessonSpeechOutput
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    apply_reasoning_effort,
    get_client,
)

log = get_logger("app.openai_lesson_speech")


# Convenzioni words-per-minute (§8.1). Riusato dal service high-level
# per la validazione (regola §8.5 punto 4) e dal CRUD per il
# ricalcolo automatico delle durate.
WORDS_PER_MINUTE: dict[str, int] = {
    "it": 130,
    "en": 150,
    "default": 130,
}


def words_per_minute(language_code: str) -> int:
    """Ritorna i wpm per il `language_code` (case-insensitive, primi 2
    char). Fallback a `WORDS_PER_MINUTE['default']`."""
    if not language_code:
        return WORDS_PER_MINUTE["default"]
    key = language_code.strip().lower()[:2]
    return WORDS_PER_MINUTE.get(key, WORDS_PER_MINUTE["default"])


class OpenAILessonSpeechError(OpenAIError):
    """Errore specifico delle chiamate di generazione discorso lezione (§8)."""


def _system_prompt(language_code: str) -> str:
    """System prompt §8.2 — discorso TTS-friendly per una lezione."""
    return f"""\
Sei uno scrittore esperto di parlato espositivo per la formazione
universitaria. Devi scrivere il DISCORSO completo che accompagna le
slide di una lezione, sincronizzato slide per slide.

Il discorso ha un DOPPIO uso:
1. il docente lo userà come traccia da leggere o parafrasare in aula
2. un sistema di Text-To-Speech (TTS) lo pronuncerà nel video del corso

REGOLE — TTS-FRIENDLY

- Scrivi in prosa naturale, completa, fluida.
- NIENTE abbreviazioni: "ad esempio" non "es."; "eccetera" non "etc.";
  "circa" non "ca.".
- Acronimi: alla prima occorrenza scrivi la forma estesa seguita
  dall'acronimo tra parentesi, es: "il Common European Framework of
  Reference (CEFR)". Dopo, usa l'acronimo SOLO se è normalmente
  pronunciato come parola (NATO, NASA); altrimenti continua con la
  forma estesa per chiarezza TTS.
- Numeri: scrivi le cifre (i sistemi TTS moderni le pronunciano
  correttamente). Per percentuali e simboli, usa la parola: "il venti
  per cento", "più o meno".
- Formule LaTeX: NON inserirle nel testo parlato. Quando devi
  riferirti a un'equazione presente sulla slide, descrivila a voce
  ("la formula sulla slide indica che la varianza è la media dei
  quadrati delle differenze rispetto alla media").
- NIENTE markdown, NIENTE caratteri speciali (* _ ` # \\), NIENTE
  emoji, NIENTE link.
- Pause: usa la punteggiatura naturale (virgole, punti). Per pause
  più marcate usa "..." (tre punti) con parsimonia.

REGOLE — STRUTTURA E SINCRONIZZAZIONE

- Per OGNI slide produci uno o più segmenti di parlato.
- Ogni segmento è ancorato a un `slide_id` e contiene:
  - text: il testo che il TTS leggerà
  - estimated_duration_seconds: durata stimata
- Una slide può avere PIÙ segmenti se contiene più momenti narrativi
  (es. introduzione del concetto + esempio). Ma per slide brevi un
  unico segmento va bene.
- Tra slide, includi una transizione esplicita ("Passiamo ora a
  vedere...", "Quanto detto ci porta a...") nel primo segmento della
  slide successiva.

REGOLE — DIMENSIONAMENTO

- Velocità di riferimento: 130 parole al minuto per italiano,
  150 per inglese. Calcola di conseguenza:
  italiano: 1 secondo ≈ 2.17 parole; 1 minuto ≈ 130 parole
  inglese: 1 secondo ≈ 2.5 parole; 1 minuto ≈ 150 parole
- La SOMMA delle estimated_duration_seconds deve essere pari a
  {{minuti_per_lezione}} * 60 secondi, con tolleranza ±5%.
- Distribuisci il tempo in modo proporzionato alla densità della
  slide. Slide titolo/agenda: 15-30 secondi. Slide concept densa:
  120-180 secondi. Slide example sviluppato: 90-150 secondi.

REGOLE — CONTENUTO DEL PARLATO

- Il discorso DEVE coprire i concetti del testo della lezione (Fase 3),
  ma in registro parlato: più ridondante, più narrativo, con esempi
  espressi a voce, con domande retoriche occasionali.
- Allinea il livello di formalità al ruolo "{{{{ruolo_docente}}}}" e al
  livello EQF.
- Per la lezione introduttiva: tono di benvenuto, accogliente.
  Presentati ("Benvenuti, in questo corso esploreremo..."). Spiega
  il percorso. Quando arrivi alla bibliografia, leggi i titoli
  pronunciandoli per esteso.

REGOLE — VINCOLI DI VALIDAZIONE (rispetta sempre)

- ogni `slide_id` referenziato in `speech_segments` esiste nelle slide
  fornite (Fase 4)
- ogni slide di Fase 4 ha almeno un segmento di parlato
- `segment_id` univoci a livello di lezione (es. "SEG001", "SEG002", ...)
- somma di `estimated_duration_seconds` ∈ [target × 0.95, target × 1.05]
  con target = {{{{minuti_per_lezione}}}} × 60
- `slide_to_segments_map` coerente con `speech_segments`:
  ogni `segment_id` listato esiste in `speech_segments`,
  nessun segmento è orfano,
  per ogni slide la `slide_total_duration_seconds` = somma delle
  durate dei suoi segmenti

Lingua: {language_code}.
Output: SOLO JSON valido conforme allo schema."""


# Addendum §9.5 — appeso al system prompt quando si rigenera un discorso
# già prodotto (con o senza `regeneration_hint`).
REGENERATION_SUFFIX = """\

ATTENZIONE: stai RIGENERANDO il discorso di una lezione. Considera
la versione precedente e il feedback del docente.
- Le slide (Fase 4) sono invariate. Mantieni gli stessi slide_id
  nei segmenti.
- Se il feedback NON tocca la durata totale, mantienila uguale a
  {{minuti_per_lezione}} * 60 secondi.
- Se il feedback chiede una nuova durata, ridistribuisci di
  conseguenza, mantenendo proporzioni sensate tra slide.
- Mantieni tutte le regole TTS-friendly."""


# JSON Schema verbatim §8.4 — passato a OpenAI come response_format.json_schema.
LESSON_SPEECH_JSON_SCHEMA: dict[str, Any] = {
    "name": "lesson_speech",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "lesson_id": {"type": "string"},
            "language": {"type": "string"},
            "target_duration_seconds": {"type": "integer"},
            "estimated_total_duration_seconds": {"type": "integer"},
            "estimated_total_word_count": {"type": "integer"},
            "speech_segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "segment_id": {
                            "type": "string",
                            "description": "Es. 'SEG001'",
                        },
                        "slide_id": {
                            "type": "string",
                            "description": (
                                "ID della slide a cui il segmento è "
                                "ancorato"
                            ),
                        },
                        "text": {
                            "type": "string",
                            "description": (
                                "Testo del parlato. TTS-friendly: "
                                "niente abbreviazioni, niente caratteri "
                                "speciali, niente markdown."
                            ),
                        },
                        "estimated_duration_seconds": {"type": "integer"},
                        "delivery_notes": {
                            "type": "string",
                            "description": (
                                "Annotazione opzionale per il docente "
                                "su tono, ritmo, pause. Una frase breve."
                            ),
                        },
                    },
                    "required": [
                        "segment_id",
                        "slide_id",
                        "text",
                        "estimated_duration_seconds",
                        "delivery_notes",
                    ],
                    "additionalProperties": False,
                },
            },
            "slide_to_segments_map": {
                "type": "array",
                "description": (
                    "Mapping inverso slide_id -> elenco segment_id. "
                    "Utile per la sincronizzazione video."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "slide_id": {"type": "string"},
                        "segment_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "slide_total_duration_seconds": {"type": "integer"},
                    },
                    "required": [
                        "slide_id",
                        "segment_ids",
                        "slide_total_duration_seconds",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "lesson_id",
            "language",
            "target_duration_seconds",
            "estimated_total_duration_seconds",
            "estimated_total_word_count",
            "speech_segments",
            "slide_to_segments_map",
        ],
        "additionalProperties": False,
    },
}


async def generate_lesson_speech(
    *,
    user_prompt: str,
    language_code: str,
    is_regeneration: bool,
) -> tuple[LessonSpeechOutput, dict[str, Any]]:
    """Chiama OpenAI per generare il discorso temporizzato di una lezione.

    Ritorna `(speech, usage)` dove `usage` è un dict con i conteggi
    token. Solleva `OpenAILessonSpeechError` su errore HTTP/parsing/
    schema. Solleva `OpenAINotConfiguredError` se la API key è assente.

    NOTA: come per Fase 3/4, `gpt-5.5` richiede `max_completion_tokens`
    (non `max_tokens`) e non accetta `temperature` custom (default 1.0).
    Output tipico: 6-12k token (prosa pura). `OPENAI_LESSON_SPEECH_MAX_TOKENS`
    deve partire da ~16000 per non troncare lezioni lunghe.
    """
    settings = get_settings()
    system_prompt = _system_prompt(language_code)
    if is_regeneration:
        system_prompt = system_prompt + REGENERATION_SUFFIX

    body = {
        "model": settings.openai_lesson_speech_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": LESSON_SPEECH_JSON_SCHEMA,
        },
        "max_completion_tokens": settings.openai_lesson_speech_max_tokens,
    }
    apply_reasoning_effort(
        body,
        settings.openai_lesson_speech_model,
        settings.openai_lesson_speech_reasoning_effort,
    )
    log.info(
        "openai_lesson_speech_request",
        chars=len(user_prompt),
        regeneration=is_regeneration,
        model=settings.openai_lesson_speech_model,
        reasoning_effort=body.get("reasoning_effort"),
    )
    try:
        async with get_client(timeout=600.0) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_lesson_speech_http_error", error=str(exc))
        raise OpenAILessonSpeechError(
            status=None, message=f"Errore HTTP verso OpenAI: {exc}"
        ) from exc

    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except Exception:
            payload = {"text": resp.text}
        message = (
            payload.get("error", {}).get("message")
            if isinstance(payload, dict)
            else None
        )
        log.error(
            "openai_lesson_speech_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAILessonSpeechError(
            status=resp.status_code,
            message=message or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    data = resp.json()
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_lesson_speech_unexpected_response", payload=data)
        raise OpenAILessonSpeechError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    if not content or not content.strip():
        usage_raw = data.get("usage") or {}
        completion_tokens = usage_raw.get("completion_tokens") or 0
        reasoning_tokens = (
            (usage_raw.get("completion_tokens_details") or {}).get(
                "reasoning_tokens"
            )
            or 0
        )
        log.error(
            "openai_lesson_speech_empty_content",
            finish_reason=finish_reason,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            max_tokens=settings.openai_lesson_speech_max_tokens,
        )
        if finish_reason == "length":
            raise OpenAILessonSpeechError(
                status=resp.status_code,
                message=(
                    f"OpenAI ha esaurito i token disponibili "
                    f"(reasoning={reasoning_tokens}, completion={completion_tokens}, "
                    f"cap={settings.openai_lesson_speech_max_tokens}). "
                    f"Aumenta OPENAI_LESSON_SPEECH_MAX_TOKENS."
                ),
                payload=data,
            )
        raise OpenAILessonSpeechError(
            status=resp.status_code,
            message=(
                f"OpenAI ha restituito un contenuto vuoto "
                f"(finish_reason={finish_reason}, reasoning={reasoning_tokens}). "
                f"Riprova; se persiste aumenta OPENAI_LESSON_SPEECH_MAX_TOKENS."
            ),
            payload=data,
        )

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error(
            "openai_lesson_speech_json_decode_failed", content=content[:500]
        )
        raise OpenAILessonSpeechError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    try:
        lesson_speech = LessonSpeechOutput.model_validate(parsed)
    except Exception as exc:
        log.error("openai_lesson_speech_schema_invalid", error=str(exc))
        raise OpenAILessonSpeechError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
        ) from exc

    usage_raw = data.get("usage") or {}
    usage = {
        "prompt": int(usage_raw.get("prompt_tokens") or 0),
        "completion": int(usage_raw.get("completion_tokens") or 0),
        "total": int(usage_raw.get("total_tokens") or 0),
        "model": settings.openai_lesson_speech_model,
    }
    log.info(
        "openai_lesson_speech_response",
        lesson_id=lesson_speech.lesson_id,
        segments=len(lesson_speech.speech_segments),
        duration=lesson_speech.estimated_total_duration_seconds,
        words=lesson_speech.estimated_total_word_count,
        tokens=usage["total"],
    )
    return lesson_speech, usage
