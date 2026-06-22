"""Validazione + auto-fix AI degli asset "fragili" a generazione (Fase 3/4).

Un asset e' "fragile" se puo' essere sintatticamente invalido e finire rotto
nell'output (PDF / frame video / preview FE):
- **Formule LaTeX**: `equations[].latex` e math inline `$...$` / `$$...$$`
  nei campi testo. Validate con `latex2mathml` (motore dell'export PDF/video)
  E con KaTeX (motore del preview FE) → una formula e' valida solo se passa
  ENTRAMBI.
- **Diagrammi Mermaid**: `visual_assets[].format=="mermaid"` (Fase 3) e
  `new_assets[].format=="mermaid"` (Fase 4). Validati con **mermaid v10.9.4**
  (la stessa versione del pre-render PDF/video; il FE usa v11 → un diagramma
  "verde" nell'editor puo' rompersi nell'output).

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
per non bloccare la generazione quando la rete e' giu'.
"""
from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from latex2mathml.converter import convert as _latex_to_mathml

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.course_lesson_content import LessonContentOutput
from app.schemas.course_lesson_slides import LessonSlidesOutput
from app.services import openai_asset_fix_service

log = get_logger("app.asset_validation")


class AssetFixUnresolvedError(Exception):
    """Un asset fragile e' rimasto invalido dopo `asset_fix_max_attempts`.

    Recuperabile: il worker la mappa su auto-retry (rigenera la lezione)."""


@dataclass(frozen=True)
class AssetCheck:
    id: str
    kind: str  # "latex" | "mermaid"
    ok: bool
    error_message: str


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
# Validazione JS (Playwright): Mermaid v10.9.4 + KaTeX
# ---------------------------------------------------------------------------

