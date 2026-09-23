"""Validazione + auto-fix AI degli asset "fragili" a generazione (Fase 3/4).

Un asset e' "fragile" se puo' essere sintatticamente invalido e finire rotto
nell'output (PDF / frame video / preview FE):
- **Formule LaTeX**: `equations[].latex` e math inline `$...$` / `$$...$$`
  nei campi testo. Validate con `latex2mathml` (motore dell'export PDF/video)
  E con KaTeX (motore del preview FE) → una formula e' valida solo se passa
  ENTRAMBI.
- **Figure** (`visual_assets[]` in Fase 3, `new_assets[]` in Fase 4) con
  `format` in `figure_render_service.RENDERABLE_FORMATS`: il kind dello slot
  e' il formato stesso e il dispatch passa dal registro (D2).
  - `mermaid`: gate statico D8 di `MermaidRenderer.validate` (tipo ammesso,
    niente `%%{init`, niente HTML nelle label) e parse con **Mermaid 11.x**,
    pin unico `settings.mermaid_cdn_version` (lo stesso del pre-render
    PDF/video in `mermaid_prerender` e del lock npm del frontend) e stessa
    inizializzazione `figure_theme.mermaid_initialize_js` (`htmlLabels:
    false` top-level): un diagramma "verde" nell'editor lo e' anche
    nell'output;
  - `vegalite`, `dot`, `function`: validazione offline completa del
    renderer (`validate(deep=True)`: schema/regole/parse E prova di render,
    il cui SVG entra in cache per l'export). Mai pass-through: un formato
    non disponibile (`available_formats()`) produce un `AssetCheck` con
    `fixable=False` e la lezione viene rigenerata senza spendere token nel
    fix AI.

Flusso: a generazione, prima di materializzare, ogni asset fragile viene
validato; quelli invalidi vengono riparati con una chiamata AI mirata
(`openai_asset_fix_service.fix_asset`) e ri-validati, fino a valido o a
`asset_fix_max_attempts`. Se un asset resta invalido si solleva
`AssetFixUnresolvedError` (recuperabile) → il worker rigenera l'intera lezione
via auto-retry. Cosi' nessun asset rotto raggiunge lo stato `ready`/output.

Fase 3, ordine fix → revisione → localizzazione (D15):
1. fix degli asset invalidi (sopra);
2. revisione figura ↔ testo (`_review_figures`,
   `openai_figure_review_service.review_figure`, kill-switch
   `figure_review_enabled`, al più `figure_review_max_attempts` chiamate per
   figura): ogni figura valida è confrontata con il testo integrale della
   prima sezione che la cita (`FIG_REF_RE` su introduzione, sezioni e
   sintesi; il corpo intero, con un tetto, se non è citata) e con la misura
   di WP5 dell'originale, resa una volta con `render_figure_map`. Il
   verdetto predefinito è `coerente`. Una riscrittura (`correggi`) passa da
   `_sanitize`, dai controlli deterministici (placeholder, stesso tipo
   Mermaid, nodi e archi non in aumento, tutti i nodi dell'originale con
   gli stessi id, nessun nodo collegato lasciato senza archi), dalla
   stessa `_validate_slots` del fix e dalla misura: originale e
   riscrittura sono letti dalla stessa `render_figure_map` (l'originale è
   un hit della cache), e la regola di `review_acceptance` vuole la
   riscrittura misurata, con incroci non superiori e nessun difetto nuovo.
   Vega-Lite e `function` non hanno archi né misura geometrica: al loro
   posto vale la conservazione dei dati di `_DATA_GUARDS` (righe di
   `data.values` e campi dell'encoding, espressioni e dominio), così
   nessun formato può perdere il contenuto della figura per una
   riscrittura. Una riscrittura respinta lascia l'originale byte-identico
   (nessuna scrittura in `content`, voce di cache dell'originale intatta)
   e il motivo torna al modello nel tentativo successivo. Nessun esito
   della revisione fa fallire la lezione: ogni errore di una chiamata
   (anche fuori da `OpenAIError`) è un tentativo perso di quella figura
   (`figure_review_call_failed`) e non tocca le chiamate sorelle del giro;
   un guasto imprevisto fuori dalle chiamate diventa `figure_review_failed`
   con gli originali intatti. Le chiamate in volo hanno un tetto per
   processo (`figure_review_max_parallel`, semaforo per loop) e le rese
   della revisione sono speculative (`cache_failures=False`): un loro
   guasto non mette in cache negativa le figure originali;
3. localizzazione dei campi rimasti in un'altra lingua, con rivalidazione
   non fatale dei kind strutturali.
Ogni chiamata AI dei tre passi lascia una voce in `assets_usage`
(`phase` fra `fix`, `review`, `localize`, `asset_id` dello slot e i campi
di `openai_pricing.build_usage_dict`, con `cost_usd`), che
`validate_and_fix_content_assets` ritorna accanto all'output e il worker
fonde in `content_tokens` (`merge_assets_usage`). Anche una chiamata
pagata che non produce nulla di usabile (200 con JSON troncato o schema
fuori contratto) lascia la sua voce: l'usage arriva con l'eccezione
(`OpenAIError.usage`). In Fase 4 lo stesso usage è solo loggato
(`slides_assets_usage`).

Playwright gira in un thread con loop dedicato (ProactorEventLoop su Windows),
stesso pattern del pre-render Mermaid in `course_lesson_pdf_service`. Se le
librerie CDN non sono raggiungibili, il LaTeX resta validato offline da
latex2mathml (gate duro) e Mermaid/KaTeX degradano a pass-through (warning),
per non bloccare la generazione quando la rete e' giu'; i tre formati nuovi
sono offline e non degradano mai.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import sys
import time
import weakref
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast, get_args

from latex2mathml.converter import convert as _latex_to_mathml

from app.core.config import get_settings
from app.core.i18n_scripts import has_target_script_chars, primary_script
from app.core.logging import get_logger
from app.schemas.course_lesson_content import SOURCE_FIGURE_FORMAT, LessonContentOutput
from app.schemas.course_lesson_slides import LessonSlidesOutput
from app.schemas.figure_function import parse_function_spec
from app.services import (
    openai_asset_fix_service,
    openai_asset_localize_service,
    openai_figure_redundancy_service,
    openai_figure_review_service,
    openai_tikz_render_review_service,
)
from app.services.figure_compute.graph_rules import GRAPH_FORMATS, graph_source_metrics
from app.services.figure_compute.vegalite_rules import (
    VegaLiteDataMetrics,
    vegalite_data_metrics,
)
from app.services.figure_numbering import FIG_REF_RE
from app.services.figure_render_service import (
    REGISTRY,
    RENDERABLE_FORMATS,
    RenderedFigure,
    TikzRenderer,
    available_formats,
    figure_asset_context,
    render_chain_variants,
    render_figure_map,
    tikz_timeout_seconds,
)
from app.services.figure_scale import FigureFit, fit_figure_width_mm, resolve_base_font_px
from app.services.figure_theme import mermaid_initialize_js
from app.services.mermaid_prerender import block_external_requests
from app.services.openai_client import OpenAIError
from app.services.openai_figure_review_service import FigureMeasure, ReviewContext
from app.services.svg_normalize import svg_intrinsic_box

log = get_logger("app.asset_validation")


class AssetFixUnresolvedError(Exception):
    """Un asset fragile e' rimasto invalido dopo `asset_fix_max_attempts`.

    Recuperabile: il worker la mappa su auto-retry (rigenera la lezione).
    `assets_usage` porta le chiamate degli asset gia' pagate nel tentativo
    fallito: nulla si materializza (la lezione viene rigenerata), ma il
    costo resta visibile nei log del worker invece di sparire.

    `code="tikz_unresolved"` quando fra gli asset irrisolti c'è una figura
    `tikz`: il worker non offre `tikz` al tentativo successivo."""

    def __init__(
        self,
        message: str,
        *,
        assets_usage: list[dict[str, Any]] | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.assets_usage: list[dict[str, Any]] = list(assets_usage or [])
        self.code = code


@dataclass(frozen=True)
class AssetCheck:
    id: str
    kind: str  # "latex" | uno di `RENDERABLE_FORMATS`
    ok: bool
    error_message: str
    # False quando il fix AI non puo' risolvere (formato non disponibile sul
    # server): `_validate_and_fix` alza subito `AssetFixUnresolvedError`.
    fixable: bool = True


# ---------------------------------------------------------------------------
# Pre-pulizia: caratteri di controllo (garbage del modello)
# ---------------------------------------------------------------------------

# C0 (escluso \t \n \r), DEL e C1: mai validi in LaTeX/Mermaid/prosa. Il
# modello a volte emette ESC (0x1B) o simili dentro le formule (es. KaTeX
# "Unexpected character: ''"). Li rimuoviamo deterministicamente
# dall'output AI PRIMA della validazione: spesso questo da solo rende
# l'asset valido senza bisogno di una chiamata di fix.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _strip_control_chars(s: str) -> str:
    if not s:
        return s
    return _CONTROL_CHARS_RE.sub("", s)


# Combining diacritical marks (U+0300–U+036F): in una sorgente LaTeX sono
# quasi sempre garbage del modello (in LaTeX si usano comandi tipo
# `\underline{}`/`\hat{}`, non combining char grezzi) e fanno fallire KaTeX
# ("Unexpected character"). Li rimuoviamo SOLO dalle sorgenti LaTeX (formule),
# MAI dalla prosa o dalle label Mermaid, dove gli accenti sono legittimi.
_COMBINING_RE = re.compile("[" + chr(0x0300) + "-" + chr(0x036F) + "]")


def _clean_latex_source(s: str) -> str:
    """Pulisce una sorgente LaTeX: caratteri di controllo + combining marks.
    Usato SOLO come primo tentativo di riparazione su un asset gia' risultato
    invalido (mai sugli asset validi)."""
    return _COMBINING_RE.sub("", _strip_control_chars(s or ""))


# ---------------------------------------------------------------------------
# Validazione JS (Playwright): Mermaid 11 + KaTeX
# ---------------------------------------------------------------------------

# Pagina headless di SOLA validazione (parse-only, niente render-to-SVG):
# separata dalla `_MERMAID_RENDERER_HTML` dell'export per non interferire.
# Mermaid con lo stesso pin del pre-render PDF/video e del frontend
# (`settings.mermaid_cdn_version`) e la stessa inizializzazione di
# `figure_theme` (`htmlLabels: false` top-level; `useMaxWidth` e' irrilevante
# per il solo parse). KaTeX con gli stessi flag del preview FE
# (`throwOnError:true`, `strict:"ignore"`). Segnaposto sostituiti sotto (le
# graffe del JS impediscono `str.format`).
_VALIDATOR_HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"></head><body>
<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@__MERMAID_VERSION__/dist/mermaid.esm.min.mjs';
import katex from 'https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.mjs';
__MERMAID_INITIALIZE__
window.__validate = async (kind, code) => {
  try {
    if (kind === 'mermaid') { await mermaid.parse(code); return { ok: true, error: '' }; }
    katex.renderToString(code, { throwOnError: true, strict: 'ignore', displayMode: false });
    return { ok: true, error: '' };
  } catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
};
window.__validatorReady = true;
</script>
</body></html>
"""


