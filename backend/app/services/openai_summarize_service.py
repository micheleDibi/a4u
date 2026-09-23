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
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.document_summary import ChunkFactsOut, DocumentSummaryOut
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    get_client,
)
from app.services.openai_http import post_chat_with_retry

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
- IDENTITÀ SOLO NEI CAMPI DEDICATI: il titolo dell'opera va SOLO in
  `source_title`, i nomi degli autori SOLO in `authors_and_references`.
  Non nominarli in abstract, key_concepts o definitions (la prosa del
  riassunto deve descrivere i contenuti, non l'opera).
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


# ---------------------------------------------------------------------------
# Pipeline a copertura totale (map → merge → reduce)
#
# Per i documenti sopra soglia il worker non fa più una singola chiamata
# ma: una chiamata MAP per chunk (fatti già nei sotto-schemi Appendice A),
# merge deterministico in codice, eventuale DIGEST dei mini-abstract, e
# una chiamata REDUCE che riusa LETTERALMENTE `SUMMARY_JSON_SCHEMA` e la
# validazione `DocumentSummaryOut`: lo schema dell'output persistito non
# cambia. `summarize_document` (single-shot) resta invariata.
# ---------------------------------------------------------------------------

# Status HTTP transient: si ritentano con backoff. NB deliberato: il 429
# QUI si ritenta (diversamente dal pattern della duplicazione, che
# tratta tutti i 4xx come terminali) — è l'unica protezione reale dal
# rate limit per un run da ~50+ chiamate.
async def _post_chat_with_retry(
    body: dict[str, Any], *, timeout: float, label: str
) -> dict[str, Any]:
    """POST /chat/completions con retry sui transient: delega al trasporto
    condiviso `openai_http.post_chat_with_retry` con il tetto dei tentativi,
    l'errore e il prefisso dei log propri del riassunto (comportamento
    invariato)."""
    settings = get_settings()
    return await post_chat_with_retry(
        body,
        timeout=timeout,
        label=label,
        max_attempts=settings.course_document_llm_retry_max,
        error_cls=OpenAISummarizeError,
        log_prefix="openai_summarize",
    )


def _parse_structured[ModelT: BaseModel](
    data: dict[str, Any], model_cls: type[ModelT], *, label: str
) -> tuple[ModelT, dict[str, Any]]:
    """Estrae content → JSON → modello Pydantic + usage dict."""
    settings = get_settings()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_summarize_unexpected_response", label=label)
        raise OpenAISummarizeError(
            status=None,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error(
            "openai_summarize_json_decode_failed",
            label=label,
            content=content[:500],
        )
        raise OpenAISummarizeError(
            status=None,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc
    try:
        validated = model_cls.model_validate(parsed)
    except Exception as exc:
        log.error(
            "openai_summarize_schema_invalid", label=label, error=str(exc)
        )
        raise OpenAISummarizeError(
            status=None,
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
    return validated, usage


CHUNK_FACTS_SYSTEM_PROMPT = """\
Sei un esperto di analisi documentale per la didattica universitaria.
Ricevi UN BLOCCO di un documento più lungo. Estrai SOLO ciò che è
presente nel blocco, già nella forma finale che verrà unita ai blocchi
degli altri estratti:

1. CHUNK_ABSTRACT (100-200 parole): di cosa tratta questo blocco.
2. OUTLINE_ITEMS: titoli di capitoli/sezioni visibili nel blocco,
   nell'ordine in cui compaiono.
3. KEY_CONCEPTS: concetti fondamentali del blocco, con explanation
   autonoma di 2-4 frasi.
4. DEFINITIONS: TUTTE le definizioni presenti nel blocco, parafrasate
   fedelmente (non copiate).
5. EXAMPLES_OR_CASES: esempi e casi studio del blocco (~3-5 frasi).
6. FORMULAS_OR_RULES: formule (LaTeX) e regole, con significato dei
   simboli.
7. AUTHORS_AND_REFERENCES: autori del documento e riferimenti citati
   nel blocco (non inventarne).
8. CANDIDATE_TAGS: parole-chiave dei temi del blocco.

PRINCIPI:
- Massima densità, zero ridondanza interna, NON inventare.
- Titolo dell'opera e nomi degli autori SOLO nel campo
  authors_and_references: non nominarli in chunk_abstract, explanation
  o definition.
- Rispetta il copyright: parafrasa, non citare letteralmente più di
  una frase breve.

Lingua: stessa del documento (rilevala). Output: SOLO JSON conforme."""


CHUNK_FACTS_JSON_SCHEMA: dict[str, Any] = {
    "name": "document_chunk_facts",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "chunk_abstract": {"type": "string"},
            "detected_language": {"type": "string"},
            "outline_items": {
                "type": "array",
                "items": {"type": "string"},
            },
            "key_concepts": SUMMARY_JSON_SCHEMA["schema"]["properties"][
                "key_concepts"
            ],
            "definitions": SUMMARY_JSON_SCHEMA["schema"]["properties"][
                "definitions"
            ],
            "examples_or_cases": SUMMARY_JSON_SCHEMA["schema"]["properties"][
                "examples_or_cases"
            ],
            "formulas_or_rules": SUMMARY_JSON_SCHEMA["schema"]["properties"][
                "formulas_or_rules"
            ],
            "authors_and_references": SUMMARY_JSON_SCHEMA["schema"][
                "properties"
            ]["authors_and_references"],
            "candidate_tags": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "chunk_abstract",
            "detected_language",
            "outline_items",
            "key_concepts",
            "definitions",
            "examples_or_cases",
            "formulas_or_rules",
            "authors_and_references",
            "candidate_tags",
        ],
        "additionalProperties": False,
    },
}


async def extract_chunk_facts(
    *,
    chunk_text: str,
    source_filename: str,
    position_label: str,
) -> tuple[ChunkFactsOut, dict[str, Any]]:
    """Fase MAP: estrae i fatti strutturati di UN chunk."""
    settings = get_settings()
    user_message = (
        f"Nome file di origine: {source_filename}\n"
        f"Posizione: {position_label}\n\n"
        "Contenuto del blocco:\n\n"
        f"{chunk_text}"
    )
    body = {
        "model": settings.openai_summarize_model,
        "messages": [
            {"role": "system", "content": CHUNK_FACTS_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": CHUNK_FACTS_JSON_SCHEMA,
        },
        "temperature": 0.2,
        "max_tokens": settings.openai_summarize_map_max_tokens,
    }
    data = await _post_chat_with_retry(
        body, timeout=180.0, label=f"map:{position_label}"
    )
    return _parse_structured(data, ChunkFactsOut, label="map")


_DIGEST_JSON_SCHEMA: dict[str, Any] = {
    "name": "document_section_digest",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"digest": {"type": "string"}},
        "required": ["digest"],
        "additionalProperties": False,
    },
}


async def consolidate_abstracts(
    *,
    abstracts_block: str,
    source_filename: str,
    group_label: str,
) -> tuple[str, dict[str, Any]]:
    """Fase DIGEST (solo se i mini-abstract eccedono il budget del
    reduce): consolida un gruppo di mini-abstract consecutivi in un
    digest di sezione, senza perdere i temi trattati."""
    settings = get_settings()
    body = {
        "model": settings.openai_summarize_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Consolida i mini-abstract consecutivi di un documento "
                    "in UN digest di sezione (300-500 parole) che preservi "
                    "tutti i temi, nell'ordine. Non inventare, non nominare "
                    "titolo dell'opera né autori. Output: SOLO JSON."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Nome file: {source_filename}\nGruppo: {group_label}\n\n"
                    f"{abstracts_block}"
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": _DIGEST_JSON_SCHEMA,
        },
        "temperature": 0.2,
        "max_tokens": settings.openai_summarize_map_max_tokens,
    }
    data = await _post_chat_with_retry(
        body, timeout=180.0, label=f"digest:{group_label}"
    )

    class _Digest(BaseModel):
        digest: str

    parsed, usage = _parse_structured(data, _Digest, label="digest")
    return parsed.digest, usage


