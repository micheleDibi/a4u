"""Riassunto AI sincrono di un paper scientifico (4 sezioni).

Output strutturato JSON validato da `PaperAISummaryOut`:
- short_summary: riassunto breve (200-400 char)
- technical_summary: riassunto tecnico (600-1200 char)
- keywords: 5-10 parole chiave
- study_limitations: limiti dello studio (200-500 char)

Lingua: lingua del corso (`course.language_code`).

Pattern speculare a `openai_course_objectives_service`: sincrono,
JSON schema strict, niente persistenza.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.paper_ai_summary import PaperAISummaryOut
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    get_client,
)

log = get_logger("app.openai_paper_summary")


class OpenAIPaperSummaryError(OpenAIError):
    """Errore specifico del riassunto AI di un paper."""


_SYSTEM_PROMPT_IT = """\
Sei un ricercatore esperto di analisi della letteratura scientifica.
Il tuo compito e' produrre un'analisi strutturata di un paper a partire
da titolo, abstract, autori e metadata. L'output deve essere conciso
ma denso di informazioni, utile a un docente universitario che valuta
se includere il paper nel materiale di un corso.

Genera 4 sezioni:

1. SHORT_SUMMARY (riassunto breve, 200-400 caratteri): in 2-3 frasi
   chiare, descrivi cosa fa il paper (obiettivo) e qual e' il risultato
   o contributo principale. Linguaggio semplice, no jargon eccessivo.

2. TECHNICAL_SUMMARY (riassunto tecnico, 600-1200 caratteri): paragrafo
   discorsivo con piu' dettaglio: contesto / problema affrontato,
   metodologia o approccio adottato, dati o esperimenti se rilevanti,
   risultati con eventuali metriche / dimensioni dell'effetto, e
   conclusioni principali. Linguaggio tecnico appropriato alla
   disciplina inferita dal paper. NON usare bullet markdown.

3. KEYWORDS (5-10 parole chiave): concetti, metodi, tecniche, dataset,
   ambiti applicativi presenti nel paper. Ogni keyword 2-4 parole.
   NO duplicati, NO sinonimi evidenti.

4. STUDY_LIMITATIONS (limiti dello studio, 200-500 caratteri): basandoti
   su quanto inferibile da abstract e contesto, indica i limiti
   metodologici plausibili (es. campione piccolo, dominio specifico,
   mancanza di replicazione, dataset proprietario, ecc.). Se i limiti
   non sono inferibili dall'abstract, indicalo esplicitamente come
   "Limiti non chiaramente desumibili dall'abstract" e prosegui con
   eventuali considerazioni generali sul tipo di studio.

