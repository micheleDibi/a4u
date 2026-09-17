"""Auto-fix AI di un singolo asset "fragile" (formula LaTeX, diagramma
Mermaid, spec Vega-Lite, sorgente DOT o spec `function`) generato dall'AI
ma non valido.

Usato a generazione (Fase 3 + Fase 4) da `asset_validation_service`: quando
un asset non supera la validazione (latex2mathml + KaTeX per le formule,
Mermaid 11.x — pin `settings.mermaid_cdn_version` — per i diagrammi, i
renderer di `figure_render_service` per Vega-Lite, DOT e `function`), questa
funzione chiede al modello di correggere SOLO la sintassi preservando il
significato. Il caller ri-valida il risultato e, se serve, ritenta.

Un prompt di sistema per kind (`_SYSTEM_PROMPTS`, coppia IT/EN); un kind
ignoto e' un errore di programmazione (`ValueError`, A20): `AssetKind` e' un
`Literal` chiuso. I vincoli Mermaid (tipi ammessi ed esclusi) sono derivati
da `figure_theme.MERMAID_D8_TYPES` / `MERMAID_EXCLUDED_TYPES` (D8, D10): un
riparatore che vietasse la 11.x sarebbe incoerente con il validatore.

Output strutturato JSON validato da `AssetFixOut`:
- fixed_content: l'asset corretto (LaTeX senza delimitatori / codice Mermaid
  grezzo / spec JSON / sorgente DOT)
- notes: una frase su cosa e' stato corretto (solo per log)

Pattern speculare a `openai_paper_summary_service`: sincrono, JSON schema
strict, niente persistenza. `openai_asset_fix_max_tokens` resta 4.000 (A16:
una spec ≤ 4.000 caratteri ≈ 1.500 token); il messaggio d'errore del
validatore e' troncato a `_ERROR_CAP` (gli errori dello schema JSON di
Vega-Lite sono piu' lunghi di quelli di KaTeX).
"""

from __future__ import annotations

import json
import time
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.figure_render_service import MERMAID_SHAPE_KEYS, MERMAID_URL_STATEMENTS
from app.services.figure_theme import MERMAID_D8_TYPES, MERMAID_EXCLUDED_TYPES
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    apply_reasoning_effort,
    get_client,
)
from app.services.openai_pricing import build_usage_dict

log = get_logger("app.openai_asset_fix")

AssetKind = Literal["latex", "mermaid", "vegalite", "dot", "function"]

# Cap sul messaggio del validatore inoltrato al modello (era 800: gli errori
# dello schema JSON di Vega-Lite e delle regole D5 sono piu' lunghi).
_ERROR_CAP = 1600
# Cap sul contesto (caption/label) della lezione: invariato.
_CONTEXT_CAP = 600

# I 15 tipi di D8 senza gli alias storici (`graph`, `stateDiagram` v1):
# accettati in lettura, ma il riparatore deve produrre la forma canonica.
_MERMAID_D8_TYPES = ", ".join(MERMAID_D8_TYPES)
_MERMAID_EXCLUDED = ", ".join(MERMAID_EXCLUDED_TYPES)
# Unico punto del gate delle risorse esterne: la lista chiusa delle chiavi
# di shape e le parole chiave di statement che portano un URL o un'icona
# nella figura vivono in `figure_render_service`. Ripeterle qui a mano
# farebbe divergere il riparatore dal 422 che deve far sparire.
_MERMAID_SHAPE_KEYS = ", ".join(f"`{key}`" for key in sorted(MERMAID_SHAPE_KEYS))
_MERMAID_URL_STMT = ", ".join(
    f"`{word}`"
    for word in sorted({w for words, _fold in MERMAID_URL_STATEMENTS.values() for w in words})
)


class OpenAIAssetFixError(OpenAIError):
    """Errore specifico dell'auto-fix di un asset (con l'eventuale `usage`
    della chiamata già pagata: vedi `OpenAIError`)."""


class AssetFixOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fixed_content: str = Field(min_length=1)
    notes: str = Field(default="")