def _validator_html() -> str:
    """Pagina del validatore con il pin corrente (letto a ogni chiamata: il
    modulo resta importabile senza ambiente configurato)."""
    return _VALIDATOR_HTML_TEMPLATE.replace(
        "__MERMAID_VERSION__", get_settings().mermaid_cdn_version
    ).replace("__MERMAID_INITIALIZE__", mermaid_initialize_js(use_max_width=False))


def __getattr__(name: str) -> str:
    """`_VALIDATOR_HTML` costruita alla prima lettura (PEP 562)."""
    if name == "_VALIDATOR_HTML":
        return _validator_html()
    raise AttributeError(name)


async def _validate_js_batch_async(
    items: list[tuple[str, str]],
) -> list[tuple[bool, str]] | None:
    """Valida (kind, code) in UNA pagina headless. Ritorna lista parallela
    `(ok, error)`, oppure `None` se le librerie CDN non si caricano
    (validazione JS non disponibile → il caller degrada)."""
    if not items:
        return []
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        log.warning("asset_validator_playwright_import_failed", error=str(exc))
        return None

    results: list[tuple[bool, str]] = []
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=["--no-sandbox"])
            try:
                page = await browser.new_page()
                await block_external_requests(page)
                await page.set_content(_validator_html(), wait_until="domcontentloaded")
                try:
                    await page.wait_for_function("window.__validatorReady === true", timeout=15_000)
                except Exception as exc:
                    log.warning("asset_validator_setup_failed", error=str(exc))
                    return None
                for kind, code in items:
                    try:
                        res = await page.evaluate(
                            "([k, c]) => window.__validate(k, c)", [kind, code]
                        )
                        results.append((bool(res.get("ok")), str(res.get("error") or "")))
                    except Exception as exc:
                        results.append((False, f"validator error: {exc}"))
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("asset_validator_browser_failed", error=str(exc))
        return None
    return results


def _validate_js_batch_sync(
    items: list[tuple[str, str]],
) -> list[tuple[bool, str]] | None:
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_validate_js_batch_async(items))
    finally:
        with contextlib.suppress(Exception):
            loop.close()


# Timeout hard sulla validazione JS: se Playwright/Chromium si impianta, il
# worker non deve MAI restare appeso in "validating_assets". Oltre la soglia
# si degrada (None) e si prosegue (LaTeX resta validato da latex2mathml).
_VALIDATION_TIMEOUT_S = 90.0


async def _validate_js_batch(
    items: list[tuple[str, str]],
) -> list[tuple[bool, str]] | None:
    if not items:
        return []
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_validate_js_batch_sync, items),
            timeout=_VALIDATION_TIMEOUT_S,
        )
    except Exception as exc:
        log.warning("asset_validator_timeout", error=str(exc))
        return None


def validate_latex_mathml(latex: str) -> tuple[bool, str]:
    """Valida una formula LaTeX con `latex2mathml` (motore dell'export
    PDF/video). Sync, offline. Ritorna `(ok, error_message)`."""
    src = (latex or "").strip()
    if not src:
        return (False, "formula vuota")
    try:
        _latex_to_mathml(src)
    except Exception as exc:
        return (False, str(exc))
    return (True, "")


# ---------------------------------------------------------------------------
# Estrazione math inline ($...$ / $$...$$) — conservativa, offset-based
# ---------------------------------------------------------------------------

# Display `$$...$$` (multilinea). Inline `$...$` con guardie anti-currency:
# niente whitespace subito dopo l'apertura o prima della chiusura.
_DISPLAY_RE = re.compile(r"(?<!\\)\$\$(.+?)(?<!\\)\$\$", re.DOTALL)
# Inner: inizia e finisce con un carattere non-`$` e non-spazio (guardia
# anti-currency + impedisce che l'inner inglobi un delimitatore `$`).
_INLINE_RE = re.compile(r"(?<!\\)\$(?!\$)([^$\s](?:[^$]*?[^$\s])?)(?<!\\)\$(?!\$)")


@dataclass
class _MathSpan:
    start: int
    end: int  # esclusivo
    inner: str
    display: bool


def _find_math_spans(text: str) -> list[_MathSpan]:
    """Trova le formule math inline/display in un campo testo, con offset
    esatti. Display-first; gli inline vengono cercati solo FUORI dai display
    per non sovrapporsi."""
    if not text or "$" not in text:
        return []
    spans: list[_MathSpan] = []
    covered: list[tuple[int, int]] = []
    for m in _DISPLAY_RE.finditer(text):
        spans.append(_MathSpan(m.start(), m.end(), m.group(1), True))
        covered.append((m.start(), m.end()))

    def _in_covered(pos: int) -> bool:
        return any(s <= pos < e for s, e in covered)

    for m in _INLINE_RE.finditer(text):
        if _in_covered(m.start()) or _in_covered(m.end() - 1):
            continue
        inner = m.group(1)
        # Salta currency evidenti: inner tutto numerico/punteggiatura.
        if not re.search(r"[A-Za-z\\]", inner):
            continue
        spans.append(_MathSpan(m.start(), m.end(), inner, False))
    return spans


# ---------------------------------------------------------------------------
# Slot: un asset fragile con sorgente corrente + commit verso l'output
# ---------------------------------------------------------------------------


@dataclass
class _Slot:
    id: str
    kind: str  # "latex" | uno di `RENDERABLE_FORMATS`
    current: str
    context: str
    commit: Callable[[str], None]  # scrive il valore finale nell'output


@dataclass
class _InlineField:
    """Accumula le sostituzioni di math inline di un singolo campo testo e le
    riscrive in-place (right-to-left) preservando il resto byte-per-byte."""

    setter: Callable[[str], None]
    original: str
    repls: list[tuple[int, int, str]] = field(default_factory=list)

    def add_repl(self, start: int, end: int, new_full: str) -> None:
        self.repls.append((start, end, new_full))

    def write(self) -> None:
        if not self.repls:
            return
        s = self.original
        for start, end, new_full in sorted(self.repls, key=lambda r: r[0], reverse=True):
            s = s[:start] + new_full + s[end:]
        self.setter(s)


def _sanitize(kind: str, value: str) -> str:
    """Ripulisce l'output del fix AI: niente code-fence/backtick; per il
    LaTeX rimuove eventuali delimitatori reintrodotti per errore."""
    v = _strip_control_chars(value or "").strip()
    if v.startswith("```"):
        # Tag del fence con cifre e trattini (```vega-lite, ```dot, ```json5):
        # `[a-zA-Z]*` lascerebbe `-lite` in testa alla spec.
        v = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", v)
        v = re.sub(r"\n?```$", "", v).strip()
    if kind == "latex":
        # Rimuove i delimitatori reintrodotti per errore dal fix, anche se
        # presenti solo in testa o solo in coda (es. un `$$` spurio finale).
        v = re.sub(r"^\\[\[(]", "", v)
        v = re.sub(r"\\[\])]$", "", v)
        v = re.sub(r"^\$+", "", v)
        v = re.sub(r"(?<!\\)\$+$", "", v)  # non toccare un `\$` escapato
        v = v.strip()
    return v


def _looks_corrupted(kind: str, value: str) -> bool:
    """Scarta un fix che reintroduce un placeholder asset (`[EQ:..]` ecc.):
    significa che il modello ha "inventato" un riferimento invece di
    correggere la sintassi."""
    return bool(re.search(r"\[(FIG|TAB|EQ|EX):", value or ""))


# ---------------------------------------------------------------------------
# Collezione slot dagli output AI
# ---------------------------------------------------------------------------


def _collect_content_slots(
    output: LessonContentOutput,
) -> tuple[list[_Slot], list[_InlineField]]:
    slots: list[_Slot] = []
    inline_fields: list[_InlineField] = []

    # Equazioni dedicate (LaTeX senza delimitatori): formula principale +
    # ogni passaggio LaTeX della dimostrazione (`proof[].latex`).
    for eq in output.equations:
        ctx = eq.label or eq.explanation or eq.statement or ""
        if (eq.latex or "").strip():
            slots.append(
                _Slot(
                    id=f"eq:{eq.equation_id}",
                    kind="latex",
                    current=eq.latex,
                    context=ctx,
                    commit=lambda v, _e=eq: setattr(_e, "latex", v),
                )
            )
        for j, step in enumerate(eq.proof):
            if (step.latex or "").strip():
                slots.append(
                    _Slot(
                        id=f"eq:{eq.equation_id}.proof{j}",
                        kind="latex",
                        current=step.latex,
                        context=ctx,
                        commit=lambda v, _s=step: setattr(_s, "latex", v),
                    )
                )

    # Figure renderizzabili (Mermaid, Vega-Lite, DOT, function): il kind e'
    # il formato, il dispatch e' del registro. Ignora image/legacy.
    for asset in output.visual_assets:
        if asset.format in RENDERABLE_FORMATS and (asset.content or "").strip():
            slots.append(
                _Slot(
                    id=f"asset:{asset.asset_id}",
                    kind=asset.format,
                    current=asset.content,
                    context=asset.caption or asset.alt_text or "",
                    commit=lambda v, _a=asset: setattr(_a, "content", v),
                )
            )

    # Math inline nei campi testo. `key` deve essere UNIVOCO per campo:
    # sezioni ed esempi usano tutti attr="content", quindi senza un prefisso
    # distinto gli id collidono (es. `content#4` di due sezioni) e il
    # fix-loop riparerebbe lo slot sbagliato.
    def _add_inline(getter_obj: Any, attr: str, ctx: str, key: str) -> None:
        text = getattr(getter_obj, attr, "") or ""
        spans = _find_math_spans(text)
        if not spans:
            return
        fld = _InlineField(
            setter=lambda v, _o=getter_obj, _a=attr: setattr(_o, _a, v),
            original=text,
        )
        inline_fields.append(fld)
        for i, sp in enumerate(spans):
            delim = "$$" if sp.display else "$"
            slots.append(
                _Slot(
                    id=f"{key}#{i}",
                    kind="latex",
                    current=sp.inner,
                    context=ctx,
                    commit=lambda v, _f=fld, _s=sp, _d=delim: _f.add_repl(
                        _s.start, _s.end, f"{_d}{v}{_d}"
                    ),
                )
            )

    _add_inline(output, "introduction", "introduzione", "introduction")
    _add_inline(output, "summary", "sintesi", "summary")
    for si, sec in enumerate(output.sections):
        _add_inline(sec, "content", sec.title or "sezione", f"sec{si}.content")
    for ei, ex in enumerate(output.examples):
        _add_inline(ex, "content", ex.title or "esempio", f"ex{ei}.content")
    # Enunciato e testo dei passaggi: math inline `$..$` validato/riparato.
    for ei, eq in enumerate(output.equations):
        _ctx = eq.label or eq.equation_id
        _add_inline(eq, "statement", _ctx, f"eq{ei}.statement")
        for j, step in enumerate(eq.proof):
            _add_inline(step, "text", _ctx, f"eq{ei}.proof{j}.text")

    return slots, inline_fields


