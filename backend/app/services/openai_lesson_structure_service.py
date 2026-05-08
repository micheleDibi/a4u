"""Generazione della struttura formativa delle lezioni (Fase 2, §5).

Implementa il system+user prompt e lo schema descritti in
`prompt_generazione_corsi.md` §5.1-5.3 e l'addendum §9.2 per la
rigenerazione mirata di un modulo. Output JSON conforme al JSON Schema,
validato con Pydantic prima di essere materializzato sui campi
`learning_objectives` / `mandatory_topics` / `prerequisites` /
`section_outline` di `course_lesson`.

Errori → `OpenAILessonStructureError` (sottoclasse di `OpenAIError`).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.course_lesson_structure import LessonStructureModuleOutput
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    apply_reasoning_effort,
    get_client,
)

log = get_logger("app.openai_lesson_structure")


class OpenAILessonStructureError(OpenAIError):
    """Errore specifico delle chiamate di generazione struttura lezioni (§5)."""


# System prompt — copia testuale di §5.1, parametrizzato sulla lingua.
def _system_prompt(language_code: str) -> str:
    return f"""\
Sei un instructional designer esperto nella progettazione didattica
universitaria. Hai già definito l'architettura del corso e ora devi
specificare la struttura formativa delle lezioni di un singolo modulo.

Per OGNI lezione devi produrre:

1. OBIETTIVI FORMATIVI (3-6 per lezione), formulati con verbi della
   tassonomia di Bloom rivisitata, allineati al livello EQF:
   - EQF 5-6: spiegare, applicare, distinguere, calcolare
   - EQF 7: analizzare, valutare, integrare, formulare
   - EQF 8: criticare, sintetizzare originalmente, formulare ipotesi
   Ogni obiettivo inizia con "Lo studente sarà in grado di..." ed è
   osservabile/valutabile.

2. TEMI OBBLIGATORI (3-7 per lezione): punti di contenuto concreti
   (NON generici). Ogni tema ha un `topic_id` stabile e un `rationale`.

3. PREREQUISITI (eventuali): conoscenze richieste prima della lezione.
   Possono essere riferimenti a temi di lezioni precedenti.

4. SECTION OUTLINE (3-7 sezioni): scaletta logica della lezione, in
   ordine. Per ogni sezione: section_id, title, purpose, covers_topic_ids.

CASO SPECIALE — LEZIONE INTRODUTTIVA (is_introductory=true):
Se la lezione in input è marcata come introduttiva, la sua struttura
è diversa:
- Obiettivi formativi: 3-5, focalizzati su orientamento ("inquadrare
  il dominio del corso", "riconoscere la struttura del percorso",
  "identificare i prerequisiti necessari", ecc.)
- Temi obbligatori devono includere ALMENO:
  T1: presentazione del corso e dei suoi obiettivi
  T2: descrizione della struttura modulare e del percorso
  T3: prerequisiti e attese verso gli studenti
  T4: bibliografia e materiali di studio
  altri 0-3 temi a discrezione (es. modalità di valutazione)
- Section outline: tipicamente "Benvenuto e contesto", "Obiettivi del
  corso", "Struttura e percorso", "Cosa serve sapere", "Materiali e
  bibliografia", "Come lavoreremo insieme".

PRINCIPI:
- Coerenza con livello EQF e profondità di contenuto
- Allineamento agli obiettivi globali del corso
- Uso prioritario dei concetti dei documenti
- Nessuna sovrapposizione tra lezioni dello stesso modulo
- Continuità con i moduli precedenti (se forniti)
- Ogni tema obbligatorio coperto in almeno una sezione

Lingua di output: {language_code}.
Output: SOLO JSON valido conforme allo schema."""


# Addendum §9.2 — appeso al system prompt quando si rigenera un modulo
# già strutturato (con o senza `regeneration_hint`).
REGENERATION_SUFFIX = """\

