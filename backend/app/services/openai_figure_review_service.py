"""Revisore AI figura ↔ testo (Fase 3, D15): una chiamata per figura valida.

Usato a generazione da `asset_validation_service`, dopo il fix degli asset
invalidi e prima della localizzazione: la figura è già valida, e il modello
dice soltanto se corrisponde al testo che la cita. Ingresso: formato,
sorgente, didascalia e testo alternativo, TESTO INTEGRALE della sezione
che cita la figura (o del corpo della lezione, se la figura non è citata),
misura della figura resa (nodi e archi dal sorgente, incroci e difetti di
lettura dalla geometria di `figure_geometry`, corpo del testo nella
dispensa da `figure_scale`) e lingua del corso. Uscita strutturata
(`FigureReviewOut`): `verdict` fra `coerente` e `correggi`, `reason` per i
log, `source` (la figura riscritta, `null` con `coerente`).

`coerente` è il verdetto predefinito, nel prompt e nel codice: un campo
mancante vale `coerente`, e il chiamante tratta come nessuna riscrittura un
`correggi` senza sorgente o con il sorgente dell'originale. Il servizio non
valida né applica nulla: la riscrittura è accettata dal chiamante solo se
supera le validazioni deterministiche e non peggiora la misura.

Tetti dichiarati del contesto: la sezione che cita la figura entra per
intero fino a `SECTION_MAX_CHARS` (24.000 caratteri, rete di sicurezza: una
sezione di Fase 3 ne conta poche migliaia); una figura non citata riceve il
corpo della lezione (introduzione, sezioni, sintesi) troncato a
`BODY_MAX_CHARS` (12.000). Didascalia e testo alternativo sono troncati a
`_CAPTION_CAP`, il motivo del rifiuto precedente a `_FEEDBACK_CAP`.

Pattern speculare a `openai_asset_fix_service`: httpx, `response_format`
json_schema strict, nessuna persistenza, prompt di sistema in italiano e in
inglese (`_SYSTEM_PROMPTS`, PROMPT 17 di `docs/PROMPTS.md`). L'usage è
quello di `openai_pricing.build_usage_dict` (con `cost_usd` e
`duration_ms`) e non si perde su una risposta inutilizzabile: se OpenAI ha
risposto 200 con un conteggio di token, l'eccezione lo porta in `usage` e
il chiamante lo contabilizza lo stesso (il caso realistico è il JSON
troncato da `max_completion_tokens`, con la figura riscritta per intero
nella risposta). `max_completion_tokens` al posto di `max_tokens`: il
modello si cambia con la sola variabile, anche verso un modello reasoning.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    apply_reasoning_effort,
    get_client,
)
from app.services.openai_pricing import build_usage_dict

log = get_logger("app.openai_figure_review")

ReviewFormat = Literal["mermaid", "vegalite", "dot", "function"]
Verdict = Literal["coerente", "correggi"]

VERDICT_COHERENT: Verdict = "coerente"
VERDICT_FIX: Verdict = "correggi"

# Tetti del contesto (docstring del modulo).
SECTION_MAX_CHARS = 24_000
BODY_MAX_CHARS = 12_000
_CAPTION_CAP = 600
_FEEDBACK_CAP = 600
_TRUNCATION_MARK = "\n[…]"
_TIMEOUT_S = 90.0


class OpenAIFigureReviewError(OpenAIError):
    """Errore specifico del revisore figura ↔ testo (con l'eventuale
    `usage` della chiamata già pagata: vedi `OpenAIError`)."""


class FigureReviewOut(BaseModel):
    """Verdetto del revisore; ogni campo mancante vale «nessuna riscrittura»."""

    model_config = ConfigDict(extra="ignore")
    verdict: Verdict = VERDICT_COHERENT
    reason: str = ""
    source: str | None = None


@dataclass(frozen=True)
class FigureMeasure:
    """Misura di una figura per il prompt e per l'accettazione.

    `nodes`/`edges` vengono dal sorgente (`graph_rules`, solo Mermaid e
    DOT); `rendered` dice se la figura è stata resa; `crossings` e
    `defects` sono la geometria della resa (`None`/`()` se la misura è
    saltata o il formato non ha archi); `text_pt` e `in_band` il corpo
    minimo del testo nella dispensa A4 di default e l'appartenenza alla
    banda 8-11 pt."""

    nodes: int | None = None
    edges: int | None = None
    rendered: bool = False
    crossings: int | None = None
    defects: tuple[str, ...] = ()
    text_pt: float | None = None
    in_band: bool | None = None

    @property
    def measured(self) -> bool:
        """La geometria esiste: figura resa e incroci contati."""
        return self.rendered and self.crossings is not None


@dataclass(frozen=True)
class ReviewContext:
    """Testo che accompagna la figura: la prima sezione che la cita
    (`cited=True`, `title` è il suo titolo) o il corpo della lezione."""

    title: str
    text: str
    cited: bool


_SYSTEM_REVIEW_IT = """\
Sei un revisore editoriale delle figure di una dispensa universitaria.
Ricevi una figura GIA' VALIDA (sorgente Mermaid, Graphviz DOT, spec
Vega-Lite o spec `function`), la sua didascalia, il testo integrale della
sezione della lezione che la cita e la misura della figura resa (nodi,
archi, incroci fra archi, difetti di lettura, corpo del testo nella
dispensa). Decidi se la figura corrisponde al testo.