def _collect_slides_slots(
    output: LessonSlidesOutput,
) -> tuple[list[_Slot], list[_InlineField]]:
    slots: list[_Slot] = []
    inline_fields: list[_InlineField] = []

    for asset in output.new_assets:
        if asset.format in RENDERABLE_FORMATS and (asset.content or "").strip():
            slots.append(
                _Slot(
                    id=f"new_asset:{asset.asset_id}",
                    kind=asset.format,
                    current=asset.content,
                    context=asset.caption or asset.alt_text or "",
                    commit=lambda v, _a=asset: setattr(_a, "content", v),
                )
            )

    def _add_inline_field(getter_obj: Any, attr: str, ctx: str, key: str) -> None:
        text = getattr(getter_obj, attr, "") or ""
        spans = _find_math_spans(text)
        if not spans:
            return
        fld = _InlineField(
            setter=lambda v, _o=getter_obj, _a=attr: setattr(_o, _a, v),
            original=text,
        )
        inline_fields.append(fld)
        for i, sp in enumerate(spans):
            delim = "$$" if sp.display else "$"
            slots.append(
                _Slot(
                    id=f"{key}#{i}",
                    kind="latex",
                    current=sp.inner,
                    context=ctx,
                    commit=lambda v, _f=fld, _s=sp, _d=delim: _f.add_repl(
                        _s.start, _s.end, f"{_d}{v}{_d}"
                    ),
                )
            )

    # new_equations di Fase 4: formula + passaggi LaTeX + enunciato/testo.
    for ei, eq in enumerate(output.new_equations):
        ctx = eq.label or eq.equation_id
        if (eq.latex or "").strip():
            slots.append(
                _Slot(
                    id=f"new_eq:{eq.equation_id}",
                    kind="latex",
                    current=eq.latex,
                    context=ctx,
                    commit=lambda v, _e=eq: setattr(_e, "latex", v),
                )
            )
        for j, step in enumerate(eq.proof):
            if (step.latex or "").strip():
                slots.append(
                    _Slot(
                        id=f"new_eq:{eq.equation_id}.proof{j}",
                        kind="latex",
                        current=step.latex,
                        context=ctx,
                        commit=lambda v, _s=step: setattr(_s, "latex", v),
                    )
                )
        _add_inline_field(eq, "statement", ctx, f"new_eq{ei}.statement")
        for j, step in enumerate(eq.proof):
            _add_inline_field(step, "text", ctx, f"new_eq{ei}.proof{j}.text")

    for s_idx, slide in enumerate(output.slides):
        _add_inline_field(slide, "body", slide.title or "slide", f"s{s_idx}.body")
        # I bullet sono una lista: math inline raro, ma lo copriamo riscrivendo
        # l'intera lista bullet se contiene formule.
        bullets = list(slide.bullets or [])
        for b_idx, bullet in enumerate(bullets):
            spans = _find_math_spans(bullet or "")
            if not spans:
                continue
            fld = _InlineField(
                setter=lambda v, _sl=slide, _bi=b_idx: _set_bullet(_sl, _bi, v),
                original=bullet,
            )
            inline_fields.append(fld)
            for i, sp in enumerate(spans):
                delim = "$$" if sp.display else "$"
                slots.append(
                    _Slot(
                        id=f"s{s_idx}.bullet{b_idx}#{i}",
                        kind="latex",
                        current=sp.inner,
                        context=slide.title or "slide",
                        commit=lambda v, _f=fld, _s=sp, _d=delim: _f.add_repl(
                            _s.start, _s.end, f"{_d}{v}{_d}"
                        ),
                    )
                )

    return slots, inline_fields


def _set_bullet(slide: Any, index: int, value: str) -> None:
    bullets = list(slide.bullets or [])
    if 0 <= index < len(bullets):
        bullets[index] = value
        slide.bullets = bullets


# ---------------------------------------------------------------------------
# Orchestrazione validate + fix
# ---------------------------------------------------------------------------


async def _validate_slots(slots: list[_Slot]) -> list[AssetCheck]:
    """Valida ogni slot per kind.

    - `latex`: latex2mathml (Python, gate duro) E KaTeX (JS);
    - `mermaid`: gate statico D8 del registro (duro, offline), poi parse con
      la 11.x del pin `settings.mermaid_cdn_version` (JS);
    - `vegalite` | `dot` | `function` | `tikz`: `validate(deep=True)` del renderer in
      un thread con `wait_for(figure_render_timeout_seconds)`, mai
      pass-through; formato non disponibile → check non fixable; timeout →
      check invalido (riparabile: il fix AI può semplificare la spec).

    Nel batch JS entrano SOLO gli slot `latex` e i `mermaid` che superano il
    gate statico: `js_pos` rimappa l'indice dello slot sulla posizione nel
    batch (i kind non-JS e i Mermaid già respinti non consumano
    posizioni). Se la validazione JS non e' disponibile (CDN
    down), il LaTeX resta gated da latex2mathml e Mermaid/KaTeX degradano a
    pass-through."""
    js_pos: dict[int, int] = {}
    js_items: list[tuple[str, str]] = []
    static_errors: dict[int, str] = {}
    for i, s in enumerate(slots):
        if s.kind == "mermaid":
            ok_s, err_s = REGISTRY["mermaid"].validate(s.current)  # gate statico duro
            if not ok_s:
                static_errors[i] = err_s
                continue  # non consuma una posizione nel batch JS
        if s.kind in ("latex", "mermaid"):
            js_pos[i] = len(js_items)
            js_items.append((s.kind, s.current))
    js_results = await _validate_js_batch(js_items)
    formats = available_formats()
    render_timeout = float(get_settings().figure_render_timeout_seconds)
    tikz_timeout = tikz_timeout_seconds()

    checks: list[AssetCheck] = []
    for i, slot in enumerate(slots):
        if slot.kind == "latex":
            ok_m, err_m = validate_latex_mathml(slot.current)
            if not ok_m:
                checks.append(AssetCheck(slot.id, "latex", False, f"latex2mathml: {err_m}"))
                continue
            if js_results is None:
                checks.append(AssetCheck(slot.id, "latex", True, ""))
            else:
                ok_k, err_k = js_results[js_pos[i]]
                checks.append(AssetCheck(slot.id, "latex", ok_k, "" if ok_k else f"KaTeX: {err_k}"))
        elif slot.kind == "mermaid":
            if i in static_errors:
                checks.append(AssetCheck(slot.id, "mermaid", False, static_errors[i]))
                continue
            if js_results is None:
                checks.append(AssetCheck(slot.id, "mermaid", True, ""))
            else:
                ok, err = js_results[js_pos[i]]
                checks.append(AssetCheck(slot.id, "mermaid", ok, "" if ok else f"mermaid: {err}"))
        else:  # vegalite | dot | function | tikz
            renderer = REGISTRY.get(slot.kind)
            if renderer is None or slot.kind not in formats:
                checks.append(
                    AssetCheck(slot.id, slot.kind, False, f"{slot.kind}_unavailable", fixable=False)
                )
                continue
            # Stesso tetto dell'endpoint `render-function`: una spec costosa
            # (o un renderer lento) non deve bloccare la validazione della
            # lezione oltre `figure_render_timeout_seconds`. Il contesto dà
            # l'`asset_id` ai log della misura geometrica nel thread.
            timeout = tikz_timeout if slot.kind == "tikz" else render_timeout
            try:
                with figure_asset_context(slot.id.split(":", 1)[-1]):
                    ok, err = await asyncio.wait_for(
                        asyncio.to_thread(renderer.validate, slot.current, deep=True),
                        timeout=timeout,
                    )
            except TimeoutError:
                ok, err = False, f"{slot.kind}: validazione oltre {timeout:g} s"
            # Sandbox occupata o motore assente: il fix AI non li risolve.
            fixable = not err.startswith(_TIKZ_ENGINE_ERRORS)
            checks.append(AssetCheck(slot.id, slot.kind, ok, "" if ok else err, fixable=fixable))
    return checks


# Prefissi degli errori di `TikzRenderer.validate` che non dipendono dal
# sorgente (`AssetCheck.fixable=False`).
_TIKZ_ENGINE_ERRORS = ("tikz_busy", "tikz_unavailable")


def _fix_cap(kind: str, max_attempts: int) -> int:
    """Fix AI ammessi per uno slot: `tikz` ne ha uno solo
    (`FIGURE_TIKZ_FIX_MAX_ATTEMPTS`, entro il tetto globale)."""
    if kind == "tikz":
        return min(max_attempts, max(0, int(get_settings().figure_tikz_fix_max_attempts)))
    return max_attempts


def _unresolved_code(invalid: list[AssetCheck]) -> str | None:
    return "tikz_unresolved" if any(c.kind == "tikz" for c in invalid) else None


def _unresolved_details(invalid: list[AssetCheck]) -> str:
    return "; ".join(f"{c.id} [{c.kind}]: {c.error_message}" for c in invalid[:5])


def _raise_if_unfixable(
    invalid: list[AssetCheck], usage_sink: list[dict[str, Any]] | None = None
) -> None:
    """Solleva subito se un check invalido non e' riparabile dal fix AI."""
    blocked = [c for c in invalid if not c.fixable]
    if blocked:
        raise AssetFixUnresolvedError(
            _unresolved_details(blocked),
            assets_usage=usage_sink,
            code=_unresolved_code(blocked),
        )


def _usage_entry(phase: str, asset_id: str | None, usage: dict[str, Any]) -> dict[str, Any]:
    """Voce di `assets_usage`: fase, slot e i campi di `build_usage_dict`."""
    return {"phase": phase, "asset_id": asset_id, **usage}