ATTENZIONE: stai RIGENERANDO un modulo già strutturato. Tieni in
considerazione la versione attuale e il feedback del docente.
- Mantieni invariate le parti che non sono in conflitto con il feedback.
- Cambia ciò che il feedback richiede esplicitamente.
- Mantieni la coerenza con i moduli precedenti e successivi.
- Mantieni invariati gli ID delle lezioni se la corrispondenza
  semantica è preservata; cambiali solo se la lezione è stata
  sostituita radicalmente."""


# JSON Schema verbatim §5.3 — passato a OpenAI come response_format.json_schema.
LESSON_STRUCTURE_JSON_SCHEMA: dict[str, Any] = {
    "name": "module_lesson_structure",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "module_id": {"type": "string"},
            "lessons": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "lesson_id": {"type": "string"},
                        "title": {"type": "string"},
                        "is_introductory": {"type": "boolean"},
                        "learning_objectives": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "mandatory_topics": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "topic_id": {"type": "string"},
                                    "topic": {"type": "string"},
                                    "rationale": {"type": "string"},
                                },
                                "required": ["topic_id", "topic", "rationale"],
                                "additionalProperties": False,
                            },
                        },
                        "prerequisites": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "section_outline": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "section_id": {"type": "string"},
                                    "title": {"type": "string"},
                                    "purpose": {"type": "string"},
                                    "covers_topic_ids": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": [
                                    "section_id",
                                    "title",
                                    "purpose",
                                    "covers_topic_ids",
                                ],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": [
                        "lesson_id",
                        "title",
                        "is_introductory",
                        "learning_objectives",
                        "mandatory_topics",
                        "prerequisites",
                        "section_outline",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["module_id", "lessons"],
        "additionalProperties": False,
    },
}


async def generate_lesson_structure(
    *,
    user_prompt: str,
    language_code: str,
    is_regeneration: bool,
) -> tuple[LessonStructureModuleOutput, dict[str, Any]]:
    """Chiama OpenAI per generare la struttura delle lezioni di un modulo.

    Ritorna `(structure, usage)` dove `usage` è un dict con i conteggi
    token. Solleva `OpenAILessonStructureError` su errore HTTP/parsing/
    schema. Solleva `OpenAINotConfiguredError` se la API key è assente.

    NOTA: come per Fase 1, il modello `gpt-5.5` richiede
    `max_completion_tokens` (NON `max_tokens`) e non accetta
    `temperature` custom (solo default 1.0).
    """
    settings = get_settings()
    system_prompt = _system_prompt(language_code)
    if is_regeneration:
        system_prompt = system_prompt + REGENERATION_SUFFIX

    body = {
        "model": settings.openai_lesson_structure_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": LESSON_STRUCTURE_JSON_SCHEMA,
        },
        "max_completion_tokens": settings.openai_lesson_structure_max_tokens,
    }
    apply_reasoning_effort(
        body,
        settings.openai_lesson_structure_model,
        settings.openai_lesson_structure_reasoning_effort,
    )
    log.info(
        "openai_lesson_structure_request",
        chars=len(user_prompt),
        regeneration=is_regeneration,
        model=settings.openai_lesson_structure_model,
        reasoning_effort=body.get("reasoning_effort"),
    )
    try:
        async with get_client(timeout=300.0) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_lesson_structure_http_error", error=str(exc))
        raise OpenAILessonStructureError(
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
            "openai_lesson_structure_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAILessonStructureError(
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
        log.error("openai_lesson_structure_unexpected_response", payload=data)
        raise OpenAILessonStructureError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    # gpt-5.5 può esaurire `max_completion_tokens` con i reasoning tokens
    # prima di emettere il content → content vuoto. Diagnostica esplicita.
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
            "openai_lesson_structure_empty_content",
            finish_reason=finish_reason,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            max_tokens=settings.openai_lesson_structure_max_tokens,
        )
        if finish_reason == "length":
            raise OpenAILessonStructureError(
                status=resp.status_code,
                message=(
                    f"OpenAI ha esaurito i token disponibili "
                    f"(reasoning={reasoning_tokens}, completion={completion_tokens}, "
                    f"cap={settings.openai_lesson_structure_max_tokens}). "
                    f"Aumenta OPENAI_LESSON_STRUCTURE_MAX_TOKENS."
                ),
                payload=data,
            )
        raise OpenAILessonStructureError(
            status=resp.status_code,
            message=(
                f"OpenAI ha restituito un contenuto vuoto "
                f"(finish_reason={finish_reason}, reasoning={reasoning_tokens}). "
                f"Riprova; se persiste aumenta OPENAI_LESSON_STRUCTURE_MAX_TOKENS."
            ),
            payload=data,
        )

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error(
            "openai_lesson_structure_json_decode_failed", content=content[:500]
        )
        raise OpenAILessonStructureError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    try:
        structure = LessonStructureModuleOutput.model_validate(parsed)
    except Exception as exc:
        log.error("openai_lesson_structure_schema_invalid", error=str(exc))
        raise OpenAILessonStructureError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
        ) from exc

    usage_raw = data.get("usage") or {}
    usage = {
        "prompt": int(usage_raw.get("prompt_tokens") or 0),
        "completion": int(usage_raw.get("completion_tokens") or 0),
        "total": int(usage_raw.get("total_tokens") or 0),
        "model": settings.openai_lesson_structure_model,
    }
    log.info(
        "openai_lesson_structure_response",
        module_id=structure.module_id,
        lessons=len(structure.lessons),
        tokens=usage["total"],
    )
    return structure, usage