PRINCIPI:
- Usa la LINGUA indicata nei METADATI > Lingua del corso (NON la
  lingua dell'abstract). Esempio: corso in italiano e abstract in
  inglese -> tutte e 4 le sezioni IN ITALIANO.
- NON inventare: se l'abstract non parla di una metrica o di un
  dataset, non citarlo nel riassunto.
- NO traduzioni letterali dell'abstract: parafrasa.
- Rispetta il copyright: niente citazioni testuali.

Output: SOLO JSON valido conforme allo schema."""


_SYSTEM_PROMPT_EN = """\
You are an expert researcher analyzing scientific literature. Your
task is to produce a structured analysis of a paper from its title,
abstract, authors and metadata. The output must be concise yet
information-dense, helpful to a university instructor evaluating
whether to include the paper in course material.

Generate 4 sections:

1. SHORT_SUMMARY (brief summary, 200-400 characters): 2-3 clear
   sentences describing the paper's objective and main result or
   contribution. Simple language, no excessive jargon.

2. TECHNICAL_SUMMARY (technical summary, 600-1200 characters): a
   narrative paragraph with more detail: context/problem addressed,
   methodology or approach, data or experiments if relevant, results
   with metrics/effect sizes when available, main conclusions. Use
   technical language appropriate to the inferred discipline. NO
   markdown bullets.

3. KEYWORDS (5-10 keywords): concepts, methods, techniques, datasets,
   application domains present in the paper. Each 2-4 words. NO
   duplicates, NO obvious synonyms.

4. STUDY_LIMITATIONS (limitations, 200-500 characters): based on what
   can be inferred from abstract and context, indicate plausible
   methodological limitations (small sample, specific domain, lack
   of replication, proprietary dataset, etc.). If limitations are
   not inferable, say so explicitly and provide general remarks on
   the study type.

PRINCIPLES:
- Use the LANGUAGE indicated in METADATA > Course language (NOT the
  abstract language). E.g., Italian course + English abstract -> all
  4 sections IN ITALIAN.
- DO NOT hallucinate: don't mention metrics or datasets not in the
  abstract.
- NO literal translations of the abstract: paraphrase.
- Respect copyright: no verbatim quotes.

Output: ONLY valid JSON conforming to the schema."""


def _system_prompt(language_code: str) -> str:
    return (
        _SYSTEM_PROMPT_IT
        if (language_code or "it").lower().split("-")[0] == "it"
        else _SYSTEM_PROMPT_EN
    )


PAPER_SUMMARY_JSON_SCHEMA: dict[str, Any] = {
    "name": "paper_ai_summary",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "short_summary": {"type": "string"},
            "technical_summary": {"type": "string"},
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
            },
            "study_limitations": {"type": "string"},
        },
        "required": [
            "short_summary",
            "technical_summary",
            "keywords",
            "study_limitations",
        ],
        "additionalProperties": False,
    },
}


async def generate_paper_summary(
    *,
    language_code: str,
    paper_context: str,
    course_context: str | None = None,
) -> tuple[PaperAISummaryOut, dict[str, Any]]:
    """Genera il riassunto AI di un paper.

    `paper_context`: stringa multi-line con titolo, autori, anno,
        journal, abstract, eventuale tldr, subjects, DOI. Costruita
        dal caller (endpoint).
    `course_context`: opzionale, contesto del corso per calibrare il
        riassunto sul livello dei destinatari.

    Ritorna `(output, usage)` dove `usage = {prompt, completion, total, model}`.
    Solleva `OpenAIPaperSummaryError` su errore HTTP/parse/schema.
    """
    if not paper_context.strip():
        raise OpenAIPaperSummaryError(
            status=None, message="Contesto paper vuoto."
        )

    settings = get_settings()
    parts = [
        "METADATI DEL CORSO (lingua dell'output AI):",
        f"Lingua del corso: {language_code}",
    ]
    if course_context:
        parts.append(course_context)
    parts.append("")
    parts.append("PAPER DA ANALIZZARE:")
    parts.append(paper_context)
    user_message = "\n".join(parts)

    body = {
        "model": settings.openai_paper_summary_model,
        "messages": [
            {"role": "system", "content": _system_prompt(language_code)},
            {"role": "user", "content": user_message},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": PAPER_SUMMARY_JSON_SCHEMA,
        },
        "temperature": 0.3,
        "max_tokens": settings.openai_paper_summary_max_tokens,
    }
    log.info(
        "openai_paper_summary_request",
        language=language_code,
        chars=len(paper_context),
        model=settings.openai_paper_summary_model,
    )
    try:
        async with get_client(timeout=120.0) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_paper_summary_http_error", error=str(exc))
        raise OpenAIPaperSummaryError(
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
            "openai_paper_summary_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAIPaperSummaryError(
            status=resp.status_code,
            message=message
            or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_paper_summary_unexpected_response", payload=data)
        raise OpenAIPaperSummaryError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error(
            "openai_paper_summary_json_decode_failed", content=content[:500]
        )
        raise OpenAIPaperSummaryError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    try:
        output = PaperAISummaryOut.model_validate(parsed)
    except Exception as exc:
        log.error("openai_paper_summary_schema_invalid", error=str(exc))
        raise OpenAIPaperSummaryError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
        ) from exc

    usage_raw = data.get("usage") or {}
    usage = {
        "prompt": int(usage_raw.get("prompt_tokens") or 0),
        "completion": int(usage_raw.get("completion_tokens") or 0),
        "total": int(usage_raw.get("total_tokens") or 0),
        "model": settings.openai_paper_summary_model,
    }
    log.info(
        "openai_paper_summary_response",
        tokens=usage["total"],
        model=usage["model"],
    )
    return output, usage