REDUCE_SYSTEM_PROMPT = """\
Sei un esperto di analisi documentale per la didattica universitaria.
Ricevi i FATTI GIÀ ESTRATTI E DEDUPLICATI dall'intero documento (con il
conteggio delle occorrenze tra blocchi) e i riassunti dei blocchi in
ordine. Componi il RIASSUNTO STRUTTURATO finale (Appendice A):

- SELEZIONA E ORGANIZZA i fatti forniti: NON riscrivere definizioni,
  formule e riferimenti — riportali fedelmente; scegli i più rilevanti
  se lo spazio non basta (le occorrenze [xN] indicano l'importanza).
- ABSTRACT (200-400 parole): sintesi dell'INTERO documento a partire
  dai riassunti dei blocchi, in ordine.
- STRUCTURE_OUTLINE: dall'elenco fornito, in ordine.
- Titolo dell'opera e autori SOLO nei campi source_title e
  authors_and_references: non nominarli nella prosa.
- NON inventare contenuti.

Lingua del riassunto: quella indicata. Output: SOLO JSON conforme."""


async def reduce_summary(
    *,
    facts_block: str,
    abstracts_block: str,
    source_filename: str,
    language_hint: str,
) -> tuple[DocumentSummaryOut, dict[str, Any]]:
    """Fase REDUCE: unica chiamata finale. Riusa LETTERALMENTE
    `SUMMARY_JSON_SCHEMA` e la validazione `DocumentSummaryOut`."""
    settings = get_settings()
    user_message = (
        f"Nome file di origine: {source_filename}\n"
        f"Lingua del documento: {language_hint or 'rileva automaticamente'}\n\n"
        "## FATTI ESTRATTI E DEDUPLICATI (seleziona e organizza)\n\n"
        f"{facts_block}\n\n"
        "## RIASSUNTI DEI BLOCCHI (in ordine)\n\n"
        f"{abstracts_block}"
    )
    body = {
        "model": settings.openai_summarize_model,
        "messages": [
            {"role": "system", "content": REDUCE_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": SUMMARY_JSON_SCHEMA,
        },
        "temperature": 0.2,
        "max_tokens": settings.openai_summarize_reduce_max_tokens,
    }
    data = await _post_chat_with_retry(body, timeout=300.0, label="reduce")
    return _parse_structured(data, DocumentSummaryOut, label="reduce")