VERDETTO:
- `coerente` e' la risposta predefinita: usala quando la figura rappresenta
  cio' che il testo spiega, anche se la disegneresti in un altro modo, e in
  ogni caso di dubbio. Con `coerente` il campo `source` e' null: NON
  riscrivere una figura che corrisponde al testo.
- `correggi` solo se la figura contraddice il testo (nodi, relazioni, verso
  delle frecce, valori o etichette diversi da quelli che il testo espone)
  oppure se la misura la dichiara illeggibile (incroci fra archi, etichette
  sovrapposte, testo fuori dalla figura). Con `correggi` il campo `source`
  contiene la figura riscritta per intero.

VINCOLI DELLA RISCRITTURA:
- Stesso formato e stesso tipo di diagramma dell'originale. Restituisci
  SOLO il sorgente grezzo: niente backtick, niente code fence, nessun testo
  prima o dopo.
- Conserva TUTTI i nodi dell'originale con i loro identificativi e le
  etichette che il testo usa: per correggere il testo di un nodo cambia la
  sua etichetta, non l'identificativo. Non scrivere mai riferimenti come
  `[FIG:...]`, `[TAB:...]`, `[EQ:...]`, `[EX:...]` ne' l'identificativo
  dell'asset.
- La riscrittura riduce la densita' solo sugli archi: gli stessi nodi, al
  piu' gli archi dell'originale e meno incroci (riordina le dichiarazioni
  dei nodi, cambia la direzione del diagramma, togli gli archi ridondanti),
  mai di piu'. Ogni nodo che nell'originale ha archi ne conserva almeno
  uno.
- In Vega-Lite conserva tutte le righe di `data.values` e i campi
  dell'encoding dell'originale; nel formato `function` conserva le
  espressioni e il dominio: cambia le etichette, l'ordine e le scelte di
  disegno, mai i dati.
- NON aggiungere contenuti assenti dal testo della sezione: nessun nodo,
  valore, etichetta o relazione che il testo non nomini.
- Etichette in testo semplice, nella lingua del corso e nel registro
  accademico del testo. Niente HTML ne' direttive `%%{init: ...}%%` in
  Mermaid; in DOT niente font, colori o attributi che leggono file, e gli
  attributi del grafo nella forma nuda (`rankdir=LR;`), non nel blocco
  `graph [...]`; in Vega-Lite niente `config` e dati solo in `data.values`.
- `reason`: una frase che motiva il verdetto (resta nei log).

Output: SOLO JSON valido conforme allo schema."""

_SYSTEM_REVIEW_EN = """\
You are an editorial reviewer of the figures of a university course
handout. You receive an ALREADY VALID figure (Mermaid source, Graphviz DOT,
Vega-Lite spec or `function` spec), its caption, the full text of the
lesson section that cites it and the measure of the rendered figure (nodes,
edges, edge crossings, reading defects, text size in the handout). Decide
whether the figure matches the text.

VERDICT:
- `coerente` is the default answer: use it when the figure shows what the
  text explains, even if you would draw it differently, and whenever in
  doubt. With `coerente` the `source` field is null: do NOT rewrite a figure
  that matches the text.
- `correggi` only if the figure contradicts the text (nodes, relations,
  arrow directions, values or labels other than those the text sets out) or
  if the measure reports it as unreadable (edge crossings, overlapping
  labels, text outside the figure). With `correggi` the `source` field holds
  the whole rewritten figure.

REWRITE CONSTRAINTS:
- Same format and same diagram type as the original. Return ONLY the raw
  source: no backticks, no code fences, no text before or after.
- Keep ALL the nodes of the original with their identifiers and the
  labels the text uses: to correct the text of a node change its label,
  not its identifier. Never write references such as `[FIG:...]`,
  `[TAB:...]`, `[EQ:...]`, `[EX:...]` or the asset identifier.
