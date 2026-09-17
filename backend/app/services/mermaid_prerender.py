"""Pre-render Mermaid → SVG (Playwright headless) e post-processing.

Estratto da `course_lesson_pdf_service`, che re-esporta i vecchi nomi: i
chiamanti (PDF dispensa, PDF slide, frame video, script di rivalidazione)
importano da lì o da qui indifferentemente.

Pin unico della libreria: `settings.mermaid_cdn_version` (lo stesso del
validatore in `asset_validation_service` e del lock npm del frontend).
Configurazione unica: `figure_theme.mermaid_initialize_js` (tema D3,
`htmlLabels: false` al livello TOP).

Perché `htmlLabels: false`: per le label Mermaid usa di default
`<foreignObject>` con HTML dentro l'SVG, che WeasyPrint non renderizza.
Con l'opzione al livello top Mermaid 11.17.2 emette `<text>` puro
(0 `<foreignObject>`) per tutti i quindici tipi di D8 — verificato dal test
`test_mermaid_no_foreignobject` sull'output reale. L'opzione per-tipo da
sola non basta: in Mermaid 11 i renderer «unificati» (flowchart, state,
block, class) leggono il valore top-level. `journey` emette due
`<foreignObject>` anche in 10.9.4 (non dipende dall'opzione) ed è per
questo escluso (`figure_theme.MERMAID_EXCLUDED_TYPES`).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import sys
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.figure_scale import SvgMetrics
from app.services.figure_theme import MERMAID_FONT_FAMILY, mermaid_initialize_js

log = get_logger("app.mermaid_prerender")


# ---------------------------------------------------------------------------
# Post-processing dell'SVG
# ---------------------------------------------------------------------------

# Con `useMaxWidth: true` Mermaid imposta `style="max-width: <natural_px>;"`
# sull'SVG generato. Questo IMPEDISCE all'SVG di crescere oltre la sua
# dimensione naturale (tipicamente ~300-400px), anche se il container del
# PDF è molto più largo (un foglio A4 ha ~170mm di content area = ~640px).
# Risultato: il diagramma rimane piccolo e le label illeggibili. Strippiamo
# quel `max-width:Xpx` lasciando tutto il resto dello stile così l'SVG
# riempie il container. La regex è indipendente dalla versione di Mermaid:
# la 11.17.2 emette ancora `max-width: <px>px;` (fixture
# `tests/fixtures/mermaid11_flowchart.svg`); se una versione futura
# cambiasse unità la funzione degrada a no-op, segnalato dal test D8.
_MERMAID_MAX_WIDTH_RE = re.compile(r"max-width\s*:\s*[\d.]+px\s*;?", re.IGNORECASE)


def _strip_mermaid_max_width(svg: str) -> str:
    return _MERMAID_MAX_WIDTH_RE.sub("", svg)


# `sankey-beta` scrive nome e valore del nodo in UN SOLO `<text>` separati da
# un a capo letterale (`<text …>Lezioni\n48</text>`): non usa `<tspan>`.
# Con `xml:space` di default la specifica SVG 1.1 dice di RIMUOVERE i fine
# riga — WeasyPrint lo fa e nel PDF si legge «Lezioni48», mentre Chromium è
# indulgente e mostra «Lezioni 48». La normalizzazione porta i due renderer
# a dire la stessa cosa. Vale per ogni figura sankey, non per i soli modelli
# degli editor. Nessun altro tipo D8 emette a capo dentro `<text>` (gli altri
# vanno a capo con `<tspan>`), quindi la sostituzione è mirata: solo il
# contenuto di un `<text>` SENZA figli.
_MERMAID_TEXT_NEWLINE_RE = re.compile(r"(<text\b[^>]*>)([^<]*\n[^<]*)(</text>)")


def _join_mermaid_text_newlines(svg: str) -> str:
    """A capo letterali dentro un `<text>` senza figli → spazio singolo."""
    return _MERMAID_TEXT_NEWLINE_RE.sub(
        lambda m: m.group(1) + " ".join(m.group(2).split()) + m.group(3), svg
    )


# ---------------------------------------------------------------------------
# Pagina headless di rendering
# ---------------------------------------------------------------------------

# Misura del corpo dei testi di un SVG nella pagina già aperta (D10): host
# fuori schermo (mai `visibility:hidden`, che azzera i testi), un solo
# `getComputedStyle` per elemento `text`/`tspan` con nodo di testo proprio
# non vuoto, filtro SOLO su `display:none` (come `_MISURA_JS` del test dei
# template frontend), host rimosso in `finally`. `fontSize` calcolato è in
# unità utente, indipendente da viewBox e larghezza resa (`font-size="10"`
# → 10, `"11pt"` → 14.667, `"4ex"` → 29.29). Ritorna `{min, median, count}`
# (`{null, null, 0}` senza testi). Il sorgente è specchiato byte per byte in
# `frontend/src/lib/figureFormats.ts` (`measureSvgFontPx`).
MEASURE_SVG_FONT_PX_JS = """(svg) => {
  const host = document.createElement("div");
  host.style.cssText = "position:absolute;left:-100000px;top:0;width:1000px";
  host.innerHTML = svg;
  document.body.appendChild(host);
  try {
    const sizes = [];
    for (const el of host.querySelectorAll("text, tspan")) {
      const own = Array.from(el.childNodes).some(
        (n) => n.nodeType === 3 && n.textContent.trim() !== "",
      );
      if (!own) continue;
      const cs = getComputedStyle(el);
      if (cs.display === "none") continue;
      const px = parseFloat(cs.fontSize);
      if (Number.isFinite(px) && px > 0) sizes.push(px);
    }
    if (sizes.length === 0) return { min: null, median: null, count: 0 };
    sizes.sort((a, b) => a - b);
    const mid = sizes.length >> 1;
    const median = sizes.length % 2 === 1 ? sizes[mid] : (sizes[mid - 1] + sizes[mid]) / 2;
    return { min: sizes[0], median, count: sizes.length };
  } finally {
    host.remove();
  }
}"""

# HTML mini-doc che carica mermaid.esm da CDN ed espone una funzione
# globale `__renderMermaid(id, code)` che ritorna SVG (o null se errore) e
# `__renderMermaidMeasured(id, code)` che ritorna `{svg, metrics}` con le
# metriche del testo misurate da `__measureSvgFontPx` nella stessa pagina
# (una sola `page.evaluate` per figura). `__renderMermaid` resta una
# stringa: i test del sanitizer e dei `<foreignObject>` lo chiamano
# direttamente. Segnaposto sostituiti da `build_mermaid_renderer_html`: la
# versione (`settings.mermaid_cdn_version`), l'istruzione
# `mermaid.initialize(...)` prodotta da `figure_theme` e la funzione di
# misura (le graffe del JS impediscono `str.format`).
_MERMAID_RENDERER_HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>body{margin:0;padding:0;font-family:__MERMAID_FONT_FAMILY__;}</style></head>
<body>
<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@__MERMAID_VERSION__/dist/mermaid.esm.min.mjs';
__MERMAID_INITIALIZE__
window.__renderMermaid = async (id, code) => {
  try {
    // Pre-validate: se la parse fallisce, NON chiamiamo render(),
    // altrimenti mermaid emette nel DOM un'icona "bomba" + scritta
    // "Syntax error in text" che finirebbe nell'SVG ritornato.
    // Con `suppressErrors: true`, parse ritorna `false` invece di
    // throware e senza side-effects nel DOM.
    const ok = await mermaid.parse(code, { suppressErrors: true });
    if (!ok) return null;
    const { svg } = await mermaid.render(id, code);
    return svg;
  } catch (e) {
    return null;
  }
};
window.__measureSvgFontPx = __MERMAID_MEASURE__;
window.__renderMermaidMeasured = async (id, code) => {
  const svg = await window.__renderMermaid(id, code);
  if (typeof svg !== "string" || !svg) return null;
  let metrics = null;
  try {
    metrics = window.__measureSvgFontPx(svg);
  } catch (e) {
    metrics = null;
  }
  return { svg, metrics };
};
window.__mermaidReady = true;
</script>
</body></html>
"""


