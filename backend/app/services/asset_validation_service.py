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
import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

from latex2mathml.converter import convert as _latex_to_mathml

from app.core.config import get_settings
from app.core.i18n_scripts import has_target_script_chars, primary_script
from app.core.logging import get_logger
from app.schemas.course_lesson_content import LessonContentOutput
from app.schemas.course_lesson_slides import LessonSlidesOutput
from app.services import openai_asset_fix_service, openai_asset_localize_service
from app.services.figure_render_service import (
    REGISTRY,
    RENDERABLE_FORMATS,
    available_formats,
    figure_asset_context,
)
from app.services.figure_theme import mermaid_initialize_js
from app.services.mermaid_prerender import block_external_requests

log = get_logger("app.asset_validation")


class AssetFixUnresolvedError(Exception):
    """Un asset fragile e' rimasto invalido dopo `asset_fix_max_attempts`.

    Recuperabile: il worker la mappa su auto-retry (rigenera la lezione)."""


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
    - `vegalite` | `dot` | `function`: `validate(deep=True)` del renderer in
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
        else:  # vegalite | dot | function
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
            try:
                with figure_asset_context(slot.id.split(":", 1)[-1]):
                    ok, err = await asyncio.wait_for(
                        asyncio.to_thread(renderer.validate, slot.current, deep=True),
                        timeout=render_timeout,
                    )
            except TimeoutError:
                ok, err = False, f"{slot.kind}: validazione oltre {render_timeout:g} s"
            checks.append(AssetCheck(slot.id, slot.kind, ok, "" if ok else err))
    return checks


def _unresolved_details(invalid: list[AssetCheck]) -> str:
    return "; ".join(f"{c.id} [{c.kind}]: {c.error_message}" for c in invalid[:5])


def _raise_if_unfixable(invalid: list[AssetCheck]) -> None:
    """Solleva subito se un check invalido non e' riparabile dal fix AI."""
    blocked = [c for c in invalid if not c.fixable]
    if blocked:
        raise AssetFixUnresolvedError(_unresolved_details(blocked))


async def _validate_and_fix(
    slots: list[_Slot],
    inline_fields: list[_InlineField],
    *,
    language_code: str,
) -> int:
    """Valida gli slot e ripara SOLO quelli invalidi. Gli asset gia' validi
    NON vengono toccati: nessun clean, nessun commit, restano byte-identici.
    Ritorna il numero di asset effettivamente modificati. Solleva
    `AssetFixUnresolvedError` (recuperabile) se uno resta invalido dopo i
    tentativi."""
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
    _raise_if_unfixable(invalid)

    # Fix AI iterativo, SOLO sugli asset ancora invalidi.
    remaining = max_attempts
    while invalid:
        if remaining <= 0:
            raise AssetFixUnresolvedError(_unresolved_details(invalid))
        remaining -= 1
        for c in invalid:
            slot = by_id[c.id]
            try:
                out, _usage = await openai_asset_fix_service.fix_asset(
                    kind=cast(openai_asset_fix_service.AssetKind, slot.kind),
                    source=slot.current,
                    error_message=c.error_message,
                    context=slot.context,
                    language_code=language_code,
                )
            except openai_asset_fix_service.OpenAIAssetFixError as exc:
                # Fix transitoriamente fallito: lascia il sorgente invariato,
                # ri-fallira' e (se non si risolve) escalera' a re-gen lezione.
                log.warning("asset_fix_call_failed", asset_id=slot.id, error=str(exc))
                continue
            candidate = _sanitize(slot.kind, out.fixed_content)
            if not candidate or _looks_corrupted(slot.kind, candidate):
                continue
            slot.current = candidate
        checks = await _validate_slots(slots)
        invalid = [c for c in checks if not c.ok]
        _raise_if_unfixable(invalid)

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


async def _localize_fields(fields: list[_LocField], *, language_code: str) -> bool:
    """Localizza i campi rimasti in lingua sbagliata. Best-effort: ogni errore
    diventa un warning e non blocca la generazione. Ritorna True se è cambiato
    un asset STRUTTURALE (ogni kind diverso da `text`: tabella o figura) → il
    chiamante ri-valida la sintassi offline.
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
        localized, _usage = await openai_asset_localize_service.localize_texts(
            items=items, language_code=language_code
        )
    except Exception as exc:
        log.warning("asset_localize_call_failed", error=str(exc), fields=len(items))
        return False

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
# API pubblica
# ---------------------------------------------------------------------------


async def validate_and_fix_content_assets(
    output: LessonContentOutput, *, language_code: str
) -> LessonContentOutput:
    """Valida e auto-corregge gli asset fragili dell'output di Fase 3, poi
    localizza (rete di sicurezza i18n) i campi testuali rimasti in lingua
    sbagliata. Muta e ritorna lo stesso `output`. Solleva
    `AssetFixUnresolvedError` (recuperabile) se un asset resta invalido."""
    slots, inline_fields = _collect_content_slots(output)
    if slots:
        fixed = await _validate_and_fix(slots, inline_fields, language_code=language_code)
        log.info(
            "content_assets_validated",
            total=len(slots),
            fixed=fixed,
            kinds=dict(Counter(s.kind for s in slots)),
        )
    structural_changed = await _localize_fields(
        _collect_content_loc_fields(output), language_code=language_code
    )
    if structural_changed:
        # La traduzione può aver toccato label Mermaid / celle tabella: ri-valida
        # la sintassi. Non-fatale: un asset strutturale che resta invalido
        # degrada (diagramma assente), non fa fallire la lezione.
        slots2, inline2 = _collect_content_slots(output)
        if slots2:
            try:
                await _validate_and_fix(slots2, inline2, language_code=language_code)
            except AssetFixUnresolvedError as exc:
                log.warning("asset_localize_revalidate_failed", error=str(exc))
    return output


async def validate_and_fix_slides_assets(
    output: LessonSlidesOutput, *, language_code: str
) -> LessonSlidesOutput:
    """Valida e auto-corregge gli asset fragili dell'output di Fase 4
    (new_assets Mermaid + math inline nelle slide), poi localizza (rete di
    sicurezza i18n) i campi testuali dei nuovi asset rimasti in lingua
    sbagliata. Muta e ritorna `output`."""
    slots, inline_fields = _collect_slides_slots(output)
    if slots:
        fixed = await _validate_and_fix(slots, inline_fields, language_code=language_code)
        log.info(
            "slides_assets_validated",
            total=len(slots),
            fixed=fixed,
            kinds=dict(Counter(s.kind for s in slots)),
        )
    structural_changed = await _localize_fields(
        _collect_slides_loc_fields(output), language_code=language_code
    )
    if structural_changed:
        slots2, inline2 = _collect_slides_slots(output)
        if slots2:
            try:
                await _validate_and_fix(slots2, inline2, language_code=language_code)
            except AssetFixUnresolvedError as exc:
                log.warning("asset_localize_revalidate_failed", error=str(exc))
    return output


__all__ = [
    "AssetCheck",
    "AssetFixUnresolvedError",
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
