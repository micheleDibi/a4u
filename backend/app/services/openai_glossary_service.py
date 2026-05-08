"""Generazione del glossario del corso (§10.1).

Single-shot: una sola chiamata per corso, riusa l'output come `{{glossario}}`
nei prompt successivi (Fasi 2, 3, 5). Il glossario non ha un worker
dedicato — viene generato sync dal `course_glossary_service` o auto-triggerato
dal worker della Fase 3 al primo passaggio se assente.

Errori → `OpenAIGlossaryError` (sottoclasse di `OpenAIError`).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.course_glossary import GlossaryOutput
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    get_client,
)

log = get_logger("app.openai_glossary")


class OpenAIGlossaryError(OpenAIError):
    """Errore specifico delle chiamate di generazione glossario (§10.1)."""


def _system_prompt(language_code: str) -> str:
    """System prompt minimal per il glossario (§10.1).

    Lo scopo è generare 10-30 termini chiave del corso, con traduzione/variante
    e una `usage_note` che chiarisce come il termine è usato nel corso.
    Coerenza terminologica garantita per tutte le fasi successive.
    """
    return f"""\
Sei un terminologo specializzato in didattica universitaria.

Il tuo compito è estrarre il GLOSSARIO ESSENZIALE di un corso: 10-30
termini chiave del dominio disciplinare che saranno usati con coerenza
nel testo delle lezioni, nelle slide e nei discorsi.

Per OGNI termine produci:
- `term`: il termine come appare nei materiali del corso
- `translation`: traduzione/variante (es. acronimo o equivalente in
  un'altra lingua), oppure stringa vuota se non rilevante
- `usage_note`: 1 frase che chiarisce COME il termine è inteso/usato
  in QUESTO corso (definizione operativa, non vocabolario generico)

PRINCIPI:
- Termini SPECIFICI del dominio, non generici
- No sinonimi quasi-identici (consolida sotto un unico term)
- Coerenza terminologica: se in input compaiono varianti
  ("ML"/"machine learning"), scegli una forma canonica e segnala
  l'altra in `translation` o `usage_note`
- Privilegia termini ricorrenti tra moduli/lezioni e nei documenti

Lingua di output: {language_code}.
Output: SOLO JSON valido conforme allo schema."""


GLOSSARY_JSON_SCHEMA: dict[str, Any] = {
    "name": "course_glossary",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "course_id": {"type": "string"},
            "terms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "term": {"type": "string"},
                        "translation": {"type": "string"},
                        "usage_note": {"type": "string"},
                    },
                    "required": ["term", "translation", "usage_note"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["course_id", "terms"],
        "additionalProperties": False,
    },
}


async def generate_glossary(
    *,
    user_prompt: str,
    language_code: str,
) -> tuple[GlossaryOutput, dict[str, Any]]:
    """Chiama OpenAI per generare il glossario di un corso.

    Ritorna `(glossary, usage)`. Solleva `OpenAIGlossaryError` su errore
    HTTP/parsing/schema. `OpenAINotConfiguredError` se la API key è
    assente.
    """
    settings = get_settings()
    system_prompt = _system_prompt(language_code)

    body = {
        "model": settings.openai_glossary_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": GLOSSARY_JSON_SCHEMA,
        },
        "max_completion_tokens": settings.openai_glossary_max_tokens,
    }
    log.info(
        "openai_glossary_request",
        chars=len(user_prompt),
        model=settings.openai_glossary_model,
    )
    try:
        async with get_client(timeout=180.0) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_glossary_http_error", error=str(exc))
        raise OpenAIGlossaryError(
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
            "openai_glossary_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAIGlossaryError(
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
        log.error("openai_glossary_unexpected_response", payload=data)
        raise OpenAIGlossaryError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    # gpt-5.5: reasoning tokens possono esaurire max_completion_tokens.
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
            "openai_glossary_empty_content",
            finish_reason=finish_reason,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            max_tokens=settings.openai_glossary_max_tokens,
        )
        if finish_reason == "length":
            raise OpenAIGlossaryError(
                status=resp.status_code,
                message=(
                    f"OpenAI ha esaurito i token disponibili "
                    f"(reasoning={reasoning_tokens}, "
                    f"completion={completion_tokens}, "
                    f"cap={settings.openai_glossary_max_tokens}). "
                    f"Aumenta OPENAI_GLOSSARY_MAX_TOKENS."
                ),
                payload=data,
            )
        raise OpenAIGlossaryError(
            status=resp.status_code,
            message=(
                f"OpenAI ha restituito un contenuto vuoto "
                f"(finish_reason={finish_reason}, reasoning={reasoning_tokens})."
            ),
            payload=data,
        )

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error("openai_glossary_json_decode_failed", content=content[:500])
        raise OpenAIGlossaryError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    try:
        glossary = GlossaryOutput.model_validate(parsed)
    except Exception as exc:
        log.error("openai_glossary_schema_invalid", error=str(exc))
        raise OpenAIGlossaryError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
        ) from exc

    usage_raw = data.get("usage") or {}
    usage = {
        "prompt": int(usage_raw.get("prompt_tokens") or 0),
        "completion": int(usage_raw.get("completion_tokens") or 0),
        "total": int(usage_raw.get("total_tokens") or 0),
        "model": settings.openai_glossary_model,
    }
    log.info(
        "openai_glossary_response",
        terms=len(glossary.terms),
        tokens=usage["total"],
    )
    return glossary, usage