async def _validate_and_fix(
    slots: list[_Slot],
    inline_fields: list[_InlineField],
    *,
    language_code: str,
    usage_sink: list[dict[str, Any]] | None = None,
) -> int:
    """Valida gli slot e ripara SOLO quelli invalidi. Gli asset gia' validi
    NON vengono toccati: nessun clean, nessun commit, restano byte-identici.
    Ritorna il numero di asset effettivamente modificati. Solleva
    `AssetFixUnresolvedError` (recuperabile) se uno resta invalido dopo i
    tentativi. Ogni chiamata di fix riuscita aggiunge a `usage_sink` una
    voce `phase="fix"` con l'id dello slot."""
    if not slots:
        return 0

    settings = get_settings()
    max_attempts = max(0, int(settings.asset_fix_max_attempts))
    by_id = {s.id: s for s in slots}
    # Snapshot dei valori originali: a fine processo committiamo SOLO gli
    # slot il cui valore e' davvero cambiato (cioe' quelli riparati).
    originals = {s.id: s.current for s in slots}

    # Giro 0: valida gli originali. Se e' tutto valido, non tocchiamo NULLA.
    checks = await _validate_slots(slots)
    invalid = [c for c in checks if not c.ok]
    if not invalid:
        return 0

    # Step deterministico (no AI): pulizia control-char/combining SOLO sugli
    # asset invalidi. Spesso il garbage del modello (ESC, ecc.) si risolve
    # qui senza spendere token.
    for c in invalid:
        slot = by_id[c.id]
        cleaned = (
            _clean_latex_source(slot.current)
            if slot.kind == "latex"
            else _strip_control_chars(slot.current)
        )
        if cleaned and cleaned != slot.current:
            slot.current = cleaned
    checks = await _validate_slots(slots)
    invalid = [c for c in checks if not c.ok]

    # Un check non fixable (formato non disponibile sul server) non va al fix
    # AI: nessun token speso, escalation immediata alla rigenerazione.
    _raise_if_unfixable(invalid, usage_sink)

    # Fix AI iterativo, SOLO sugli asset ancora invalidi. Tetto globale in
    # giri (`asset_fix_max_attempts`) e tetto per slot (`_fix_cap`: `tikz`
    # uno solo); per gli altri formati il secondo coincide col primo.
    remaining = max_attempts
    spent: dict[str, int] = {}
    while invalid:
        capped = [c for c in invalid if spent.get(c.id, 0) >= _fix_cap(c.kind, max_attempts)]
        if remaining <= 0 or capped:
            raise AssetFixUnresolvedError(
                _unresolved_details(invalid),
                assets_usage=usage_sink,
                code=_unresolved_code(invalid),
            )
        remaining -= 1
        for c in invalid:
            spent[c.id] = spent.get(c.id, 0) + 1
            slot = by_id[c.id]
            try:
                out, usage = await openai_asset_fix_service.fix_asset(
                    kind=cast(openai_asset_fix_service.AssetKind, slot.kind),
                    source=slot.current,
                    error_message=c.error_message,
                    context=slot.context,
                    language_code=language_code,
                )
            except openai_asset_fix_service.OpenAIAssetFixError as exc:
                # Fix transitoriamente fallito: lascia il sorgente invariato,
                # ri-fallira' e (se non si risolve) escalera' a re-gen lezione.
                # Se la chiamata e' stata pagata lo stesso (200 inutilizzabile)
                # il suo usage entra comunque in `usage_sink` (D16).
                cost: Any = None
                if usage_sink is not None and isinstance(exc.usage, dict):
                    usage_sink.append(_usage_entry("fix", slot.id, exc.usage))
                    cost = exc.usage.get("cost_usd")
                log.warning(
                    "asset_fix_call_failed", asset_id=slot.id, error=str(exc), cost_usd=cost
                )
                continue
            if usage_sink is not None:
                usage_sink.append(_usage_entry("fix", slot.id, usage))
            candidate = _sanitize(slot.kind, out.fixed_content)
            if not candidate or _looks_corrupted(slot.kind, candidate):
                continue
            slot.current = candidate
        checks = await _validate_slots(slots)
        invalid = [c for c in checks if not c.ok]
        _raise_if_unfixable(invalid, usage_sink)

    # Commit CHIRURGICO: solo gli slot davvero cambiati (riparati). Gli asset
    # gia' validi non vengono ne' committati ne' riscritti; i campi inline
    # senza span cambiati restano byte-identici (write() e' no-op se 0 repl).
    fixed_count = 0
    for slot in slots:
        if slot.current != originals[slot.id]:
            slot.commit(slot.current)
            fixed_count += 1
    for fld in inline_fields:
        fld.write()
    return fixed_count


# ---------------------------------------------------------------------------
# Rete di sicurezza i18n: localizza i campi asset rimasti in lingua sbagliata
# ---------------------------------------------------------------------------

# Stripping di math/sintassi: SOLO per il gate (decidere se un campo va
# localizzato). Il valore inviato al traduttore resta quello integrale.
_GATE_MATH_RE = re.compile(r"\$\$.*?\$\$|\$[^$]*\$", re.DOTALL)
_GATE_LATEX_CMD_RE = re.compile(r"\\[A-Za-z]+")
_GATE_TAG_RE = re.compile(r"\[[A-Za-z]+:[^\]]+\]")


def _gate_prose(text: str) -> str:
    """Rimuove math/LaTeX/placeholder per isolare la prosa leggibile."""
    s = _GATE_MATH_RE.sub(" ", text)
    s = _GATE_LATEX_CMD_RE.sub(" ", s)
    s = _GATE_TAG_RE.sub(" ", s)
    return s


def _needs_localization(text: str, language_code: str) -> bool:
    """True se `text` contiene prosa NON nello script della lingua target.

    Affidabile per lingue a script non-latino (giapponese, cirillico, ...): se
    il testo contiene già almeno un carattere dello script atteso lo consideriamo
    a posto (caso dominante: campo interamente nella lingua sbagliata). Per
    lingue latine il gate è gestito a monte (sempre spento).
    """
    if not text or not text.strip():
        return False
    if has_target_script_chars(text, language_code):
        return False
    return any(ch.isalpha() for ch in _gate_prose(text))


@dataclass
class _LocField:
    """Un campo testuale di un asset candidato alla localizzazione."""

    key: str
    text: str
    kind: str  # "text" | "table" | uno di `RENDERABLE_FORMATS`
    apply: Callable[[str], None]


def _add_figure_loc_fields(fields: list[_LocField], *, prefix: str, asset: Any) -> None:
    """Campi testuali di una figura secondo il suo renderer (D7): Mermaid
    l'intero sorgente (chiave vuota → `{prefix}.content`, come sempre);
    Vega-Lite `title`/`axis.title`/`legend.title`/`header.title`/testi
    letterali; DOT i valori di `label|xlabel|headlabel|taillabel`;
    `function` le label di espressioni e annotazioni. Ogni traduzione e'
    riapplicata al contenuto corrente dell'asset con
    `apply_translations` (una chiave alla volta: il contenuto e' riletto a
    ogni applicazione)."""
    fmt = asset.format
    renderer = REGISTRY.get(fmt) if fmt in RENDERABLE_FORMATS else None
    if renderer is None or not (asset.content or "").strip():
        return
    for sub, text in renderer.extract_translatable(asset.content).items():
        if not text or not text.strip():
            continue
        key = f"{prefix}.content" if not sub else f"{prefix}.content.{sub}"

        def _apply(v: str, _a: Any = asset, _r: Any = renderer, _sub: str = sub) -> None:
            _a.content = _r.apply_translations(_a.content, {_sub: v})

        fields.append(_LocField(key, text, fmt, _apply))


def _collect_content_loc_fields(output: LessonContentOutput) -> list[_LocField]:
    """Campi testuali localizzabili dell'output di Fase 3."""
    fields: list[_LocField] = []

    def add(key: str, text: str, kind: str, setter: Callable[[str], None]) -> None:
        if text and text.strip():
            fields.append(_LocField(key, text, kind, setter))

    for i, a in enumerate(output.visual_assets):
        add(f"va.{i}.caption", a.caption, "text", lambda v, a=a: setattr(a, "caption", v))
        add(f"va.{i}.alt_text", a.alt_text, "text", lambda v, a=a: setattr(a, "alt_text", v))
        _add_figure_loc_fields(fields, prefix=f"va.{i}", asset=a)
    for i, t in enumerate(output.tables):
        add(f"tb.{i}.caption", t.caption, "text", lambda v, t=t: setattr(t, "caption", v))
        add(f"tb.{i}.markdown", t.markdown, "table", lambda v, t=t: setattr(t, "markdown", v))
    for i, e in enumerate(output.equations):
        add(f"eq.{i}.label", e.label, "text", lambda v, e=e: setattr(e, "label", v))
        add(
            f"eq.{i}.explanation",
            e.explanation,
            "text",
            lambda v, e=e: setattr(e, "explanation", v),
        )
        add(f"eq.{i}.statement", e.statement, "text", lambda v, e=e: setattr(e, "statement", v))
        for j, p in enumerate(e.proof):
            add(f"eq.{i}.proof.{j}.text", p.text, "text", lambda v, p=p: setattr(p, "text", v))
    for i, ex in enumerate(output.examples):
        add(f"ex.{i}.title", ex.title, "text", lambda v, ex=ex: setattr(ex, "title", v))
        add(f"ex.{i}.content", ex.content, "text", lambda v, ex=ex: setattr(ex, "content", v))
    return fields


def _collect_slides_loc_fields(output: LessonSlidesOutput) -> list[_LocField]:
    """Campi testuali localizzabili degli asset NUOVI di Fase 4. La prosa delle
    slide (`title`/`body`/`bullets`) è coperta dal prompt hardening."""
    fields: list[_LocField] = []

    def add(key: str, text: str, kind: str, setter: Callable[[str], None]) -> None:
        if text and text.strip():
            fields.append(_LocField(key, text, kind, setter))

    for i, a in enumerate(output.new_assets):
        add(f"na.{i}.caption", a.caption, "text", lambda v, a=a: setattr(a, "caption", v))
        add(f"na.{i}.alt_text", a.alt_text, "text", lambda v, a=a: setattr(a, "alt_text", v))
        _add_figure_loc_fields(fields, prefix=f"na.{i}", asset=a)
    for i, t in enumerate(output.new_tables):
        add(f"nt.{i}.caption", t.caption, "text", lambda v, t=t: setattr(t, "caption", v))
        add(f"nt.{i}.markdown", t.markdown, "table", lambda v, t=t: setattr(t, "markdown", v))
    for i, e in enumerate(output.new_equations):
        add(f"ne.{i}.label", e.label, "text", lambda v, e=e: setattr(e, "label", v))
        add(
            f"ne.{i}.explanation",
            e.explanation,
            "text",
            lambda v, e=e: setattr(e, "explanation", v),
        )
        add(f"ne.{i}.statement", e.statement, "text", lambda v, e=e: setattr(e, "statement", v))
        for j, p in enumerate(e.proof):
            add(f"ne.{i}.proof.{j}.text", p.text, "text", lambda v, p=p: setattr(p, "text", v))
    for i, ex in enumerate(output.new_examples):
        add(f"nx.{i}.title", ex.title, "text", lambda v, ex=ex: setattr(ex, "title", v))
        add(f"nx.{i}.content", ex.content, "text", lambda v, ex=ex: setattr(ex, "content", v))
    return fields