_SYSTEM_MERMAID_IT = f"""\
Sei un esperto di diagrammi Mermaid. Ricevi un diagramma Mermaid che NON e'
valido (non supera il parsing). Correggilo affinche' sia sintatticamente
valido e renderizzabile, PRESERVANDO il significato e i contenuti originali
(stesso tipo di diagramma, stessi nodi, etichette e relazioni).

VINCOLI RIGIDI:
- Compatibilita' con Mermaid 11.x. Tipi ammessi: {_MERMAID_D8_TYPES}.
  Tipi vietati (non renderizzabili nel PDF): {_MERMAID_EXCLUDED}.
- Restituisci SOLO il codice Mermaid grezzo: NIENTE backtick, NIENTE code
  fence ```, niente testo prima o dopo.
- Mantieni il tipo di diagramma dichiarato se ammesso e corretto; se la prima
  riga e' errata o assente, scegli il tipo ammesso piu' adatto al contenuto.
- Etichette in testo semplice (le label sono rese come `<text>` SVG): niente
  HTML ne' markdown dentro le label, niente direttive `%%{{init: ...}}%%` ne'
  frontmatter di configurazione; se servono caratteri speciali (`(`, `)`,
  `:`, `"`) racchiudi l'etichetta tra virgolette doppie come da sintassi
  Mermaid.
- MAI risorse esterne: nelle shape `@{{ ... }}` sono ammesse SOLO le chiavi
  {_MERMAID_SHAPE_KEYS} (niente `img:` ne' `icon:`), e sono vietati gli
  statement {_MERMAID_URL_STMT}; le figure non caricano file ne' URL.
- NON aggiungere ne' rimuovere contenuti rispetto all'originale: correggi
  solo la sintassi.

Output: SOLO JSON valido conforme allo schema."""

_SYSTEM_MERMAID_EN = f"""\
You are a Mermaid diagram expert. You receive a Mermaid diagram that is NOT
valid (it fails parsing). Fix it so it is syntactically valid and
renderable, PRESERVING the original meaning and content (same diagram type,
same nodes, labels and relations).

STRICT CONSTRAINTS:
- Compatible with Mermaid 11.x. Allowed types: {_MERMAID_D8_TYPES}.
  Forbidden types (not renderable in the PDF): {_MERMAID_EXCLUDED}.
- Return ONLY the raw Mermaid code: NO backticks, NO ``` code fences, no
  text before or after.
- Keep the declared diagram type if allowed and correct; if the first line
  is wrong or missing, choose the allowed type best suited to the content.
- Plain-text labels (labels are rendered as SVG `<text>`): no HTML or
  markdown inside labels, no `%%{{init: ...}}%%` directives or configuration
  frontmatter; if special characters (`(`, `)`, `:`, `"`) are needed, wrap
  the label in double quotes per Mermaid syntax.
- NEVER use external resources: inside `@{{ ... }}` shapes ONLY the keys
  {_MERMAID_SHAPE_KEYS} are allowed (no `img:`, no `icon:`), and the
  statements {_MERMAID_URL_STMT} are forbidden; figures load no file, no URL.
- Do NOT add or remove content vs the original: fix syntax only.

Output: ONLY valid JSON conforming to the schema."""

_SYSTEM_LATEX_IT = """\
Sei un esperto di LaTeX matematico. Ricevi una formula LaTeX che NON e'
valida. Correggila affinche' sia valida SIA con KaTeX (strict:"ignore") SIA
con latex2mathml, PRESERVANDO il significato matematico.

VINCOLI RIGIDI:
- Restituisci SOLO il corpo della formula, SENZA delimitatori: niente
  `$...$`, `$$...$$`, `\\(...\\)`, `\\[...\\]`, niente backtick.
- Usa solo comandi supportati sia da KaTeX sia da latex2mathml; evita
  pacchetti/macro esotici o ambienti non supportati.
- Correggi solo la sintassi (parentesi/graffe sbilanciate, comandi errati,
  argomenti mancanti). NON cambiare il significato della formula.
- NON aggiungere testo descrittivo o commenti.

Output: SOLO JSON valido conforme allo schema."""