# Pagina headless di SOLA validazione (parse-only, niente render-to-SVG):
# separata dalla `_MERMAID_RENDERER_HTML` dell'export per non interferire.
# Mermaid pinnato a 10.9.4 (la versione dell'output PDF/video). KaTeX con gli
# stessi flag del preview FE (`throwOnError:true`, `strict:"ignore"`).
_VALIDATOR_HTML = """<!doctype html>
<html><head><meta charset="utf-8"></head><body>
<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10.9.4/dist/mermaid.esm.min.mjs';
import katex from 'https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.mjs';
mermaid.initialize({
  startOnLoad: false, theme: 'default', securityLevel: 'loose',
  flowchart: { htmlLabels: false }, class: { htmlLabels: false },
  state: { htmlLabels: false },
});
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
    except Exception as exc:  # noqa: BLE001 — Playwright non installato
        log.warning("asset_validator_playwright_import_failed", error=str(exc))
        return None

    results: list[tuple[bool, str]] = []
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=["--no-sandbox"])
            try:
                page = await browser.new_page()
                await page.set_content(
                    _VALIDATOR_HTML, wait_until="domcontentloaded"
                )
                try:
                    await page.wait_for_function(
                        "window.__validatorReady === true", timeout=15_000
                    )
                except Exception as exc:  # noqa: BLE001 — CDN non raggiungibile
                    log.warning("asset_validator_setup_failed", error=str(exc))
                    return None
                for kind, code in items:
                    try:
                        res = await page.evaluate(
                            "([k, c]) => window.__validate(k, c)", [kind, code]
                        )
                        results.append(
                            (bool(res.get("ok")), str(res.get("error") or ""))
                        )
                    except Exception as exc:  # noqa: BLE001
                        results.append((False, f"validator error: {exc}"))
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 — launch/browser non disponibile
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
        try:
            loop.close()
        except Exception:  # noqa: BLE001
            pass


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
    except Exception as exc:  # noqa: BLE001 — incl. TimeoutError -> degrada
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
    except Exception as exc:  # noqa: BLE001 — convertitore di terze parti
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
    kind: str  # "latex" | "mermaid"
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
        v = re.sub(r"^```[a-zA-Z]*\n?", "", v)
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

    # Diagrammi Mermaid (solo format=="mermaid"; ignora image/legacy).
    for asset in output.visual_assets:
        if asset.format == "mermaid" and (asset.content or "").strip():
            slots.append(
                _Slot(
                    id=f"asset:{asset.asset_id}",
                    kind="mermaid",
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
        if asset.format == "mermaid" and (asset.content or "").strip():
            slots.append(
                _Slot(
                    id=f"new_asset:{asset.asset_id}",
                    kind="mermaid",
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
    """Valida ogni slot: LaTeX con latex2mathml (Python) E KaTeX (JS);
    Mermaid con v10.9.4 (JS). Se la validazione JS non e' disponibile (CDN
    down), il LaTeX resta gated da latex2mathml e Mermaid/KaTeX degradano a
    pass-through."""
    js_items = [(s.kind, s.current) for s in slots]
    js_results = await _validate_js_batch(js_items)

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
                ok_k, err_k = js_results[i]
                checks.append(
                    AssetCheck(slot.id, "latex", ok_k, "" if ok_k else f"KaTeX: {err_k}")
                )
        else:  # mermaid
            if js_results is None:
                checks.append(AssetCheck(slot.id, "mermaid", True, ""))
            else:
                ok, err = js_results[i]
                checks.append(
                    AssetCheck(slot.id, "mermaid", ok, "" if ok else f"mermaid: {err}")
                )
    return checks


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

    # Fix AI iterativo, SOLO sugli asset ancora invalidi.
    remaining = max_attempts
    while invalid:
        if remaining <= 0:
            details = "; ".join(
                f"{c.id} [{c.kind}]: {c.error_message}" for c in invalid[:5]
            )
            raise AssetFixUnresolvedError(details)
        remaining -= 1
        for c in invalid:
            slot = by_id[c.id]
            try:
                out, _usage = await openai_asset_fix_service.fix_asset(
                    kind=slot.kind,
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
# API pubblica
# ---------------------------------------------------------------------------


async def validate_and_fix_content_assets(
    output: LessonContentOutput, *, language_code: str
) -> LessonContentOutput:
    """Valida e auto-corregge gli asset fragili dell'output di Fase 3.
    Muta e ritorna lo stesso `output`. Solleva `AssetFixUnresolvedError`
    (recuperabile) se un asset resta invalido."""
    slots, inline_fields = _collect_content_slots(output)
    if not slots:
        return output
    fixed = await _validate_and_fix(slots, inline_fields, language_code=language_code)
    log.info(
        "content_assets_validated",
        total=len(slots),
        fixed=fixed,
        mermaid=sum(1 for s in slots if s.kind == "mermaid"),
    )
    return output


async def validate_and_fix_slides_assets(
    output: LessonSlidesOutput, *, language_code: str
) -> LessonSlidesOutput:
    """Valida e auto-corregge gli asset fragili dell'output di Fase 4
    (new_assets Mermaid + math inline nelle slide). Muta e ritorna `output`."""
    slots, inline_fields = _collect_slides_slots(output)
    if not slots:
        return output
    fixed = await _validate_and_fix(slots, inline_fields, language_code=language_code)
    log.info(
        "slides_assets_validated",
        total=len(slots),
        fixed=fixed,
        mermaid=sum(1 for s in slots if s.kind == "mermaid"),
    )
    return output


__all__ = [
    "validate_and_fix_content_assets",
    "validate_and_fix_slides_assets",
    "validate_assets_for_test",
    "validate_latex_mathml",
    "AssetFixUnresolvedError",
    "AssetCheck",
]


async def validate_assets_for_test(
    items: list[tuple[str, str, str]],
) -> list[AssetCheck]:
    """Helper per i test: valida una lista `(id, kind, source)` senza fix."""
    slots = [
        _Slot(id=i, kind=k, current=s, context="", commit=lambda v: None)
        for (i, k, s) in items
    ]
    return await _validate_slots(slots)