async def _localize_fields(
    fields: list[_LocField],
    *,
    language_code: str,
    usage_sink: list[dict[str, Any]] | None = None,
) -> bool:
    """Localizza i campi rimasti in lingua sbagliata. Best-effort: ogni errore
    diventa un warning e non blocca la generazione. Ritorna True se è cambiato
    un asset STRUTTURALE (ogni kind diverso da `text`: tabella o figura) → il
    chiamante ri-valida la sintassi offline. La chiamata riuscita aggiunge a
    `usage_sink` una voce `phase="localize"` (una chiamata copre più campi:
    `asset_id` è `None` e `fields` ne dà il numero).
    """
    settings = get_settings()
    if not settings.asset_localize_enabled:
        return False
    # Gate spento per lingue a script latino: nessun rilevamento affidabile e
    # nessuna spesa di token (la difesa lì è il prompt hardening).
    if primary_script(language_code) is None:
        return False
    suspect = [f for f in fields if _needs_localization(f.text, language_code)]
    if not suspect:
        return False
    items = {f.key: f.text for f in suspect}
    try:
        localized, usage = await openai_asset_localize_service.localize_texts(
            items=items, language_code=language_code
        )
    except Exception as exc:
        log.warning("asset_localize_call_failed", error=str(exc), fields=len(items))
        return False
    if usage_sink is not None and usage:
        usage_sink.append({**_usage_entry("localize", None, usage), "fields": len(items)})

    structural_changed = False
    changed = 0
    for f in suspect:
        v = localized.get(f.key)
        if v and v != f.text:
            f.apply(v)
            changed += 1
            if f.kind != "text":
                structural_changed = True
    log.info(
        "asset_localize_applied",
        target=language_code,
        suspect=len(suspect),
        changed=changed,
    )
    return structural_changed


# ---------------------------------------------------------------------------
# Revisione figura ↔ testo (D15)
# ---------------------------------------------------------------------------

# Box del fit nella misura inviata al revisore: la dispensa A4 del template
# di default (`course_lesson_pdf_service._compute_template_margins_cm({})`,
# 170 × 242 mm) meno il padding del wrapper per formato (solo Mermaid);
# valori pinnati da un test contro le costanti del PDF (qui niente import
# del servizio PDF, che carica WeasyPrint). Tabella e non confronto
# letterale sul formato (D2, doc 17 § 9).
_REVIEW_FIT_BOX_MM: tuple[float, float] = (170.0, 242.0)
_REVIEW_BODY_PADDING_MM: dict[str, float] = {"mermaid": 2.0}
# Box del ribaltamento della catena: il box del revisore al netto del
# padding di Mermaid, cioè quello con cui la dispensa decide (D15).
_REVIEW_CHAIN_BOX_MM: tuple[float, float] = (
    _REVIEW_FIT_BOX_MM[0] - _REVIEW_BODY_PADDING_MM["mermaid"],
    _REVIEW_FIT_BOX_MM[1],
)
# Suffisso della chiave della riscrittura nella validazione e nella mappa di
# resa: l'originale è `asset:<id>`, la riscrittura `asset:<id>#review`.
_REVIEW_SUFFIX = "#review"
_LOG_CAP = 300
# Id elencati nel motivo di `nodes_removed` / `nodes_isolated` (il motivo
# torna al modello e finisce nei log: basta un campione ordinato).
_REVIEW_IDS_SHOWN = 8

# Chiamate del revisore in volo per processo (`figure_review_max_parallel`):
# un giro ne lancia una per figura in attesa e le lezioni corrono in
# parallelo (`course_lesson_content_max_concurrency`), quindi senza tetto le
# chiamate simultanee sarebbero figure × lezioni. Un semaforo per loop, come
# `figure_render_service._render_semaphore` (uvicorn e test usano loop
# diversi); il valore è letto alla prima chiamata del loop.
_review_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)

OUTCOME_COHERENT = "coherent"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_ACCEPTED = "accepted"
OUTCOME_REJECTED = "rejected"


@dataclass
class _ReviewItem:
    """Una figura in revisione: l'originale non cambia fino al commit."""

    asset: Any
    key: str
    fmt: str
    original: str
    context: ReviewContext
    measure: FigureMeasure
    feedback: str = ""


@dataclass(frozen=True)
class _Judgement:
    ok: bool
    reason: str
    crossings_before: int | None
    crossings_after: int | None


def _review_semaphore() -> asyncio.Semaphore:
    """Semaforo delle chiamate del revisore del loop corrente."""
    loop = asyncio.get_running_loop()
    sem = _review_semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(max(1, int(get_settings().figure_review_max_parallel)))
        _review_semaphores[loop] = sem
    return sem


def _review_context(output: LessonContentOutput, asset_id: str) -> ReviewContext:
    """Testo del primo blocco che cita la figura (introduzione, sezioni,
    sintesi: l'ordine della numerazione; poi esempi e tabelle, dove il tag
    è un rimando ma non una posizione di inserimento), id confrontato con
    `.strip().lower()`; se nessuno la cita, il corpo intero (il tetto lo
    applica il servizio del revisore)."""
    target = asset_id.strip().lower()
    blocks: list[tuple[str, str]] = [("Introduzione", output.introduction or "")]
    blocks.extend((s.title, s.content or "") for s in output.sections)
    blocks.append(("Sintesi", output.summary or ""))
    # Stesso corpus dei warning di Fase 3 (course_lesson_content_service):
    # una figura citata solo in un esempio o in una tabella è comunque
    # citata, e il revisore deve vedere quella frase.
    extra: list[tuple[str, str]] = [
        (ex.title or "Esempio", ex.content or "") for ex in output.examples
    ]
    extra.extend((t.caption or "Tabella", t.markdown or "") for t in output.tables)
    for title, text in [*blocks, *extra]:
        if any(m.group(1).strip().lower() == target for m in FIG_REF_RE.finditer(text)):
            return ReviewContext(title=title, text=text, cited=True)
    body = "\n\n".join(text.strip() for _title, text in blocks if text.strip())
    return ReviewContext(title="", text=body, cited=False)


def _fit_for_review(fmt: str, fig: RenderedFigure) -> FigureFit | None:
    """Fit della figura nel box del revisore (lo stesso della dispensa)."""
    box = svg_intrinsic_box(fig.svg)
    base, _font_source = resolve_base_font_px(fmt, fig.metrics)
    if box is None or base is None:
        return None
    padding = _REVIEW_BODY_PADDING_MM.get(fmt, 0.0)
    return fit_figure_width_mm(
        vb_w=box.vb_w,
        vb_h=box.vb_h,
        base_font_px=base,
        box_w_mm=_REVIEW_FIT_BOX_MM[0] - padding,
        box_h_mm=_REVIEW_FIT_BOX_MM[1],
        variant="lesson",
        intrinsic_w_px=box.width_px,
    )


def _figure_measure(fmt: str, source: str, fig: RenderedFigure | None) -> FigureMeasure:
    """Misura di WP5 di una figura: nodi e archi dal sorgente sanificato
    (solo grafi), geometria e corpo del testo dalla resa (se c'è)."""
    renderer = REGISTRY.get(fmt)
    sanitized = renderer.sanitize(source) if renderer is not None else source
    counts = graph_source_metrics(fmt, sanitized)
    nodes = counts.nodes if counts is not None else None
    edges = counts.edges if counts is not None else None
    if fig is None:
        return FigureMeasure(nodes=nodes, edges=edges)
    metrics = fig.metrics
    text_pt: float | None = None
    in_band: bool | None = None
    fit = _fit_for_review(fmt, fig)
    if fit is not None:
        text_pt, in_band = fit.text_pt, fit.in_band
        # Stessa scelta dell'export (D15): sotto la banda vince la variante
        # verticale, se c'è e se migliora. Senza questo il revisore
        # leggerebbe «fuori banda» su una catena che la dispensa stampa in
        # banda.
        if not fit.in_band and fig.chain_variant is not None:
            flipped = _fit_for_review(fmt, fig.chain_variant)
            if flipped is not None and flipped.text_pt > fit.text_pt:
                metrics = fig.chain_variant.metrics
                text_pt, in_band = flipped.text_pt, flipped.in_band
    return FigureMeasure(
        nodes=nodes,
        edges=edges,
        rendered=True,
        crossings=metrics.crossings if metrics is not None else None,
        defects=metrics.defects if metrics is not None else (),
        text_pt=text_pt,
        in_band=in_band,
    )


def _defect_codes(defects: tuple[str, ...]) -> Counter[str]:
    """Difetti contati per codice (`codice: dettaglio`): il dettaglio cita le
    etichette, che una riscrittura legittima può cambiare."""
    return Counter(d.split(":", 1)[0].strip() for d in defects)


def review_acceptance(
    fmt: str, original: FigureMeasure | None, candidate: FigureMeasure | None
) -> tuple[bool, str]:
    """Regola di accettazione della misura per una riscrittura già valida.

    - Vega-Lite e `function` non hanno misura geometrica: decide la sola
      validazione (`(True, "")`).
    - Mermaid e DOT: la riscrittura deve essere resa (`measure_unavailable`,
      per esempio Chromium assente) e misurata (`measure_skipped`: tetto di
      lavoro o geometria fuori scala). Una riscrittura saltata è respinta
      anche quando è saltato l'originale: mai un'accettazione senza misura.
    - Originale misurato: incroci non superiori (`crossings: n > m`) e,
      per ogni codice di difetto, non più occorrenze dell'originale
      (`new_defects: …`).
    - Originale non reso o non misurato: la misura della riscrittura è la
      prova, e deve essere senza difetti.
    """
    if fmt not in GRAPH_FORMATS:
        return True, ""
    if candidate is None or not candidate.rendered:
        return False, "measure_unavailable"
    after = candidate.crossings
    if after is None:
        return False, "measure_skipped"
    new_codes = _defect_codes(candidate.defects)
    before = original.crossings if original is not None and original.rendered else None
    if original is not None and before is not None:
        if after > before:
            return False, f"crossings: {after} > {before}"
        new_codes -= _defect_codes(original.defects)
    if new_codes:
        return False, "new_defects: " + ", ".join(sorted(new_codes))
    return True, ""


def _ids_shown(ids: frozenset[str]) -> str:
    shown = sorted(ids)
    tail = ", …" if len(shown) > _REVIEW_IDS_SHOWN else ""
    return ", ".join(shown[:_REVIEW_IDS_SHOWN]) + tail


def _vegalite_data(source: str) -> VegaLiteDataMetrics | None:
    """Misura dei dati di una spec Vega-Lite, `None` se il sorgente non è un
    oggetto JSON (`RecursionError`: il decoder C cade oltre ~1.000 livelli,
    come in `figure_render_service._parse_vegalite`)."""
    try:
        spec = json.loads(source)
    except (ValueError, RecursionError):
        return None
    if not isinstance(spec, dict):
        return None
    return vegalite_data_metrics(spec)