- The rewrite reduces density only on the edges: the same nodes, at most
  the edges of the original and fewer crossings (reorder the node
  declarations, change the diagram direction, drop redundant edges), never
  more. Every node that has edges in the original keeps at least one.
- In Vega-Lite keep every row of `data.values` and the encoding fields of
  the original; in the `function` format keep the expressions and the
  domain: change labels, order and drawing choices, never the data.
- Do NOT add content missing from the section text: no node, value, label
  or relation the text does not name.
- Plain-text labels, in the course language and in the academic register of
  the text. No HTML and no `%%{init: ...}%%` directives in Mermaid; in DOT no
  fonts, colours or attributes that read files, and graph attributes in the
  bare form (`rankdir=LR;`), not in a `graph [...]` block; in Vega-Lite no
  `config` and data only in `data.values`.
- `reason`: one sentence explaining the verdict (kept in the logs).

Output: ONLY valid JSON conforming to the schema."""

# Prompt per lingua: `it` per i corsi in italiano, `en` per ogni altra lingua.
_SYSTEM_PROMPTS: dict[str, str] = {"it": _SYSTEM_REVIEW_IT, "en": _SYSTEM_REVIEW_EN}


def _is_it(language_code: str) -> bool:
    return (language_code or "it").lower().split("-")[0] == "it"


def _system_prompt(language_code: str) -> str:
    return _SYSTEM_PROMPTS["it" if _is_it(language_code) else "en"]


FIGURE_REVIEW_JSON_SCHEMA: dict[str, Any] = {
    "name": "figure_review",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": [VERDICT_COHERENT, VERDICT_FIX]},
            "reason": {"type": "string"},
            "source": {"type": ["string", "null"]},
        },
        "required": ["verdict", "reason", "source"],
        "additionalProperties": False,
    },
}


def _capped(text: str, cap: int) -> str:
    value = (text or "").strip()
    if len(value) <= cap:
        return value
    return value[:cap].rstrip() + _TRUNCATION_MARK


def _measure_lines(fmt: str, measure: FigureMeasure) -> list[str]:
    lines: list[str] = []
    if measure.nodes is not None and measure.edges is not None:
        lines.append(f"- nodi: {measure.nodes}; archi: {measure.edges}")
    if fmt in ("mermaid", "dot"):
        if measure.measured:
            lines.append(f"- incroci fra archi: {measure.crossings}")
        else:
            lines.append("- incroci fra archi: non misurati (figura non resa o misura saltata)")
    if measure.rendered:
        defects = "; ".join(measure.defects) if measure.defects else "nessuno"
        lines.append(f"- difetti di lettura: {defects}")
    if measure.text_pt is not None:
        band = "dentro" if measure.in_band else "fuori"
        lines.append(
            f"- corpo minimo del testo nella dispensa: {measure.text_pt:g} pt "
            f"(banda 8-11 pt: {band})"
        )
    if not lines:
        lines.append("- misura non disponibile")
    return lines


def build_user_message(
    *,
    fmt: str,
    source: str,
    caption: str,
    alt_text: str,
    context: ReviewContext,
    measure: FigureMeasure,
    language_code: str,
    feedback: str = "",
) -> str:
    """Messaggio user della chiamata (etichette in italiano, come il fix)."""
    lang = "it" if _is_it(language_code) else "en"
    parts: list[str] = [
        f"FORMATO: {fmt}",
        f"LINGUA DEL CORSO: {lang}",
        f"DIDASCALIA: {_capped(caption, _CAPTION_CAP) or '(assente)'}",
        f"TESTO ALTERNATIVO: {_capped(alt_text, _CAPTION_CAP) or '(assente)'}",
        "",
        "MISURA DELLA FIGURA RESA:",
        *_measure_lines(fmt, measure),
        "",
    ]
    if context.cited:
        parts.append(f"SEZIONE CHE CITA LA FIGURA: {context.title.strip() or '(senza titolo)'}")
        parts.append(_capped(context.text, SECTION_MAX_CHARS))
    else:
        parts.append(
            "LA FIGURA NON E' CITATA NEL TESTO. CORPO DELLA LEZIONE "
            f"(al piu' {BODY_MAX_CHARS} caratteri):"
        )
        parts.append(_capped(context.text, BODY_MAX_CHARS))
    if feedback.strip():
        parts.append("")
        parts.append("RISCRITTURA PRECEDENTE RESPINTA DALLA VALIDAZIONE:")
        parts.append(_capped(feedback, _FEEDBACK_CAP))
    parts.append("")
    parts.append("FIGURA DA REVISIONARE:")
    parts.append(source)
    return "\n".join(parts)


async def review_figure(
    *,
    fmt: ReviewFormat,
    source: str,
    caption: str,
    alt_text: str,
    context: ReviewContext,
    measure: FigureMeasure,
    language_code: str,
    feedback: str = "",
) -> tuple[FigureReviewOut, dict[str, Any]]:
    """Chiede il verdetto su una figura valida.

    `feedback` è il motivo per cui la riscrittura del tentativo precedente è
    stata respinta (vuoto al primo tentativo). Ritorna `(output, usage)`,
    con `usage` di `build_usage_dict`. Solleva `OpenAIFigureReviewError` su
    errore HTTP/parse/schema, `OpenAINotConfiguredError` se manca la API
    key; se la risposta 200 è inutilizzabile ma porta il conteggio dei
    token, l'eccezione lo porta in `usage` e la chiamata pagata resta
    contabilizzata.
    """
    settings = get_settings()
    model = settings.openai_figure_review_model
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt(language_code)},
            {
                "role": "user",
                "content": build_user_message(
                    fmt=fmt,
                    source=source,
                    caption=caption,
                    alt_text=alt_text,
                    context=context,
                    measure=measure,
                    language_code=language_code,
                    feedback=feedback,
                ),
            },
        ],
        "response_format": {"type": "json_schema", "json_schema": FIGURE_REVIEW_JSON_SCHEMA},
        "max_completion_tokens": settings.openai_figure_review_max_tokens,
    }
    apply_reasoning_effort(body, model, settings.openai_figure_review_reasoning_effort)

    log.info(
        "openai_figure_review_request",
        format=fmt,
        model=model,
        source_chars=len(source or ""),
        context_chars=len(context.text or ""),
        cited=context.cited,
    )
    started = time.monotonic()
    try:
        async with get_client(timeout=_TIMEOUT_S) as client:
            resp = await client.post("/chat/completions", json=body, timeout=_TIMEOUT_S)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_figure_review_http_error", error=str(exc))
        raise OpenAIFigureReviewError(
            status=None, message=f"Errore HTTP verso OpenAI: {exc}"
        ) from exc
    duration_ms = int((time.monotonic() - started) * 1000)

    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except Exception:
            payload = {"text": resp.text}
        message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
        log.error(
            "openai_figure_review_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAIFigureReviewError(
            status=resp.status_code,
            message=message or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    try:
        data = resp.json()
    except ValueError as exc:
        # Un 200 con un corpo che non è JSON (proxy, pagina d'errore) è un
        # errore della chiamata come gli altri: il chiamante lo conta come
        # tentativo perso, senza usage (il corpo non lo porta).
        log.error("openai_figure_review_body_not_json", error=str(exc)[:300])
        raise OpenAIFigureReviewError(
            status=resp.status_code,
            message=f"Corpo della risposta OpenAI non JSON: {exc}",
        ) from exc
    # Usage PRIMA di leggere il verdetto: una risposta 200 inutilizzabile
    # (JSON troncato da `max_completion_tokens`, schema fuori contratto) ha
    # comunque i token pagati, e li porta con sé l'eccezione (`usage`), che
    # il chiamante contabilizza in `content_tokens.assets`.
    raw_usage = data.get("usage") if isinstance(data, dict) else None
    usage = build_usage_dict(
        model=model,
        reasoning_effort_setting=settings.openai_figure_review_reasoning_effort,
        openai_usage=raw_usage if isinstance(raw_usage, dict) else {},
        duration_ms=duration_ms,
    )
    billed = usage if isinstance(raw_usage, dict) and raw_usage else None

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_figure_review_unexpected_response", payload=data)
        raise OpenAIFigureReviewError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
            usage=billed,
        ) from exc

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        log.error("openai_figure_review_json_decode_failed", content=str(content)[:500])
        raise OpenAIFigureReviewError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
            usage=billed,
        ) from exc

    try:
        output = FigureReviewOut.model_validate(parsed)
    except Exception as exc:
        log.error("openai_figure_review_schema_invalid", error=str(exc))
        raise OpenAIFigureReviewError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
            usage=billed,
        ) from exc

    log.info(
        "openai_figure_review_response",
        format=fmt,
        verdict=output.verdict,
        tokens=usage["total"],
        cost_usd=usage["cost_usd"],
    )
    return output, usage


__all__ = [
    "BODY_MAX_CHARS",
    "FIGURE_REVIEW_JSON_SCHEMA",
    "SECTION_MAX_CHARS",
    "VERDICT_COHERENT",
    "VERDICT_FIX",
    "FigureMeasure",
    "FigureReviewOut",
    "OpenAIFigureReviewError",
    "ReviewContext",
    "ReviewFormat",
    "build_user_message",
    "review_figure",
]