# ---------------------------------------------------------------------------
# Isolamento di rete della pagina headless
# ---------------------------------------------------------------------------

# Unica origine che le pagine headless devono poter contattare: il CDN da
# cui importano i moduli (Mermaid nel pre-render, Mermaid e KaTeX nel
# validatore). Tutto il resto è bloccato PRIMA della richiesta: se un
# giorno un costrutto Mermaid sfuggisse al gate statico (`A@{ img: "…" }`,
# SEC-1) il server non eseguirebbe comunque la GET verso l'host scelto
# dall'autore. La barra finale è parte del prefisso: `cdn.jsdelivr.net.…`
# non lo soddisfa.
PRERENDER_ALLOWED_PREFIX = "https://cdn.jsdelivr.net/"
# Schemi che non escono dal processo (il documento stesso, gli URL inline).
_PRERENDER_INERT_SCHEMES = ("about:", "data:", "blob:")


def allows_prerender_url(url: str) -> bool:
    """`True` se la pagina headless può eseguire la richiesta."""
    lowered = (url or "").strip().lower()
    return lowered.startswith(PRERENDER_ALLOWED_PREFIX) or lowered.startswith(
        _PRERENDER_INERT_SCHEMES
    )


async def block_external_requests(page: Any) -> None:
    """Instrada TUTTE le richieste della pagina e annulla quelle che
    `allows_prerender_url` non ammette (isolamento di rete del pre-render,
    difesa in profondità di SEC-1)."""

    async def _guard(route: Any) -> None:
        url = route.request.url
        if allows_prerender_url(url):
            await route.continue_()
            return
        log.warning("prerender_request_blocked", url=url[:200])
        await route.abort()

    await page.route("**/*", _guard)


