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
e dai METADATI del corso, una proposta DETTAGLIATA E RICCA di:

1. OBJECTIVES (obiettivi del corso): testo discorsivo MOLTO DETTAGLIATO
   in lingua del corso. **Lunghezza target: 2500-5000 caratteri** (NON
   piu' breve di 2500). Struttura consigliata:

   a) PARAGRAFO INTRODUTTIVO (~400-600 caratteri): contesto del corso,
      collocazione disciplinare, motivazione formativa, profilo dello
      studente atteso al termine. Spiega PERCHE' questo corso ha senso
      per i destinatari indicati nei metadati.

   b) SEZIONE "Al termine del corso lo studente sara' in grado di:"
      con 6-12 obiettivi formativi espressi come PROSA ARTICOLATA
      (NON come elenco puntato breve). Per ciascun obiettivo:
      - usa un VERBO PERFORMATIVO chiaro all'inizio (comprendere,
        applicare, analizzare, valutare, progettare, sintetizzare,
        confrontare, interpretare, sperimentare, modellare,
        argomentare, ecc.);
      - articola il "cosa" (oggetto specifico dell'apprendimento,
        ancorato ai contenuti del documento) e il "come/perche'"
        (criterio di padronanza, condizioni di applicazione,
        contesto d'uso);
      - mantieni una frase di 200-400 caratteri per obiettivo.
      Distribuisci gli obiettivi su tre dimensioni quando pertinente:
      SAPERE (conoscenze teoriche/concettuali), SAPER FARE
      (competenze applicative/procedurali), SAPER ESSERE
      (atteggiamenti professionali, autonomia di giudizio,
      capacita' comunicative). Non e' obbligatorio etichettare le
      sezioni: integra fluidamente in un testo coeso.

   c) PARAGRAFO CONCLUSIVO (~300-500 caratteri): contesto applicativo
      e prospettive d'uso delle competenze acquisite (per quali studi
      successivi, ruoli professionali, contesti di vita o di ricerca
      saranno utili). Allinea al livello EQF e ai destinatari indicati
      nei metadati.

   Stile: prosa fluida e tecnicamente accurata, con periodi articolati
   ma chiari. NON usare bullet point markdown (-, *), NON usare titoli
   markdown (##). Usa eventualmente paragrafi separati da una riga
   vuota (`\\n\\n`).

2. ARGOMENTI_CHIAVE: lista di 8-15 argomenti, ognuno 2-5 parole, che
   coprono i topic principali del documento e sono coerenti con i
   metadati del corso. NO frasi lunghe, NO duplicati, NO sinonimi
   evidenti. Ordine logico (dal piu' fondamentale al piu' specifico).

PRINCIPI:
- BASATI SUL DOCUMENTO: ogni obiettivo formativo deve ancorarsi a
  contenuti effettivamente presenti nel documento di riferimento. Se
  il documento tratta solo un sotto-tema dei metadati corso, restringi
  la proposta a quel sotto-tema (non inventare oggetti di apprendimento
  non documentati).
- COERENZA CON I METADATI: se i destinatari sono "studenti universitari
  triennale" non proporre obiettivi da master; se la profondita' e'
  "introduttiva" non parlare di stati dell'arte di ricerca; se l'EQF e'
  basso, calibra il livello cognitivo (descrivere/riconoscere) invece
  di alto (valutare criticamente/sintetizzare).
- LINGUA: usa la lingua indicata in METADATI > Lingua del corso.
- NO INVENZIONI: non aggiungere obiettivi o argomenti non presenti nel
  documento solo per coprire i metadati. Se il documento non tratta
  qualcosa, omettilo.
- RICCHEZZA E DETTAGLIO: non essere generico. Cita concetti specifici
  ancorati al documento (es. "i modelli di regressione lineare e
  logistica" invece di "i modelli statistici"). Il valore formativo
  della proposta dipende dalla specificita'.
- Rispetta il copyright: non citare letteralmente frasi del documento;
  parafrasa.

Output: SOLO JSON valido conforme allo schema. Il campo `objectives`
NON deve mai essere piu' breve di 2500 caratteri."""


_SYSTEM_PROMPT_EN = """\
You are an expert in university instructional design. Your task is to
generate, starting from a REFERENCE DOCUMENT provided by the instructor
and the COURSE METADATA, a DETAILED AND RICH proposal of:

1. OBJECTIVES (course objectives): VERY DETAILED narrative text in the
   course language. **Target length: 2500-5000 characters** (no shorter
   than 2500). Recommended structure:

   a) OPENING PARAGRAPH (~400-600 chars): course context, disciplinary
      positioning, formative rationale, expected student profile at
      the end. Explain WHY this course makes sense for the audience
      indicated in the metadata.

   b) SECTION "By the end of the course, the student will be able to:"
      with 6-12 learning objectives expressed as ARTICULATED PROSE
      (NOT short bullet points). For each objective:
      - start with a clear PERFORMATIVE VERB (understand, apply,
        analyze, evaluate, design, synthesize, compare, interpret,
        experiment, model, argue, etc.);
      - articulate the "what" (specific learning object, anchored in
        the document's content) and the "how/why" (mastery criterion,
        application conditions, usage context);
      - keep each objective as a 200-400 character sentence.
      Distribute objectives across three dimensions where relevant:
      KNOWLEDGE (theoretical/conceptual), SKILLS (applied/procedural),
      DISPOSITIONS (professional attitudes, autonomy of judgment,
      communication). It is NOT required to label these sections:
      integrate them smoothly into cohesive text.

   c) CLOSING PARAGRAPH (~300-500 chars): application context and
      perspectives for using the acquired skills (which subsequent
      studies, professional roles, life or research contexts they will
      serve). Align with the EQF level and target audience.

   Style: fluid, technically accurate prose with articulated but clear
   sentences. DO NOT use markdown bullets (-, *), DO NOT use markdown
   headers (##). Optionally separate paragraphs with a blank line
   (`\\n\\n`).

2. ARGOMENTI_CHIAVE (key topics): list of 8-15 topics, each 2-5 words,
   covering the document's main themes and consistent with course
   metadata. NO long sentences, NO duplicates, NO obvious synonyms.
   Logical order (from most fundamental to most specific).

PRINCIPLES:
- BASE ON THE DOCUMENT: every learning objective must anchor to
  content actually present in the reference document. If the document
  covers only a sub-theme of the course metadata, restrict accordingly
  (do not invent learning objects not in the document).
- METADATA CONSISTENCY: if the audience is "undergraduate students",
  don't propose master-level objectives; if depth is "introductory",
  don't discuss research state-of-the-art; if the EQF is low,
  calibrate the cognitive level (describe/recognize) instead of high
  (critically evaluate/synthesize).
- LANGUAGE: use the language indicated in METADATA > Course language.
- NO HALLUCINATIONS: don't add objectives or topics not present in the
  document just to cover the metadata. If the document doesn't cover
  something, omit it.
- RICHNESS AND DETAIL: don't be generic. Mention specific concepts
  anchored to the document (e.g. "linear and logistic regression
  models" instead of "statistical models"). The formative value of
  the proposal depends on its specificity.
- Respect copyright: do not quote document sentences literally;
  paraphrase.

Output: ONLY valid JSON conforming to the schema. The `objectives`
field must NEVER be shorter than 2500 characters."""


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