def _vegalite_guard(before_src: str, after_src: str) -> tuple[str, str]:
    """Conservazione dei dati di una spec Vega-Lite: la riscrittura non
    toglie righe inline (`data.values`, `datasets`), non toglie blocchi
    `data.sequence` e cita ancora tutti i campi dell'originale. Correggere
    un valore sbagliato resta ammesso (il confronto è sui conteggi e sui
    campi, non sul contenuto delle righe); cancellare i dati o sostituirli
    con altri no. Un sorgente che non è JSON non ha misura: decide la
    validazione."""
    before = _vegalite_data(before_src)
    after = _vegalite_data(after_src)
    if before is None or after is None:
        return "", ""
    if after.rows < before.rows:
        return OUTCOME_REJECTED, f"rows_removed: righe {before.rows} → {after.rows}"
    if after.sequences < before.sequences:
        return (
            OUTCOME_REJECTED,
            f"sequence_removed: sequenze {before.sequences} → {after.sequences}",
        )
    missing = before.fields - after.fields
    if missing:
        return OUTCOME_REJECTED, f"fields_removed: campi {_ids_shown(missing)}"
    return "", ""


@dataclass(frozen=True)
class _FunctionData:
    """Contenuto di una spec `function`: espressioni (senza spazi) e dominio."""

    expressions: frozenset[str]
    domain: tuple[float, float]


def _function_data(source: str) -> _FunctionData | None:
    """Misura di una spec `function`, `None` se non passa il parse."""
    spec, _issues = parse_function_spec(source)
    if spec is None:
        return None
    exprs = frozenset("".join(e.expr.split()) for e in spec.expressions)
    return _FunctionData(expressions=exprs, domain=spec.domain)


def _function_guard(before_src: str, after_src: str) -> tuple[str, str]:
    """Conservazione del contenuto di una spec `function`: le espressioni
    dell'originale restano tutte e il dominio non si restringe. La
    matematica è il dato della figura: la riscrittura cambia etichette,
    `show` e disegno, non la funzione tracciata."""
    before = _function_data(before_src)
    after = _function_data(after_src)
    if before is None or after is None:
        return "", ""
    missing = before.expressions - after.expressions
    if missing:
        return OUTCOME_REJECTED, f"expressions_changed: espressioni {_ids_shown(missing)}"
    (lo, hi), (lo2, hi2) = before.domain, after.domain
    if lo2 > lo or hi2 < hi:
        return (
            OUTCOME_REJECTED,
            f"domain_reduced: dominio [{lo:g}, {hi:g}] → [{lo2:g}, {hi2:g}]",
        )
    return "", ""


# Formati che il revisore figura ↔ testo (PROMPT 17) sa leggere.
REVIEWED_FORMATS: frozenset[str] = frozenset(get_args(openai_figure_review_service.ReviewFormat))

# Conservazione dei dati per i formati senza misura geometrica: tabella per
# formato e non confronto letterale (D2, doc 17 § 9).
_DATA_GUARDS: dict[str, Callable[[str, str], tuple[str, str]]] = {
    "vegalite": _vegalite_guard,
    "function": _function_guard,
}


def _review_guard(item: _ReviewItem, candidate: str) -> tuple[str, str]:
    """Controlli deterministici prima della validazione: `(esito, motivo)`
    con esito `unchanged`, `rejected` o `""` (la riscrittura prosegue).

    Per i grafi (`graph_source_metrics`) la riscrittura ha lo stesso tipo,
    non più nodi né archi (`density_increased`), tutti i nodi
    dell'originale con gli stessi id e, per i tipi senza id, lo stesso
    numero di nodi (`nodes_removed`), e nessun nodo che nell'originale era
    estremo di un arco resta senza archi (`nodes_isolated`). Togliere archi
    resta ammesso: è il modo dichiarato di ridurre gli incroci, e i due
    controlli sui nodi impediscono che la riduzione cancelli contenuto.
    Vega-Lite e `function` non hanno né archi né misura geometrica: al loro
    posto vale la conservazione dei dati di `_DATA_GUARDS` (righe, campi,
    espressioni, dominio), così nemmeno per loro una riscrittura può
    cancellare o sostituire il contenuto della figura."""
    if not candidate:
        return OUTCOME_REJECTED, "missing_source"
    renderer = REGISTRY.get(item.fmt)
    if renderer is None:
        return OUTCOME_REJECTED, f"{item.fmt}_unavailable"
    before_src = renderer.sanitize(item.original)
    after_src = renderer.sanitize(candidate)
    if after_src == before_src:
        return OUTCOME_UNCHANGED, OUTCOME_UNCHANGED
    if _looks_corrupted(item.fmt, candidate):
        return OUTCOME_REJECTED, "placeholder"
    before = graph_source_metrics(item.fmt, before_src)
    after = graph_source_metrics(item.fmt, after_src)
    if before is not None and after is not None:
        if before.kind != after.kind:
            return OUTCOME_REJECTED, f"type_changed: {before.kind} → {after.kind}"
        if after.nodes > before.nodes or after.edges > before.edges:
            return (
                OUTCOME_REJECTED,
                f"density_increased: nodi {before.nodes} → {after.nodes}, "
                f"archi {before.edges} → {after.edges}",
            )
        missing = before.node_ids - after.node_ids
        if missing or after.nodes < before.nodes:
            detail = f" (mancanti: {_ids_shown(missing)})" if missing else ""
            return (
                OUTCOME_REJECTED,
                f"nodes_removed: nodi {before.nodes} → {after.nodes}{detail}",
            )
        isolated = before.linked_ids - after.linked_ids
        if isolated:
            return OUTCOME_REJECTED, f"nodes_isolated: senza archi {_ids_shown(isolated)}"
    guard = _DATA_GUARDS.get(item.fmt)
    if guard is not None:
        return guard(before_src, after_src)
    return "", ""


def _discard(_value: str) -> None:
    """Commit nullo degli slot di prova: la riscrittura si applica solo
    alla fine della revisione."""


def _log_verdict(
    item: _ReviewItem,
    *,
    attempt: int,
    review: openai_figure_review_service.FigureReviewOut,
    cost_usd: Any,
    outcome: str,
    rejection: str = "",
) -> None:
    log.info(
        "figure_review_verdict",
        asset_id=item.key,
        format=item.fmt,
        attempt=attempt,
        verdict=review.verdict,
        accepted=outcome == OUTCOME_ACCEPTED,
        outcome=outcome,
        reason=review.reason[:_LOG_CAP],
        rejection=rejection[:_LOG_CAP] or None,
        cost_usd=cost_usd,
    )


def _reject(
    item: _ReviewItem,
    *,
    attempt: int,
    review: openai_figure_review_service.FigureReviewOut,
    cost_usd: Any,
    reason: str,
    crossings_before: int | None,
    crossings_after: int | None,
) -> None:
    """Riscrittura respinta: l'originale resta, il motivo torna al modello."""
    _log_verdict(
        item,
        attempt=attempt,
        review=review,
        cost_usd=cost_usd,
        outcome=OUTCOME_REJECTED,
        rejection=reason,
    )
    log.warning(
        "figure_review_rejected",
        asset_id=item.key,
        format=item.fmt,
        attempt=attempt,
        reason=reason[:_LOG_CAP],
        crossings_before=crossings_before,
        crossings_after=crossings_after,
    )
    item.feedback = reason


async def _ask_review(
    item: _ReviewItem,
    *,
    attempt: int,
    language_code: str,
    usage_sink: list[dict[str, Any]],
) -> tuple[openai_figure_review_service.FigureReviewOut, dict[str, Any]] | None:
    """Una chiamata del revisore (sotto `_review_semaphore`); ogni errore è
    un tentativo perso.

    La cattura è ampia di proposito: le chiamate di un giro corrono in
    `asyncio.gather`, e un'eccezione propagata da una sola chiamata
    chiuderebbe il giro lasciando in volo le altre (già pagate, con l'usage
    perso) e scarterebbe le loro riscritture. Un tentativo perso su una
    risposta comunque pagata (200 con JSON troncato o schema fuori
    contratto) lascia la sua voce di usage: il costo è contabilizzato anche
    senza verdetto (D16)."""
    try:
        async with _review_semaphore():
            return await openai_figure_review_service.review_figure(
                fmt=cast(openai_figure_review_service.ReviewFormat, item.fmt),
                source=item.original,
                caption=item.asset.caption or "",
                alt_text=item.asset.alt_text or "",
                context=item.context,
                measure=item.measure,
                language_code=language_code,
                feedback=item.feedback,
            )
    except OpenAIError as exc:
        cost: Any = None
        if isinstance(exc.usage, dict):
            usage_sink.append(_usage_entry("review", item.key, exc.usage))
            cost = exc.usage.get("cost_usd")
        log.warning(
            "figure_review_call_failed",
            asset_id=item.key,
            attempt=attempt,
            error=str(exc)[:_LOG_CAP],
            cost_usd=cost,
        )
        return None
    except Exception as exc:
        log.warning(
            "figure_review_call_failed",
            asset_id=item.key,
            attempt=attempt,
            error=f"{type(exc).__name__}: {exc}"[:_LOG_CAP],
            cost_usd=None,
        )
        return None


