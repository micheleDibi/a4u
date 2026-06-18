"""Generazione del testo completo di una lezione (Fase 3, §6).

Una chiamata API per lezione: prende in input la struttura formativa
approvata (Fase 2 — `learning_objectives`, `mandatory_topics`,
`prerequisites`, `section_outline`), il glossario corso e i documenti
di riferimento; produce il testo Markdown della lezione + asset visivi
(Mermaid, formule LaTeX, tabelle, esempi), esercizi auto-studio,
references e coverage_check.

Errori → `OpenAILessonContentError` (sottoclasse di `OpenAIError`).
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.course_lesson_content import (
    LessonAssessmentOutput,
    LessonContentOutput,
)
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    apply_reasoning_effort,
    get_client,
)
from app.services.openai_pricing import build_usage_dict

log = get_logger("app.openai_lesson_content")


class OpenAILessonContentError(OpenAIError):
    """Errore specifico delle chiamate di generazione contenuti lezione (§6)."""


# System prompt — versione rivista per output naturale, senza codici
# tecnici nel testo e senza struttura accademica rigida visibile.
def _system_prompt(language_code: str) -> str:
    return f"""\
Sei un autore di materiale didattico universitario di alto livello.
Il tuo compito è scrivere il TESTO COMPLETO di una singola lezione,
in stile capitolo di manuale o dispensa estesa, partendo dalla sua
struttura formativa già approvata.

REQUISITI — TESTO

- Markdown, in lingua {language_code}.
- Tono coerente con ruolo "{{ruolo_docente}}", stile
  "{{stile_insegnamento}}" e livello EQF {{livello_eqf}}.
- NON usare h1 nel content (riservato al titolo della lezione).
- Anticipa fraintendimenti tipici degli studenti.

