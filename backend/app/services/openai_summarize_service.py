"""Riassunto strutturato di un documento di corso (Appendice A).

Implementa il prompt e lo schema descritti in `prompt_generazione_corsi.md`
Appendice A. Output JSON conforme allo schema, validato con Pydantic prima
di essere persistito in DB.

Errori → `OpenAISummarizeError` (sottoclasse di `OpenAIError`).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.document_summary import DocumentSummaryOut
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    get_client,
)

log = get_logger("app.openai_summarize")


class OpenAISummarizeError(OpenAIError):
    """Errore specifico delle chiamate di riassunto documento."""


SUMMARIZE_SYSTEM_PROMPT = """\
Sei un esperto di analisi documentale per la didattica universitaria.
Il tuo compito è produrre un RIASSUNTO STRUTTURATO ad alta densità
informativa di un documento fornito dal docente. Il riassunto sarà
l'unica rappresentazione del documento usata per generare materiale
didattico (architettura del corso, lezioni, slide). Vi si attingerà
ripetutamente: deve quindi essere completo, accurato e ben organizzato.

Per estrarre un riassunto di alta qualità:

1. ABSTRACT (200-400 parole): cosa tratta il documento, in che
   prospettiva, su quale arco di contenuti, con quale tesi o approccio.
   Deve permettere a chi non legge il documento di capire se è
   pertinente per un certo tema didattico.

2. KEY CONCEPTS (10-25 voci): i concetti fondamentali. Per ognuno:
   nome e una explanation autonoma di 2-4 frasi che catturi la
   sostanza, non un mero rimando.

3. DEFINITIONS (tutte quelle presenti): per ogni termine definito nel
   documento, riporta la definizione il più fedelmente possibile
   (parafrasata in modo accurato, NON copiata letteralmente).

4. EXAMPLES_OR_CASES (tutti quelli rilevanti): esempi, casi studio,
   applicazioni concrete presenti nel documento. Per ognuno una
   sintesi che ne preservi il valore didattico (~3-5 frasi).

5. FORMULAS_OR_RULES: equazioni, regole, principi formali. Per le
   formule usa LaTeX. Per ognuna spiega il significato dei simboli e
   il dominio di applicazione.

6. AUTHORS_AND_REFERENCES: autori del documento e riferimenti
   bibliografici citati al suo interno (non inventarne).

7. STRUCTURE_OUTLINE: un breve indice del documento (capitoli/sezioni
   principali) per orientare chi lo userà come riferimento.

8. DIDACTIC_RELEVANCE_TAGS (5-15 tag): parole-chiave che descrivono
   i temi trattati. Devono essere utili per filtrare il documento
   quando il sistema deve scegliere quali estratti passare a una
   specifica lezione.

PRINCIPI:
- Massimizza la densità informativa, minimizza la ridondanza.
- NON inventare contenuti: se qualcosa non è nel documento, non
  metterlo nel riassunto.
- Rispetta il copyright: non citare letteralmente più di una frase
  breve. Parafrasa.

Lingua del riassunto: stessa del documento (rilevala automaticamente).
Output: SOLO JSON valido conforme allo schema."""


# JSON Schema dell'Appendice A — passato a OpenAI come response_format.json_schema.
SUMMARY_JSON_SCHEMA: dict[str, Any] = {
    "name": "document_summary",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "source_title": {"type": "string"},
            "detected_language": {"type": "string"},
            "abstract": {"type": "string"},
            "structure_outline": {
                "type": "array",
                "items": {"type": "string"},
            },
            "key_concepts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "explanation": {"type": "string"},
                    },
                    "required": ["name", "explanation"],
                    "additionalProperties": False,
                },
            },
            "definitions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "term": {"type": "string"},
                        "definition": {"type": "string"},
                    },
                    "required": ["term", "definition"],
                    "additionalProperties": False,
                },
            },
            "examples_or_cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "synthesis": {"type": "string"},
                    },
                    "required": ["title", "synthesis"],
                    "additionalProperties": False,
                },
            },
            "formulas_or_rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "latex_or_text": {"type": "string"},
                        "meaning": {"type": "string"},
                    },
                    "required": ["label", "latex_or_text", "meaning"],
                    "additionalProperties": False,
                },
            },
            "authors_and_references": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["author", "cited_reference"],
                        },
                        "value": {"type": "string"},
                    },
                    "required": ["type", "value"],
                    "additionalProperties": False,
                },
            },
            "didactic_relevance_tags": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "source_title",
            "detected_language",
            "abstract",
            "structure_outline",
            "key_concepts",
            "definitions",
            "examples_or_cases",
            "formulas_or_rules",
            "authors_and_references",
            "didactic_relevance_tags",
        ],
        "additionalProperties": False,
    },
}


async def summarize_document(
    *,
    text: str,
    source_filename: str,
) -> tuple[DocumentSummaryOut, dict[str, Any]]:
    """Genera un riassunto strutturato del documento.

    Ritorna `(summary, usage)` dove `usage` è un dict
    `{prompt, completion, total, model}` con i conteggi token.
    Solleva `OpenAISummarizeError` su errore HTTP, parsing o validazione.
    Solleva `OpenAINotConfiguredError` se la API key è assente.
    """
    if not text.strip():
        raise OpenAISummarizeError(
            status=None,
            message="Documento privo di testo estraibile (forse scansione? OCR non supportato).",
        )

    settings = get_settings()
    user_message = (
        f"Nome file di origine: {source_filename}\n\n"
        "Contenuto testuale del documento (potrebbe essere stato troncato):\n\n"
        f"{text}"
    )

    body = {
        "model": settings.openai_summarize_model,
        "messages": [
            {"role": "system", "content": SUMMARIZE_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": SUMMARY_JSON_SCHEMA,
        },
        "temperature": 0.2,
        "max_tokens": settings.openai_summarize_max_tokens,
    }
    log.info(
        "openai_summarize_request",
        filename=source_filename,
        chars=len(text),
        model=settings.openai_summarize_model,
    )
    try:
        async with get_client(timeout=300.0) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_summarize_http_error", error=str(exc))
        raise OpenAISummarizeError(
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
            "openai_summarize_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAISummarizeError(
            status=resp.status_code,
            message=message or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_summarize_unexpected_response", payload=data)
        raise OpenAISummarizeError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error("openai_summarize_json_decode_failed", content=content[:500])
        raise OpenAISummarizeError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    try:
        summary = DocumentSummaryOut.model_validate(parsed)
    except Exception as exc:
        log.error("openai_summarize_schema_invalid", error=str(exc))
        raise OpenAISummarizeError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
        ) from exc

    usage_raw = data.get("usage") or {}
    usage = {
        "prompt": int(usage_raw.get("prompt_tokens") or 0),
        "completion": int(usage_raw.get("completion_tokens") or 0),
        "total": int(usage_raw.get("total_tokens") or 0),
        "model": settings.openai_summarize_model,
    }
    log.info(
        "openai_summarize_response",
        filename=source_filename,
        tokens=usage["total"],
        model=usage["model"],
    )
    return summary, usage