_SYSTEM_LATEX_EN = """\
You are a mathematical LaTeX expert. You receive a LaTeX formula that is NOT
valid. Fix it so it is valid BOTH with KaTeX (strict:"ignore") AND with
latex2mathml, PRESERVING the mathematical meaning.

STRICT CONSTRAINTS:
- Return ONLY the formula body, WITHOUT delimiters: no `$...$`, `$$...$$`,
  `\\(...\\)`, `\\[...\\]`, no backticks.
- Use only commands supported by both KaTeX and latex2mathml; avoid exotic
  packages/macros or unsupported environments.
- Fix syntax only (unbalanced braces/parens, wrong commands, missing
  arguments). Do NOT change the formula's meaning.
- Do NOT add descriptive text or comments.

Output: ONLY valid JSON conforming to the schema."""

# Vega-Lite: le regole D5 sono quelle di `figure_compute.vegalite_rules`; il
# messaggio d'errore inoltrato al modello indica la vista (profondita') e la
# regola violata. Niente f-string: le graffe sono JSON letterale.
_SYSTEM_VEGALITE_IT = """\
Sei un esperto di Vega-Lite (versione 6). Ricevi la spec JSON di un grafico
che NON supera la validazione (schema JSON, regole del renderer offline o
motore di render). Correggila PRESERVANDO i dati, i canali e il significato
del grafico.

VINCOLI RIGIDI:
- Restituisci SOLO la spec JSON (un unico oggetto): NIENTE backtick, NIENTE
  code fence, niente testo prima o dopo, nessuna chiave duplicata.
- La spec e' AUTOSUFFICIENTE: dati solo inline in `data.values` (mai
  `data.url`, mai `data.name` senza `datasets`), massimo 200 righe.
- NON scrivere `config`, `$schema`, `selection`, `params`, `tooltip`,
  `usermeta`, `encoding.href`, `mark: "image"`: il tema lo inietta il
  renderer e il grafico e' statico.
- Sui mark `line`, `area`, `point`, `trail` aggiungi `"clip": true` (forma
  oggetto: {"type": "line", "clip": true}); su ogni canale `x`/`y`
  quantitativo dichiara `scale.domain` come [min, max] numerici.
- Al massimo una `title` (stringa, solo al livello radice, ≤ 120 caratteri);
  `axis.format` solo con specificatori d3 brevi.
- Le funzioni matematiche (seno, esponenziale, potenze, funzioni razionali su
  una sequenza) NON si tracciano in Vega-Lite: se l'errore lo indica, lascia
  la spec com'e' e scrivilo in `notes`.
- Conserva i dati e le etichette nella lingua del corso; correggi solo cio'
  che l'errore segnala.

Output: SOLO JSON valido conforme allo schema."""

_SYSTEM_VEGALITE_EN = """\
You are a Vega-Lite (version 6) expert. You receive the JSON spec of a chart
that FAILS validation (JSON schema, offline renderer rules or render engine).
Fix it while PRESERVING the data, the channels and the meaning of the chart.

STRICT CONSTRAINTS:
- Return ONLY the JSON spec (a single object): NO backticks, NO code fences,
  no text before or after, no duplicate keys.
- The spec is SELF-CONTAINED: inline data only in `data.values` (never
  `data.url`, never `data.name` without `datasets`), at most 200 rows.
- Do NOT write `config`, `$schema`, `selection`, `params`, `tooltip`,
  `usermeta`, `encoding.href`, `mark: "image"`: the theme is injected by the
  renderer and the chart is static.
- On `line`, `area`, `point`, `trail` marks add `"clip": true` (object form:
  {"type": "line", "clip": true}); on every quantitative `x`/`y` channel
  declare `scale.domain` as numeric [min, max].
- At most one `title` (string, root level only, ≤ 120 characters);
  `axis.format` only with short d3 specifiers.
- Mathematical functions (sine, exponential, powers, rational functions over a
  sequence) are NOT plotted in Vega-Lite: if the error says so, leave the spec
  unchanged and say it in `notes`.
- Keep data and labels in the course language; fix only what the error
  reports.

Output: ONLY valid JSON conforming to the schema."""