def build_mermaid_renderer_html(*, version: str | None = None) -> str:
    """Pagina di rendering con il pin richiesto (default: il setting) e
    l'inizializzazione del tema (`useMaxWidth: true`: l'SVG riempie il
    contenitore; il `max-width` naturale è poi rimosso da
    `_strip_mermaid_max_width`)."""
    pin = version or get_settings().mermaid_cdn_version
    return (
        _MERMAID_RENDERER_HTML_TEMPLATE.replace("__MERMAID_VERSION__", pin)
        .replace("__MERMAID_FONT_FAMILY__", MERMAID_FONT_FAMILY)
        .replace("__MERMAID_INITIALIZE__", mermaid_initialize_js(use_max_width=True))
        .replace("__MERMAID_MEASURE__", MEASURE_SVG_FONT_PX_JS)
    )


def __getattr__(name: str) -> str:
    """`_MERMAID_RENDERER_HTML` è costruita alla prima lettura (PEP 562): il
    pin viene da `get_settings()` e il modulo resta importabile senza ambiente
    configurato (test puri, `--help` degli script)."""
    if name == "_MERMAID_RENDERER_HTML":
        return build_mermaid_renderer_html()
    raise AttributeError(name)


@dataclass(frozen=True)
class MermaidPrerender:
    """SVG post-processato e metriche del testo misurate nella stessa pagina
    (`None` se la misura è fallita: la figura resta valida)."""

    svg: str
    metrics: SvgMetrics | None


def _metrics_from_page(raw: object, *, preview: str) -> SvgMetrics | None:
    """`SvgMetrics` dal dizionario `{min, median, count}` della pagina;
    `None` (con warning) se assente o malformato: una misura fallita NON
    degrada la figura."""
    if isinstance(raw, dict):
        count = raw.get("count")
        minimum = raw.get("min")
        median = raw.get("median")
        if count == 0:
            return SvgMetrics(None, None, 0, "no_text")
        if (
            isinstance(count, int)
            and count > 0
            and isinstance(minimum, int | float)
            and minimum > 0
            and isinstance(median, int | float)
        ):
            return SvgMetrics(float(minimum), float(median), count, "measured")
    log.warning("mermaid_font_measure_failed", preview=preview)
    return None


