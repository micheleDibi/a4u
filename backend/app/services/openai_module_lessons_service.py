"""Generazione AI delle lezioni di un singolo modulo.

Usato quando l'utente aggiunge manualmente un nuovo modulo: dopo la
creazione, il backend chiama questo servizio per popolare automaticamente
le N lezioni del modulo (N = course.lessons_per_module) usando come
contesto il corso, gli altri moduli già definiti, e i riassunti dei
documenti caricati.

Output JSON strict via `response_format=json_schema`. Schema minimale:
solo titolo + sintesi per ogni lezione (no bibliografia, perché in
generale il modulo aggiunto manualmente non è introduttivo).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    apply_reasoning_effort,
    get_client,
)

log = get_logger("app.openai_module_lessons")


class OpenAIModuleLessonsError(OpenAIError):
    """Errore specifico della generazione lezioni a livello modulo."""


SYSTEM_PROMPT = """\
Sei un instructional designer esperto. Devi generare SOLO le lezioni di
un singolo modulo di un corso universitario già parzialmente definito.

Linee guida:
1. Genera esattamente N lezioni (numero specificato dall'utente).
2. Ogni lezione deve avere:
   - title: titolo conciso (max 200 caratteri)
   - summary: sintesi di 1-3 frasi (50-300 parole) che descrive cosa si
     impara in quella lezione.
3. Le lezioni devono progredire in modo logico all'interno del modulo.
4. Mantieni coerenza con titolo e descrizione del modulo target.
5. Evita ridondanza con le lezioni degli altri moduli del corso.
6. Lingua di output: rispetta la lingua specificata (codice ISO).

Output: JSON strict secondo lo schema richiesto, niente testo
aggiuntivo."""


JSON_SCHEMA: dict[str, Any] = {
    "name": "module_lessons",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["lessons"],
        "properties": {
            "lessons": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "summary"],
                    "properties": {
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                    },
                },
            }
        },
    },
}


async def generate_module_lessons(
    *,
    user_prompt: str,
    language_code: str,
    expected_count: int,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Chiama OpenAI per generare le lezioni del modulo. Ritorna
    `(lessons, usage)` dove `lessons` è una lista di dict
    `{title, summary}` e `usage` contiene token e modello."""
    settings = get_settings()

    body = {
        "model": settings.openai_modules_lessons_model,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
                + f"\n\nLingua dell'output (ISO): {language_code}.",
            },
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": JSON_SCHEMA,
        },
        "max_completion_tokens": settings.openai_architecture_max_tokens,
    }
    apply_reasoning_effort(
        body,
        settings.openai_modules_lessons_model,
        settings.openai_architecture_reasoning_effort,
    )

    log.info(
        "openai_module_lessons_request",
        chars=len(user_prompt),
        expected_count=expected_count,
        model=settings.openai_modules_lessons_model,
        reasoning_effort=body.get("reasoning_effort"),
    )

    try:
        async with get_client(timeout=300.0) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_module_lessons_http_error", error=str(exc))
        raise OpenAIModuleLessonsError(
            f"Errore HTTP verso OpenAI: {exc}"
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
            "openai_module_lessons_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAIModuleLessonsError(
            message or f"OpenAI ha risposto con HTTP {resp.status_code}."
        )

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_module_lessons_unexpected_response", payload=data)
        raise OpenAIModuleLessonsError(
            "Risposta OpenAI in formato inatteso."
        ) from exc

    try:
        parsed = json.loads(content)
        lessons = parsed.get("lessons", [])
        if not isinstance(lessons, list):
            raise ValueError("'lessons' non è una lista")
        clean = [
            {
                "title": str(l["title"]).strip(),
                "summary": str(l.get("summary", "")).strip(),
            }
            for l in lessons
            if isinstance(l, dict) and l.get("title")
        ]
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        log.error(
            "openai_module_lessons_parse_failed", content=content[:500]
        )
        raise OpenAIModuleLessonsError(
            f"Output OpenAI non valido: {exc}"
        ) from exc

    if not clean:
        raise OpenAIModuleLessonsError(
            "OpenAI non ha restituito lezioni utilizzabili."
        )

    usage_raw = data.get("usage") or {}
    usage = {
        "prompt": int(usage_raw.get("prompt_tokens") or 0),
        "completion": int(usage_raw.get("completion_tokens") or 0),
        "total": int(usage_raw.get("total_tokens") or 0),
        "model": settings.openai_modules_lessons_model,
    }

    log.info(
        "openai_module_lessons_response",
        count=len(clean),
        expected=expected_count,
        tokens=usage["total"],
    )
    return clean, usage
