"""Generazione delle slide di una lezione (Fase 4, §7).

Una chiamata API per lezione: prende in input il testo completo della
lezione (output di Fase 3) e produce la sequenza di slide dimensionata
sui `minuti_per_lezione` del corso. Le slide RIUSANO gli asset di Fase
3 referenziandoli per ID; possono produrre `new_assets` solo se non c'è
un asset adatto a coprire il bisogno.

Errori → `OpenAILessonSlidesError` (sottoclasse di `OpenAIError`).
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.course_lesson_slides import LessonSlidesOutput
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    apply_reasoning_effort,
    get_client,
)
from app.services.openai_pricing import build_usage_dict

log = get_logger("app.openai_lesson_slides")


class OpenAILessonSlidesError(OpenAIError):
    """Errore specifico delle chiamate di generazione slide lezione (§7)."""


def _system_prompt(language_code: str) -> str:
    """System prompt §7.1 — slide design didattico per una lezione."""
    return f"""\
Sei un esperto di didattica e di slide design universitario. Hai
ricevuto il testo completo di una lezione (con asset visivi già
prodotti) e devi trasformarlo in una sequenza di SLIDE per una
lezione di {{minuti_per_lezione}} minuti.

PRINCIPI

1. RIUSO DEGLI ASSET: gli asset di Fase 3 (visual_assets, tables,
   equations, examples) sono già stati creati. Quando una slide ne
   ha bisogno, REFERENZIALI tramite il loro ID nel campo
   `references_assets`. NON ricreare lo stesso contenuto.

2. UNA SLIDE DEDICATA PER OGNI ASSET VISIVO E PER OGNI TABELLA
   (regola tassativa, vale identica per slide e video):
   - Ogni asset visivo (`visual_assets`: diagrammi Mermaid e
     immagini) e ogni tabella (`tables`) va su una SLIDE TUTTA SUA,
     separata. NON va MAI inserito in una slide di contenuto.
   - Una slide dedicata referenzia ESATTAMENTE UN asset visivo o
     UNA tabella: `references_assets` contiene quell'unico ID. È
     VIETATO referenziare due o più asset visivi/tabelle nella
     stessa slide — se servono due diagrammi, fai due slide.
   - La slide dedicata ha: `title` chiaro che introduce l'asset,
     `body` breve (1-2 frasi) che lo spiega/contestualizza, zero
     bullet (o pochissimi). `type` = `diagram` per un
     `visual_asset`, `table` per una `tables`.
   - Le slide di contenuto (concept, definition, summary, ...)
     portano la prosa e i bullet e NON contengono diagrammi,
     immagini o tabelle: la loro `references_assets` resta vuota.
   - ECCEZIONE — equazioni ed esempi NON sono asset visivi:
     `equations` ed `examples` possono restare inline in una slide
     di contenuto/formula/example, anche più d'uno, senza slide
     dedicata. Il limite "uno per slide" vale SOLO per asset
     visivi e tabelle.

3. NUOVI ASSET solo se necessario: puoi proporre nuovi asset in
   `new_assets` solo se il contenuto del testo richiede una
   visualizzazione che NON è già stata prodotta in Fase 3 (es. uno
   schema di sintesi, un'icona di sezione, un grafico di confronto
   non presente). Usa lo stesso formato di Fase 3 (mermaid/latex/
   markdown/image_prompt). Per evitare collisioni di ID, prefissa con
   `*_new_*` (es. `fig_new_1`, `tab_new_2`). Anche i `new_assets`
   seguono il punto 2: una slide dedicata ciascuno.

4. NUMERO DI SLIDE: stima ~2-3 minuti per slide di contenuto, meno
   per slide di apertura/transizione/agenda. Anche le lezioni brevi
   richiedono un overhead strutturale fisso (~6 slide: titolo, agenda,
   prerequisiti, sintesi, takeaways, riferimenti). Per
   {{minuti_per_lezione}} minuti, target indicativo delle slide di
   contenuto + struttura:
   - 15 min →  6-10 slide   (overhead strutturale + 1-3 di contenuto)
   - 20 min →  8-12 slide
   - 30 min → 12-15 slide
   - 45 min → 18-23 slide
   - 60 min → 22-30 slide
   - 90 min → 32-42 slide
   A questi numeri si AGGIUNGE una slide dedicata per ogni asset
   visivo e per ogni tabella (punto 2): con 5 asset visivi e 2
   tabelle il totale cresce di ~7 slide. Adatta in funzione della
   densità del contenuto.