_SYSTEM_DOT_IT = """\
Sei un esperto di Graphviz DOT. Ricevi il sorgente di un grafo che NON supera
la validazione (sintassi rifiutata da `dot` o attributo non ammesso).
Correggilo PRESERVANDO nodi, archi, etichette e struttura.

VINCOLI RIGIDI:
- Restituisci SOLO il sorgente DOT grezzo: NIENTE backtick, NIENTE code fence,
  niente testo prima o dopo. Deve iniziare con `graph`, `digraph` o `strict`.
- Etichette (`label`) nella lingua del corso, testo semplice tra virgolette
  doppie; niente HTML-like label `<...>`.
- MAI attributi che leggono file o risorse esterne: `image`, `shapefile`,
  `imagepath`, `fontpath`, `stylesheet`, `URL`, `href`, `target`.
- NON impostare font o colori globali (`graph [...]`, `node [...]`, `edge
  [...]` li inietta il renderer) se non erano gia' presenti; niente
  `fontname` esplicito.
- Correggi solo la sintassi (parentesi, punti e virgola, virgolette, frecce
  `->` nei grafi diretti e `--` in quelli non diretti); NON aggiungere ne'
  rimuovere nodi o archi.

Output: SOLO JSON valido conforme allo schema."""

_SYSTEM_DOT_EN = """\
You are a Graphviz DOT expert. You receive the source of a graph that FAILS
validation (syntax rejected by `dot` or a forbidden attribute). Fix it while
PRESERVING nodes, edges, labels and structure.

STRICT CONSTRAINTS:
- Return ONLY the raw DOT source: NO backticks, NO code fences, no text before
  or after. It must start with `graph`, `digraph` or `strict`.
- Labels (`label`) in the course language, plain text in double quotes; no
  HTML-like labels `<...>`.
- NEVER use attributes that read files or external resources: `image`,
  `shapefile`, `imagepath`, `fontpath`, `stylesheet`, `URL`, `href`, `target`.
- Do NOT set global fonts or colours (`graph [...]`, `node [...]`, `edge
  [...]` are injected by the renderer) unless they were already present; no
  explicit `fontname`.
- Fix syntax only (brackets, semicolons, quotes, `->` arrows in directed
  graphs and `--` in undirected ones); do NOT add or remove nodes or edges.

Output: ONLY valid JSON conforming to the schema."""

# `function`: la spec e' `app.schemas.figure_function.FunctionFigureSpec`
# (WP7); il parser accetta solo espressioni Python-like con la whitelist di
# funzioni di `figure_compute.function_parse`.
_SYSTEM_FUNCTION_IT = """\
Sei un esperto di analisi matematica e di specifiche JSON. Ricevi la spec
JSON di una figura calcolata (`FunctionFigureSpec`: kind, expressions,
variable, domain, range, show, annotations, parameter, sampling, levels)
che NON supera la validazione. Correggila PRESERVANDO le funzioni studiate e
l'intento didattico.

VINCOLI RIGIDI:
- Restituisci SOLO la spec JSON (un unico oggetto) conforme a
  FunctionFigureSpec: NIENTE backtick, NIENTE code fence, nessuna chiave non
  prevista, nessun testo prima o dopo.
- Espressioni in sintassi Python: potenza con `**` (mai `^`), moltiplicazione
  esplicita (`2*x`, mai `2x`), sola variabile dichiarata (`variable`, piu'
  l'eventuale `parameter.name`), funzioni SOLO tra: sin, cos, tan, exp, log,
  sqrt, abs, asin, acos, atan, sinh, cosh, tanh, floor; costanti `pi` ed `E`.
- `domain` e `range` sono [min, max] numerici finiti con min < max; le
  annotazioni (`tangent`, `area`, `point`) restano dentro il dominio.
- NON inserire valori calcolati (zeri, massimi, integrali, asintoti): li
  calcola il renderer; correggi solo cio' che l'errore segnala.

Output: SOLO JSON valido conforme allo schema."""

