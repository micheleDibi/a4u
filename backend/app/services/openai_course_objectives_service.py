"""Generazione AI di obiettivi corso + argomenti chiave a partire da un
documento di riferimento (PDF/DOCX/TXT) caricato dall'utente.

Output strict JSON validato Pydantic (vedi
`schemas.course_objectives_generation.CourseObjectivesGenerationOutput`).
Errore -> `OpenAICourseObjectivesError` (sottoclasse di `OpenAIError`).

Pattern di chiamata sincrono (analogo a `openai_summarize_service`):
l'utente attende il risultato nel dialog FE, niente worker async.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.course_objectives_generation import (
    CourseObjectivesGenerationOutput,
)
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    get_client,
)

log = get_logger("app.openai_course_objectives")


class OpenAICourseObjectivesError(OpenAIError):
    """Errore specifico della generazione obiettivi/argomenti."""


_SYSTEM_PROMPT_IT = """\
Sei un esperto di progettazione didattica universitaria. Il tuo compito
e' generare, a partire da un DOCUMENTO di riferimento fornito dal docente
e dai METADATI del corso, una proposta di:

1. OBJECTIVES (obiettivi del corso): testo discorsivo 200-1200 caratteri,
   in lingua del corso, che descrive cosa lo studente sapra' fare al
   termine. Usa verbi performativi (comprendere, applicare, analizzare,
   valutare, progettare, ecc.). Stile pedagogico, NON elenco puntato:
   prosa fluida. Calibra il livello cognitivo in base al livello EQF e
   alla profondita' contenuto indicati nei metadati.

2. ARGOMENTI_CHIAVE: lista di 5-15 argomenti, ognuno 2-5 parole, che
   coprono i topic principali del documento e sono coerenti con i
   metadati del corso. NO frasi lunghe, NO duplicati, NO sinonimi
   evidenti. Ordine logico (dal piu' fondamentale al piu' specifico).

PRINCIPI:
- BASATI SUL DOCUMENTO: gli argomenti chiave devono coprire gli effettivi
  contenuti del documento. Se il documento tratta solo un sotto-tema dei
  metadati corso, restringi la proposta a quel sotto-tema.
- COERENZA CON I METADATI: se i destinatari sono "studenti universitari
  triennale" non proporre obiettivi da master; se la profondita' e'
  "introduttiva" non parlare di stati dell'arte di ricerca.
- LINGUA: usa la lingua indicata in METADATI > Lingua del corso.
- NO INVENZIONI: non aggiungere argomenti non presenti nel documento solo
  per coprire i metadati. Se il documento non tratta qualcosa, omettila.
- Rispetta il copyright: non citare letteralmente frasi del documento.

Output: SOLO JSON valido conforme allo schema."""


_SYSTEM_PROMPT_EN = """\
You are an expert in university instructional design. Your task is to
generate, starting from a REFERENCE DOCUMENT provided by the instructor
and the COURSE METADATA, a proposal of:

1. OBJECTIVES (course objectives): narrative text 200-1200 characters,
   in the course language, describing what the student will be able to
   do at the end. Use performative verbs (understand, apply, analyze,
   evaluate, design, etc.). Pedagogical style, NOT bullet points:
   flowing prose. Calibrate the cognitive level based on the EQF level
   and content depth indicated in the metadata.

2. ARGOMENTI_CHIAVE (key topics): list of 5-15 topics, each 2-5 words,
   covering the document's main themes and consistent with course
   metadata. NO long sentences, NO duplicates, NO obvious synonyms.
   Logical order (from most fundamental to most specific).

PRINCIPLES:
- BASE ON THE DOCUMENT: key topics must reflect the document's actual
  content. If the document covers only a sub-theme, restrict accordingly.
- METADATA CONSISTENCY: if the target audience is "undergraduate
  students", don't propose master-level objectives; if depth is
  "introductory", don't discuss research state-of-the-art.