5. STRUTTURA STANDARD:
   - 1 slide titolo
   - 1 slide agenda/obiettivi della lezione
   - 0-1 slide richiamo prerequisiti (se non introduttiva)
   - sviluppo dei contenuti seguendo le sezioni del testo, con slide
     di tipo concept, definition, example, formula come appropriato;
     SUBITO DOPO la slide di contenuto che introduce un asset visivo
     o una tabella, inserisci la slide dedicata a quell'asset (tipo
     diagram / table) — così il discorso scorre in modo coerente
   - 1+ slide di sintesi
   - 1 slide takeaways
   - 1 slide riferimenti (per lezione introduttiva: anche bibliografia
     consigliata)

6. CONTENUTO PER SLIDE
   - title: max 8 parole, evocativo ma chiaro
   - body: opzionale, 1-3 frasi di prosa breve (max ~50 parole, ~400
     caratteri) per accompagnare/contestualizzare i bullet o
     sostituirli quando il contenuto è meglio espresso in forma
     discorsiva. È IMPORTANTE alternare slide bullet-only e slide
     con body+bullet o body-only: una sequenza di sole bullet è
     visivamente piatta e pesante da leggere. Tipicamente:
       * title slide → body 1 frase (sottotitolo)
       * concept/definition → body 2-3 frasi + 0-3 bullet di esempio
       * slide dedicata diagram/table → body 1-2 frasi che
         introducono l'asset, 0 bullet
       * agenda/takeaways → body vuoto, 3-6 bullet
       * summary → body 1-2 frasi conclusive
   - bullets: 0-6 punti, max ~14 parole per punto. Linguaggio adatto
     al livello EQF {{livello_eqf}}. Le slide dedicate a un asset
     visivo/tabella hanno 0 bullet (o pochissimi).
   - references_assets: SOLO sulle slide dedicate, con UN SOLO ID di
     asset visivo o tabella. Le slide di contenuto la lasciano vuota
     (gli `equations`/`examples` inline sono l'unica eccezione).
   - source_section_id: la sezione del testo da cui questa slide è
     derivata (utile per validare la copertura). Vuoto per slide
     strutturali (title, agenda, ...). La slide dedicata a un asset
     usa la stessa `source_section_id` della slide di contenuto che
     lo introduce.

7. TIPI DI SLIDE: title, agenda, prerequisites, concept, definition,
   diagram, formula, table, example, case_study, exercise, discussion,
   summary, takeaways, references, bibliography (solo introduttive).

8. CASO SPECIALE — LEZIONE INTRODUTTIVA:
   - Slide di benvenuto e presentazione del corso
   - Slide con la struttura del corso (se serve un diagramma/mappa,
     è una slide dedicata a quell'unico diagramma)
   - Slide prerequisiti (cosa serve sapere)
   - Slide "come lavoreremo" (stile didattico)
   - Slide bibliografia (1-2 slide con i testi consigliati)

DIVIETI ASSOLUTI NELLE SLIDE
- NON citare codici tecnici interni come `M1.L1`, `T1`, `S2`, `asset_id`
  nel testo visibile (titoli, bullet, caption). Sono identificatori di
  sistema. Se devi richiamare un'altra lezione, usa il TITOLO.
- `slide_id` come `S01`, `S02` è OK per uso interno (mai visibile).

VINCOLI DI VALIDAZIONE (rispetta sempre)
- `total_slides == len(slides)`
- `slide_number` univoci e sequenziali 1, 2, ..., N
- ogni `references_assets[i]` deve essere un asset_id presente
  in Fase 3 (visual_assets, tables, equations, examples) OPPURE in
  `new_assets`
- NESSUNA slide può referenziare più di UN asset visivo
  (`visual_assets` / `new_assets`) o tabella (`tables`): al massimo 1
  in totale fra i due. Equazioni ed esempi NON rientrano in questo
  limite. Una slide di contenuto non ne referenzia nessuno.
- ogni `source_section_id` non vuoto deve referenziare una sezione
  esistente nel testo della lezione
- ogni sezione del testo dovrebbe essere referenziata da almeno una
  slide (best effort)

LINGUA — REGOLA TASSATIVA
TUTTO il testo leggibile dall'utente DEVE essere scritto in {language_code}: `title`,
`body` e `bullets` di OGNI slide, e OGNI campo testuale degli asset, inclusi i NUOVI
asset di Fase 4. In particolare:
- `caption` e `alt_text` di `new_assets`, e le ETICHETTE/testo dei nodi DENTRO il loro
  codice Mermaid (le label, NON la sintassi);
- `caption`, intestazioni e celle (`markdown`) di `new_tables`;
- `label`, `statement`, `explanation` e il `text` di ogni passo di `proof` in `new_equations`;
- `title` e `content` di `new_examples`.
Restano invariati SOLO: la notazione matematica LaTeX (campi `latex`), la struttura
sintattica di Mermaid (tipo di diagramma, frecce, ID dei nodi), gli ID e gli `slide_id`.
NON lasciare in nessun campo testo in un'altra lingua (es. italiano): traduci tutto in
{language_code}.
Output: SOLO JSON valido conforme allo schema."""


# Addendum §9.4 — appeso al system prompt quando si rigenerano slide già
# generate (con o senza `regeneration_hint`).
REGENERATION_SUFFIX = """\

ATTENZIONE: stai RIGENERANDO le slide di una lezione già slidificata.
Considera la versione precedente e il feedback del docente.
- Il testo della lezione (Fase 3) è invariato e va rispettato.
- Mantieni gli stessi asset_id già presenti in Fase 3.
- Cambia struttura, numero, ordine o contenuto delle slide secondo
  il feedback.
- Se possibile, mantieni lo stesso slide_id per slide che corrispondono
  semanticamente alla versione precedente (utile per riusare il
  discorso esistente nella futura Fase 5)."""


# JSON Schema verbatim §7.3 — passato a OpenAI come response_format.json_schema.
LESSON_SLIDES_JSON_SCHEMA: dict[str, Any] = {
    "name": "lesson_slides",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "lesson_id": {"type": "string"},
            "total_slides": {"type": "integer"},
            "slides": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "slide_number": {"type": "integer"},
                        "slide_id": {
                            "type": "string",
                            "description": "Es. 'S01', 'S02'",
                        },
                        "type": {
                            "type": "string",
                            "enum": [
                                "title",
                                "agenda",
                                "prerequisites",
                                "concept",
                                "definition",
                                "diagram",
                                "formula",
                                "table",
                                "example",
                                "case_study",
                                "exercise",
                                "discussion",
                                "summary",
                                "takeaways",
                                "references",
                                "bibliography",
                            ],
                        },
                        "title": {"type": "string"},
                        "body": {
                            "type": "string",
                            "description": (
                                "Prosa breve (1-3 frasi) di contesto/"
                                "descrizione. Vuota se la slide è "
                                "puramente bullet o schematica."
                            ),
                        },
                        "bullets": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "references_assets": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "source_section_id": {"type": "string"},
                    },
                    "required": [
                        "slide_number",
                        "slide_id",
                        "type",
                        "title",
                        "body",
                        "bullets",
                        "references_assets",
                        "source_section_id",
                    ],
                    "additionalProperties": False,
                },
            },
            "new_assets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "asset_id": {"type": "string"},
                        "asset_type": {
                            "type": "string",
                            "enum": [
                                "diagram",
                                "schema",
                                "image",
                                "illustration",
                                "chart",
                            ],
                        },
                        "format": {
                            "type": "string",
                            "enum": [
                                "mermaid",
                                "image_prompt",
                                "image_search_query",
                                "description",
                            ],
                        },
                        "content": {"type": "string"},
                        "caption": {"type": "string"},
                        "alt_text": {"type": "string"},
                    },
                    "required": [
                        "asset_id",
                        "asset_type",
                        "format",
                        "content",
                        "caption",
                        "alt_text",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["lesson_id", "total_slides", "slides", "new_assets"],
        "additionalProperties": False,
    },
}


async def generate_lesson_slides(
    *,
    user_prompt: str,
    language_code: str,
    is_regeneration: bool,
) -> tuple[LessonSlidesOutput, dict[str, Any]]:
    """Chiama OpenAI per generare le slide di una lezione.

    Ritorna `(slides, usage)` dove `usage` è un dict con i conteggi
    token. Solleva `OpenAILessonSlidesError` su errore HTTP/parsing/
    schema. Solleva `OpenAINotConfiguredError` se la API key è assente.

    NOTA: come per Fase 3, `gpt-5.5` richiede `max_completion_tokens`
    (non `max_tokens`) e non accetta `temperature` custom (default 1.0).
    Output tipico per slide: 4-8k token + reasoning, quindi
    `OPENAI_LESSON_SLIDES_MAX_TOKENS` deve partire da ~16000 per non
    troncare.
    """
    settings = get_settings()
    system_prompt = _system_prompt(language_code)
    if is_regeneration:
        system_prompt = system_prompt + REGENERATION_SUFFIX

    body = {
        "model": settings.openai_lesson_slides_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": LESSON_SLIDES_JSON_SCHEMA,
        },
        "max_completion_tokens": settings.openai_lesson_slides_max_tokens,
    }
    apply_reasoning_effort(
        body,
        settings.openai_lesson_slides_model,
        settings.openai_lesson_slides_reasoning_effort,
    )
    log.info(
        "openai_lesson_slides_request",
        chars=len(user_prompt),
        regeneration=is_regeneration,
        model=settings.openai_lesson_slides_model,
        reasoning_effort=body.get("reasoning_effort"),
    )
    t0 = time.monotonic()
    try:
        async with get_client(timeout=600.0) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_lesson_slides_http_error", error=str(exc))
        raise OpenAILessonSlidesError(
            status=None, message=f"Errore HTTP verso OpenAI: {exc}"
        ) from exc
    duration_ms = int((time.monotonic() - t0) * 1000)

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
            "openai_lesson_slides_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAILessonSlidesError(
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
        log.error("openai_lesson_slides_unexpected_response", payload=data)
        raise OpenAILessonSlidesError(
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
            "openai_lesson_slides_empty_content",
            finish_reason=finish_reason,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            max_tokens=settings.openai_lesson_slides_max_tokens,
        )
        if finish_reason == "length":
            raise OpenAILessonSlidesError(
                status=resp.status_code,
                message=(
                    f"OpenAI ha esaurito i token disponibili "
                    f"(reasoning={reasoning_tokens}, completion={completion_tokens}, "
                    f"cap={settings.openai_lesson_slides_max_tokens}). "
                    f"Aumenta OPENAI_LESSON_SLIDES_MAX_TOKENS."
                ),
                payload=data,
            )
        raise OpenAILessonSlidesError(
            status=resp.status_code,
            message=(
                f"OpenAI ha restituito un contenuto vuoto "
                f"(finish_reason={finish_reason}, reasoning={reasoning_tokens}). "
                f"Riprova; se persiste aumenta OPENAI_LESSON_SLIDES_MAX_TOKENS."
            ),
            payload=data,
        )

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error(
            "openai_lesson_slides_json_decode_failed", content=content[:500]
        )
        raise OpenAILessonSlidesError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    try:
        lesson_slides = LessonSlidesOutput.model_validate(parsed)
    except Exception as exc:
        log.error("openai_lesson_slides_schema_invalid", error=str(exc))
        raise OpenAILessonSlidesError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
        ) from exc

    usage = build_usage_dict(
        model=settings.openai_lesson_slides_model,
        reasoning_effort_setting=settings.openai_lesson_slides_reasoning_effort,
        openai_usage=data.get("usage") or {},
        duration_ms=duration_ms,
    )
    log.info(
        "openai_lesson_slides_response",
        lesson_id=lesson_slides.lesson_id,
        total_slides=lesson_slides.total_slides,
        new_assets=len(lesson_slides.new_assets),
        tokens=usage["total"],
        duration_ms=usage["duration_ms"],
        cost_usd=usage["cost_usd"],
    )
    return lesson_slides, usage