- STILE — il testo deve leggersi come prosa didattica scritta da un
  docente, non come scheda tecnica. Spiega in modo discorsivo,
  intercalando definizioni, intuizioni ed esempi quando servono, senza
  mai usare etichette esplicite tipo "Definizione formale",
  "Spiegazione intuitiva", "Esempio:". Segui questi principi:

  - Varia deliberatamente la lunghezza delle frasi: alterna periodi
    lunghi a frasi brevissime, anche di poche parole. Evita un ritmo
    uniforme.
  - Non mantenere una struttura sintattica uniforme; evita schemi
    retorici ripetitivi.
  - Non aprire i paragrafi con connettivi standard (Inoltre, Tuttavia,
    È importante notare). Entra nel merito.
  - Evita formule stereotipate ("è importante notare", "si osserva
    che", "in conclusione", "in questo contesto"), salvo quando
    strettamente necessarie.
  - Non rendere simmetrica la lunghezza dei paragrafi: alcuni concetti
    richiedono poche righe, altri una trattazione molto più ampia.
  - Evita le triadi automatiche. Se un concetto ha due aspetti, dinne
    due; non gonfiarli a tre.
  - Dove pertinente — non in ogni sezione, ma quando il concetto lo
    giustifica — non limitarti a definire: spiega perché un'idea si è
    sviluppata e quali problemi cercava di risolvere.
  - Inserisci, in modo irregolare, osservazioni tipiche di una lezione
    reale: errori frequenti, dubbi comuni, intuizioni maturate nella
    pratica della disciplina.
  - Introduci domande naturali che uno studente potrebbe porsi, ma
    raramente e solo quando la domanda guida davvero il ragionamento;
    non aprire ogni paragrafo con una domanda retorica.
  - La sintesi non deve ripetere: deve aggiungere una prospettiva, non
    elencare i punti già visti.
  - Usa esempi concreti e specifici (numeri, nomi, casi reali della
    disciplina), non generici.

ESEMPI DI STILE DI RIFERIMENTO

Il testo che produci deve avvicinarsi, per ritmo e costruzione, ai
seguenti campioni di prosa (scritti da un autore umano). Non copiarne i
contenuti: imitane la texture. Il primo mostra il RITMO da cercare
(periodi lunghi spezzati da incisi e trattini, esempi concreti,
passaggi che rovesciano la prospettiva). Il secondo mostra il REGISTRO
DIDATTICO (come si spiega un concetto tecnico in prosa: si definisce, si
scioglie la definizione, si chiarisce a cosa serve).

Campione A — ritmo:
<<<
Grazie alla crescita esponenziale del progresso tecnologico, le aziende,
sia di servizi sia di prodotti, stanno diventando — o potrebbero
diventare, se adottassero consapevolmente tali tecnologie — più
performanti e più efficienti. Ma ogni nuova tecnologia introduce anche
nuove vulnerabilità nei sistemi di produzione. Ormai, non sono più solo
i comuni PC da lavoro, i server o i datacenter ad essere connessi in
rete, ma cellulari, climatizzatori, macchinari per lavorazione di
tessuti e simili; e se noi possiamo gestire climatizzatori e macchinari
industriali da remoto, potrebbe farlo anche un hacker.
>>>

Campione B — registro didattico:
<<<
Un file system è quella parte di un sistema operativo responsabile di
gestione e organizzazione dei file. Per gestire un elevato numero di
file, un FS è strutturato in directory, cioè in un insieme di nodi
contenenti informazioni su tutti i file. Una directory è un file
speciale creato con l'obiettivo di risolvere la corrispondenza tra il
nome del file in formato testuale e il suo identificativo interno.
>>>

Replica QUESTO modo di scrivere — non questi argomenti — applicandolo
alla materia della lezione.

DELIMITATORI MATH — REGOLA RIGIDA
- Per math INLINE nel testo Markdown usa SEMPRE `$...$` (es. `$\\varphi$`,
  `$P \\lor \\neg P$`). NON usare `\\(...\\)`, NON usare parentesi tonde
  attorno al comando LaTeX (es. `(\\varphi)` è sbagliato — non viene
  renderizzato).
- Per math DISPLAY (formule centrate su linea propria) nel testo Markdown
  usa SEMPRE `$$...$$`. NON usare `\\[...\\]`. Tuttavia, le formule
  importanti vanno in `equations[]` come asset dedicato e referenziate
  nel testo via `[EQ:equation_id]` invece che inline.

DIVIETI ASSOLUTI NEL TESTO VISIBILE
- NON citare mai nel testo codici tecnici interni come `M1.L1`,
  `M2.L5`, `T1`, `S2`, `asset_id`, `VIS-...`, `FIG-...`. Questi sono
  identificatori di sistema e non devono apparire al lettore.
- Quando vuoi richiamare un'altra lezione del corso, usa il suo
  TITOLO (es. "Nella lezione sulla Trasformata di Fourier abbiamo
  visto..."), MAI il codice.
- Le caption di figure, tabelle, formule devono essere brevi
  descrizioni semantiche; NON includere codici come "[A1]" o
  "Figura M1.L2.01".

CASO SPECIALE — LEZIONE INTRODUTTIVA (is_introductory=true):
- Nessun caso studio o dimostrazione tecnica complessa
- Tono di benvenuto, accessibile, motivante
- Tratta la bibliografia consigliata (riprendi e amplia la
  `recommended_bibliography` data in input, aggiungendo per ogni testo
  un breve commento sul suo ruolo nel corso)
- Spiega "come lavoreremo": lo stile d'aula, le aspettative
- Anteprima dei moduli successivi (richiamati per titolo, non per
  codice)

DIMENSIONAMENTO

Linea guida (non vincolante):
- profondita = introduttivo: ~250-400 parole per tema obbligatorio
- profondita = intermedio: ~400-700 parole per tema
- profondita = avanzato: ~700-1200 parole per tema
- profondita = specialistico: ~1000-1800 parole per tema
+ introduzione (~150-300) + sintesi (~150-300).

REQUISITI — ASSET VISIVI

- 1-3 diagrammi/schemi per lezione (NON per la lezione introduttiva,
  dove sono opzionali e tipicamente 0-1)
- formule LaTeX TUTTE le volte che la disciplina lo richiede
- tabelle quando devi confrontare alternative o riassumere
  classificazioni

Per ogni asset: `asset_id` stabile (uso interno), referenziato almeno
una volta nel testo tramite `[FIG:asset_id]`, `[TAB:asset_id]`,
`[EQ:asset_id]` (questi tag verranno sostituiti dal renderer con
l'asset rendering — non devono apparire al lettore finale, ma servono
al parser). La `caption` è una breve descrizione semantica leggibile.

FORMATI ACCETTATI:
- visual_assets → SOLO `format = "mermaid"`, content = codice Mermaid
  valido. NON generare prompt per immagini, query di ricerca o
  descrizioni testuali: l'utente caricherà eventualmente immagini
  reali a mano dall'editor.
- formula → format = "latex" (senza delimitatori $...$)
- table → format = "markdown"

ALLINEAMENTO

- Ogni obiettivo formativo in almeno una sezione
- Ogni tema obbligatorio in almeno una sezione
- Compila `coverage_check` mappando obiettivi e temi alle sezioni

RIFERIMENTI

- Cita i documenti di riferimento DOVE LI USI
- NON inventare bibliografia. Eventuali letture aggiuntive devono
  essere etichettate `source = "suggerimento_generale"`.

NON GENERARE ESERCIZI: il campo `exercises_for_self_study` non è più
richiesto.

Lingua: {language_code}.
Output: SOLO JSON valido conforme allo schema."""


# Addendum §9.3 — appeso al system prompt quando si rigenera una lezione
# già scritta (con o senza `regeneration_hint`).
REGENERATION_SUFFIX = """\

ATTENZIONE: stai RIGENERANDO una lezione già scritta. Tieni in
considerazione la versione precedente e il feedback del docente.
- Mantieni invariati gli obiettivi formativi e i temi obbligatori
  forniti, a meno che il feedback non li tocchi esplicitamente.
- Riusa gli asset visivi della versione precedente quando ancora
  pertinenti, mantenendo gli stessi asset_id.
- Se il feedback chiede di rimuovere/sostituire un asset, fallo e
  documenta il cambiamento.
- Mantieni stile, lessico e registro coerenti con il resto del corso."""


# JSON Schema verbatim §6.4 — passato a OpenAI come response_format.json_schema.
LESSON_CONTENT_JSON_SCHEMA: dict[str, Any] = {
    "name": "lesson_content",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "lesson_id": {"type": "string"},
            "lesson_title": {"type": "string"},
            "is_introductory": {"type": "boolean"},
            "estimated_word_count": {"type": "integer"},
            "introduction": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section_id": {"type": "string"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "objectives_addressed": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "topics_addressed": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "section_id",
                        "title",
                        "content",
                        "objectives_addressed",
                        "topics_addressed",
                    ],
                    "additionalProperties": False,
                },
            },
            "summary": {"type": "string"},
            "key_takeaways": {
                "type": "array",
                "items": {"type": "string"},
            },
            "visual_assets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "asset_id": {"type": "string"},
                        "format": {
                            "type": "string",
                            "enum": ["mermaid"],
                        },
                        "content": {"type": "string"},
                        "caption": {"type": "string"},
                        "alt_text": {"type": "string"},
                    },
                    "required": [
                        "asset_id",
                        "format",
                        "content",
                        "caption",
                        "alt_text",
                    ],
                    "additionalProperties": False,
                },
            },
            "tables": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "table_id": {"type": "string"},
                        "markdown": {"type": "string"},
                        "caption": {"type": "string"},
                    },
                    "required": ["table_id", "markdown", "caption"],
                    "additionalProperties": False,
                },
            },
            "equations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "equation_id": {"type": "string"},
                        "latex": {"type": "string"},
                        "label": {"type": "string"},
                        "explanation": {"type": "string"},
                    },
                    "required": [
                        "equation_id",
                        "latex",
                        "label",
                        "explanation",
                    ],
                    "additionalProperties": False,
                },
            },
            "examples": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "example_id": {"type": "string"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["example_id", "title", "content"],
                    "additionalProperties": False,
                },
            },
            "references": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "citation": {"type": "string"},
                        "source": {
                            "type": "string",
                            "enum": [
                                "documento_caricato",
                                "suggerimento_generale",
                            ],
                        },
                    },
                    "required": ["citation", "source"],
                    "additionalProperties": False,
                },
            },
            "coverage_check": {
                "type": "object",
                "properties": {
                    "objectives_covered": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "objective": {"type": "string"},
                                "covered_in_section_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": [
                                "objective",
                                "covered_in_section_ids",
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "topics_covered": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "topic_id": {"type": "string"},
                                "covered_in_section_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": [
                                "topic_id",
                                "covered_in_section_ids",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["objectives_covered", "topics_covered"],
                "additionalProperties": False,
            },
        },
        "required": [
            "lesson_id",
            "lesson_title",
            "is_introductory",
            "estimated_word_count",
            "introduction",
            "sections",
            "summary",
            "key_takeaways",
            "visual_assets",
            "tables",
            "equations",
            "examples",
            "references",
            "coverage_check",
        ],
        "additionalProperties": False,
    },
}


async def generate_lesson_content(
    *,
    user_prompt: str,
    language_code: str,
    is_regeneration: bool,
) -> tuple[LessonContentOutput, dict[str, Any]]:
    """Chiama OpenAI per generare il testo completo di una lezione.

    Ritorna `(content, usage)` dove `usage` è un dict con i conteggi
    token. Solleva `OpenAILessonContentError` su errore HTTP/parsing/
    schema. Solleva `OpenAINotConfiguredError` se la API key è assente.

    NOTA: il modello `gpt-5.5` richiede `max_completion_tokens` (NON
    `max_tokens`) e non accetta `temperature` custom (solo default 1.0).
    L'output di una lezione completa è 8-15k token + reasoning, quindi
    `OPENAI_LESSON_CONTENT_MAX_TOKENS` deve partire alto (32000).
    """
    settings = get_settings()
    system_prompt = _system_prompt(language_code)
    if is_regeneration:
        system_prompt = system_prompt + REGENERATION_SUFFIX

    body = {
        "model": settings.openai_lesson_content_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": LESSON_CONTENT_JSON_SCHEMA,
        },
        "max_completion_tokens": settings.openai_lesson_content_max_tokens,
    }
    apply_reasoning_effort(
        body,
        settings.openai_lesson_content_model,
        settings.openai_lesson_content_reasoning_effort,
    )
    log.info(
        "openai_lesson_content_request",
        chars=len(user_prompt),
        regeneration=is_regeneration,
        model=settings.openai_lesson_content_model,
        reasoning_effort=body.get("reasoning_effort"),
    )
    t0 = time.monotonic()
    try:
        # Timeout esteso: lezione completa può richiedere 60-120s di reasoning.
        async with get_client(timeout=600.0) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_lesson_content_http_error", error=str(exc))
        raise OpenAILessonContentError(
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
            "openai_lesson_content_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAILessonContentError(
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
        log.error("openai_lesson_content_unexpected_response", payload=data)
        raise OpenAILessonContentError(
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
            "openai_lesson_content_empty_content",
            finish_reason=finish_reason,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            max_tokens=settings.openai_lesson_content_max_tokens,
        )
        if finish_reason == "length":
            raise OpenAILessonContentError(
                status=resp.status_code,
                message=(
                    f"OpenAI ha esaurito i token disponibili "
                    f"(reasoning={reasoning_tokens}, completion={completion_tokens}, "
                    f"cap={settings.openai_lesson_content_max_tokens}). "
                    f"Aumenta OPENAI_LESSON_CONTENT_MAX_TOKENS."
                ),
                payload=data,
            )
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=(
                f"OpenAI ha restituito un contenuto vuoto "
                f"(finish_reason={finish_reason}, reasoning={reasoning_tokens}). "
                f"Riprova; se persiste aumenta OPENAI_LESSON_CONTENT_MAX_TOKENS."
            ),
            payload=data,
        )

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error(
            "openai_lesson_content_json_decode_failed", content=content[:500]
        )
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    try:
        lesson_content = LessonContentOutput.model_validate(parsed)
    except Exception as exc:
        log.error("openai_lesson_content_schema_invalid", error=str(exc))
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
        ) from exc

    usage = build_usage_dict(
        model=settings.openai_lesson_content_model,
        reasoning_effort_setting=settings.openai_lesson_content_reasoning_effort,
        openai_usage=data.get("usage") or {},
        duration_ms=duration_ms,
    )
    log.info(
        "openai_lesson_content_response",
        lesson_id=lesson_content.lesson_id,
        word_count=lesson_content.estimated_word_count,
        sections=len(lesson_content.sections),
        assets=len(lesson_content.visual_assets),
        tokens=usage["total"],
        duration_ms=usage["duration_ms"],
        cost_usd=usage["cost_usd"],
    )
    return lesson_content, usage


# ---------------------------------------------------------------------------
# Verifica delle competenze (lezione `is_assessment`) — Fase 3
# ---------------------------------------------------------------------------


def _assessment_system_prompt(language_code: str) -> str:
    return f"""\
Sei un docente universitario esperto di valutazione dell'apprendimento.
Il tuo compito è redigere una VERIFICA DELLE COMPETENZE per un modulo di
un corso: un elenco di domande a scelta multipla e di domande aperte che
misurano le competenze e le conoscenze trattate nel modulo.

REQUISITI GENERALI
- Lingua: {language_code}.
- Le domande verificano la PADRONANZA degli argomenti del modulo nel suo
  insieme, non la memoria di una singola lezione.
- DIVIETO ASSOLUTO: non fare MAI riferimento a lezioni specifiche. Non
  scrivere "nella lezione X", "come visto nella lezione...", non citare
  titoli né codici di lezione. Ogni domanda deve essere autoconsistente,
  comprensibile da sola, formulata come verifica di competenza.
- Non citare codici interni (es. M1.L2, T1, S3).
- Copri in modo equilibrato TUTTI gli argomenti forniti in input.
- Varia il livello cognitivo (ricordare, comprendere, applicare, analizzare).

DOMANDE A SCELTA MULTIPLA
- Ogni domanda ha ESATTAMENTE 4 opzioni, con `option_id` "A", "B", "C", "D".
- ESATTAMENTE una opzione è corretta: indicala in `correct_option_id`.
- I distrattori (opzioni errate) devono essere plausibili e pertinenti,
  non palesemente assurdi.
- Evita "tutte le precedenti" / "nessuna delle precedenti".

DOMANDE APERTE
- `text`: la consegna della domanda.
- `expected_answer`: una traccia sintetica della risposta attesa — i
  punti chiave / i criteri che il docente userà per la correzione (non
  un tema svolto per esteso).

QUANTITÀ
- Produci ESATTAMENTE il numero di domande a scelta multipla e di domande
  aperte indicato nell'input.
- `question_id` univoci e brevi (es. "MC1", "MC2", ..., "OP1", "OP2").

Output: SOLO JSON valido conforme allo schema."""


LESSON_ASSESSMENT_JSON_SCHEMA: dict[str, Any] = {
    "name": "lesson_assessment",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "lesson_id": {"type": "string"},
            "lesson_title": {"type": "string"},
            "multiple_choice_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question_id": {"type": "string"},
                        "text": {"type": "string"},
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "option_id": {"type": "string"},
                                    "text": {"type": "string"},
                                },
                                "required": ["option_id", "text"],
                                "additionalProperties": False,
                            },
                        },
                        "correct_option_id": {"type": "string"},
                    },
                    "required": [
                        "question_id",
                        "text",
                        "options",
                        "correct_option_id",
                    ],
                    "additionalProperties": False,
                },
            },
            "open_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question_id": {"type": "string"},
                        "text": {"type": "string"},
                        "expected_answer": {"type": "string"},
                    },
                    "required": ["question_id", "text", "expected_answer"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "lesson_id",
            "lesson_title",
            "multiple_choice_questions",
            "open_questions",
        ],
        "additionalProperties": False,
    },
}


async def generate_lesson_assessment(
    *,
    user_prompt: str,
    language_code: str,
    is_regeneration: bool,
) -> tuple[LessonAssessmentOutput, dict[str, Any]]:
    """Chiama OpenAI per generare la verifica delle competenze di un modulo.

    Stessa configurazione modello/token della generazione contenuti
    (Fase 3 — usa `openai_lesson_content_*`). Ritorna `(assessment, usage)`.
    Solleva `OpenAILessonContentError` su errore HTTP/parsing/schema,
    `OpenAINotConfiguredError` se la API key è assente.
    """
    settings = get_settings()
    system_prompt = _assessment_system_prompt(language_code)
    if is_regeneration:
        system_prompt = system_prompt + REGENERATION_SUFFIX

    body = {
        "model": settings.openai_lesson_content_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": LESSON_ASSESSMENT_JSON_SCHEMA,
        },
        "max_completion_tokens": settings.openai_lesson_content_max_tokens,
    }
    apply_reasoning_effort(
        body,
        settings.openai_lesson_content_model,
        settings.openai_lesson_content_reasoning_effort,
    )
    log.info(
        "openai_lesson_assessment_request",
        chars=len(user_prompt),
        regeneration=is_regeneration,
        model=settings.openai_lesson_content_model,
    )
    t0 = time.monotonic()
    try:
        async with get_client(timeout=600.0) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_lesson_assessment_http_error", error=str(exc))
        raise OpenAILessonContentError(
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
            "openai_lesson_assessment_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=message
            or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    data = resp.json()
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_lesson_assessment_unexpected_response", payload=data)
        raise OpenAILessonContentError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    if not content or not content.strip():
        log.error(
            "openai_lesson_assessment_empty_content",
            finish_reason=finish_reason,
            max_tokens=settings.openai_lesson_content_max_tokens,
        )
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=(
                f"OpenAI ha restituito un contenuto vuoto "
                f"(finish_reason={finish_reason}). Riprova; se persiste "
                f"aumenta OPENAI_LESSON_CONTENT_MAX_TOKENS."
            ),
            payload=data,
        )

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error(
            "openai_lesson_assessment_json_decode_failed",
            content=content[:500],
        )
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    try:
        assessment = LessonAssessmentOutput.model_validate(parsed)
    except Exception as exc:
        log.error("openai_lesson_assessment_schema_invalid", error=str(exc))
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
        ) from exc

    usage = build_usage_dict(
        model=settings.openai_lesson_content_model,
        reasoning_effort_setting=settings.openai_lesson_content_reasoning_effort,
        openai_usage=data.get("usage") or {},
        duration_ms=duration_ms,
    )
    log.info(
        "openai_lesson_assessment_response",
        lesson_id=assessment.lesson_id,
        mc_questions=len(assessment.multiple_choice_questions),
        open_questions=len(assessment.open_questions),
        tokens=usage["total"],
        cost_usd=usage["cost_usd"],
    )
    return assessment, usage