async def _prerender_mermaid_batch_async(
    codes: list[str],
) -> list[MermaidPrerender | None]:
    """Implementazione async del pre-render. NON va chiamata direttamente
    dal worker uvicorn — Playwright richiede `subprocess_exec`, che su
    Windows è supportato SOLO da `ProactorEventLoop` (non dal
    SelectorEventLoop che uvicorn può aver impostato). Wrappare via
    `_prerender_mermaid_to_svg_batch` che gira in un thread con loop
    dedicato.

    Renderizza una lista di sorgenti mermaid a SVG con UNA sola
    sessione Playwright headless (~1s startup + ~50-200ms per
    diagramma) e misura nella stessa pagina il corpo dei testi
    (`__renderMermaidMeasured`, una `page.evaluate` per figura). Ritorna
    lista parallela; ogni elemento è `MermaidPrerender(svg, metrics)` o
    `None` se il rendering ha fallito.
    """
    if not codes:
        return []

    from playwright.async_api import async_playwright

    results: list[MermaidPrerender | None] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        try:
            page = await browser.new_page()
            await block_external_requests(page)
            await page.set_content(build_mermaid_renderer_html(), wait_until="domcontentloaded")
            try:
                await page.wait_for_function("window.__mermaidReady === true", timeout=15_000)
            except Exception as exc:  # CDN irraggiungibile o pagina non pronta
                log.warning("mermaid_renderer_setup_failed", error=str(exc))
                # Non possiamo renderizzare nulla → tutti None.
                return [None] * len(codes)

            for i, code in enumerate(codes):
                if not (code or "").strip():
                    results.append(None)
                    continue
                try:
                    rendered = await page.evaluate(
                        "([id, code]) => window.__renderMermaidMeasured(id, code)",
                        [f"mmd-{i}", code],
                    )
                    svg = rendered.get("svg") if isinstance(rendered, dict) else None
                    if isinstance(svg, str) and svg.strip():
                        results.append(
                            MermaidPrerender(
                                svg=_join_mermaid_text_newlines(_strip_mermaid_max_width(svg)),
                                metrics=_metrics_from_page(
                                    rendered.get("metrics"), preview=code[:80]
                                ),
                            )
                        )
                    else:
                        log.warning(
                            "mermaid_render_returned_empty",
                            preview=code[:80],
                        )
                        results.append(None)
                except Exception as exc:  # errore JS/Playwright: quel diagramma degrada
                    log.warning(
                        "mermaid_render_failed",
                        error=str(exc),
                        preview=code[:80],
                    )
                    results.append(None)
        finally:
            await browser.close()
    return results


def _prerender_mermaid_batch_sync(
    codes: list[str],
) -> list[MermaidPrerender | None]:
    """Sync wrapper: crea un loop asyncio NUOVO e dedicato (su Windows
    forza `ProactorEventLoop`, l'unico che supporta `subprocess_exec`
    necessario al transport di Playwright). Va chiamato da un thread
    diverso dal main (via `asyncio.to_thread`) per non interferire col
    loop di uvicorn."""
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_prerender_mermaid_batch_async(codes))
    finally:
        with contextlib.suppress(Exception):
            loop.close()


def _project_svgs(rendered: list[MermaidPrerender | None]) -> list[str | None]:
    return [r.svg if r is not None else None for r in rendered]


async def _prerender_mermaid_to_svg_batch_async(
    codes: list[str],
) -> list[str | None]:
    """Nome storico: proiezione `.svg` di `_prerender_mermaid_batch_async`."""
    return _project_svgs(await _prerender_mermaid_batch_async(codes))


def _prerender_mermaid_to_svg_batch_sync(
    codes: list[str],
) -> list[str | None]:
    """Nome storico: proiezione `.svg` di `_prerender_mermaid_batch_sync`
    (script di rivalidazione, re-export di `course_lesson_pdf_service`)."""
    return _project_svgs(_prerender_mermaid_batch_sync(codes))


async def _prerender_mermaid_to_svg_batch(
    codes: list[str],
) -> list[str | None]:
    """Wrapper async: esegue il pre-render Playwright in un thread pool.
    Il thread crea il proprio loop (ProactorEventLoop su Windows) così
    indipendente dal loop scelto da uvicorn. Stessa firma della vecchia
    versione async — caller non cambia."""
    if not codes:
        return []
    return await asyncio.to_thread(_prerender_mermaid_to_svg_batch_sync, codes)


# ---------------------------------------------------------------------------
# Pulizia del sorgente
# ---------------------------------------------------------------------------

# Righe spurie a volte emesse dall'AI nel codice Mermaid: fence markdown
# residuo (```/```mermaid) o nodi-segnaposto isolati come `mermaid` /
# `all` / `all:`. Passano mermaid.parse ma compaiono come box anomali.
# Rimosse solo quando una riga è ESATTAMENTE uno di questi token (non
# tocchiamo archi/nodi reali tipo `A --> all` o `all[Etichetta]`).
# Mirror del sanitizer frontend in MermaidDiagram.tsx. Resta scoped a
# Mermaid: `all` è un nodo legittimo in DOT.
_MERMAID_JUNK_LINE_RE = re.compile(r"^(?:```.*|mermaid|all)\s*:?\s*$", re.IGNORECASE)


def _sanitize_mermaid_code(code: str) -> str:
    if not code:
        return code
    lines = [ln for ln in code.split("\n") if not _MERMAID_JUNK_LINE_RE.match(ln.strip())]
    return "\n".join(lines).strip()