async def _judge_candidates(
    candidates: list[tuple[_ReviewItem, str]], *, language_code: str
) -> list[_Judgement]:
    """Validazione e misura delle riscritture di un giro.

    Una sola `_validate_slots` per tutte (un Chromium per il parse dei
    Mermaid, `validate(deep=True)` per gli altri formati, come nel fix),
    poi una sola `render_figure_map` con originale e riscrittura di ogni
    grafo valido: l'originale è di norma un hit della cache (resa per il
    prompt), la riscrittura DOT è già in cache dalla validazione profonda,
    quella Mermaid è resa e misurata nella pagina del pre-render."""
    slots = [
        _Slot(
            id=f"{item.key}{_REVIEW_SUFFIX}",
            kind=item.fmt,
            current=candidate,
            context="",
            commit=_discard,
        )
        for item, candidate in candidates
    ]
    checks = {c.id: c for c in await _validate_slots(slots)}
    results: dict[int, _Judgement] = {}
    graphs: list[int] = []
    for i, ((item, _candidate), slot) in enumerate(zip(candidates, slots, strict=True)):
        check = checks[slot.id]
        if not check.ok:
            reason = f"invalid: {check.error_message}"
            results[i] = _Judgement(False, reason, item.measure.crossings, None)
        elif item.fmt in GRAPH_FORMATS:
            graphs.append(i)
        else:
            ok, reason = review_acceptance(item.fmt, item.measure, None)
            results[i] = _Judgement(ok, reason, None, None)
    if graphs:
        entries: list[dict[str, str]] = []
        for i in graphs:
            item, candidate = candidates[i]
            entries.append({"format": item.fmt, "asset_id": item.key, "content": item.original})
            entries.append(
                {"format": item.fmt, "asset_id": item.key + _REVIEW_SUFFIX, "content": candidate}
            )
        started = time.monotonic()
        figures = await render_figure_map(entries, language=language_code, cache_failures=False)
        figures = await render_chain_variants(
            entries,
            figures,
            box_mm=_REVIEW_CHAIN_BOX_MM,
            variant="lesson",
            language=language_code,
        )
        log.info(
            "figure_review_measured",
            stage="candidates",
            figures=len(entries),
            rendered=len(figures),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        for i in graphs:
            item, candidate = candidates[i]
            original_fig = figures.get(item.key)
            candidate_fig = figures.get(item.key + _REVIEW_SUFFIX)
            before = _figure_measure(item.fmt, item.original, original_fig)
            after = _figure_measure(item.fmt, candidate, candidate_fig)
            ok, reason = review_acceptance(
                item.fmt,
                before if original_fig is not None else None,
                after if candidate_fig is not None else None,
            )
            results[i] = _Judgement(ok, reason, before.crossings, after.crossings)
    return [results[i] for i in range(len(candidates))]


async def _review_round(
    pending: list[_ReviewItem],
    *,
    attempt: int,
    language_code: str,
    usage_sink: list[dict[str, Any]],
    accepted: dict[str, str],
) -> list[_ReviewItem]:
    """Un giro di revisione (chiamate concorrenti, una per figura in
    attesa); ritorna le figure da riproporre al giro successivo."""
    calls = await asyncio.gather(
        *(
            _ask_review(item, attempt=attempt, language_code=language_code, usage_sink=usage_sink)
            for item in pending
        )
    )
    retry: list[_ReviewItem] = []
    candidates: list[tuple[_ReviewItem, str]] = []
    reviews: list[tuple[openai_figure_review_service.FigureReviewOut, Any]] = []
    for item, result in zip(pending, calls, strict=True):
        if result is None:
            retry.append(item)
            continue
        review, usage = result
        usage_sink.append(_usage_entry("review", item.key, usage))
        cost = usage.get("cost_usd")
        if review.verdict == openai_figure_review_service.VERDICT_COHERENT:
            _log_verdict(
                item, attempt=attempt, review=review, cost_usd=cost, outcome=OUTCOME_COHERENT
            )
            continue
        candidate = _sanitize(item.fmt, review.source or "")
        outcome, reason = _review_guard(item, candidate)
        if outcome == OUTCOME_UNCHANGED:
            _log_verdict(
                item, attempt=attempt, review=review, cost_usd=cost, outcome=OUTCOME_UNCHANGED
            )
        elif outcome == OUTCOME_REJECTED:
            _reject(
                item,
                attempt=attempt,
                review=review,
                cost_usd=cost,
                reason=reason,
                crossings_before=item.measure.crossings,
                crossings_after=None,
            )
            retry.append(item)
        else:
            candidates.append((item, candidate))
            reviews.append((review, cost))
    if not candidates:
        return retry
    judgements = await _judge_candidates(candidates, language_code=language_code)
    for (item, candidate), (review, cost), verdict in zip(
        candidates, reviews, judgements, strict=True
    ):
        if verdict.ok:
            accepted[item.key] = candidate
            _log_verdict(
                item, attempt=attempt, review=review, cost_usd=cost, outcome=OUTCOME_ACCEPTED
            )
            continue
        _reject(
            item,
            attempt=attempt,
            review=review,
            cost_usd=cost,
            reason=verdict.reason,
            crossings_before=verdict.crossings_before,
            crossings_after=verdict.crossings_after,
        )
        retry.append(item)
    return retry


async def _review_figures(
    output: LessonContentOutput,
    *,
    language_code: str,
    usage_sink: list[dict[str, Any]],
) -> int:
    """Revisione figura ↔ testo delle figure valide di Fase 3 (docstring del
    modulo). Ritorna il numero di riscritture applicate; le scrive in
    `content` solo alla fine, tutte insieme."""
    settings = get_settings()
    if not settings.figure_review_enabled:
        return 0
    max_attempts = max(0, int(settings.figure_review_max_attempts))
    # Solo i formati del revisore (`ReviewFormat`): `tikz` (WP6) ha la sua
    # revisione della resa e non passa da qui.
    assets = [
        a
        for a in output.visual_assets
        if a.format in REVIEWED_FORMATS and (a.content or "").strip()
    ]
    if max_attempts == 0 or not assets:
        return 0
    if not settings.openai_api_key:
        # Niente resa a vuoto: senza chiave nessuna chiamata può partire.
        log.info("figure_review_skipped", reason="openai_not_configured", figures=len(assets))
        return 0

    keys = [f"asset:{a.asset_id}" for a in assets]
    started = time.monotonic()
    originals = [
        {"format": a.format, "asset_id": key, "content": a.content}
        for a, key in zip(assets, keys, strict=True)
    ]
    rendered = await render_figure_map(
        originals,
        language=language_code,
        cache_failures=False,
    )
    rendered = await render_chain_variants(
        originals,
        rendered,
        box_mm=_REVIEW_CHAIN_BOX_MM,
        variant="lesson",
        language=language_code,
    )
    log.info(
        "figure_review_measured",
        stage="original",
        figures=len(assets),
        rendered=len(rendered),
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    items = [
        _ReviewItem(
            asset=a,
            key=key,
            fmt=a.format,
            original=a.content,
            context=_review_context(output, a.asset_id),
            measure=_figure_measure(a.format, a.content, rendered.get(key)),
        )
        for a, key in zip(assets, keys, strict=True)
    ]
    graphs = [it for it in items if it.fmt in GRAPH_FORMATS]
    if graphs and not any(it.measure.rendered for it in graphs):
        # Niente chiamate sterili: se NESSUN grafo del lotto è stato reso
        # la resa non è disponibile (Chromium o `dot` assenti, batch in
        # timeout) e `review_acceptance` respingerà ogni riscrittura con
        # `measure_unavailable`. Un fallimento di resa della singola figura
        # resta in revisione: lì una riscrittura può davvero ripararla.
        log.info(
            "figure_review_skipped",
            reason="render_unavailable",
            figures=len(graphs),
            formats=sorted({it.fmt for it in graphs}),
        )
        items = [it for it in items if it.fmt not in GRAPH_FORMATS]
    if not items:
        return 0
    accepted: dict[str, str] = {}
    pending = items
    for attempt in range(1, max_attempts + 1):
        if not pending:
            break
        pending = await _review_round(
            pending,
            attempt=attempt,
            language_code=language_code,
            usage_sink=usage_sink,
            accepted=accepted,
        )
    for item in items:
        if item.key in accepted:
            item.asset.content = accepted[item.key]
    if accepted:
        log.info("figure_review_applied", accepted=len(accepted), figures=len(items))
    return len(accepted)


def assets_cost_usd(assets: list[dict[str, Any]]) -> float:
    """Somma dei `cost_usd` noti delle chiamate degli asset (un modello
    fuori listino vale `None` e non entra)."""
    return float(
        sum(
            float(a["cost_usd"])
            for a in assets
            if isinstance(a.get("cost_usd"), int | float) and not isinstance(a["cost_usd"], bool)
        )
    )


# ---------------------------------------------------------------------------
# Revisore delle ridondanze delle figure di fonte (PROMPT 19, J-Q8)
# ---------------------------------------------------------------------------

_FIG_TAG_RE = re.compile(r"\[FIG:\s*([^\]\s]+)\s*\]", re.IGNORECASE)
_OTHER_SOURCE_CAP = 240


@dataclass(frozen=True)
class SourceFigureInfo:
    """Dati della riga `course_document_figure` che il revisore riceve."""

    description: str
    original_caption: str


def _citing_section(output: LessonContentOutput, asset_id: str) -> tuple[str, str]:
    """(titolo, testo) della prima parte della lezione che cita la figura."""
    key = asset_id.lower()
    parts = [
        ("Introduzione", output.introduction or ""),
        *((s.title, s.content) for s in output.sections),
        ("Sintesi", output.summary or ""),
    ]
    for title, text in parts:
        if any(m.group(1).lower() == key for m in _FIG_TAG_RE.finditer(text)):
            return title, text
    return "", ""


def _other_summary(asset: Any, infos: Mapping[str, SourceFigureInfo]) -> str:
    info = infos.get(asset.asset_id)
    if info is not None:
        return info.description
    return " ".join((asset.content or "").split())[:_OTHER_SOURCE_CAP]


async def review_source_figure_redundancy(
    output: LessonContentOutput,
    infos: Mapping[str, SourceFigureInfo],
    *,
    language_code: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Verdetti di coerenza e ridondanza delle figure di fonte della lezione.

    Una chiamata per figura (sotto `_review_semaphore`, tetto complessivo
    `figure_redundancy_timeout_seconds`); ogni errore vale «nessun avviso»
    per quella figura. Ritorna il dict da salvare in
    `course_lesson.content_figure_review` (None senza verdetti) e le voci di
    usage `phase="redundancy"`, comprese quelle delle risposte pagate e
    inutilizzabili. Non tocca mai `output`."""
    settings = get_settings()
    sources = [a for a in output.visual_assets if a.format == SOURCE_FIGURE_FORMAT]
    usage: list[dict[str, Any]] = []
    if not settings.figure_redundancy_enabled or not sources:
        return None, usage

    async def one(asset: Any) -> tuple[str, Any] | None:
        info = infos.get(asset.asset_id, SourceFigureInfo(description="", original_caption=""))
        title, text = _citing_section(output, asset.asset_id)
        others = tuple(
            openai_figure_redundancy_service.OtherFigure(
                asset_id=o.asset_id,
                format=o.format,
                caption=o.caption or "",
                summary=_other_summary(o, infos),
            )
            for o in output.visual_assets
            if o.asset_id != asset.asset_id
        )
        item = openai_figure_redundancy_service.RedundancyInput(
            asset_id=asset.asset_id,
            description=info.description,
            original_caption=info.original_caption,
            lesson_caption=asset.caption or "",
            section_title=title,
            section_text=text,
            others=others,
            language_code=language_code,
        )
        try:
            async with _review_semaphore():
                verdict, call_usage = await openai_figure_redundancy_service.review_redundancy(item)
        except OpenAIError as exc:
            if isinstance(exc.usage, dict):
                usage.append(_usage_entry("redundancy", asset.asset_id, exc.usage))
            log.warning(
                "figure_redundancy_call_failed",
                asset_id=asset.asset_id,
                error=str(exc)[:_LOG_CAP],
            )
            return None
        except Exception as exc:
            log.warning(
                "figure_redundancy_call_failed",
                asset_id=asset.asset_id,
                error=f"{type(exc).__name__}: {exc}"[:_LOG_CAP],
            )
            return None
        usage.append(_usage_entry("redundancy", asset.asset_id, call_usage))
        return asset.asset_id, verdict

    # Tetto di lotto: i verdetti già arrivati restano (il loro costo è già
    # nell'usage); solo le chiamate ancora in corso vengono annullate.
    tasks = [asyncio.ensure_future(one(a)) for a in sources]
    done, pending = await asyncio.wait(
        tasks, timeout=float(settings.figure_redundancy_timeout_seconds)
    )
    if pending:
        log.warning("figure_redundancy_timeout", figures=len(sources), pending=len(pending))
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    results = [task.result() for task in tasks if task in done and not task.cancelled()]
    figures: dict[str, Any] = {}
    for result in results:
        if result is None:
            continue
        asset_id, verdict = result
        flagged = [p.model_dump() for p in verdict.pairs if p.verdict != "distinta"]
        figures[asset_id] = {
            "coherence": verdict.coherence,
            "reason": verdict.reason,
            "pairs": flagged,
        }
        log.info(
            "lesson_content_figure_redundancy",
            asset_id=asset_id,
            coherence=verdict.coherence,
            pairs={p["other"]: p["verdict"] for p in flagged},
        )
    if not figures:
        return None, usage
    return (
        {
            "version": 1,
            "model": settings.openai_figure_redundancy_model,
            "reviewed_at": datetime.now(UTC).isoformat(),
            "figures": figures,
        },
        usage,
    )


def merge_assets_usage(usage: dict[str, Any], assets: list[dict[str, Any]]) -> dict[str, Any]:
    """Usage della chiamata di Fase 3 con le chiamate degli asset accanto:
    `assets` (le voci di `validate_and_fix_content_assets`) e
    `assets_cost_usd` (somma dei `cost_usd` noti; un modello fuori listino
    vale `None` e non entra). `cost_usd` resta quello della chiamata
    principale. Non muta `usage`."""
    return {
        **usage,
        "assets": [dict(a) for a in assets],
        "assets_cost_usd": assets_cost_usd(assets),
    }


# ---------------------------------------------------------------------------
# API pubblica
# ---------------------------------------------------------------------------


async def _review_tikz_renders(
    output: LessonContentOutput,
    *,
    language_code: str,
    usage_sink: list[dict[str, Any]],
    fix_spent: set[str],
) -> None:
    """Revisione Vision della resa delle figure `tikz` (PROMPT 21).

    Una chiamata per figura, sotto `_review_semaphore`. Con verdetto
    `difetti` e l'unico fix ancora da spendere, i difetti diventano il
    messaggio d'errore del fix (PROMPT 12, kind `tikz`): la riscrittura vale
    solo se `validate(deep=True)` la accetta senza difetti geometrici,
    altrimenti resta l'originale. Nessuna nuova chiamata Vision dopo il
    fix. Ogni errore o timeout vale «nessun effetto»; l'usage va in
    `usage_sink` (`phase="render_review"` e `"fix"`)."""
    settings = get_settings()
    assets = [a for a in output.visual_assets if a.format == "tikz" and (a.content or "").strip()]
    renderer = REGISTRY.get("tikz")
    if (
        not settings.figure_tikz_render_review_enabled
        or not assets
        or not isinstance(renderer, TikzRenderer)
        or "tikz" not in available_formats()
    ):
        return
    timeout = tikz_timeout_seconds()
    fix_cap = _fix_cap("tikz", max(0, int(settings.asset_fix_max_attempts)))

    async def one(asset: Any) -> tuple[Any, str] | None:
        key = f"asset:{asset.asset_id}"
        try:
            png = await asyncio.wait_for(
                asyncio.to_thread(renderer.render_png, asset.content), timeout=timeout
            )
        except TimeoutError:
            png = None
        if png is None:
            log.info("tikz_render_review_skipped", asset_id=asset.asset_id, reason="no_png")
            return None
        context = _review_context(output, asset.asset_id)
        labels = list(renderer.extract_translatable(asset.content).values())
        try:
            async with _review_semaphore():
                verdict, call_usage = await openai_tikz_render_review_service.review_render(
                    png,
                    caption=asset.caption or "",
                    citing_text=context.text,
                    labels=labels,
                    language_code=language_code,
                )
        except OpenAIError as exc:
            if isinstance(exc.usage, dict):
                usage_sink.append(_usage_entry("render_review", key, exc.usage))
            log.warning(
                "tikz_render_review_call_failed", asset_id=asset.asset_id, error=str(exc)[:_LOG_CAP]
            )
            return None
        usage_sink.append(_usage_entry("render_review", key, call_usage))
        log.info(
            "tikz_render_review",
            asset_id=asset.asset_id,
            verdict=verdict.verdict,
            defects=[d.kind for d in verdict.defects],
        )
        if verdict.verdict == "ok" or asset.asset_id in fix_spent or fix_cap <= 0:
            return None
        feedback = "revisione della resa: " + "; ".join(
            f"{d.kind}: {d.detail}" for d in verdict.defects
        )
        try:
            out, fix_usage = await openai_asset_fix_service.fix_asset(
                kind="tikz",
                source=asset.content,
                error_message=feedback,
                context=asset.caption or asset.alt_text or "",
                language_code=language_code,
            )
        except openai_asset_fix_service.OpenAIAssetFixError as exc:
            if isinstance(exc.usage, dict):
                usage_sink.append(_usage_entry("fix", key, exc.usage))
            log.warning("tikz_render_review_fix_failed", asset_id=asset.asset_id, error=str(exc))
            return None
        usage_sink.append(_usage_entry("fix", key, fix_usage))
        candidate = _sanitize("tikz", out.fixed_content)
        if not candidate or _looks_corrupted("tikz", candidate):
            return None
        try:
            ok, err = await asyncio.wait_for(
                asyncio.to_thread(renderer.validate, candidate, deep=True), timeout=timeout
            )
        except TimeoutError:
            ok, err = False, f"tikz: validazione oltre {timeout:g} s"
        if not ok:
            log.info("tikz_render_review_fix_rejected", asset_id=asset.asset_id, error=err[:200])
            return None
        return asset, candidate

    results = await asyncio.gather(*(one(a) for a in assets), return_exceptions=True)
    # Le riscritture si applicano solo a fine giro: un guasto lascia tutto
    # com'era.
    for result in results:
        if isinstance(result, BaseException):
            log.warning("tikz_render_review_failed", error=f"{type(result).__name__}"[:_LOG_CAP])
        elif result is not None:
            asset, candidate = result
            asset.content = candidate
            log.info("tikz_render_review_fix_accepted", asset_id=asset.asset_id)


async def validate_and_fix_content_assets(
    output: LessonContentOutput, *, language_code: str
) -> tuple[LessonContentOutput, list[dict[str, Any]]]:
    """Valida e auto-corregge gli asset fragili dell'output di Fase 3,
    revisiona le figure contro il testo, poi localizza (rete di sicurezza
    i18n) i campi testuali rimasti in lingua sbagliata. Muta `output` e lo
    ritorna con l'usage delle chiamate AI degli asset (`phase` fra `fix`,
    `review`, `localize`). Solleva `AssetFixUnresolvedError` (recuperabile)
    se un asset resta invalido; la revisione non solleva mai."""
    usage: list[dict[str, Any]] = []
    slots, inline_fields = _collect_content_slots(output)
    tikz_before = {a.asset_id: a.content for a in output.visual_assets if a.format == "tikz"}
    if slots:
        fixed = await _validate_and_fix(
            slots, inline_fields, language_code=language_code, usage_sink=usage
        )
        log.info(
            "content_assets_validated",
            total=len(slots),
            fixed=fixed,
            kinds=dict(Counter(s.kind for s in slots)),
        )
    # Revisione Vision della resa `tikz` (PROMPT 21): consultiva, come quella
    # figura ↔ testo non solleva mai. Un `tikz` già riscritto dal fix ha
    # speso il suo unico fix.
    if tikz_before:
        spent = {
            a.asset_id
            for a in output.visual_assets
            if a.asset_id in tikz_before and a.content != tikz_before[a.asset_id]
        }
        try:
            await _review_tikz_renders(
                output, language_code=language_code, usage_sink=usage, fix_spent=spent
            )
        except Exception as exc:
            log.warning("tikz_render_review_failed", error=f"{type(exc).__name__}: {exc}"[:500])
    # La revisione non deve mai costare una rigenerazione: il worker tratta
    # ogni eccezione come recuperabile. Le riscritture si applicano solo a
    # fine revisione, quindi un guasto lascia gli originali intatti.
    try:
        await _review_figures(output, language_code=language_code, usage_sink=usage)
    except Exception as exc:
        log.warning("figure_review_failed", error=f"{type(exc).__name__}: {exc}"[:500])
    structural_changed = await _localize_fields(
        _collect_content_loc_fields(output), language_code=language_code, usage_sink=usage
    )
    if structural_changed:
        # La traduzione può aver toccato label Mermaid / celle tabella: ri-valida
        # la sintassi. Non-fatale: un asset strutturale che resta invalido
        # degrada (diagramma assente), non fa fallire la lezione.
        slots2, inline2 = _collect_content_slots(output)
        if slots2:
            try:
                await _validate_and_fix(
                    slots2, inline2, language_code=language_code, usage_sink=usage
                )
            except AssetFixUnresolvedError as exc:
                log.warning("asset_localize_revalidate_failed", error=str(exc))
    return output, usage


async def validate_and_fix_slides_assets(
    output: LessonSlidesOutput, *, language_code: str
) -> LessonSlidesOutput:
    """Valida e auto-corregge gli asset fragili dell'output di Fase 4
    (new_assets Mermaid + math inline nelle slide), poi localizza (rete di
    sicurezza i18n) i campi testuali dei nuovi asset rimasti in lingua
    sbagliata. Muta e ritorna `output`. L'usage delle chiamate AI è solo
    loggato (`slides_assets_usage`): `slides_tokens` non lo raccoglie."""
    usage: list[dict[str, Any]] = []
    slots, inline_fields = _collect_slides_slots(output)
    if slots:
        fixed = await _validate_and_fix(
            slots, inline_fields, language_code=language_code, usage_sink=usage
        )
        log.info(
            "slides_assets_validated",
            total=len(slots),
            fixed=fixed,
            kinds=dict(Counter(s.kind for s in slots)),
        )
    structural_changed = await _localize_fields(
        _collect_slides_loc_fields(output), language_code=language_code, usage_sink=usage
    )
    if structural_changed:
        slots2, inline2 = _collect_slides_slots(output)
        if slots2:
            try:
                await _validate_and_fix(
                    slots2, inline2, language_code=language_code, usage_sink=usage
                )
            except AssetFixUnresolvedError as exc:
                log.warning("asset_localize_revalidate_failed", error=str(exc))
    if usage:
        log.info(
            "slides_assets_usage",
            calls=len(usage),
            phases=dict(Counter(u["phase"] for u in usage)),
            cost_usd=merge_assets_usage({}, usage)["assets_cost_usd"],
        )
    return output


__all__ = [
    "AssetCheck",
    "AssetFixUnresolvedError",
    "merge_assets_usage",
    "review_acceptance",
    "validate_and_fix_content_assets",
    "validate_and_fix_slides_assets",
    "validate_assets_for_test",
    "validate_latex_mathml",
]


async def validate_assets_for_test(
    items: list[tuple[str, str, str]],
) -> list[AssetCheck]:
    """Helper per i test: valida una lista `(id, kind, source)` senza fix."""
    slots = [
        _Slot(id=i, kind=k, current=s, context="", commit=lambda v: None) for (i, k, s) in items
    ]
    return await _validate_slots(slots)