- LANGUAGE: use the language indicated in METADATA > Course language.
- NO HALLUCINATIONS: don't add topics not present in the document just
  to cover the metadata. If the document doesn't cover something, omit.
- Respect copyright: don't quote document sentences literally.

Output: ONLY valid JSON conforming to the schema."""


def _system_prompt(language_code: str) -> str:
    # IT default; en/* in inglese; altri locales in inglese (il prompt e'
    # istruzioni meta, mentre l'output sara' nella lingua del corso).
    return (
        _SYSTEM_PROMPT_IT
        if (language_code or "it").lower().split("-")[0] == "it"
        else _SYSTEM_PROMPT_EN
    )


# JSON Schema strict per response_format.
COURSE_OBJECTIVES_JSON_SCHEMA: dict[str, Any] = {
    "name": "course_objectives_generation",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "objectives": {"type": "string"},
            "argomenti_chiave": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["objectives", "argomenti_chiave"],
        "additionalProperties": False,
    },
}


async def generate_objectives_and_topics(
    *,
    language_code: str,
    course_context: str,
    document_text: str,
    source_filename: str,
) -> tuple[CourseObjectivesGenerationOutput, dict[str, Any]]:
    """Genera obiettivi + argomenti chiave dal documento + contesto corso.

    `course_context`: stringa multi-line con titolo, lingua, tassonomie,
        CFU, ecc. del corso (costruita dal caller).
    `document_text`: testo del documento (gia' estratto e troncato a
        `settings.course_document_max_chars` dal caller).
    `source_filename`: nome file di origine, per il log e per dare
        contesto al modello.

    Ritorna `(output, usage)` dove `usage = {prompt, completion, total, model}`.
    Solleva `OpenAICourseObjectivesError` su errore HTTP/parse/schema.
    """
    if not document_text.strip():
        raise OpenAICourseObjectivesError(
            status=None,
            message=(
                "Documento privo di testo estraibile (forse scansione? "
                "OCR non supportato)."
            ),
        )

    settings = get_settings()
    user_message = (
        f"METADATI DEL CORSO:\n{course_context}\n\n"
        f"DOCUMENTO DI RIFERIMENTO (file: {source_filename}, "
        f"potrebbe essere stato troncato):\n\n{document_text}"
    )
    body = {
        "model": settings.openai_objectives_model,
        "messages": [
            {"role": "system", "content": _system_prompt(language_code)},
            {"role": "user", "content": user_message},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": COURSE_OBJECTIVES_JSON_SCHEMA,
        },
        "temperature": 0.3,
        "max_tokens": settings.openai_objectives_max_tokens,
    }
    log.info(
        "openai_course_objectives_request",
        filename=source_filename,
        chars=len(document_text),
        language=language_code,
        model=settings.openai_objectives_model,
    )
    try:
        async with get_client(timeout=300.0) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_course_objectives_http_error", error=str(exc))
        raise OpenAICourseObjectivesError(
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
            "openai_course_objectives_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAICourseObjectivesError(
            status=resp.status_code,
            message=message
            or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_course_objectives_unexpected_response", payload=data)
        raise OpenAICourseObjectivesError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error(
            "openai_course_objectives_json_decode_failed",
            content=content[:500],
        )
        raise OpenAICourseObjectivesError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    try:
        output = CourseObjectivesGenerationOutput.model_validate(parsed)
    except Exception as exc:
        log.error("openai_course_objectives_schema_invalid", error=str(exc))
        raise OpenAICourseObjectivesError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
        ) from exc

    usage_raw = data.get("usage") or {}
    usage = {
        "prompt": int(usage_raw.get("prompt_tokens") or 0),
        "completion": int(usage_raw.get("completion_tokens") or 0),
        "total": int(usage_raw.get("total_tokens") or 0),
        "model": settings.openai_objectives_model,
    }
    log.info(
        "openai_course_objectives_response",
        filename=source_filename,
        objectives_chars=len(output.objectives),
        topics_count=len(output.argomenti_chiave),
        tokens=usage["total"],
        model=usage["model"],
    )
    return output, usage
