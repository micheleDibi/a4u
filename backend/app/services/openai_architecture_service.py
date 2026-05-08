"""Generazione dell'architettura del corso (Fase 1, §4).

Implementa il system+user prompt e lo schema descritti in
`prompt_generazione_corsi.md` §4.1-4.3. Output JSON conforme al JSON
Schema, validato con Pydantic prima di essere materializzato.

Errori → `OpenAIArchitectureError` (sottoclasse di `OpenAIError`).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.course_architecture import ArchitectureOutput
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    apply_reasoning_effort,
    get_client,
)

log = get_logger("app.openai_architecture")


class OpenAIArchitectureError(OpenAIError):
    """Errore specifico delle chiamate di generazione architettura."""


# System prompt — copia testuale di §4.1, parametrizzato sulla lingua.
def _system_prompt(language_code: str) -> str:
    return f"""\
Sei un instructional designer esperto nella progettazione di corsi
universitari. Il tuo compito è costruire l'architettura didattica di
un corso a partire dai parametri forniti dal docente e dai materiali
di riferimento.

Principi di progettazione:

1. PROGRESSIONE COERENTE: i moduli devono seguire una progressione
   logica (dal generale al specifico, oppure dal fondamentale
   all'applicato), coerente con lo stile di insegnamento e il livello
   EQF richiesto.

2. COPERTURA COMPLETA: tutti gli argomenti chiave forniti devono essere
   coperti. Distribuiscili tra i moduli in modo equilibrato.

3. STRUTTURA FISSA: il numero di moduli e di lezioni per modulo è
   determinato dai parametri di input e NON può essere modificato.
   Ogni modulo deve avere ESATTAMENTE `numero_lezioni_per_modulo` lezioni.
   Il numero totale di moduli deve essere ESATTAMENTE `numero_moduli`.

4. LEZIONE 1 INTRODUTTIVA: la PRIMA lezione del PRIMO modulo è sempre
   una lezione introduttiva al corso. Deve:
   - presentare gli obiettivi formativi globali del corso
   - illustrare la struttura del corso (moduli e percorso didattico)
   - chiarire i prerequisiti richiesti agli studenti
   - presentare la modalità didattica e lo stile d'aula
   - includere una BIBLIOGRAFIA CONSIGLIATA di 4-8 testi
   Marca questa lezione con `is_introductory: true` e popola il campo
   `recommended_bibliography`.

5. BIBLIOGRAFIA — REGOLA CRITICA: NON inventare titoli di libri,
   autori, editori o anni di pubblicazione. Usa SOLO testi:
   (a) presenti nei documenti di riferimento forniti, oppure
   (b) testi di riferimento ampiamente noti del campo, di cui sei
       altamente certo. In questo secondo caso marca esplicitamente la
       voce con `confidence: "to_verify"` perché il docente possa
       confermare. Se non ne hai abbastanza per arrivare a 4 voci sicure,
       lascia meno voci ma TUTTE accurate.

6. GRANULARITÀ: ogni lezione copre 1-3 concetti principali. Distribuisci
   in modo che nessuna sia sovraccarica e nessuna troppo leggera.

7. ALLINEAMENTO EQF: complessità del linguaggio, profondità di analisi
   e autonomia richiesta agli studenti coerenti con il livello EQF.

8. NESSUNA SOVRAPPOSIZIONE tra lezioni se non per richiami intenzionali.

9. USO DEI DOCUMENTI: privilegia concetti, definizioni e impostazione
   presenti nei documenti di riferimento.

Lingua di output: {language_code}.
Output: SOLO JSON valido conforme allo schema fornito."""


REGENERATION_SUFFIX = """\

ATTENZIONE: stai RIGENERANDO un'architettura già prodotta. Tieni in
considerazione la versione attuale e il feedback del docente.
- Mantieni invariate le parti che non sono in conflitto con il feedback.
- Cambia ciò che il feedback richiede esplicitamente.
- Mantieni la coerenza globale del corso e la progressione didattica.
- Mantieni invariati i `module_id` / `lesson_id` se la corrispondenza
  semantica è preservata; cambiali solo se il modulo o la lezione sono
  stati sostituiti radicalmente."""


# JSON Schema dell'architettura — passato a OpenAI come response_format.json_schema.
ARCHITECTURE_JSON_SCHEMA: dict[str, Any] = {
    "name": "course_architecture",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "course_overview": {"type": "string"},
            "pedagogical_rationale": {"type": "string"},
            "modules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "module_id": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "lessons": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "lesson_id": {"type": "string"},
                                    "title": {"type": "string"},
                                    "summary": {"type": "string"},
                                    "is_introductory": {"type": "boolean"},
                                    "recommended_bibliography": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "authors": {"type": "string"},
                                                "title": {"type": "string"},
                                                "publisher": {"type": "string"},
                                                "year": {"type": "string"},
                                                "note": {"type": "string"},
                                                "source": {
                                                    "type": "string",
                                                    "enum": [
                                                        "from_uploaded_documents",
                                                        "general_knowledge_suggestion",
                                                    ],
                                                },
                                                "confidence": {
                                                    "type": "string",
                                                    "enum": ["confirmed", "to_verify"],
                                                },
                                            },
                                            "required": [
                                                "authors",
                                                "title",
                                                "publisher",
                                                "year",
                                                "note",
                                                "source",
                                                "confidence",
                                            ],
                                            "additionalProperties": False,
                                        },
                                    },
                                },
                                "required": [
                                    "lesson_id",
                                    "title",
                                    "summary",
                                    "is_introductory",
                                    "recommended_bibliography",
                                ],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["module_id", "title", "description", "lessons"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["course_overview", "pedagogical_rationale", "modules"],
        "additionalProperties": False,
    },
}


async def generate_architecture(
    *,
    user_prompt: str,
    language_code: str,
    is_regeneration: bool,
) -> tuple[ArchitectureOutput, dict[str, Any]]:
    """Chiama OpenAI per generare l'architettura del corso.

    Ritorna `(architecture, usage)` dove `usage` è un dict con i conteggi
    token. Solleva `OpenAIArchitectureError` su errore HTTP/parsing/schema.
    Solleva `OpenAINotConfiguredError` se la API key è assente.
    """
    settings = get_settings()
    system_prompt = _system_prompt(language_code)
    if is_regeneration:
        system_prompt = system_prompt + REGENERATION_SUFFIX

    body = {
        "model": settings.openai_modules_lessons_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": ARCHITECTURE_JSON_SCHEMA,
        },
        "max_completion_tokens": settings.openai_architecture_max_tokens,
    }
    apply_reasoning_effort(
        body,
        settings.openai_modules_lessons_model,
        settings.openai_architecture_reasoning_effort,
    )
    log.info(
        "openai_architecture_request",
        chars=len(user_prompt),
        regeneration=is_regeneration,
        model=settings.openai_modules_lessons_model,
        reasoning_effort=body.get("reasoning_effort"),
    )
    try:
        async with get_client(timeout=300.0) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_architecture_http_error", error=str(exc))
        raise OpenAIArchitectureError(
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
            "openai_architecture_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAIArchitectureError(
            status=resp.status_code,
            message=message or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    data = resp.json()
    try:
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_architecture_unexpected_response", payload=data)
        raise OpenAIArchitectureError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    # Diagnostica per content vuoto: spesso accade per finish_reason
    # `length` (max_tokens troppo basso, il reasoning ha consumato tutto
    # il budget prima di emettere JSON), `content_filter` (safety guard
    # OpenAI), o `refusal` (il modello rifiuta esplicitamente).
    if not content.strip():
        finish_reason = choice.get("finish_reason")
        refusal = message.get("refusal")
        usage = data.get("usage", {})
        log.error(
            "openai_architecture_empty_content",
            finish_reason=finish_reason,
            refusal=refusal,
            usage=usage,
            model=data.get("model"),
        )
        if finish_reason == "length":
            hint = (
                " — finish_reason=length: il modello ha esaurito i token "
                "prima di emettere il JSON. Aumenta "
                "OPENAI_ARCHITECTURE_MAX_TOKENS nel .env (consigliato "
                "16000+) o usa un modello con context window più ampia."
            )
        elif finish_reason == "content_filter":
            hint = (
                " — finish_reason=content_filter: il safety filter OpenAI "
                "ha bloccato la risposta. Riformula prompt/documenti."
            )
        elif refusal:
            hint = f" — il modello ha rifiutato: {refusal[:200]}"
        else:
            hint = (
                f" — finish_reason={finish_reason}. Verifica modello "
                f"({data.get('model')}) e usage: {usage}."
            )
        raise OpenAIArchitectureError(
            status=resp.status_code,
            message=f"OpenAI ha restituito una risposta vuota{hint}",
            payload=data,
        )

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error("openai_architecture_json_decode_failed", content=content[:500])
        raise OpenAIArchitectureError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    try:
        architecture = ArchitectureOutput.model_validate(parsed)
    except Exception as exc:
        log.error("openai_architecture_schema_invalid", error=str(exc))
        raise OpenAIArchitectureError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
        ) from exc

    usage_raw = data.get("usage") or {}
    usage = {
        "prompt": int(usage_raw.get("prompt_tokens") or 0),
        "completion": int(usage_raw.get("completion_tokens") or 0),
        "total": int(usage_raw.get("total_tokens") or 0),
        "model": settings.openai_modules_lessons_model,
    }
    log.info(
        "openai_architecture_response",
        modules=len(architecture.modules),
        tokens=usage["total"],
    )
    return architecture, usage