_SYSTEM_FUNCTION_EN = """\
You are an expert in mathematical analysis and JSON specifications. You
receive the JSON spec of a computed figure (`FunctionFigureSpec`: kind,
expressions, variable, domain, range, show, annotations, parameter, sampling,
levels) that FAILS validation. Fix it while PRESERVING the studied functions
and the teaching intent.

STRICT CONSTRAINTS:
- Return ONLY the JSON spec (a single object) conforming to FunctionFigureSpec:
  NO backticks, NO code fences, no unexpected keys, no text before or after.
- Expressions in Python syntax: powers with `**` (never `^`), explicit
  multiplication (`2*x`, never `2x`), only the declared variable (`variable`,
  plus the optional `parameter.name`), functions ONLY among: sin, cos, tan,
  exp, log, sqrt, abs, asin, acos, atan, sinh, cosh, tanh, floor; constants
  `pi` and `E`.
- `domain` and `range` are finite numeric [min, max] with min < max;
  annotations (`tangent`, `area`, `point`) stay inside the domain.
- Do NOT insert computed values (zeros, extrema, integrals, asymptotes): the
  renderer computes them; fix only what the error reports.

Output: ONLY valid JSON conforming to the schema."""

# Coppia (IT, EN) per kind: un kind assente e' un errore di programmazione.
_SYSTEM_PROMPTS: dict[str, tuple[str, str]] = {
    "latex": (_SYSTEM_LATEX_IT, _SYSTEM_LATEX_EN),
    "mermaid": (_SYSTEM_MERMAID_IT, _SYSTEM_MERMAID_EN),
    "vegalite": (_SYSTEM_VEGALITE_IT, _SYSTEM_VEGALITE_EN),
    "dot": (_SYSTEM_DOT_IT, _SYSTEM_DOT_EN),
    "function": (_SYSTEM_FUNCTION_IT, _SYSTEM_FUNCTION_EN),
}


def _is_it(language_code: str) -> bool:
    return (language_code or "it").lower().split("-")[0] == "it"


def _system_prompt(kind: AssetKind, language_code: str) -> str:
    """Prompt di sistema del kind nella lingua del corso (it → IT, altrimenti
    EN). Kind ignoto → `ValueError` (A20)."""
    try:
        it_prompt, en_prompt = _SYSTEM_PROMPTS[kind]
    except KeyError as exc:
        raise ValueError(f"kind di asset sconosciuto per il fix AI: {kind!r}") from exc
    return it_prompt if _is_it(language_code) else en_prompt


ASSET_FIX_JSON_SCHEMA: dict[str, Any] = {
    "name": "asset_fix",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "fixed_content": {"type": "string"},
            "notes": {"type": "string"},
        },
        "required": ["fixed_content", "notes"],
        "additionalProperties": False,
    },
}


async def fix_asset(
    *,
    kind: AssetKind,
    source: str,
    error_message: str,
    context: str = "",
    language_code: str,
) -> tuple[AssetFixOut, dict[str, Any]]:
    """Corregge un singolo asset invalido via AI.

    `kind`: "latex" (formula senza delimitatori) | "mermaid" (codice grezzo)
    | "vegalite" (spec JSON) | "dot" (sorgente DOT) | "function" (spec JSON
    di `FunctionFigureSpec`); altro → `ValueError`.
    `source`: l'asset invalido cosi' com'e'.
    `error_message`: messaggio dal validatore (KaTeX/latex2mathml/mermaid/
    renderer del registro), troncato a `_ERROR_CAP`.
    `context`: opzionale — caption/label/explanation per orientare il fix.

    Ritorna `(output, usage)`, con `usage` di
    `openai_pricing.build_usage_dict` (`cost_usd`, `duration_ms`). Solleva
    `OpenAIAssetFixError` su errore HTTP/parse/schema;
    `OpenAINotConfiguredError` se manca la API key. Se la risposta 200 è
    inutilizzabile ma porta il conteggio dei token, l'eccezione lo porta in
    `usage` (vedi `OpenAIError`): il chiamante contabilizza la chiamata
    pagata invece di perderla.
    """
    settings = get_settings()
    lang = "it" if _is_it(language_code) else "en"

    parts: list[str] = [
        f"TIPO ASSET: {kind}",
        f"LINGUA DEL CORSO (per eventuali etichette testuali): {lang}",
    ]
    if context.strip():
        parts.append(f"CONTESTO (caption/label): {context.strip()[:_CONTEXT_CAP]}")
    parts.append("")
    parts.append("ERRORE DI VALIDAZIONE:")
    parts.append((error_message or "(nessun dettaglio)").strip()[:_ERROR_CAP])
    parts.append("")
    parts.append("ASSET DA CORREGGERE:")
    parts.append(source)
    user_message = "\n".join(parts)

    body: dict[str, Any] = {
        "model": settings.openai_asset_fix_model,
        "messages": [
            {"role": "system", "content": _system_prompt(kind, language_code)},
            {"role": "user", "content": user_message},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": ASSET_FIX_JSON_SCHEMA,
        },
        "max_tokens": settings.openai_asset_fix_max_tokens,
    }
    apply_reasoning_effort(
        body, settings.openai_asset_fix_model, settings.openai_asset_fix_reasoning_effort
    )

    log.info(
        "openai_asset_fix_request",
        kind=kind,
        model=settings.openai_asset_fix_model,
        source_chars=len(source or ""),
    )
    started = time.monotonic()
    try:
        async with get_client(timeout=90.0) as client:
            resp = await client.post("/chat/completions", json=body, timeout=90.0)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_asset_fix_http_error", error=str(exc))
        raise OpenAIAssetFixError(status=None, message=f"Errore HTTP verso OpenAI: {exc}") from exc
    duration_ms = int((time.monotonic() - started) * 1000)

    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except Exception:
            payload = {"text": resp.text}
        message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
        log.error(
            "openai_asset_fix_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAIAssetFixError(
            status=resp.status_code,
            message=message or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    data = resp.json()
    # Usage uniforme alle fasi della pipeline (D16): con `cost_usd` e
    # `duration_ms`, raccolto da `asset_validation_service` in
    # `content_tokens.assets`. Calcolato PRIMA di leggere il contenuto: una
    # risposta 200 inutilizzabile (JSON troncato da `max_tokens`, schema
    # fuori contratto) è comunque pagata, e l'eccezione porta l'usage.
    raw_usage = data.get("usage") if isinstance(data, dict) else None
    usage = build_usage_dict(
        model=settings.openai_asset_fix_model,
        reasoning_effort_setting=settings.openai_asset_fix_reasoning_effort,
        openai_usage=raw_usage if isinstance(raw_usage, dict) else {},
        duration_ms=duration_ms,
    )
    billed = usage if isinstance(raw_usage, dict) and raw_usage else None

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_asset_fix_unexpected_response", payload=data)
        raise OpenAIAssetFixError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
            usage=billed,
        ) from exc

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error("openai_asset_fix_json_decode_failed", content=content[:500])
        raise OpenAIAssetFixError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
            usage=billed,
        ) from exc

    try:
        output = AssetFixOut.model_validate(parsed)
    except Exception as exc:
        log.error("openai_asset_fix_schema_invalid", error=str(exc))
        raise OpenAIAssetFixError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
            usage=billed,
        ) from exc

    log.info(
        "openai_asset_fix_response", kind=kind, tokens=usage["total"], cost_usd=usage["cost_usd"]
    )
    return output, usage


__all__ = ["AssetFixOut", "AssetKind", "OpenAIAssetFixError", "fix_asset"]
