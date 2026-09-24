"""Registro dei renderer delle figure accademiche (D2) e orchestratore (Q1).

Quattro famiglie di asset visivi condividono un protocollo unico
(`FigureRenderer`): Mermaid 11 (pre-render Playwright), Vega-Lite
(vl-convert nel processo figlio), Graphviz DOT (binario `dot`) e
`function` (numpy + matplotlib in thread, sympy nel processo figlio: il
motore è `figure_function_service`, qui vive solo il renderer). I
renderer sono oggetti sincroni e puri: il chiamante decide thread o
processo. `render_function` è l'ingresso asincrono dell'endpoint
`render-function` (semaforo dei render + `to_thread` + `wait_for`).

Tre punti di ingresso:
- `available_formats()`: i formati offerti al modello e accettati dal
  validatore (kill-switch del setting E dipendenza presente), calcolati
  una volta per processo;
- `render_figure_map(assets, *, language)` (e la proiezione
  `render_svg_map`): l'UNICO punto in cui compaiono `asyncio.to_thread`,
  `asyncio.wait_for` e il semaforo dei render CPU-bound; raggruppa per
  formato, un batch per formato, cache LRU degli SVG e cache negativa dei
  render falliti; non solleva mai (le chiavi assenti attivano il fallback
  del partial);
- `validate_visual_assets_or_raise(...)`: gate offline del PATCH manuale
  (A15) che valida SOLO gli asset con `(format, content)` cambiati e
  solleva `ValidationAppError` 422 con `meta.errors` per asset.

Cache degli SVG: chiave `(formato, sha256(sanitizzato), THEME_VERSION)`.
La lingua NON entra nella chiave: `render_svg` del protocollo non la
riceve, quindi l'SVG non può dipenderne (la didascalia calcolata di
`function` è composta fuori dall'SVG da `figure_theme.function_caption`);
così l'SVG prodotto dalla validazione profonda del worker
(`validate(deep=True)`, senza lingua) è lo stesso hit che serve l'export.

Gli SVG di Vega-Lite, DOT e `function` passano da
`svg_normalize.normalize_svg`; quelli Mermaid no (catena byte-identica,
A11). Progettazione: `docs/courses/17-visual-figures.md`.

Metriche di leggibilità accanto all'SVG (D10): il valore della cache e
della mappa di resa è `RenderedFigure(svg, metrics)`, dove `metrics` è
misurato in Chromium per Mermaid (`MermaidRenderer.render_figure_batch`) e
letto dagli attributi per gli altri formati (`RenderedFigure.from_svg`).
L'SVG resta byte-identico e `THEME_VERSION` invariato perché il tema non
cambia; `render_svg_map` e `render_svg_batch` sono proiezioni `.svg`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import lru_cache, partial
from pathlib import Path
from typing import Any, Protocol

from app.core.config import get_settings
from app.core.errors import ConflictError, ValidationAppError
from app.core.logging import get_logger
from app.schemas.course_lesson_content import VISUAL_ASSET_CONTENT_MAX_CHARS
from app.schemas.figure_function import FunctionFigureSpec, format_issues, parse_function_spec
from app.services import figure_function_service, figure_geometry
from app.services.figure_compute.chain_layout import vertical_chain_variant
from app.services.figure_compute.graph_rules import (
    GRAPH_TOO_DENSE,
    check_graph_rules,
    crossings_violation,
    format_graph_violations,
)
from app.services.figure_compute.isolated import (
    FigureComputeError,
    FigureTimeoutError,
    run_isolated,
)
from app.services.figure_compute.vegalite_rules import (
    USE_FUNCTION_FORMAT,
    check_vegalite_rules,
    nesting_violation,
)
from app.services.figure_function_service import (
    FUNCTION_SPEC_INVALID,
    FunctionRenderError,
    FunctionRenderResult,
)
from app.services.figure_geometry import GeometryReport
from app.services.figure_scale import (
    FigureBoxMm,
    FigureVariant,
    SvgMetrics,
    fit_figure_width_mm,
    resolve_base_font_px,
)
from app.services.figure_theme import (
    MERMAID_ALLOWED_TYPES,
    THEME_VERSION,
    VEGALITE_THEME_CONFIG,
    dot_defaults_prelude,
)
from app.services.json_spans import replace_strings as replace_json_strings
from app.services.mermaid_prerender import (
    MermaidPrerender,
    _join_mermaid_text_newlines,
    _prerender_mermaid_batch_sync,
    _sanitize_mermaid_code,
    _strip_mermaid_max_width,
)
from app.services.svg_normalize import (
    SvgRejectedError,
    iter_style_bodies,
    iter_tag_contents,
    normalize_svg,
    svg_base_font_px,
    svg_intrinsic_box,
)

log = get_logger("app.figure_render")

RENDERABLE_FORMATS: tuple[str, ...] = ("mermaid", "vegalite", "dot", "function", "tikz")
# Formati promossi dalla REGOLA DI SCELTA del PROMPT 3 (system prompt): `tikz`
# (WP6, spento di default) si offre solo con un blocco del messaggio user,
# così con il formato spento il system prompt resta byte-identico.
PROMPTED_FORMATS: tuple[str, ...] = ("mermaid", "vegalite", "dot", "function")

# Tipi di errore del payload 422 (`meta.errors[].type`), stessa forma
# `loc/msg/type` del handler Pydantic (`core/errors.py`).
FIGURE_INVALID = "figure_invalid"
FIGURE_FORMAT_UNAVAILABLE = "figure_format_unavailable"
MERMAID_TYPE_NOT_ALLOWED = "mermaid_type_not_allowed"
VEGALITE_USE_FUNCTION_FORMAT = USE_FUNCTION_FORMAT
# `FUNCTION_SPEC_INVALID` è importato da `figure_function_service`;
# `GRAPH_TOO_DENSE` (soglie editoriali D13/D14) da `graph_rules`.

# Un messaggio di `validate` che inizia con uno di questi prefissi viene
# classificato con quel `type`; tutto il resto è `figure_invalid`.
_TYPED_PREFIXES: tuple[str, ...] = (
    MERMAID_TYPE_NOT_ALLOWED,
    VEGALITE_USE_FUNCTION_FORMAT,
    FUNCTION_SPEC_INVALID,
    GRAPH_TOO_DENSE,
)

VEGALITE_SCHEMA_URL = "https://vega.github.io/schema/vega-lite/v6.json"
VEGALITE_MAX_CHARS = 4_000
DOT_MAX_EDGES = 600
_ERROR_CAP = 1_600
_PAYLOAD_MSG_CAP = 600
NEGATIVE_CACHE_TTL_S = 60.0
_NEGATIVE_CACHE_MAX = 1_024

# Stessa classe di `asset_validation_service._CONTROL_CHARS_RE` (C0 senza
# \t \n \r, DEL, C1): il modulo non può importarla da lì (ciclo).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_FENCE_OPEN_RE = re.compile(r"^```[a-zA-Z0-9_-]*[ \t]*\n?")
_FENCE_CLOSE_RE = re.compile(r"\n?```\s*$")


def _strip_fence_and_control(content: str) -> str:
    """Rimuove caratteri di controllo e un eventuale code-fence che
    avvolge l'intero contenuto. Nessun'altra trasformazione: i byte del
    contenuto restano quelli del docente o del modello."""
    v = _CONTROL_CHARS_RE.sub("", content or "").strip()
    if v.startswith("```"):
        v = _FENCE_OPEN_RE.sub("", v, count=1)
        v = _FENCE_CLOSE_RE.sub("", v, count=1).strip()
    return v


def error_type_for(message: str) -> str:
    """`type` del payload 422 per il messaggio di `validate`."""
    for prefix in _TYPED_PREFIXES:
        if message.startswith(prefix):
            return prefix
    return FIGURE_INVALID


# ---------------------------------------------------------------------------
# Protocollo
# ---------------------------------------------------------------------------


class FigureRenderer(Protocol):
    """Renderer di un formato. Metodi sincroni e puri.

    - `available()`: la dipendenza è presente (binario, modulo);
    - `sanitize()`: fence e caratteri di controllo, mai un round-trip che
      alteri i byte;
    - `validate(deep=False)`: gate offline; `deep=True` aggiunge la prova
      di render, il cui SVG entra in cache;
    - `render_svg()` / `render_svg_batch()`: `None` per la figura che
      fallisce, mai un'eccezione;
    - `extract_translatable()` / `apply_translations()`: campi testuali
      per la localizzazione D7, con chiavi che sono percorsi DENTRO il
      contenuto (`title`, `encoding.x.axis.title`, `label.0`); la chiave
      vuota `""` indica l'intero contenuto (Mermaid).

    Metodi FACOLTATIVI, letti con `getattr` (i renderer registrati
    dall'esterno e i fake dei test non cambiano): `render_figure_batch`
    (SVG con le metriche accanto, D10) e `measure(svg) -> GeometryReport`
    (geometria di un SVG reso in Python, D14: oggi solo DOT; per Mermaid la
    misura vive nella pagina del pre-render). Il registro chiama
    `measure(svg)` con il solo SVG; `DotRenderer` accetta in più
    `work_left`, il residuo del tetto di lavoro del suo batch.
    """

    fmt: str

    def available(self) -> bool: ...

    def sanitize(self, content: str) -> str: ...

    def validate(self, content: str, *, deep: bool = False) -> tuple[bool, str]: ...

    def render_svg(self, content: str, *, asset_id: str = "") -> str | None: ...

    def render_svg_batch(
        self, contents: list[str], *, asset_ids: list[str]
    ) -> list[str | None]: ...

    def extract_translatable(self, content: str) -> dict[str, str]: ...

    def apply_translations(self, content: str, tr: Mapping[str, str]) -> str: ...


@dataclass(frozen=True)
class RenderedFigure:
    """SVG di una figura con le metriche del testo accanto (D10). `metrics`
    è `None` solo per i record costruiti a mano; `from_svg` le legge dagli
    attributi (`svg_base_font_px`), `MermaidRenderer` le misura in
    Chromium. `metrics.crossings` / `metrics.defects` portano la geometria
    (D14): misurata nella pagina del pre-render per Mermaid, in Python per
    DOT (`DotRenderer.measure`), assente (`None`, `()`) per gli altri
    formati. L'SVG non è mai modificato.

    `chain_variant` (D15) è la stessa figura resa con la direzione
    verticale, quando il sorgente è una catena orizzontale che a questa
    superficie esce sotto la banda (`render_chain_variants`): la resa
    sceglie fra le due MISURANDOLE (`_figure_width_style`). È `None`
    ovunque altrove, non entra mai nella cache degli SVG (la variante ha
    una chiave propria) e il sorgente salvato non cambia."""

    svg: str
    metrics: SvgMetrics | None = None
    chain_variant: RenderedFigure | None = None

    @classmethod
    def from_svg(cls, svg: str) -> RenderedFigure:
        return cls(svg=svg, metrics=svg_base_font_px(svg))


# Mappa `{asset_id → svg | RenderedFigure}` accettata dai renderer del PDF:
# una stringa vale `RenderedFigure.from_svg` (metriche parsate).
VisualSvgMap = Mapping[str, str | RenderedFigure]


# ---------------------------------------------------------------------------
# Cache LRU degli SVG + cache negativa
# ---------------------------------------------------------------------------

CacheKey = tuple[str, str, str]

_svg_cache: OrderedDict[CacheKey, RenderedFigure] = OrderedDict()
_negative_cache: dict[CacheKey, float] = {}
_cache_lock = threading.Lock()


def cache_key(fmt: str, sanitized: str) -> CacheKey:
    digest = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
    return (fmt, digest, THEME_VERSION)


def _cache_get_figure(key: CacheKey) -> RenderedFigure | None:
    with _cache_lock:
        fig = _svg_cache.get(key)
        if fig is not None:
            _svg_cache.move_to_end(key)
        return fig


def _cache_get(key: CacheKey) -> str | None:
    """Proiezione `.svg` della cache (contratto storico dei chiamanti)."""
    fig = _cache_get_figure(key)
    return fig.svg if fig is not None else None


def _cache_put(key: CacheKey, value: str | RenderedFigure) -> None:
    """Una stringa entra come `RenderedFigure.from_svg` (metriche parsate
    al put: la validazione profonda e i `render_svg` non cambiano)."""
    fig = value if isinstance(value, RenderedFigure) else RenderedFigure.from_svg(value)
    size = max(1, int(get_settings().figure_svg_cache_size))
    with _cache_lock:
        _svg_cache[key] = fig
        _svg_cache.move_to_end(key)
        while len(_svg_cache) > size:
            _svg_cache.popitem(last=False)
        _negative_cache.pop(key, None)


def _cache_is_negative(key: CacheKey) -> bool:
    with _cache_lock:
        stamp = _negative_cache.get(key)
        if stamp is None:
            return False
        if time.monotonic() - stamp > NEGATIVE_CACHE_TTL_S:
            del _negative_cache[key]
            return False
        return True


def _cache_negative(key: CacheKey) -> None:
    with _cache_lock:
        _negative_cache[key] = time.monotonic()
        if len(_negative_cache) > _NEGATIVE_CACHE_MAX:
            oldest = sorted(_negative_cache, key=_negative_cache.__getitem__)
            for stale in oldest[: len(_negative_cache) - _NEGATIVE_CACHE_MAX]:
                del _negative_cache[stale]


def clear_svg_cache() -> None:
    """Svuota cache positiva e negativa (test, cambio di tema a caldo)."""
    with _cache_lock:
        _svg_cache.clear()
        _negative_cache.clear()


def _render_failed(fmt: str, asset_id: str, reason: str) -> None:
    log.warning("figure_render_failed", format=fmt, asset_id=asset_id, reason=reason[:300])


# `asset_id` dei log della misura quando la firma del chiamante non lo porta
# (`validate(deep=True)` del protocollo): lo imposta `figure_asset_context`
# e `asyncio.to_thread` lo copia nel thread del renderer.
_asset_context: ContextVar[str] = ContextVar("figure_asset_id", default="")


@contextlib.contextmanager
def figure_asset_context(asset_id: str) -> Iterator[None]:
    """`asset_id` dei log di geometria emessi dentro il blocco, anche dai
    thread avviati con `asyncio.to_thread`."""
    token = _asset_context.set(asset_id)
    try:
        yield
    finally:
        _asset_context.reset(token)


def with_geometry(
    metrics: SvgMetrics, report: GeometryReport | None, *, fmt: str, asset_id: str
) -> SvgMetrics:
    """`metrics` con la geometria della figura resa (D14) e i log strutturati:
    `figure_measure_skipped` per un tetto di segmenti o di lavoro (incroci
    `None`), `figure_geometry_defects` quando ci sono difetti o gli incroci
    superano `MAX_EDGE_CROSSINGS` (la voce `graph_too_dense:` entra fra i
    difetti).
    Solo diagnostica, per ogni formato e in ogni percorso (validazione ed
    export): la figura resta valida e l'SVG non cambia."""
    if report is None:
        return metrics
    if report.skipped is not None:
        log.warning(
            "figure_measure_skipped",
            format=fmt,
            asset_id=asset_id,
            reason=report.skipped,
            segments=report.segments,
            edges=report.edges,
            work=report.work,
        )
        return replace(metrics, crossings=None, defects=report.defects)
    defects = list(report.defects)
    over = crossings_violation(report.crossings, fmt=fmt)
    if over is not None:
        defects.insert(0, over)
    if defects:
        log.warning(
            "figure_geometry_defects",
            format=fmt,
            asset_id=asset_id,
            crossings=report.crossings,
            defects=defects,
        )
    return replace(metrics, crossings=report.crossings, defects=tuple(defects))


def _measured_figure(
    renderer: object, svg: str, *, asset_id: str, work_left: int | None = None
) -> tuple[RenderedFigure, int]:
    """`RenderedFigure.from_svg` più la geometria, se il renderer espone
    `measure` (metodo facoltativo del protocollo, letto con `getattr`), e
    il lavoro eseguito dalla misura (`GeometryReport.spent`, anche quando
    un tetto l'ha interrotta; 0 se assente o fallita). `work_left` è
    passato a `measure` solo se dato (i renderer esterni hanno la firma a
    un argomento)."""
    fig = RenderedFigure.from_svg(svg)
    measure = getattr(renderer, "measure", None)
    if not callable(measure) or fig.metrics is None:
        return fig, 0
    fmt = str(getattr(renderer, "fmt", ""))
    try:
        report = measure(svg) if work_left is None else measure(svg, work_left=work_left)
    except Exception as exc:  # la misura è diagnostica: mai a spese della figura
        log.warning("figure_measure_failed", format=fmt, asset_id=asset_id, error=str(exc)[:300])
        return fig, 0
    if not isinstance(report, GeometryReport):
        return fig, 0
    spent = max(0, report.spent)
    metrics = with_geometry(fig.metrics, report, fmt=fmt, asset_id=asset_id)
    return RenderedFigure(svg=svg, metrics=metrics), spent


def _figure_from_svg(renderer: object, svg: str, *, asset_id: str) -> RenderedFigure:
    """`_measured_figure` senza batch: solo il tetto per figura."""
    return _measured_figure(renderer, svg, asset_id=asset_id)[0]


# ---------------------------------------------------------------------------
# Mermaid — gate statico D8, pre-render Playwright (catena invariata)
# ---------------------------------------------------------------------------

# Mermaid 11 tratta `initialize` come alias di `init` (`detectInit` usa
# `/(?:init\b)|(?:initialize\b)/`); `%%\s*\{` è un soprainsieme della sua
# `%%{` contigua.
_INIT_DIRECTIVE_RE = re.compile(r"%%\s*\{\s*init(?:ialize)?\b", re.IGNORECASE)
# Frontmatter YAML: Mermaid 11 lo carica con js-yaml e legge SOLO le chiavi
# `title`, `displayMode` e `config` (quest'ultima equivale alla direttiva
# `%%{init}%%`: sovrascriverebbe tema e opzioni imposti dal renderer, D3).
# Una chiave `config` può essere scritta in molte forme YAML (`"config"`,
# `'config'`, `"con\x66ig"`, `{config: …}` in forma flow, chiave complessa
# `? config`, alias `*a`): senza un parser YAML (PyYAML non è una
# dipendenza dichiarata del backend) l'unico gate deterministico è una
# lista chiusa: ogni riga del frontmatter deve essere vuota, un commento
# `#` o una voce `title:` / `displayMode:` in forma blocco. Un frontmatter
# che Mermaid ignora (chiave sconosciuta) è rifiutato con lo stesso esito.
_FRONTMATTER_LINE_RE = re.compile(r"^(?:title|displayMode)\s*:(?:\s|$)")
# `cleanupText` normalizza `\r\n` e `\r` in `\n` PRIMA che Mermaid tolga il
# frontmatter, le direttive e i commenti (`preprocessDiagram` in
# `mermaid.core.mjs`): un `\r` dentro una riga di commento apre per il
# renderer una riga nuova che il gate, dividendo su `\n`, non vedeva mai
# (`%%nota\rclick A href "http://…"` → `<a xlink:href>`, giro 7).
_MERMAID_CR_RE = re.compile(r"\r\n?")
# Riga che `cleanupComments` toglie DAVVERO: `^\s*%%(?!{)[^\n]+\n?`. Due
# differenze rispetto al `startswith("%%")` del gate, entrambe sfruttabili:
# una riga che comincia per `%%{` non è un commento (il lookahead la
# esclude: la direttiva la toglie `removeDirectives`, il resto della riga
# resta uno statement) e un `%%` nudo non lo è (serve almeno un carattere
# dopo). Il `\s` di JavaScript comprende `U+FEFF`, quello di Python no.
_MERMAID_COMMENT_LINE_RE = re.compile(r"^[\s\ufeff]*%%(?!\{)[^\n]")
# `directiveRegex` di Mermaid 11.17.2 (`chunk-DU6HZSFF.mjs:5010`), usata da
# `removeDirectives` per cancellare le direttive dal sorgente prima del
# parse. La chiusura `}%%` è FACOLTATIVA anche per lei.
_MERMAID_DIRECTIVE_RE = re.compile(
    r"%{2}\{\s*(?:(?:\w+)\s*:|(?:\w+))\s*(?:(?:\w+)|(?:(?:(?!\}%{2}).|\r?\n)*))?\s*(?:\}%{2})?",
    re.IGNORECASE,
)
# Tag HTML nelle label (`<b>`, `<script>`, `<table>`, ...): con
# `htmlLabels: false` finirebbero in chiaro nel `<text>`. Regola generica,
# non un elenco di tag: `<` seguito da un nome di elemento e chiuso da `>`
# sulla stessa riga. Non sono tag e passano: le frecce (`-->`, `<|--`,
# `->>`, `<-->`), le annotazioni `<<interface>>` (doppio `<`), un `<`
# isolato (`a < b`) e un `<b` non chiuso (`A[x <b] --> B`: la sezione degli
# attributi non attraversa `]`, `)`, `}`).
# Eccezione `<br>`: non è HTML reso in chiaro ma un a capo di Mermaid, che
# 10.9.4 e 11.17.2 rendono come `tspan.row` senza alcun `<foreignObject>`.
# Il criterio NON è `lineBreakRegex = /<br\s*\/?>/gi`: la label passa dal
# parser HTML del browser PRIMA di quella regex, che vede quindi una forma
# già normalizzata. Le varianti con spazi dopo la barra (`<br/ >`,
# `<br / >`) e la forma di chiusura (`</br>`) diventano `<br>` e sono a
# capo a tutti gli effetti — misurato sul pre-render di produzione:
# `test_mermaid_br_forms_render_as_a_line_break` rende ogni forma e la
# confronta con l'SVG di `<br>` (identico a meno dell'id `mmd-N`).
# Il lookahead ricalca quell'insieme misurato — `<`, una `/` facoltativa,
# `br`, poi soli spazi e `/` fino a `>` — di cui `lineBreakRegex` è un
# sottoinsieme (giro 3). Restano tag e sono rifiutati `<br x>` e
# `<br class="x">`: il parser li serializza in chiaro nella label.
_HTML_TAG_RE = re.compile(
    r"(?<!<)(?!</?[bB][rR][\s/]*>)</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^>\n\]\)\}]*)?/?>(?!>)"
)
# Inizio di un tag (stesse condizioni di `_HTML_TAG_RE`, senza attributi) e
# carattere che chiude la sezione degli attributi: servono a `_html_tag`.
_HTML_TAG_START_RE = re.compile(r"(?<!<)(?!</?[bB][rR][\s/]*>)</?[a-zA-Z][a-zA-Z0-9-]*")
_HTML_TAG_ATTR_STOP_RE = re.compile(r"[>\n\]\)\}]")
# Attributi di shape dei flowchart Mermaid 11 (`A@{ img: "https://…" }`):
# il nodo diventa un `<image href="…">` che il Chromium del pre-render,
# WeasyPrint (dispensa e slide) e il browser del docente dereferenziano —
# SSRF dal server e risorsa esterna dentro il PDF consegnato.
# `svg_normalize` non può fare da rete perché Mermaid ne è esente (A11),
# quindi le difese successive sono la scansione dell'SVG
# (`_svg_external_ref`) e l'isolamento di rete del pre-render
# (`mermaid_prerender.allows_prerender_url`).
#
# Il blocco `@{ … }` NON è testo: Mermaid lo passa a js-yaml
# (`addVertex` → `load(yamlData, {schema: JSON_SCHEMA})`), esattamente
# come il frontmatter. Una lista di pattern testuali (`\bimg\s*:`) è
# quindi evadibile con gli escape di YAML — `A@{ "\\x69mg": "…" }` è la
# chiave `img` per js-yaml e non lo è per la regex (SEC-1, residuo dei
# giri 2 e 3, misurato end-to-end: gate verde, `mermaid.parse` verde,
# PATCH 200, `<image href="…">` nell'SVG reso). Vale qui lo stesso
# ragionamento già applicato al frontmatter: senza un parser YAML
# l'unico gate deterministico è una lista CHIUSA di chiavi ammesse.
# L'elenco è quello che Mermaid 11.17.2 legge davvero da `doc`: nodo
# (`shape`, `label`, `labelType`, `form`, `pos`, `w`, `h`, `constraint`),
# arco (`animate`, `animation`, `curve`); `img` e `icon` — le sole due
# che puntano a una risorsa — restano fuori, e con loro ogni chiave
# scritta in una forma diversa da quella piana (`"\\x69mg"`, `? img`,
# `<<`, un alias `*a`).
#
# Il gate è dichiaratamente BEST-EFFORT (sezione 15): rincorre un parser
# vero (lexer con stati + js-yaml) e il giro 4 lo ha perso sostituendo la
# scansione larga del giro 3 con la sola lista chiusa, riaprendo sette
# vettori. Dal giro 5 i controlli si SOMMANO — lista chiusa delle chiavi,
# scansione `img:`/`icon:` sul blocco grezzo (giro 3) e schemi di URL —
# e basta che uno segnali per rifiutare: nessun sorgente rifiutato da un
# giro precedente può tornare ammesso.
MERMAID_SHAPE_KEYS = frozenset(
    {
        "shape",
        "label",
        "labelType",
        "form",
        "pos",
        "w",
        "h",
        "constraint",
        "animate",
        "animation",
        "curve",
    }
)
_MERMAID_SHAPE_URL_RE = re.compile(r"\b(?:https?|file|data|blob|ftp):", re.IGNORECASE)
# Rete del giro 3, rimessa dal giro 5: le due chiavi che puntano a una
# risorsa, cercate come TESTO sul blocco grezzo. Non vede gli escape YAML
# (`"\\x69mg"`), ma vede le forme che la segmentazione in voci non riesce a
# spezzare — una `,` dentro una riga di una mappa in forma blocco, che per
# js-yaml è un errore di indentazione ma per il gate era una voce sola.
# Sommata alla lista chiusa, non sostituita ad essa.
_MERMAID_SHAPE_RESOURCE_KEY_RE = re.compile(r"\b(?:img|icon)\s*:", re.IGNORECASE)
# Sostituzione che il lexer applica al testo DENTRO le virgolette dello
# stato `shapeDataStr` (regola 10 di `chunk-SHT3W25Y.mjs`:
# `yytext.replace(/\n\s*/g, "<br/>")`), prima che `addVertex` decida se
# passare a js-yaml una mappa in forma flow o in forma blocco.
_MERMAID_SHAPE_STR_NEWLINE_RE = re.compile(r"\n\s*")
# Indicatori di collezione di YAML in forma flow: `(` NON è fra questi (per
# js-yaml `label: (` è lo scalare `(`), quindi non protegge le virgole che
# seguono. Gli statement invece passano dal parser di Mermaid, dove `(` apre
# la sezione di una label (`A(fai clic; qui)`).
_YAML_FLOW_BRACKETS = "[{"
_MERMAID_STATEMENT_BRACKETS = "[({"
_BRACKET_PAIRS = {"[": "]", "(": ")", "{": "}"}
# Modalità di quotatura di `_split_top_level`. Nessuna delle tre coincide
# con il parser vero — il lexer di Mermaid, js-yaml e il gate hanno tre
# idee diverse di che cosa sia una stringa — quindi il gate non ne sceglie
# una: le prova a coppie e rifiuta se una qualsiasi delle due segnala
# (unione, mai scambio; giro 6).
#   * `any`    — `"` e `'` aprono ovunque: è il comportamento dei giri 2-5,
#                tenuto perché è l'unico che vede certe forme sbilanciate.
#   * `double` — solo `"` apre. Per il lexer dei flowchart l'apice NON è un
#                delimitatore ma uno dei caratteri ammessi in un
#                NODE_STRING, insieme a `"` stesso (la classe
#                `[A-Za-z0-9!"#$%&'*+./?\\_]` più l'apice inverso, in
#                `chunk-SHT3W25Y.mjs`):
#                un apice dispari «quotava» il resto della riga per il gate
#                e nascondeva il `;` dello statement successivo
#                (`A[it's]; click A href "http://…"` → `<a xlink:href>`,
#                misurato al giro 6).
#   * `yaml`   — `"` e `'` aprono solo a inizio nodo, come js-yaml: in
#                mezzo a uno scalare piano sono caratteri come gli altri
#                (`label: x'y` è lo scalare `x'y`), mentre per il gate
#                erano un delimitatore e la virgola successiva spariva
#                insieme alla voce che apriva (`A@{ label: x'y,
#                "\x69mg": "\x68ttp://…" }` → `<image href>` e GET
#                arrivata, misurato al giro 6).
_QUOTING_ANY = "any"
_QUOTING_DOUBLE = "double"
_QUOTING_YAML = "yaml"
# Caratteri dopo i quali (a meno di spazi) può iniziare un nodo YAML: solo
# lì un apice o una virgoletta aprono uno scalare quotato.
_YAML_NODE_START = ",:[{\n"
# Separatori di statement: Mermaid tratta `;` come un a capo, e il lexer
# salta il `\r` come qualsiasi altro spazio, quindi anche un `\r` separa
# due statement (`A --> B\rclick A href "http://…"` registra il click,
# misurato al giro 6) mentre `str.split("\n")` non lo vede. Il `\r` entra
# QUI e non nella divisione in righe: spostare quella cambierebbe anche il
# riconoscimento del tipo e del frontmatter, e un sorgente oggi rifiutato
# (`---\rtitle: x\r---\rflowchart LR`, tipo `---`) tornerebbe ammesso.
_MERMAID_STATEMENT_SEPARATORS = ";\r"
# `U+FEFF` (BOM) è l'unico carattere in cui `\s` di JavaScript — cioè il
# whitespace che il lexer di Mermaid salta — è più largo dell'insieme di
# `str.strip()` di Python. Davanti a `click`, `link`, `links` o
# `properties` nascondeva la parola chiave al gate mentre il renderer la
# eseguiva: misurato al giro 6 con `<a xlink:href>` in flowchart,
# classDiagram e stateDiagram e `<image xlink:href>` più la GET arrivata
# al listener in sequenceDiagram.
# La coda si cerca solo dall'inizio di una corsa (lookbehind) e la si
# prende intera: con `[\s\ufeff]+$` `sub` riscorreva una corsa interna
# da ognuna delle sue posizioni (quadratico, 1,5 s su 12.000 spazi).
_MERMAID_TRIM_RE = re.compile(r"^[\s\ufeff]+|(?<![\s\ufeff])[\s\ufeff]++$")
# Statement che attaccano a un nodo un URL, un'icona o una callback: non
# passano dalle shape e nessun gate li vedeva (SEC-1, terza via). Elenco
# MISURATO rendendo ogni parola chiave in ognuna delle 15 famiglie D8 e
# cercando l'URL negli ATTRIBUTI dell'SVG reso: solo queste coppie lo
# producono (`sequenceDiagram` con `properties A: {"icon": "http://…"}`
# dà un `<image xlink:href>`, cioè una GET vera; `click`, `link` e
# `links` danno un `<a xlink:href>` verso l'host scelto dall'autore).
# Fuori da queste coppie la parola resta testo — in `erDiagram` `click`
# è il nome di un'entità, in `mindmap` e `timeline` il testo di un nodo —
# e non va rifiutata. Il valore booleano dice se la parola chiave è
# case-insensitive: lo è nel lexer di `sequenceDiagram` (`PROPERTIES`
# funziona) e in quello di `stateDiagram` (regole `/^(?:click\b)/i` e
# `/^(?:href\b)/i` di `chunk-IMKFNOWR.mjs`, dove `CLICK A HREF "…"` rende),
# NON in quello di `flowchart` e `classDiagram`, dove `Link --> Other` è
# una classe legittima e `CLICK …` non parsa.
# `stateDiagram` è entrato nella mappa al giro 5: `click A href "http://…"`
# arrivava fino al PDF come `<a xlink:href>` (misurato in entrambe le
# scritture del tipo). Uno stato che si chiama davvero `click` non esiste —
# `stateDiagram-v2 / click --> B` non parsa — quindi la coppia non ha falsi
# positivi renderizzabili.
MERMAID_URL_STATEMENTS: dict[str, tuple[frozenset[str], bool]] = {
    "flowchart": (frozenset({"click"}), False),
    "graph": (frozenset({"click"}), False),
    "classDiagram": (frozenset({"click", "link"}), False),
    "sequenceDiagram": (frozenset({"link", "links", "properties", "details"}), True),
    "stateDiagram": (frozenset({"click"}), True),
    "stateDiagram-v2": (frozenset({"click"}), True),
}
_MERMAID_FIRST_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
# Costrutti che caricano una risorsa esterna in un SVG Mermaid già reso.
# Questa scansione è una difesa indipendente, non un doppione del gate: il
# gate è una euristica best-effort e ogni giro della revisione ha trovato
# sorgenti che lo passano e producono davvero un `<image href>` o un
# `<a xlink:href>` (giro 6: l'apice dispari, il BOM e il `\r`). Applicati
# SOLO al contenuto dei tag e ai blocchi `<style>` (vedi
# `_svg_external_ref`).
_SVG_EXTERNAL_REF_RE = re.compile(
    r"<(?:image|script|iframe)\b|url\(\s*[\'\"]?\s*(?:https?:|file:|//)|@import",
    re.IGNORECASE,
)
# `<a xlink:href="http://…">`, prodotto dagli statement `click`, `link` e
# `links`. Non è una richiesta immediata come `<image>`, ma è un
# collegamento verso un host scelto dall'autore che finisce nel PDF
# consegnato e nella vista lezione, dove un lettore lo segue. Fino al
# giro 4 la scansione non lo cercava perché «è il gate degli statement a
# impedirne la nascita»: era una delega, non una difesa, e `stateDiagram`
# ci passava in mezzo (giro 5). L'`href` esterno è riconosciuto come in
# `svg_normalize`: valore quotato che non inizia per `#`, oppure non
# quotato e non frammento; `\b` prima di `href` copre anche `xlink:href`.
_SVG_EXTERNAL_ANCHOR_RE = re.compile(
    r"^<\s*(?:[A-Za-z_][\w.-]*:)?a\b.*?\bhref\s*=\s*(?:[\"']\s*(?!#)|(?![\"'\s#]))",
    re.IGNORECASE | re.DOTALL,
)


def _svg_external_ref(svg: str) -> str | None:
    """Primo riferimento esterno trovato negli ATTRIBUTI o nel CSS di un
    SVG Mermaid già reso; `None` se è pulito.

    La scansione è limitata al contenuto dei tag e ai blocchi `<style>`,
    come quella di `svg_normalize` per gli altri renderer: il testo dei
    nodi non è codice, e cercarvi `@import` o `url(https://…)` faceva
    sparire dall'export una figura legittima la cui label parla di CSS —
    visibile nell'editor (il render client-side non scandisce nulla) e
    sostituita da un `<pre>` in dispensa, slide e video."""
    for tag in iter_tag_contents(svg):
        found = _SVG_EXTERNAL_REF_RE.search(tag)
        if found is not None:
            return found.group(0)
        if _SVG_EXTERNAL_ANCHOR_RE.search(tag) is not None:
            return "<a href esterno>"
    for css in iter_style_bodies(svg):
        found = _SVG_EXTERNAL_REF_RE.search(css)
        if found is not None:
            return found.group(0)
    return None


def _mermaid_shape_blocks(code: str) -> Iterator[str]:
    """Blocchi `@{ … }` del sorgente, delimitatori compresi, con lo stesso
    criterio di chiusura del lexer di Mermaid 11.

    Il lexer entra in `shapeData` su `@{` e ne esce sulla `}` che incontra
    fuori dalle virgolette: lo stato `shapeDataStr` (regole 8/9/10 del
    lexer di 11.17.2: `["]` push, `["]` pop, `[^"]+`) fa sì che una `}`
    dentro una stringa quotata NON chiuda la shape, e la barra rovesciata
    non vi ha alcun ruolo di escape. Una regex `@\\{[^}]*\\}` si fermava
    invece alla prima `}`, e quel disallineamento nascondeva al gate tutto
    quello che seguiva (`A@{ label: "}", img: "http://…" }`, SEC-1).

    Uno `@{` senza chiusura estende il blocco fino alla fine del sorgente:
    è il caso in cui Mermaid arriva a EOF dentro la shape e la parse
    fallisce, quindi rifiutare tutto è la scelta prudente."""
    i, n = 0, len(code)
    while True:
        start = code.find("@{", i)
        if start < 0:
            return
        j, in_string = start + 2, False
        while j < n:
            ch = code[j]
            if ch == '"':
                in_string = not in_string
            elif ch == "}" and not in_string:
                j += 1
                break
            j += 1
        yield code[start:j]
        i = j


def _mermaid_shape_metadata(block: str) -> str:
    """Testo che il lexer consegna ad `addVertex` per un blocco `@{ … }`.

    Non è il sorgente grezzo: nello stato `shapeDataStr` la regola 10 del
    lexer sostituisce `/\\n\\s*/g` con `<br/>`, quindi un a capo scritto
    DENTRO le virgolette sparisce dal `metadata` — e con lui la scelta
    della forma, perché `addVertex` guarda `metadata.includes("\\n")` per
    decidere se avvolgere il testo in `{ … }` (mappa flow, voci separate
    da virgola) o passarlo così com'è (mappa blocco, una voce per riga).
    Leggere il sorgente grezzo desincronizzava il gate dal parser: in
    `A@{ label: "a\\nb", img: "http://…" }` il gate vedeva la forma blocco
    e una voce sola (chiave `label`, ammessa) mentre js-yaml leggeva la
    forma flow e la chiave `img` (SEC-1, riaperto dal giro 4, misurato con
    la GET al listener e l'`<image href>` nell'SVG).

    I delimitatori restano fuori: il lexer azzera `yytext` su `@{`
    (regola 7) e non restituisce la `}` di chiusura (regola 12). La `}`
    finale si toglie solo se le virgolette del blocco sono bilanciate:
    se non lo sono quella `}` sta dentro una stringa ed è testo."""
    inner = block[2:]
    if inner.endswith("}") and inner.count('"') % 2 == 0:
        inner = inner[:-1]
    out: list[str] = []
    quoted, start = False, 0
    for i, ch in enumerate(inner):
        if ch != '"':
            continue
        chunk = inner[start:i]
        out.append(_MERMAID_SHAPE_STR_NEWLINE_RE.sub("<br/>", chunk) if quoted else chunk)
        out.append('"')
        quoted, start = not quoted, i + 1
    tail = inner[start:]
    out.append(_MERMAID_SHAPE_STR_NEWLINE_RE.sub("<br/>", tail) if quoted else tail)
    return "".join(out)


def _mermaid_shape_entries(block: str, *, quoting: str = _QUOTING_ANY) -> Iterator[str]:
    """Voci di primo livello di un blocco `@{ … }` (delimitatori esclusi).

    Segue la stessa biforcazione di `addVertex` sul `metadata` che il
    lexer produce (`_mermaid_shape_metadata`): senza a capo Mermaid
    avvolge il contenuto in `{ … }` e js-yaml lo legge come mappa in
    forma flow (voci separate da virgola); con almeno un a capo lo legge
    come mappa in forma blocco (una voce per riga). Le collezioni flow
    annidate (`[`, `{`) non separano; le parentesi tonde sì, perché per
    YAML non sono un indicatore. Le voci vuote e i commenti `#` sono
    saltati, come nel gate del frontmatter."""
    metadata = _mermaid_shape_metadata(block)
    seps = "\n" if "\n" in metadata else ","
    for entry in _split_top_level(metadata, seps, brackets=_YAML_FLOW_BRACKETS, quoting=quoting):
        stripped = entry.strip()
        if stripped and not stripped.startswith("#"):
            yield stripped


def _split_top_level(
    text: str,
    separators: str,
    *,
    brackets: str = _MERMAID_STATEMENT_BRACKETS,
    quoting: str = _QUOTING_ANY,
) -> Iterator[str]:
    """Spezza `text` sui separatori che stanno fuori dalle virgolette e
    fuori dalle parentesi annidate di `brackets`; `quoting` dice quali
    virgolette aprono e dove (vedi i `_QUOTING_*`)."""
    closers = {_BRACKET_PAIRS[b] for b in brackets}
    openers = '"' if quoting == _QUOTING_DOUBLE else "\"'"
    depth, quote, start, node_start = 0, "", 0, True
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote, node_start = "", False
            continue
        if ch in openers and (quoting != _QUOTING_YAML or node_start):
            quote = ch
        elif ch in brackets:
            depth += 1
        elif ch in closers:
            depth = max(0, depth - 1)
        elif depth == 0 and ch in separators:
            yield text[start:i]
            start = i + 1
        node_start = ch in _YAML_NODE_START or (node_start and ch in " \t")
    yield text[start:]


def _mermaid_shape_key(entry: str, *, quoting: str = _QUOTING_ANY) -> str:
    """Chiave di una voce del blocco: il testo prima dei due punti di
    primo livello (l'intera voce se non ce ne sono), tolto un solo strato
    di virgolette esterne.

    Le virgolette si tolgono senza interpretare gli escape: `"img"` è la
    chiave `img` (rifiutata perché fuori dalla lista), `"\\x69mg"` resta
    `\\x69mg` e non somiglia ad alcuna chiave ammessa. È il verso giusto
    in cui sbagliare: ogni forma che non riconosciamo è rifiutata."""
    brackets = _YAML_FLOW_BRACKETS if quoting == _QUOTING_YAML else _MERMAID_STATEMENT_BRACKETS
    key = next(_split_top_level(entry, ":", brackets=brackets, quoting=quoting)).strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        key = key[1:-1]
    return key.strip()


def _mermaid_shape_violation(block: str) -> str | None:
    """Prima chiave non ammessa di un blocco `@{ … }` (`None` se il blocco
    è tutto dentro la lista chiusa).

    Le voci si ricavano con DUE segmentazioni e basta che una delle due
    trovi una chiave fuori lista (unione, mai scambio): `any` è quella dei
    giri 2-5, `yaml` apre le stringhe solo dove può iniziare un nodo,
    come js-yaml. Nessuna delle due è il parser vero, e ognuna vede quello
    che l'altra si perde: `A@{ label: x'y, "\\x69mg": "…" }` è una voce
    sola per la prima (l'apice dispari le «quota» la virgola) e due per la
    seconda, che quindi legge la chiave `\\x69mg` e rifiuta."""
    for quoting in (_QUOTING_ANY, _QUOTING_YAML):
        for entry in _mermaid_shape_entries(block, quoting=quoting):
            key = _mermaid_shape_key(entry, quoting=quoting)
            if key not in MERMAID_SHAPE_KEYS:
                # I due punti finali distinguono il dettaglio di una shape
                # da quello di uno statement in `MermaidRenderer.validate`:
                # la troncatura va fatta PRIMA di aggiungerli.
                return f"{(key or entry)[:38]}:"
    return None


def _mermaid_statements(lines: Iterable[str], *, quoting: str = _QUOTING_ANY) -> Iterator[str]:
    """Statement del corpo: Mermaid tratta `;` e `\\r` come un a capo,
    quindi una riga di `str.split("\\n")` può contenerne più d'uno
    (`A-->B; click A href "…"` registra il click, misurato). Il separatore
    dentro le virgolette o dentro la sezione di una label non separa:
    `A["fai clic; click qui"]` è un solo statement, e si rende.

    Il BOM davanti allo statement si toglie con il whitespace: per il
    lexer è uno spazio, per `str.strip()` no."""
    for line in lines:
        for stmt in _split_top_level(line, _MERMAID_STATEMENT_SEPARATORS, quoting=quoting):
            stripped = _MERMAID_TRIM_RE.sub("", stmt)
            if stripped:
                yield stripped


def _mermaid_url_statement(kind: str, lines: Iterable[str]) -> str | None:
    """Prima parola chiave di statement che, nella famiglia dichiarata,
    porta un URL o un'icona nell'SVG (`None` se non ce ne sono).

    Come per le shape, gli statement si ricavano con DUE segmentazioni e
    basta che una delle due veda la parola chiave: per il lexer dei
    flowchart l'apice è un carattere di NODE_STRING, non un delimitatore,
    e un apice dispari nascondeva il `;` dello statement successivo."""
    entry = MERMAID_URL_STATEMENTS.get(_MERMAID_TYPE_ALIASES.get(kind, kind))
    if entry is None:
        return None
    keywords, fold = entry
    for quoting in (_QUOTING_ANY, _QUOTING_DOUBLE):
        for stmt in _mermaid_statements(lines, quoting=quoting):
            token = _MERMAID_FIRST_TOKEN_RE.match(stmt)
            if token is None:
                continue
            word = token.group(0)
            if (word.lower() if fold else word) in keywords:
                return word
    return None


# Esiti del gate statico (condivisi con `scripts/revalidate_mermaid_assets.py`).
MERMAID_GATE_EMPTY = "mermaid_empty"
MERMAID_GATE_TYPE = MERMAID_TYPE_NOT_ALLOWED
MERMAID_GATE_INIT = "mermaid_init_directive"
MERMAID_GATE_HTML = "mermaid_html_in_label"
MERMAID_GATE_RESOURCE = "mermaid_external_resource"

# Alias del tipo accettati in lettura oltre a quelli di `MERMAID_ALLOWED_TYPES`
# (`graph`, `stateDiagram`): Mermaid 11 tratta `classDiagram-v2` come
# `classDiagram`. Il confronto sul token è ESATTO: `flowchartXYZ` e
# `flowchart-elk` (layout esterno, assente nel pre-render da CDN) sono
# rifiutati.
_MERMAID_TYPE_ALIASES: dict[str, str] = {"classDiagram-v2": "classDiagram"}


def _split_mermaid_frontmatter(code: str) -> tuple[list[str], list[str]]:
    """`(righe del frontmatter YAML, righe del corpo)`; il frontmatter è il
    blocco `---...---` iniziale (dopo eventuali righe vuote). Come la
    `frontMatterRegex` di Mermaid 11, il `---` di chiusura deve avere lo
    stesso rientro di quello di apertura: un `---` con rientro diverso è
    parte del frontmatter, non la sua fine. Senza chiusura tutto il
    sorgente è frontmatter (corpo vuoto → tipo `?`)."""
    lines = code.split("\n")
    i, n = 0, len(lines)
    while i < n and not lines[i].strip():
        i += 1
    if i < n and lines[i].strip() == "---":
        indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
        j = i + 1
        while j < n and lines[j].rstrip() != indent + "---":
            j += 1
        return lines[i + 1 : j], lines[j + 1 :]
    return [], lines[i:]


def _frontmatter_violation(frontmatter: list[str]) -> str | None:
    """Prima riga del frontmatter che non è vuota, un commento `#` o una
    voce `title:` / `displayMode:` (`None` se il frontmatter è ammesso)."""
    for raw in frontmatter:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not _FRONTMATTER_LINE_RE.match(line):
            return line
    return None


def mermaid_first_meaningful_line(code: str) -> str:
    """Prima riga utile: salta righe vuote, commenti `%%` e il frontmatter
    YAML `---...---` iniziale."""
    _frontmatter, body = _split_mermaid_frontmatter(code)
    for raw in body:
        stripped = raw.strip()
        if stripped and not stripped.startswith("%%"):
            return stripped
    return ""


def mermaid_declared_type(code: str) -> str:
    """Tipo dichiarato: primo token della prima riga utile (`graph TD` →
    `graph`, `flowchart LR;` → `flowchart`)."""
    line = mermaid_first_meaningful_line(code)
    return line.split()[0].rstrip(";") if line else ""


def _mermaid_gate_views(code: str) -> Iterator[tuple[str, bool]]:
    """Le due letture del sorgente su cui il gate ripete TUTTI i controlli,
    come `(testo, fedele)`.

    * `(code, False)` — la lettura dei giri 1-6: righe da `str.split("\\n")`,
      commento = riga che comincia per `%%`.
    * `(…, True)` — la lettura di `preprocessDiagram`: `\\r\\n?` normalizzato
      in `\\n` da `cleanupText` e direttive tolte da `removeDirectives`,
      con il criterio di commento vero di `cleanupComments`.

    Sono due VISTE, non una sostituzione: il gate rifiuta se una qualsiasi
    delle due segnala. La prima resta identica a oggi, quindi nessun
    sorgente rifiutato da un albero precedente può tornare ammesso — la
    regola dell'unione vale anche qui, come per la segmentazione delle
    virgolette (giro 6). Serviva un'unione e non uno scambio proprio
    perché la lettura fedele è più permissiva in un punto: con `\\r` come
    a capo `---\\rtitle: x\\r---\\rflowchart LR` diventa un frontmatter
    valido, mentre per la prima lettura il tipo è `---` e non è ammesso."""
    yield (code, False)
    yield (_MERMAID_DIRECTIVE_RE.sub("", _MERMAID_CR_RE.sub("\n", code)), True)


def mermaid_static_gate(code: str) -> tuple[str, str]:
    """Gate statico D8 su un sorgente già sanificato: `("", "")` se passa,
    altrimenti `(esito, dettaglio)` con esito in `MERMAID_GATE_*` e
    dettaglio = tipo dichiarato (`?` se assente), motivo della direttiva o
    tag trovato. Unico punto del gate: `MermaidRenderer.validate` e lo
    script di rivalidazione lo consumano con messaggi propri.

    I controlli girano su ogni vista di `_mermaid_gate_views` e basta che
    una segnali."""
    if not code.strip():
        return (MERMAID_GATE_EMPTY, "")
    for view, faithful in _mermaid_gate_views(code):
        outcome, detail = _mermaid_gate_once(view, faithful=faithful)
        if outcome:
            return (outcome, detail)
    return ("", "")


def _html_tag(line: str) -> str | None:
    """Primo tag di `line`, cioè `_HTML_TAG_RE.search(line).group(0)`
    (`None` se non ce ne sono), in tempo lineare.

    La regex è provata solo dove può cominciare un tag. Un tentativo con
    attributi legge fino al primo carattere che chiude la sezione (`>`,
    `]`, `)`, `}`, a capo) e, se fallisce, fallisce lì: ogni `<` fra il nome
    e quel carattere ha la stessa sezione e la stessa fine (un `>` più
    vicino sarebbe esso stesso quel carattere), quindi fallisce allo stesso
    punto e la ricerca riparte da lì. `search` invece riprovava da ogni
    `<`: su `<a <a <a …` senza chiusura era quadratica (1,2 s su 12.000
    caratteri, nel gate sincrono del salvataggio).

    Il ragionamento vale per una riga senza a capo, come quelle del gate
    (`split("\\n")`): `\\n` è insieme spazio e fine della sezione, e un nome
    che finisce proprio lì la oltrepassa. Con un a capo resta la regex."""
    if "\n" in line:
        found = _HTML_TAG_RE.search(line)
        return found.group(0) if found is not None else None
    pos = 0
    while (start := _HTML_TAG_START_RE.search(line, pos)) is not None:
        tag = _HTML_TAG_RE.match(line, start.start())
        if tag is not None:
            return tag.group(0)
        if line[start.end() : start.end() + 1].isspace():
            stop = _HTML_TAG_ATTR_STOP_RE.search(line, start.end() + 1)
            pos = stop.start() if stop is not None else len(line)
        else:
            pos = start.start() + 1
    return None


def _mermaid_gate_once(code: str, *, faithful: bool) -> tuple[str, str]:
    """Un passaggio del gate su una singola vista del sorgente; `faithful`
    sceglie il criterio con cui si scartano le righe di commento."""
    kind = mermaid_declared_type(code)
    if _MERMAID_TYPE_ALIASES.get(kind, kind) not in MERMAID_ALLOWED_TYPES:
        return (MERMAID_GATE_TYPE, kind or "?")
    if _INIT_DIRECTIVE_RE.search(code):
        return (MERMAID_GATE_INIT, "direttiva %%{init ...}%% non ammessa")
    frontmatter, body = _split_mermaid_frontmatter(code)
    bad_line = _frontmatter_violation(frontmatter)
    if bad_line is not None:
        return (
            MERMAID_GATE_INIT,
            f"frontmatter: riga `{bad_line[:40]}` non ammessa "
            "(ammesse solo `title:` e `displayMode:`)",
        )
    # Solo le righe del corpo che non sono commenti: un tag in un commento
    # o nel frontmatter non viene renderizzato. Nella vista fedele il
    # criterio è quello di `cleanupComments`, che NON toglie né una riga
    # `%%{…` (è una direttiva: `removeDirectives` ne cancella solo la
    # direttiva e lo statement che segue resta) né un `%%` nudo.
    if faithful:
        lines = [line for line in body if not _MERMAID_COMMENT_LINE_RE.match(line)]
    else:
        lines = [line for line in body if not line.lstrip().startswith("%%")]
    for line in lines:
        tag = _html_tag(line)
        if tag is not None:
            return (MERMAID_GATE_HTML, tag)
    # Le direttive `@{ … }` possono occupare più righe: si guarda il corpo
    # intero, senza i commenti.
    # Unione dei tre controlli, mai uno scambio: basta che uno segnali.
    for block in _mermaid_shape_blocks("\n".join(lines)):
        bad_key = _mermaid_shape_violation(block)
        if bad_key is not None:
            return (MERMAID_GATE_RESOURCE, bad_key)
        resource_key = _MERMAID_SHAPE_RESOURCE_KEY_RE.search(block)
        if resource_key is not None:
            return (MERMAID_GATE_RESOURCE, resource_key.group(0).strip())
        url = _MERMAID_SHAPE_URL_RE.search(block)
        if url is not None:
            return (MERMAID_GATE_RESOURCE, url.group(0))
    statement = _mermaid_url_statement(kind, lines)
    if statement is not None:
        return (MERMAID_GATE_RESOURCE, statement)
    return ("", "")


class MermaidRenderer:
    """Gate statico D8 al salvataggio (A15) e pre-render con un solo
    Chromium per lezione. Il parse JS resta nel batch del validatore;
    l'SVG non passa da `normalize_svg` (byte-identico a oggi)."""

    fmt = "mermaid"

    def available(self) -> bool:
        return True

    def sanitize(self, content: str) -> str:
        return _sanitize_mermaid_code(_CONTROL_CHARS_RE.sub("", content or ""))

    def validate(self, content: str, *, deep: bool = False) -> tuple[bool, str]:
        code = self.sanitize(content)
        outcome, detail = mermaid_static_gate(code)
        if outcome == MERMAID_GATE_EMPTY:
            return (False, "sorgente Mermaid vuoto")
        if outcome == MERMAID_GATE_TYPE:
            return (
                False,
                f"{MERMAID_TYPE_NOT_ALLOWED}: tipo `{detail}` non ammesso; tipi consentiti: "
                + ", ".join(MERMAID_ALLOWED_TYPES),
            )
        if outcome == MERMAID_GATE_INIT:
            return (
                False,
                f"{MERMAID_TYPE_NOT_ALLOWED}: {detail} (il tema è imposto dal renderer)",
            )
        if outcome == MERMAID_GATE_RESOURCE:
            # Il dettaglio finisce con `:` quando viene da una shape
            # (chiave non ammessa o URL), altrimenti è la parola chiave di
            # uno statement che porta un URL o un'icona nella figura.
            if detail.endswith(":"):
                ammesse = ", ".join(f"`{k}`" for k in sorted(MERMAID_SHAPE_KEYS))
                return (
                    False,
                    f"{MERMAID_TYPE_NOT_ALLOWED}: risorsa esterna non ammessa nella shape "
                    f"`@{{ … }}` (`{detail}`); le figure non caricano file né URL e nelle "
                    f"shape sono ammesse solo le chiavi {ammesse}",
                )
            return (
                False,
                f"{MERMAID_TYPE_NOT_ALLOWED}: lo statement `{detail}` non è ammesso "
                "(porta un URL o un'icona nella figura); le figure non caricano "
                "file né URL",
            )
        if outcome == MERMAID_GATE_HTML:
            kind = mermaid_declared_type(code)
            hint = ""
            if _MERMAID_TYPE_ALIASES.get(kind, kind) == "classDiagram":
                hint = "; per i tipi generici usa `List~int~`, non `List<int>`"
            return (
                False,
                f"{MERMAID_TYPE_NOT_ALLOWED}: HTML nelle label non ammesso ({detail[:40]}){hint}",
            )
        # Soglie editoriali (D13) DOPO il gate statico, sul sorgente: qui e
        # non in `_mermaid_gate_once`, che gira su due viste dello stesso
        # sorgente. Gli incroci di una figura Mermaid si misurano solo nel
        # pre-render (export): il gate al salvataggio non ha l'SVG.
        violations = check_graph_rules(self.fmt, code)
        if violations:
            return (False, format_graph_violations(violations))
        if deep:
            svg = self.render_svg(code)
            if svg is None:
                return (False, "mermaid: render fallito (parse o Chromium non disponibile)")
        return (True, "")

    def render_svg(self, content: str, *, asset_id: str = "") -> str | None:
        """Fuori dal percorso di produzione (un Chromium per chiamata):
        l'export usa il batch."""
        return self.render_svg_batch([content], asset_ids=[asset_id])[0]

    def render_svg_batch(self, contents: list[str], *, asset_ids: list[str]) -> list[str | None]:
        """Proiezione `.svg` di `render_figure_batch`."""
        return [
            fig.svg if fig is not None else None
            for fig in self.render_figure_batch(contents, asset_ids=asset_ids)
        ]

    def render_figure_batch(
        self, contents: list[str], *, asset_ids: list[str]
    ) -> list[RenderedFigure | None]:
        """Batch con le metriche misurate nella pagina del pre-render
        (`_prerender_mermaid_batch_sync`, simbolo di modulo patchabile).
        Se la misura manca, le metriche vengono dagli attributi/regola
        radice con un warning: mai un fallback silenzioso. La geometria
        (incroci, testo fuori dalla tela) arriva dalla stessa pagina e
        passa da `with_geometry`."""
        codes = [self.sanitize(c) for c in contents]
        rendered: list[MermaidPrerender | None] = _prerender_mermaid_batch_sync(codes)
        out: list[RenderedFigure | None] = []
        for item, asset_id in zip(rendered, asset_ids, strict=True):
            if item is None or not item.svg:
                out.append(None)
                continue
            # Il post-processing è già applicato dal pre-render ed è
            # idempotente: qui rende esplicito il contratto del registro.
            svg = _join_mermaid_text_newlines(_strip_mermaid_max_width(item.svg))
            ref = _svg_external_ref(svg)
            if ref is not None:
                _render_failed(self.fmt, asset_id, f"risorsa esterna nell'SVG: {ref}")
                out.append(None)
                continue
            metrics = item.metrics
            if metrics is None:
                metrics = svg_base_font_px(svg)
                log.warning(
                    "mermaid_font_measure_missing", asset_id=asset_id, source=metrics.source
                )
            elif metrics.font_px_min is not None:
                # `getComputedStyle` misura in unità utente: px naturali
                # solo attraverso `px_per_unit` (1.0 con radice `width="100%"`).
                box = svg_intrinsic_box(svg)
                ppu = box.px_per_unit if box is not None else 1.0
                if ppu != 1.0:
                    median = metrics.font_px_median
                    metrics = SvgMetrics(
                        metrics.font_px_min * ppu,
                        median * ppu if median is not None else None,
                        metrics.text_count,
                        metrics.source,
                    )
            # Geometria misurata nella stessa pagina (D14): gli incroci oltre
            # soglia sono un warning e una voce del report, mai un rifiuto
            # (come per DOT in `validate(deep=True)`).
            metrics = with_geometry(
                metrics, getattr(item, "geometry", None), fmt=self.fmt, asset_id=asset_id
            )
            out.append(RenderedFigure(svg=svg, metrics=metrics))
        return out

    def extract_translatable(self, content: str) -> dict[str, str]:
        return {"": content} if (content or "").strip() else {}

    def apply_translations(self, content: str, tr: Mapping[str, str]) -> str:
        return tr.get("", content)


# ---------------------------------------------------------------------------
# Vega-Lite — schema JSON v6 + regole D5, vl-convert nel processo figlio
# ---------------------------------------------------------------------------


def _vegalite_schema_path() -> Path | None:
    spec = importlib.util.find_spec("altair")
    if spec is None or not spec.submodule_search_locations:
        return None
    path = Path(next(iter(spec.submodule_search_locations)))
    path = path / "vegalite" / "v6" / "schema" / "vega-lite-schema.json"
    return path if path.is_file() else None


@lru_cache(maxsize=1)
def _vegalite_validator() -> Any:
    """`Draft7Validator` sullo schema di Vega-Lite v6 letto dal wheel di
    altair, senza `import altair`. Costo misurato: lettura 7 ms, init
    0,02 ms, 0,5-1 ms per spec."""
    from jsonschema import Draft7Validator

    path = _vegalite_schema_path()
    if path is None:
        raise RuntimeError("schema Vega-Lite non trovato (pacchetto altair assente)")
    return Draft7Validator(json.loads(path.read_text(encoding="utf-8")))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"chiave duplicata: {key!r}")
        out[key] = value
    return out


def _parse_vegalite(content: str) -> tuple[dict[str, Any] | None, str]:
    """`(spec, "")` oppure `(None, errore)`: lunghezza, JSON, chiavi
    duplicate, oggetto radice, annidamento ≤ `MAX_NESTING`. Punto comune
    di validazione, render e localizzazione: tutto ciò che ricorre sulla
    spec (jsonschema, regole D5, visita dei campi testuali, `json.dumps`,
    pickle verso il figlio) parte da qui con l'annidamento già limitato."""
    if len(content) > VEGALITE_MAX_CHARS:
        return (None, f"spec oltre {VEGALITE_MAX_CHARS} caratteri ({len(content)})")
    if not content.strip():
        return (None, "spec vuota")
    try:
        spec = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, RecursionError) as exc:
        # JSONDecodeError è una ValueError; il decoder C solleva
        # RecursionError oltre ~1.000 livelli (`[[[[…]]]]` in 3.000 caratteri).
        return (None, f"JSON non valido: {exc}"[:_ERROR_CAP])
    if not isinstance(spec, dict):
        return (None, "la spec deve essere un oggetto JSON")
    nesting = nesting_violation(spec)
    if nesting is not None:
        return (None, nesting)
    return (spec, "")


def _vegalite_spec_for_render(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Copia della spec con `$schema` imposto se assente (vl-convert sceglie
    la versione di Vega-Lite dal `$schema`)."""
    out = dict(spec)
    out.setdefault("$schema", VEGALITE_SCHEMA_URL)
    return out


# `aria: false`: l'SVG va in `<img alt>` (PDF, slide, video) e gli
# attributi `aria-label` di Vega ripeterebbero i valori dei dati dentro i
# tag, dove la scansione di `normalize_svg` cerca `href=`/`url(`/`on*=`.
_VEGALITE_RENDER_CONFIG: dict[str, Any] = {**VEGALITE_THEME_CONFIG, "aria": False}

_VEGALITE_TARGET_ONE = "app.services.figure_compute.vegalite_render:render_svg"
_VEGALITE_TARGET_BATCH = "app.services.figure_compute.vegalite_render:render_svg_batch"

# Campi testuali localizzabili di una vista (D7): chiave = percorso dentro
# la spec, con gli indici delle liste come segmenti numerici.
_VEGALITE_TEXT_LEAVES = ("title",)
_VEGALITE_CHANNEL_TEXT = ("title", "axis.title", "legend.title", "header.title")
_VEGALITE_COMPOSITION = ("layer", "hconcat", "vconcat", "concat")


def _walk_vegalite_text(node: Any, path: list[str], out: dict[str, str]) -> None:
    if not isinstance(node, Mapping):
        return
    title = node.get("title")
    if isinstance(title, str) and title.strip():
        out[".".join([*path, "title"])] = title
    elif isinstance(title, Mapping) and isinstance(title.get("text"), str):
        out[".".join([*path, "title", "text"])] = title["text"]
    mark = node.get("mark")
    if isinstance(mark, Mapping) and isinstance(mark.get("text"), str) and mark["text"].strip():
        out[".".join([*path, "mark", "text"])] = mark["text"]
    encoding = node.get("encoding")
    if isinstance(encoding, Mapping):
        for channel, ch in encoding.items():
            if not isinstance(ch, Mapping):
                continue
            for leaf in _VEGALITE_CHANNEL_TEXT:
                parts = leaf.split(".")
                cur: Any = ch
                for part in parts:
                    cur = cur.get(part) if isinstance(cur, Mapping) else None
                if isinstance(cur, str) and cur.strip():
                    out[".".join([*path, "encoding", str(channel), *parts])] = cur
            if channel == "text" and isinstance(ch.get("value"), str) and ch["value"].strip():
                out[".".join([*path, "encoding", "text", "value"])] = ch["value"]
    for key in _VEGALITE_COMPOSITION:
        items = node.get(key)
        if isinstance(items, list):
            for i, item in enumerate(items):
                _walk_vegalite_text(item, [*path, key, str(i)], out)
    if "spec" in node:
        _walk_vegalite_text(node["spec"], [*path, "spec"], out)


def _set_by_path(root: Any, path: str, value: str) -> None:
    parts = path.split(".")
    cur: Any = root
    for part in parts[:-1]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(path)
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    elif isinstance(cur, dict) and last in cur:
        cur[last] = value
    else:
        raise KeyError(path)


class VegaLiteRenderer:
    """Validazione: lunghezza ≤ 4.000, JSON senza chiavi duplicate, schema
    JSON v6 (`Draft7Validator`, `best_match`), regole D5 ed euristica del
    criterio 10; profonda: anche la prova di render. Render: `$schema`
    imposto, `config` del tema, `vl_convert` in `run_isolated`, poi
    `normalize_svg`."""

    fmt = "vegalite"

    def available(self) -> bool:
        return (
            importlib.util.find_spec("vl_convert") is not None
            and importlib.util.find_spec("jsonschema") is not None
            and _vegalite_schema_path() is not None
        )

    def sanitize(self, content: str) -> str:
        return _strip_fence_and_control(content)

    def validate(self, content: str, *, deep: bool = False) -> tuple[bool, str]:
        sanitized = self.sanitize(content)
        spec, err = _parse_vegalite(sanitized)
        if spec is None:
            return (False, err)
        try:
            validator = _vegalite_validator()
        except RuntimeError as exc:
            return (False, str(exc))
        from jsonschema.exceptions import best_match

        try:
            error = best_match(validator.iter_errors(spec))
            if error is not None:
                where = ".".join(str(p) for p in error.absolute_path) or "$"
                return (False, f"{where}: {error.message}"[:_ERROR_CAP])
            violations = check_vegalite_rules(spec)
        except RecursionError:
            # Difesa in profondità: `_parse_vegalite` limita già
            # l'annidamento, qui nessuna eccezione deve attraversare il gate.
            return (False, "spec troppo annidata per la validazione")
        if violations:
            return (False, "; ".join(violations)[:_ERROR_CAP])
        if deep:
            try:
                svg = self._render_or_raise(spec)
            except (FigureTimeoutError, FigureComputeError, SvgRejectedError) as exc:
                return (False, f"render: {exc}"[:_ERROR_CAP])
            _cache_put(cache_key(self.fmt, sanitized), svg)
        return (True, "")

    def _render_or_raise(self, spec: Mapping[str, Any]) -> str:
        settings = get_settings()
        payload = {"spec": _vegalite_spec_for_render(spec), "config": _VEGALITE_RENDER_CONFIG}
        raw = run_isolated(
            _VEGALITE_TARGET_ONE, payload, timeout=settings.figure_render_timeout_seconds
        )
        return normalize_svg(str(raw), max_bytes=settings.figure_svg_max_bytes).svg

    def render_svg(self, content: str, *, asset_id: str = "") -> str | None:
        sanitized = self.sanitize(content)
        key = cache_key(self.fmt, sanitized)
        hit = _cache_get(key)
        if hit is not None:
            return hit
        spec, err = _parse_vegalite(sanitized)
        if spec is None:
            _render_failed(self.fmt, asset_id, err)
            return None
        try:
            svg = self._render_or_raise(spec)
        except (FigureTimeoutError, FigureComputeError, SvgRejectedError) as exc:
            _cache_negative(key)
            _render_failed(self.fmt, asset_id, str(exc))
            return None
        _cache_put(key, svg)
        return svg

    def render_svg_batch(self, contents: list[str], *, asset_ids: list[str]) -> list[str | None]:
        """Un solo processo figlio per il batch: `None` per la spec che
        fallisce (il motivo lo dà `validate(deep=True)` sulla singola)."""
        settings = get_settings()
        out: list[str | None] = [None] * len(contents)
        specs: list[dict[str, Any]] = []
        positions: list[int] = []
        for i, content in enumerate(contents):
            spec, err = _parse_vegalite(self.sanitize(content))
            if spec is None:
                _render_failed(self.fmt, asset_ids[i], err)
                continue
            specs.append(_vegalite_spec_for_render(spec))
            positions.append(i)
        if not specs:
            return out
        try:
            raws = run_isolated(
                _VEGALITE_TARGET_BATCH,
                {"specs": specs, "config": _VEGALITE_RENDER_CONFIG},
                timeout=settings.figure_render_timeout_seconds,
            )
        except (FigureTimeoutError, FigureComputeError) as exc:
            for i in positions:
                _render_failed(self.fmt, asset_ids[i], str(exc))
            return out
        for i, raw in zip(positions, raws, strict=True):
            if not raw:
                _render_failed(self.fmt, asset_ids[i], "vl-convert: render fallito")
                continue
            try:
                out[i] = normalize_svg(str(raw), max_bytes=settings.figure_svg_max_bytes).svg
            except SvgRejectedError as exc:
                _render_failed(self.fmt, asset_ids[i], str(exc))
        return out

    def extract_translatable(self, content: str) -> dict[str, str]:
        spec, _err = _parse_vegalite(self.sanitize(content))
        if spec is None:
            return {}
        out: dict[str, str] = {}
        _walk_vegalite_text(spec, [], out)
        return out

    def apply_translations(self, content: str, tr: Mapping[str, str]) -> str:
        source = self.sanitize(content)
        spec, _err = _parse_vegalite(source)
        if spec is None or not tr:
            return content
        applied: dict[str, str] = {}
        for path, value in tr.items():
            try:
                _set_by_path(spec, path, value)
            except (KeyError, IndexError, ValueError):
                continue
            applied[path] = value
        # Sostituzione chirurgica delle sole stringhe tradotte: la spec
        # NON viene riserializzata, quindi la formattazione del docente
        # sopravvive e il round-trip con traduzioni identiche è
        # byte-identico (I18N-3).
        out = replace_json_strings(source, applied)
        if out is None or (len(out) > VEGALITE_MAX_CHARS >= len(source)):
            # Sorgente non scandibile, oppure la formattazione conservata
            # farebbe superare il tetto D5 a una spec che lo rispettava:
            # la forma compatta è la sola che recupera quel margine.
            return json.dumps(spec, ensure_ascii=False, separators=(",", ":"))
        return out


# ---------------------------------------------------------------------------
# Graphviz DOT — binario `dot` senza shell, cwd vuoto, env minimale
# ---------------------------------------------------------------------------

_DOT_HEADER_RE = re.compile(r"^\s*(?:strict\s+)?(?:di)?graph\b", re.IGNORECASE)
# Attributi che fanno leggere file locali o risorse esterne a `dot`, con i
# composti degli archi e delle label (`labelURL`, `headhref`, `tailtarget`,
# `edgeURL`, ...). `dot` apre il file indicato e il suo stderr distingue
# un path esistente da uno assente. Il confronto è sul NOME dell'attributo
# come lo vede lo scanner di Graphviz (`_dot_forbidden_attribute`): in DOT
# un nome può essere quotato (`"image"=`), concatenato (`"ima"+"ge"=`),
# spezzato da una continuazione di riga (`"ima\⏎ge"=`), separato dall'`=`
# da un commento (`image/**/=`) o scritto come stringa HTML (`<image>=`);
# verificato con dot 15.1.1: tutte le forme aprono il file. Una regex
# `\bimage\s*=` sul sorgente grezzo non le vede. Confronto senza
# distinzione di maiuscole per prudenza (`dot` è case-sensitive).
_DOT_FORBIDDEN_BASE = (
    "image",
    "shapefile",
    "imagepath",
    "fontpath",
    "stylesheet",
    "url",
    "href",
    "target",
)
_DOT_FORBIDDEN_NAMES = frozenset(
    f"{prefix}{base}"
    for prefix in ("", "label", "head", "tail", "edge")
    for base in _DOT_FORBIDDEN_BASE
)
# `SRC` dell'`<IMG>` nelle label HTML-like (`label=<<IMG SRC="…"/>>`).
_DOT_HTML_IMG_SRC_RE = re.compile(r"<\s*img\b[^>]*\bsrc\s*=", re.IGNORECASE)
# Identificatore o numerale DOT (lettere, `_`, caratteri non ASCII).
_DOT_ID_RE = re.compile(
    r"[A-Za-z_\x80-\uffff][A-Za-z_0-9\x80-\uffff]*|-?(?:\.[0-9]+|[0-9]+\.?[0-9]*)"
)
_DOT_EDGE_RE = re.compile(r"->|--")
_DOT_BLOCK_RES = {
    "graph": re.compile(r"\bgraph\s*\["),
    "node": re.compile(r"\bnode\s*\["),
    "edge": re.compile(r"\bedge\s*\["),
}
_DOT_LABEL_RE = re.compile(
    r"\b(label|xlabel|headlabel|taillabel)\s*=\s*\"((?:[^\"\\]|\\.)*)\"", re.IGNORECASE
)
# Escape delle virgolette dentro il corpo di una label (D7): la forma
# letta (`\\"`) e quella da riscrivere (`"` non ancora escapato).
_DOT_ESCAPED_QUOTE_RE = re.compile(r'(?<!\\)\\"')
_DOT_BARE_QUOTE_RE = re.compile(r'(?<!\\)"')
# Ambiente minimale del figlio `dot`: oltre a PATH e LANG/LC_ALL del piano,
# le chiavi che fontconfig usa per trovare la propria configurazione e la
# cache dei font (HOME/XDG_CACHE_HOME: senza, nel container rescandisce le
# famiglie a ogni run e scrive «No writable cache directories» su stderr;
# FONTCONFIG_*: configurazione esplicita, se presente). Nessuna variabile
# che influenzi l'esecuzione (LD_*, DYLD_*, GV*), nessuna shell.
_DOT_ENV_KEYS = ("PATH", "HOME", "FONTCONFIG_PATH", "FONTCONFIG_FILE", "XDG_CACHE_HOME")

_dot_missing_logged = False


class _DotError(RuntimeError):
    """Esito negativo di `dot` (returncode, timeout, esecuzione)."""


def _dot_binary() -> str | None:
    return shutil.which(get_settings().graphviz_dot_path or "dot")


def _dot_read_qstring(src: str, start: int) -> tuple[str, int]:
    """Legge la stringa quotata che inizia in `src[start] == '"'` come lo
    scanner di Graphviz (`\\"` → `"`, `\\\\` conservato, `\\`+newline
    ignorato, ogni altro `\\` conservato). Ritorna `(testo, indice dopo la
    virgoletta di chiusura)`; una stringa non chiusa termina alla fine."""
    out: list[str] = []
    i, n = start + 1, len(src)
    while i < n:
        ch = src[i]
        if ch == "\\" and i + 1 < n:
            nxt = src[i + 1]
            if nxt == "\n":
                i += 2
                continue
            if nxt == "\r" and src.startswith("\r\n", i + 1):
                i += 3
                continue
            if nxt == '"':
                out.append('"')
                i += 2
                continue
            if nxt == "\\":
                # Coppia conservata (come lo scanner reale) e consumata
                # INTERA: leggerne solo il primo carattere farebbe passare
                # il `\"` successivo per un apice escapato e la stringa
                # inghiottirebbe l'attributo che segue.
                out.append("\\\\")
                i += 2
                continue
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            return ("".join(out), i + 1)
        out.append(ch)
        i += 1
    return ("".join(out), n)


def _dot_skip_blank(src: str, i: int) -> int:
    """Indice del primo carattere significativo da `i`: salta spazi,
    commenti `/* */`, `//` e `#`.

    Il `#` è documentato come riga di preprocessore, ma lo scanner di
    Graphviz 15.1.1 lo tratta come commento fino a fine riga OVUNQUE, non
    solo a colonna 0: verificato che `digraph { a # -> b⏎; c }` non
    produce archi e che `digraph { a# [image="…"] ⏎ b }` non apre alcun
    file. Limitarlo alla colonna 0 lasciava desincronizzare il
    tokenizzatore da una virgoletta dentro un commento a metà riga
    (`digraph { # "⏎ x [image="…"] }`, SEC-2). Dentro le stringhe non
    arriva mai: `_dot_tokens` le legge con `_dot_read_string_atom`."""
    n = len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            i += 1
        elif src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif src.startswith("//", i) or ch == "#":
            end = src.find("\n", i)
            i = n if end < 0 else end
        else:
            break
    return i


def _dot_read_string_atom(src: str, i: int) -> tuple[str, str, int] | None:
    """`(tipo, testo, indice successivo)` per la stringa che inizia in
    `src[i]`: `str` per una stringa quotata (decodificata), `html` per una
    stringa `<…>`. `None` se in `i` non inizia una stringa."""
    if src[i] == '"':
        text, j = _dot_read_qstring(src, i)
        return ("str", text, j)
    if src[i] == "<":
        depth, j, n = 0, i, len(src)
        while j < n:
            if src[j] == "<":
                depth += 1
            elif src[j] == ">":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        return ("html", src[i + 1 : j], j + 1)
    return None


def _dot_tokens(src: str) -> Iterator[tuple[str, str]]:
    """Token significativi del sorgente DOT come `(tipo, testo)`: `id`
    (identificatore o numerale), `str` (stringa quotata decodificata, con
    le concatenazioni `"a" + "b"` già unite), `html` (contenuto della
    stringa `<…>`), `op` (un carattere). Nessun parse della grammatica:
    basta per sapere quale nome precede un `=`."""
    i, n = 0, len(src)
    while True:
        i = _dot_skip_blank(src, i)
        if i >= n:
            return
        ch = src[i]
        atom = _dot_read_string_atom(src, i) if ch in '"<' else None
        if atom is not None:
            kind, text, i = atom
            # Concatenazione: lo scanner di Graphviz unisce con `+` ogni
            # stringa, quotata o HTML, in un solo ID — `<ima>+"ge"=` è
            # `image=` tanto quanto `"ima"+"ge"=`.
            while True:
                j = _dot_skip_blank(src, i)
                if j < n and src[j] == "+":
                    k = _dot_skip_blank(src, j + 1)
                    more = _dot_read_string_atom(src, k) if k < n else None
                    if more is not None:
                        if more[0] == "html":
                            kind = "html"
                        text += more[1]
                        i = more[2]
                        continue
                break
            yield (kind, text)
            continue
        m = _DOT_ID_RE.match(src, i)
        if m:
            yield ("id", m.group(0))
            i = m.end()
            continue
        yield ("op", ch)
        i += 1


def _dot_forbidden_attribute(src: str) -> str | None:
    """Nome (come scritto) del primo attributo che fa leggere a `dot` un
    file o una risorsa esterna, oppure `SRC` per un `<IMG SRC=…>` in una
    label HTML-like; `None` se il sorgente è pulito. Guarda solo i nomi
    seguiti da `=`: `label="vedi image=1"` è testo e passa."""
    prev: tuple[str, str] | None = None
    for token in _dot_tokens(src):
        kind, text = token
        if kind == "op" and text == "=" and prev is not None and prev[0] != "op":
            name = prev[1].strip()
            if name.lower() in _DOT_FORBIDDEN_NAMES:
                return name
        if kind == "html" and _DOT_HTML_IMG_SRC_RE.search(text):
            return "SRC"
        prev = token
    return None


def _dot_label_unescape(raw: str) -> str:
    """Corpo di una label DOT nella forma che va al traduttore: torna `"`
    solo l'apice escapato. Le sequenze che `dot` interpreta (`\\n` a capo,
    `\\l` allineamento) e i backslash raddoppiati restano come sono, perché
    il modello deve riconsegnarle intatte."""
    return _DOT_ESCAPED_QUOTE_RE.sub('"', raw)


def _dot_label_escape(value: str) -> str:
    """Inverso di `_dot_label_unescape`: escapa le sole virgolette non già
    escapate, mai i backslash (raddoppiarli trasformerebbe l'a capo `\\n`
    in un `\\n` letterale a ogni passata di localizzazione). Un backslash
    finale isolato viene raddoppiato: da solo escaperebbe la virgoletta di
    chiusura."""
    out = _DOT_BARE_QUOTE_RE.sub('\\"', value)
    if (len(out) - len(out.rstrip("\\"))) % 2:
        out += "\\"
    return out


def _dot_with_theme(source: str) -> str:
    """Inserisce `graph/node/edge [...]` del tema dopo la `{` di apertura,
    saltando i blocchi che il sorgente definisce già."""
    brace = source.find("{")
    if brace < 0:
        return source
    skip = frozenset(k for k, rx in _DOT_BLOCK_RES.items() if rx.search(source))
    prelude = dot_defaults_prelude(skip=skip)
    if not prelude:
        return source
    return source[: brace + 1] + "\n" + prelude + "\n" + source[brace + 1 :]


def _dot_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _DOT_ENV_KEYS if k in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["LANG"] = "C.UTF-8"
    env["LC_ALL"] = "C.UTF-8"
    return env


def _run_dot(source: str) -> str:
    """UN solo `subprocess.run` di `dot -Tsvg`; ritorna l'SVG grezzo o
    solleva `_DotError` con il motivo."""
    binary = _dot_binary()
    if binary is None:
        raise _DotError("dot_unavailable")
    timeout = get_settings().figure_render_timeout_seconds
    with tempfile.TemporaryDirectory(prefix="a4u-dot-") as cwd:
        try:
            proc = subprocess.run(  # argv esplicito, nessuna shell
                [binary, "-Tsvg", "-Gcharset=utf8"],
                input=source.encode("utf-8"),
                capture_output=True,
                timeout=timeout,
                check=False,
                cwd=cwd,
                env=_dot_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise _DotError("dot_timeout") from exc
        except OSError as exc:
            raise _DotError(f"dot_exec_failed: {exc}") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise _DotError((stderr or f"dot: exit {proc.returncode}")[:_ERROR_CAP])
    return proc.stdout.decode("utf-8", errors="replace")


class DotRenderer:
    """Validazione statica (lunghezza, intestazione, attributi che leggono
    file, numero di archi come tetto di risorsa, poi le soglie editoriali
    di `graph_rules`) e, profonda, la prova di render con il tema iniettato;
    `dot` assente → `dot_unavailable`, non fixable. Ogni SVG reso porta la
    geometria nelle metriche (`measure`, D14): gli incroci oltre
    `MAX_EDGE_CROSSINGS` sono un warning e una voce del report, mai un
    rifiuto, anche in `validate(deep=True)`. La misura è in Python nello
    stesso thread del render, dentro il timeout del batch: il suo costo è
    limitato per costruzione (docstring di `figure_geometry`), con un tetto
    di lavoro per figura e uno per batch (`MAX_MEASURE_WORK`,
    `MAX_BATCH_MEASURE_WORK`), e oltre li salta, mai la figura."""

    fmt = "dot"

    def available(self) -> bool:
        global _dot_missing_logged
        found = _dot_binary() is not None
        if not found and not _dot_missing_logged:
            _dot_missing_logged = True
            log.error(
                "graphviz_dot_missing",
                path=get_settings().graphviz_dot_path or "dot",
                hint="installare graphviz o impostare GRAPHVIZ_DOT_PATH",
            )
        return found

    def sanitize(self, content: str) -> str:
        return _strip_fence_and_control(content)

    def validate(self, content: str, *, deep: bool = False) -> tuple[bool, str]:
        if not self.available():
            return (False, "dot_unavailable")
        src = self.sanitize(content)
        max_chars = int(get_settings().figure_dot_max_chars)
        if not src:
            return (False, "sorgente DOT vuoto")
        if len(src) > max_chars:
            return (False, f"sorgente DOT oltre {max_chars} caratteri ({len(src)})")
        if not _DOT_HEADER_RE.match(src):
            return (False, "il sorgente deve iniziare con `graph`, `digraph` o `strict`")
        forbidden = _dot_forbidden_attribute(src)
        if forbidden is not None:
            return (False, f"attributo non ammesso: `{forbidden}=` (risorse esterne o file)")
        edges = len(_DOT_EDGE_RE.findall(src))
        if edges > DOT_MAX_EDGES:
            return (False, f"troppi archi ({edges} > {DOT_MAX_EDGES})")
        violations = check_graph_rules(self.fmt, src)
        if violations:
            return (False, format_graph_violations(violations))
        if deep:
            try:
                svg = self._render_or_raise(src)
            except (_DotError, SvgRejectedError) as exc:
                return (False, str(exc)[:_ERROR_CAP])
            # Geometria misurata una volta e messa in cache con la figura:
            # gli incroci oltre soglia producono `figure_geometry_defects` e
            # la voce del report all'export, non un rifiuto (un grafo a
            # strati completi ne ha per costruzione e il fix AI non può
            # toglierli: il worker rigenererebbe la lezione).
            figure = _figure_from_svg(self, svg, asset_id=_asset_context.get())
            _cache_put(cache_key(self.fmt, src), figure)
        return (True, "")

    def measure(self, svg: str, *, work_left: int | None = None) -> GeometryReport:
        """Geometria dell'SVG reso: incroci arco × arco e i quattro difetti
        di lettura (`figure_geometry.measure_dot_svg`), entro il tetto per
        figura e, se dato, il residuo `work_left` del batch."""
        return figure_geometry.measure_dot_svg(svg, work_left=work_left)

    def _render_or_raise(self, sanitized: str) -> str:
        raw = _run_dot(_dot_with_theme(sanitized))
        return normalize_svg(raw, max_bytes=get_settings().figure_svg_max_bytes).svg

    def render_figure(self, content: str, *, asset_id: str = "") -> RenderedFigure | None:
        """SVG con metriche e geometria; un hit della cache torna tale e
        quale (la geometria è stata misurata al primo render)."""
        return self._render_figure(content, asset_id=asset_id, work_left=None)[0]

    def _render_figure(
        self, content: str, *, asset_id: str, work_left: int | None
    ) -> tuple[RenderedFigure | None, int]:
        """`render_figure` con il residuo di lavoro del batch; ritorna anche
        il lavoro speso dalla misura (0 per un hit o un fallimento)."""
        sanitized = self.sanitize(content)
        key = cache_key(self.fmt, sanitized)
        hit = _cache_get_figure(key)
        if hit is not None:
            return hit, 0
        try:
            svg = self._render_or_raise(sanitized)
        except (_DotError, SvgRejectedError) as exc:
            _cache_negative(key)
            _render_failed(self.fmt, asset_id, str(exc))
            return None, 0
        fig, spent = _measured_figure(self, svg, asset_id=asset_id, work_left=work_left)
        _cache_put(key, fig)
        return fig, spent

    def render_svg(self, content: str, *, asset_id: str = "") -> str | None:
        fig = self.render_figure(content, asset_id=asset_id)
        return fig.svg if fig is not None else None

    def render_figure_batch(
        self, contents: list[str], *, asset_ids: list[str]
    ) -> list[RenderedFigure | None]:
        """Le figure in ordine, con un solo tetto di lavoro della misura per
        l'intero batch (ogni figura sottrae il lavoro eseguito, anche se la
        sua misura è stata saltata): esaurito, le figure seguenti escono
        senza geometria (`batch_work_cap`), mai senza SVG. Un'eccezione
        imprevista costa la sola figura che l'ha sollevata (`None` e
        `figure_render_failed`), non le altre del batch: senza questo
        confine `render_figure_map` le perdeva tutte."""
        budget = figure_geometry.MAX_BATCH_MEASURE_WORK
        out: list[RenderedFigure | None] = []
        for content, aid in zip(contents, asset_ids, strict=True):
            try:
                fig, spent = self._render_figure(content, asset_id=aid, work_left=budget)
            except Exception as exc:  # difesa in profondità, per figura
                _render_failed(self.fmt, aid, f"{type(exc).__name__}: {exc}")
                fig, spent = None, 0
            budget -= spent
            out.append(fig)
        return out

    def render_svg_batch(self, contents: list[str], *, asset_ids: list[str]) -> list[str | None]:
        return [
            fig.svg if fig is not None else None
            for fig in self.render_figure_batch(contents, asset_ids=asset_ids)
        ]

    def extract_translatable(self, content: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for n, m in enumerate(_DOT_LABEL_RE.finditer(self.sanitize(content))):
            value = _dot_label_unescape(m.group(2))
            if any(ch.isalpha() for ch in value):
                out[f"{m.group(1).lower()}.{n}"] = value
        return out

    def apply_translations(self, content: str, tr: Mapping[str, str]) -> str:
        if not tr:
            return content
        counter = iter(range(10**9))

        def _replace(m: re.Match[str]) -> str:
            n = next(counter)
            key = f"{m.group(1).lower()}.{n}"
            if key not in tr:
                return m.group(0)
            return f'{m.group(1)}="{_dot_label_escape(tr[key])}"'

        return _DOT_LABEL_RE.sub(_replace, content)


# ---------------------------------------------------------------------------
# function — Pydantic + AST offline, numerico/matplotlib in thread, sympy nel figlio
# ---------------------------------------------------------------------------


# Avvertenza del motore per un'espressione senza alcun campione finito
# (`sqrt(x)` su [-2, -1], `x/0`, `log(x)` su un dominio negativo): la
# figura esce senza rami e `validate(deep=True)` la rifiuta.
_EXPRESSION_UNDEFINED_RE = re.compile(r"expression_\d+_undefined")


class FunctionRenderer:
    """Validazione: `parse_function_spec` (struttura Pydantic + controlli
    semantici + passo 1 dell'AST, senza sympy); profonda: anche il render
    completo, il cui SVG entra in cache. Render: `render_function_sync`
    del motore (numerico, simbolico nel figlio con timeout, disegno,
    `normalize_svg`). L'SVG non dipende dalla lingua: la didascalia
    calcolata è composta fuori dall'SVG (`function_caption`).

    Due cache con traffico diverso: quella degli SVG (questo modulo) e
    quella dei risultati del motore (`figure_function_service`), da cui
    `computed_caption` legge la coda della didascalia (D9). Le anteprime
    dell'editor (`render_function`) riempiono SOLO la seconda, quindi
    possono espellerne il risultato di una figura il cui SVG è ancora in
    cache: un hit della cache SVG vale solo se anche il risultato è
    presente (`result_cached`), altrimenti `render_svg` ricalcola e
    ripopola entrambe."""

    fmt = "function"

    def available(self) -> bool:
        return figure_function_service.dependencies_available()

    def sanitize(self, content: str) -> str:
        return _strip_fence_and_control(content)

    def validate(self, content: str, *, deep: bool = False) -> tuple[bool, str]:
        sanitized = self.sanitize(content)
        spec, issues = parse_function_spec(sanitized)
        if spec is None:
            return (False, f"{FUNCTION_SPEC_INVALID}: {format_issues(issues)}"[:_ERROR_CAP])
        if deep:
            try:
                result = figure_function_service.render_function_sync(spec, language=None)
            except FunctionRenderError as exc:
                return (False, f"render: {exc}"[:_ERROR_CAP])
            undefined = [w for w in result.warnings if _EXPRESSION_UNDEFINED_RE.fullmatch(w)]
            if undefined:
                # Nessun campione finito: la figura esiste ma è senza curva
                # (dominio incompatibile con quello naturale). Il worker la
                # manda al fix AI invece di pubblicare una figura vuota.
                return (
                    False,
                    "espressione indefinita su tutto il dominio: "
                    "correggi `domain` oppure l'espressione",
                )
            _cache_put(cache_key(self.fmt, sanitized), result.svg)
        return (True, "")

    def result_cached(self, sanitized: str) -> bool:
        """Il risultato del motore (SVG e `computed`) della spec è nella
        cache dei risultati: un hit della cache SVG è completo."""
        spec, _issues = parse_function_spec(sanitized)
        if spec is None:
            return False
        return figure_function_service.cached_result(spec, language=None) is not None

    def render_svg(self, content: str, *, asset_id: str = "") -> str | None:
        sanitized = self.sanitize(content)
        key = cache_key(self.fmt, sanitized)
        spec, issues = parse_function_spec(sanitized)
        if spec is None:
            _render_failed(self.fmt, asset_id, format_issues(issues))
            return None
        hit = _cache_get(key)
        if hit is not None and figure_function_service.cached_result(spec, language=None):
            return hit
        # SVG assente, oppure presente ma con il risultato del motore
        # espulso dalla cache dei risultati: si ricalcola (nel thread di
        # render, mai in quello dell'HTML) e `render_function_sync`
        # ripopola la cache dei risultati; l'SVG è byte-identico.
        try:
            result = figure_function_service.render_function_sync(spec, language=None)
        except FunctionRenderError as exc:
            _cache_negative(key)
            _render_failed(self.fmt, asset_id, str(exc))
            return None
        _cache_put(key, result.svg)
        return result.svg

    def render_svg_batch(self, contents: list[str], *, asset_ids: list[str]) -> list[str | None]:
        return [
            self.render_svg(c, asset_id=aid) for c, aid in zip(contents, asset_ids, strict=True)
        ]

    def extract_translatable(self, content: str) -> dict[str, str]:
        return figure_function_service.extract_translatable(self.sanitize(content))

    def apply_translations(self, content: str, tr: Mapping[str, str]) -> str:
        # Stessa sanificazione di `extract_translatable`: un contenuto con
        # fence ```json riceve le traduzioni sul JSON, non resta invariato.
        return figure_function_service.apply_translations(self.sanitize(content), tr)

    def computed_caption(self, content: str, *, language: str | None, asset_id: str = "") -> str:
        """Coda della didascalia calcolata (D9) nella lingua richiesta,
        letta dalla cache dei risultati del motore popolata da
        `render_svg`/`validate(deep=True)`; mai calcolata qui. Vuota se la
        spec non è valida (nessun SVG a monte: il fallback è già loggato)
        o se il risultato non è (più) in cache: quest'ultimo caso non deve
        accadere dopo `render_figure_map` (che lo ripopola, anche quando è
        chiamata dalla proiezione `render_svg_map`) ed è loggato come
        `figure_caption_missing`, così una coda persa non è silenziosa."""
        spec, _issues = parse_function_spec(self.sanitize(content))
        if spec is None:
            return ""
        hit = figure_function_service.cached_result(spec, language=language)
        if hit is None:
            log.warning(
                "figure_caption_missing",
                format=self.fmt,
                asset_id=asset_id,
                language=language,
                reason="result_not_cached",
            )
            return ""
        return hit.computed_caption


class FigureEngineBusyError(Exception):
    """Il motore della figura è occupato da un altro lavoro pesante (TeX
    in coda o estrazione Docling in corso): non è un errore del sorgente,
    quindi niente cache negativa; la resa si ritenta più tardi."""


_SVG_ROOT_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)
_UNITLESS_SIZE_RE = re.compile(r'\b(width|height)="([0-9.]+)"')


def _tikz_root_in_pt(svg: str) -> str:
    """pdftocairo scrive `width="298.74"` senza unità, ma le unità del
    viewBox sono pt: letto come px la figura usciva al 75% (testo sotto il
    minimo dichiarato dall'oracolo, Fase D). L'unità esplicita fa
    convertire a `normalize_svg` pt → px (px_per_unit = 4/3)."""
    match = _SVG_ROOT_RE.search(svg)
    if match is None:
        return svg
    root = _UNITLESS_SIZE_RE.sub(lambda m: f'{m.group(1)}="{m.group(2)}pt"', match.group(0))
    return svg[: match.start()] + root + svg[match.end() :]


def tikz_timeout_seconds() -> float:
    """Tetto di una validazione o resa `tikz` vista dal chiamante: attende
    anche la coda della sandbox (`heavy_job_lock`) prima di compilare, e il
    tetto comune (`figure_render_timeout_seconds`, 20 s) la darebbe per
    scaduta."""
    settings = get_settings()
    queue_and_compile = (
        settings.figure_tikz_queue_timeout_seconds + settings.figure_tikz_timeout_seconds
    )
    return max(float(settings.figure_render_timeout_seconds), float(queue_and_compile) + 5.0)


@dataclass(frozen=True)
class TikzPreview:
    """Esito di `TikzRenderer.preview` (anteprima dell'editor)."""

    svg: str
    font_px_min: float | None
    warnings: tuple[str, ...]
    content_hash: str
    cached: bool


class TikzPreviewError(Exception):
    """Anteprima `tikz` rifiutata; `code` è quello della risposta API."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TikzRenderer:
    """Formato `tikz` (WP6): un solo ambiente `tikzpicture` o `circuitikz`,
    compilato con XeLaTeX nella sandbox del container
    (`tikz_compile_service`), SVG di pdftocairo normalizzato.

    - validazione statica: `tikz_lexer.check` (allowlist) e script
      supportato dai font del preambolo; profonda: compilazione vera e
      oracolo geometrico (`tikz_geometry`), i cui difetti bloccano la figura
      (fix AI) salvo `strict_geometry=False` (PATCH e anteprima: avvisi);
    - `available()`: `FIGURE_TIKZ_ENABLED` e autotest della sandbox;
    - chiave di cache con `PREAMBLE_VERSION`: un cambio del preambolo
      invalida solo le figure `tikz`;
    - metriche del testo dal PDF (`font_px_min`): nell'SVG di pdftocairo i
      glifi sono tracciati, non `<text>`;
    - traduzione (D7): testi dei nodi (`\node … {testo}`, `node[…]{testo}`)
      senza matematica, con l'escape di TeX e un nuovo controllo del lexer.
    """

    fmt = "tikz"

    def available(self) -> bool:
        from app.services import tikz_compile_service

        return bool(get_settings().figure_tikz_enabled) and tikz_compile_service.available()

    def sanitize(self, content: str) -> str:
        from app.services.figure_compute.tikz_lexer import strip_comments

        return strip_comments(_strip_fence_and_control(content)).strip()

    def _key(self, sanitized: str) -> CacheKey:
        from app.services.figure_compute.tikz_preamble import PREAMBLE_VERSION

        return cache_key(self.fmt, f"{PREAMBLE_VERSION}\n{sanitized}")

    def static_check(self, content: str) -> str | None:
        """Motivo del rifiuto statico (lexer, script), None se ammesso."""
        from app.services.figure_compute import tikz_preamble
        from app.services.figure_compute.tikz_lexer import TikzSourceError, check

        src = self.sanitize(content)
        if not src:
            return "sorgente TikZ vuoto"
        try:
            check(src, max_chars=int(get_settings().figure_tikz_max_chars))
            tikz_preamble.font_for(src)
        except (TikzSourceError, tikz_preamble.TikzScriptUnsupportedError) as exc:
            return str(exc)[:_ERROR_CAP]
        return None

    def validate(
        self, content: str, *, deep: bool = False, strict_geometry: bool = True
    ) -> tuple[bool, str]:
        problem = self.static_check(content)
        if problem is not None:
            return (False, problem)
        if not deep:
            return (True, "")
        if not self.available():
            return (False, "tikz_unavailable")
        from app.services.tikz_compile_service import (
            TikzBusyError,
            TikzEngineUnavailableError,
        )

        src = self.sanitize(content)
        cached = _cache_get_figure(self._key(src))
        if cached is not None:
            defects = cached.metrics.defects if cached.metrics is not None else ()
            if strict_geometry and defects:
                return (False, "difetti geometrici: " + "; ".join(defects)[:_ERROR_CAP])
            return (True, "")
        try:
            figure = self._render_or_raise(src)
        # Coda e motore non dipendono dal sorgente: prefissi riconosciuti
        # dalla validazione di Fase 3 (nessun fix AI).
        except TikzBusyError as exc:
            return (False, f"tikz_busy: {exc}"[:_ERROR_CAP])
        except TikzEngineUnavailableError as exc:
            return (False, f"tikz_unavailable: {exc}"[:_ERROR_CAP])
        except Exception as exc:  # compilazione, SVG
            return (False, str(exc)[:_ERROR_CAP])
        _cache_put(self._key(src), figure)
        defects = figure.metrics.defects if figure.metrics is not None else ()
        if strict_geometry and defects:
            return (False, "difetti geometrici: " + "; ".join(defects)[:_ERROR_CAP])
        return (True, "")

    def _render_or_raise(self, sanitized: str) -> RenderedFigure:
        from app.services import tikz_compile_service
        from app.services.figure_compute import tikz_geometry, tikz_preamble

        document, lines = tikz_preamble.document(sanitized)
        result = tikz_compile_service.compile_document(document, preamble_lines=lines)
        svg = normalize_svg(
            _tikz_root_in_pt(result.svg), max_bytes=get_settings().figure_svg_max_bytes
        ).svg
        geometry = tikz_geometry.analyze(result.pdf, has_axis=tikz_preamble.uses_axis(sanitized))
        font_px = geometry.font_px_min
        metrics = SvgMetrics(
            font_px_min=font_px,
            font_px_median=font_px,
            text_count=1 if font_px is not None else 0,
            source="parsed" if font_px is not None else "no_text",
            defects=geometry.defects,
        )
        return RenderedFigure(svg=svg, metrics=metrics)

    def render_figure(self, content: str, *, asset_id: str = "") -> RenderedFigure | None:
        sanitized = self.sanitize(content)
        key = self._key(sanitized)
        hit = _cache_get_figure(key)
        if hit is not None:
            return hit
        if _cache_is_negative(key):
            _render_failed(self.fmt, asset_id, "negative_cache")
            return None
        if self.static_check(sanitized) is not None or not self.available():
            _render_failed(self.fmt, asset_id, "tikz non valido o motore non disponibile")
            return None
        from app.services.tikz_compile_service import TikzBusyError

        try:
            figure = self._render_or_raise(sanitized)
        except TikzBusyError as exc:
            # Occupato non vuol dire rotto: niente cache negativa.
            _render_failed(self.fmt, asset_id, f"tikz_busy: {exc}")
            raise FigureEngineBusyError(str(exc)) from exc
        except Exception as exc:
            _cache_negative(key)
            _render_failed(self.fmt, asset_id, str(exc))
            return None
        _cache_put(key, figure)
        return figure

    def render_svg(self, content: str, *, asset_id: str = "") -> str | None:
        try:
            figure = self.render_figure(content, asset_id=asset_id)
        except FigureEngineBusyError:
            return None
        return figure.svg if figure is not None else None

    def render_png(self, content: str, *, dpi: int = 150) -> bytes | None:
        """PNG della resa per la revisione Vision (PROMPT 21). Ricompila (la
        cache tiene solo l'SVG) e rasterizza la pagina del PDF con
        pypdfium2: il PDF è quello della sandbox, non un documento
        dell'utente. None se il sorgente o il motore non lo permettono."""
        import io

        import pypdfium2 as pdfium

        from app.services import tikz_compile_service
        from app.services.figure_compute import tikz_preamble

        sanitized = self.sanitize(content)
        if self.static_check(sanitized) is not None or not self.available():
            return None
        try:
            document, lines = tikz_preamble.document(sanitized)
            result = tikz_compile_service.compile_document(document, preamble_lines=lines)
            pdf = pdfium.PdfDocument(result.pdf)
            try:
                image = pdf[0].render(scale=dpi / 72.0).to_pil()
            finally:
                pdf.close()
            buf = io.BytesIO()
            image.save(buf, format="PNG")
        except Exception as exc:
            log.warning("tikz_png_failed", error=f"{type(exc).__name__}: {exc}"[:_ERROR_CAP])
            return None
        return buf.getvalue()

    def preview(
        self, content: str, *, before_compile: Callable[[], None] | None = None
    ) -> TikzPreview:
        """Anteprima dell'editor (`POST …/render-tikz`): SVG, corpo minimo
        del testo e difetti geometrici come AVVISI (mai bloccanti qui).

        `before_compile` gira solo se serve compilare (niente cache): la
        quota per utente dell'endpoint non si consuma sugli hit. Errori con
        il codice dell'API in `TikzPreviewError`."""
        from app.services import tikz_compile_service

        if "tikz" not in available_formats():
            raise TikzPreviewError("figure_format_unavailable", "Formato tikz non disponibile.")
        sanitized = self.sanitize(content)
        problem = self.static_check(sanitized)
        if problem is not None:
            raise TikzPreviewError("tikz_source_invalid", problem)
        key = self._key(sanitized)
        figure = _cache_get_figure(key)
        cached = figure is not None
        if figure is None:
            if before_compile is not None:
                before_compile()
            try:
                figure = self._render_or_raise(sanitized)
            except tikz_compile_service.TikzBusyError as exc:
                raise TikzPreviewError("tikz_busy", str(exc)) from exc
            except tikz_compile_service.TikzEngineUnavailableError as exc:
                raise TikzPreviewError("figure_format_unavailable", str(exc)) from exc
            except tikz_compile_service.TikzTimeoutError as exc:
                raise TikzPreviewError("tikz_render_timeout", str(exc)) from exc
            except Exception as exc:  # compilazione, SVG
                raise TikzPreviewError("tikz_compile_failed", str(exc)[:_ERROR_CAP]) from exc
            _cache_put(key, figure)
        metrics = figure.metrics
        return TikzPreview(
            svg=figure.svg,
            font_px_min=metrics.font_px_min if metrics is not None else None,
            warnings=tuple(metrics.defects) if metrics is not None else (),
            content_hash=key[1],
            cached=cached,
        )

    def render_figure_batch(
        self, contents: list[str], *, asset_ids: list[str]
    ) -> list[RenderedFigure | None]:
        return [
            self.render_figure(c, asset_id=aid) for c, aid in zip(contents, asset_ids, strict=True)
        ]

    def render_svg_batch(self, contents: list[str], *, asset_ids: list[str]) -> list[str | None]:
        return [
            figure.svg if figure is not None else None
            for figure in self.render_figure_batch(contents, asset_ids=asset_ids)
        ]

    def extract_translatable(self, content: str) -> dict[str, str]:
        from app.services.figure_compute.tikz_translate import extract

        return extract(self.sanitize(content))

    def apply_translations(self, content: str, tr: Mapping[str, str]) -> str:
        from app.services.figure_compute.tikz_lexer import TikzSourceError, check
        from app.services.figure_compute.tikz_translate import apply

        if not tr:
            return content
        sanitized = self.sanitize(content)
        translated = apply(sanitized, tr)
        try:
            check(translated, max_chars=int(get_settings().figure_tikz_max_chars))
        except TikzSourceError:
            log.warning("tikz_translation_reverted", reason="lexer")
            return content
        return translated


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------

REGISTRY: dict[str, FigureRenderer] = {
    "mermaid": MermaidRenderer(),
    "vegalite": VegaLiteRenderer(),
    "dot": DotRenderer(),
    "function": FunctionRenderer(),
    "tikz": TikzRenderer(),
}


def register_renderer(renderer: FigureRenderer) -> None:
    """Registra (o sostituisce) il renderer di un formato (test, estensioni).
    Invalida la cache di `available_formats`."""
    REGISTRY[renderer.fmt] = renderer
    available_formats.cache_clear()


@lru_cache(maxsize=1)
def available_formats() -> tuple[str, ...]:
    """Formati offerti al modello (enum dello schema strict) e accettati dal
    validatore: kill-switch del setting E dipendenza presente. Mermaid è
    sempre disponibile. Calcolato una volta per processo e loggato."""
    settings = get_settings()
    switches = {
        "vegalite": settings.figure_vegalite_enabled,
        "dot": settings.figure_dot_enabled,
        "function": settings.figure_function_enabled,
        "tikz": settings.figure_tikz_enabled,
    }
    out = ["mermaid"]
    for fmt in RENDERABLE_FORMATS[1:]:
        renderer = REGISTRY.get(fmt)
        if switches.get(fmt) and renderer is not None and renderer.available():
            out.append(fmt)
    log.info("figure_formats_available", formats=out)
    return tuple(out)


# ---------------------------------------------------------------------------
# Orchestratore asincrono
# ---------------------------------------------------------------------------

_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)

# Tetto minimo del batch per formato, applicato sopra
# `figure_render_timeout_seconds` (20 s). Il batch Mermaid paga un costo
# fisso (lancio di Chromium, caricamento della CDN con `wait_for_function`
# fino a 15 s) prima del primo render: con il timeout unico una CDN lenta
# farebbe perdere in blocco tutte le figure Mermaid della lezione. Oggi
# `_prerender_mermaid_for_lesson` non ha alcun tetto complessivo.
_BATCH_TIMEOUT_FLOOR_S: dict[str, float] = {"mermaid": 60.0, "tikz": 60.0}
# Quota di tempo per figura del batch, sopra il pavimento e sopra il tetto
# della singola resa: il tetto è del BATCH, quindi senza questa quota
# raddoppiare le figure dimezzava il budget di ciascuna. Con la regola
# nuova del prompt (4-8 figure per lezione invece di 1-3) è la differenza
# fra un lotto che passa e uno che scade tutto insieme.
_BATCH_TIMEOUT_PER_FIGURE_S: float = 6.0
# Formati per cui un timeout dell'INTERO batch non entra in cache negativa:
# il tempo è dominato dal costo fisso, non attribuibile alle singole figure
# (un render fallito per figura, `None` nella lista, resta in cache negativa).
_NO_NEGATIVE_CACHE_ON_BATCH_TIMEOUT: frozenset[str] = frozenset({"mermaid"})


def _batch_timeout(fmt: str, base: float, count: int = 1) -> float:
    """Tetto del batch: il massimo fra il tetto della singola resa, il
    pavimento del formato (costo fisso del motore) e la quota per figura
    moltiplicata per le figure del lotto."""
    per_figure = _BATCH_TIMEOUT_PER_FIGURE_S * max(1, count)
    return max(base, _BATCH_TIMEOUT_FLOOR_S.get(fmt, 0.0), per_figure)


def _render_semaphore() -> asyncio.Semaphore:
    """Un semaforo per loop (uvicorn e test usano loop diversi)."""
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(max(1, int(get_settings().figure_render_max_workers)))
        _semaphores[loop] = sem
    return sem


async def render_function(
    spec: FunctionFigureSpec, *, language: str | None
) -> FunctionRenderResult:
    """Ingresso asincrono dell'endpoint `render-function`: cache dei
    risultati del motore, poi semaforo dei render CPU-bound + `to_thread`
    + `wait_for(figure_render_timeout_seconds)`. Solleva
    `FigureTimeoutError` (timeout complessivo) o `FunctionRenderError`
    (calcolo numerico/disegno falliti); il timeout del solo passo
    simbolico NON solleva (risultato `approximate`)."""
    timeout = float(get_settings().figure_render_timeout_seconds)
    async with _render_semaphore():
        # La scadenza è passata anche al motore: allo scadere di `wait_for`
        # il thread non è interrompibile, ma il motore la controlla fra un
        # passo e l'altro (numerico, figlio sympy, disegno) e si ferma al
        # primo controllo invece di completare il lavoro a vuoto.
        deadline = time.monotonic() + timeout
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    figure_function_service.render_function_sync,
                    spec,
                    language=language,
                    deadline=deadline,
                ),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise FigureTimeoutError(f"render-function: oltre {timeout:g} s") from exc


def function_computed_caption(content: str, *, language: str | None, asset_id: str = "") -> str:
    """Coda della didascalia di una figura `function` già renderizzata
    (`FunctionRenderer.computed_caption`): il PDF la passa al partial come
    `extra_caption`. Stringa vuota per ogni altro caso (`asset_id` è solo
    contesto del log `figure_caption_missing`)."""
    renderer = REGISTRY.get("function")
    if not isinstance(renderer, FunctionRenderer):
        return ""
    return renderer.computed_caption(content, language=language, asset_id=asset_id)


def _svg_cache_hit_complete(renderer: FigureRenderer, sanitized: str) -> bool:
    """Un hit della cache SVG basta ai formati il cui unico prodotto è
    l'SVG. Per `function` serve anche il risultato del motore (coda della
    didascalia, D9) nella cache dei risultati, che ha traffico diverso: le
    anteprime dell'editor la riempiono senza toccare quella degli SVG. Se
    manca, la figura torna nel batch e `render_svg` ripopola entrambe."""
    if isinstance(renderer, FunctionRenderer):
        return renderer.result_cached(sanitized)
    return True


def _iter_renderable(assets: Sequence[Mapping[str, Any]]) -> Iterator[tuple[str, str, str]]:
    for asset in assets:
        fmt = str(asset.get("format") or "")
        asset_id = str(asset.get("asset_id") or "")
        content = asset.get("content")
        if fmt in RENDERABLE_FORMATS and asset_id and isinstance(content, str) and content.strip():
            yield fmt, asset_id, content


def _render_batch(renderer: FigureRenderer, contents: list[str], asset_ids: list[str]) -> Any:
    """Dispatch del batch: `render_figure_batch` se il renderer lo espone
    (Mermaid, metriche misurate), altrimenti `render_svg_batch` del
    protocollo. Nessun metodo nuovo nel `Protocol`: i renderer registrati
    dall'esterno e i fake dei test non cambiano. Ritorna la lista grezza:
    la guardia di lunghezza e il wrapping stanno in `render_figure_map`."""
    batch = getattr(renderer, "render_figure_batch", None)
    if callable(batch):
        return batch(contents, asset_ids=asset_ids)
    return renderer.render_svg_batch(contents, asset_ids=asset_ids)


def _as_figure(
    item: object, renderer: object = None, *, asset_id: str = ""
) -> RenderedFigure | None:
    """Record della mappa di resa: un `RenderedFigure` passa tale e quale,
    una stringa diventa `RenderedFigure.from_svg` con la geometria se il
    renderer espone `measure`."""
    if isinstance(item, RenderedFigure):
        return item if item.svg else None
    if isinstance(item, str) and item:
        return _figure_from_svg(renderer, item, asset_id=asset_id)
    return None


async def render_svg_map(assets: Sequence[Mapping[str, Any]], *, language: str) -> dict[str, str]:
    """`{asset_id: svg}`: proiezione `.svg` di `render_figure_map`."""
    figures = await render_figure_map(assets, language=language)
    return {asset_id: fig.svg for asset_id, fig in figures.items()}


async def render_figure_map(
    assets: Sequence[Mapping[str, Any]],
    *,
    language: str,
    cache_failures: bool = True,
    raise_on_busy: bool = False,
) -> dict[str, RenderedFigure]:
    """`{asset_id: RenderedFigure}` per gli asset renderizzabili di una
    lezione (SVG e metriche del testo, D10).

    Unico punto di `asyncio.to_thread` + `asyncio.wait_for` + semaforo
    (tenuto dal chiamante async, rilasciato anche su timeout). Raggruppa
    per formato e chiama UN batch per formato (`_render_batch`); serve
    dalla cache LRU (per `function` solo se anche il risultato del motore
    è in cache: `_svg_cache_hit_complete`) e salta le chiavi in cache
    negativa (render fallito negli ultimi 60 s). Non solleva mai: timeout
    ed eccezioni producono `figure_render_failed` e la chiave resta
    assente (fallback del partial). `language` è solo contesto di log
    (vedi la docstring del modulo sulla chiave di cache).

    `cache_failures=False`: i fallimenti di questa chiamata non entrano in
    cache negativa. Lo usa chi rende per MISURARE e non per pubblicare (la
    revisione figura ↔ testo di `asset_validation_service`, che mette
    originale e riscrittura nello stesso batch): un timeout del batch non
    deve togliere le figure ORIGINALI all'export dei 60 s successivi. La
    cache positiva resta scritta in ogni caso.
    """
    settings = get_settings()
    timeout = float(settings.figure_render_timeout_seconds)
    formats = available_formats()
    result: dict[str, RenderedFigure] = {}
    pending: dict[str, list[tuple[str, str, CacheKey]]] = {}

    for fmt, asset_id, content in _iter_renderable(assets):
        renderer = REGISTRY.get(fmt)
        if renderer is None or fmt not in formats:
            _render_failed(fmt, asset_id, "format_unavailable")
            continue
        sanitized = renderer.sanitize(content)
        key = _renderer_key(renderer, fmt, sanitized)
        hit = _cache_get_figure(key)
        if hit is not None and _svg_cache_hit_complete(renderer, sanitized):
            result[asset_id] = hit
            continue
        if _cache_is_negative(key):
            _render_failed(fmt, asset_id, "negative_cache")
            continue
        if not sanitized:
            # Contenuto svuotato dalla sanificazione (fence vuoto, sole
            # righe di controllo): niente da rendere e, per Mermaid,
            # nessun Chromium da avviare a vuoto (REG-4).
            _render_failed(fmt, asset_id, "sorgente vuoto dopo la sanificazione")
            continue
        pending.setdefault(fmt, []).append((asset_id, sanitized, key))

    sem = _render_semaphore()
    for fmt, items in pending.items():
        renderer = REGISTRY[fmt]
        ids = [aid for aid, _s, _k in items]
        contents = [s for _a, s, _k in items]
        fmt_timeout = _batch_timeout(fmt, timeout, len(items))
        async with sem:
            try:
                svgs = await asyncio.wait_for(
                    asyncio.to_thread(_render_batch, renderer, contents, ids),
                    timeout=fmt_timeout,
                )
            except TimeoutError:
                for aid, _s, key in items:
                    if cache_failures and fmt not in _NO_NEGATIVE_CACHE_ON_BATCH_TIMEOUT:
                        _cache_negative(key)
                    _render_failed(fmt, aid, f"timeout dopo {fmt_timeout:g} s")
                continue
            except FigureEngineBusyError:
                # Motore occupato (TeX durante un'estrazione): niente cache
                # negativa; chi pubblica (PDF, frame) chiede di sollevare e il
                # suo worker ritenta invece di salvare un segnaposto.
                if raise_on_busy:
                    raise
                continue
            except Exception as exc:  # il renderer non deve sollevare; difesa in profondità
                for aid, _s, key in items:
                    if cache_failures:
                        _cache_negative(key)
                    _render_failed(fmt, aid, f"{type(exc).__name__}: {exc}")
                continue
        # Il contratto vuole una lista parallela agli item: un renderer
        # registrato che lo violasse non deve far saltare l'export (uno
        # `zip(strict=True)` qui risalirebbe fino al worker, COR-1).
        svgs = list(svgs) if isinstance(svgs, list) else []
        if len(svgs) != len(items):
            log.warning(
                "figure_render_batch_length_mismatch",
                format=fmt,
                expected=len(items),
                got=len(svgs),
            )
            svgs = (svgs + [None] * len(items))[: len(items)]
        for (aid, _s, key), item in zip(items, svgs, strict=True):
            fig = _as_figure(item, renderer, asset_id=aid)
            if fig is not None:
                _cache_put(key, fig)
                result[aid] = fig
            elif cache_failures:
                _cache_negative(key)
    log.info(
        "figure_render_map",
        language=language,
        requested=sum(len(v) for v in pending.values()),
        rendered=len(result),
    )
    return result


def _renderer_key(renderer: Any, fmt: str, sanitized: str) -> CacheKey:
    """Chiave di cache del renderer se ne dichiara una (`tikz`: con
    `PREAMBLE_VERSION`), altrimenti quella comune: una sola voce per figura
    nella LRU, condivisa da `render_figure_map` e dal renderer."""
    own = getattr(renderer, "_key", None)
    return own(sanitized) if callable(own) else cache_key(fmt, sanitized)


def _out_of_band(fig: RenderedFigure, *, box_mm: FigureBoxMm, variant: FigureVariant) -> bool:
    """La figura, resa in questo box, porta il testo SOTTO il pavimento
    della banda della superficie (stesso fit della resa, `figure_scale`).
    Una geometria non leggibile o un box degenere valgono «in banda»: non
    si tenta nulla."""
    box = svg_intrinsic_box(fig.svg)
    if box is None:
        return False
    base, _source = resolve_base_font_px("mermaid", fig.metrics)
    fit = fit_figure_width_mm(
        vb_w=box.vb_w,
        vb_h=box.vb_h,
        base_font_px=base,
        box_w_mm=box_mm[0],
        box_h_mm=box_mm[1],
        variant=variant,
        intrinsic_w_px=box.width_px,
    )
    return fit is not None and not fit.in_band


async def render_chain_variants(
    assets: Sequence[Mapping[str, Any]],
    figures: Mapping[str, RenderedFigure],
    *,
    box_mm: FigureBoxMm,
    variant: FigureVariant,
    language: str,
    lesson_code: str | None = None,
) -> dict[str, RenderedFigure]:
    """La mappa di resa con la variante verticale accanto alle figure che
    la meritano (D15).

    Merita la variante la figura Mermaid che in questo box esce SOTTO la
    banda della superficie e il cui sorgente è una catena lineare
    dichiarata in orizzontale (`chain_layout.vertical_chain_variant`).
    Solo quelle: le figure in banda e i grafi con diramazioni non costano
    nemmeno una misura in più. Le varianti sono rese in UN batch da
    `render_figure_map`, quindi con la cache, il semaforo e il timeout
    consueti; la chiave di cache della variante è la sua
    (`fmt`, sha256 del sorgente variante, `THEME_VERSION`), così dalla
    seconda volta la resa in più è gratis.

    Il riconoscimento legge il sorgente SANIFICATO, cioè quello che
    `render_figure_map` rende davvero (`MermaidRenderer.sanitize`: via i
    fence markdown residui, le righe-segnaposto `mermaid`/`all` e i
    caratteri di controllo). Sul grezzo una sola riga spuria — di quelle
    che l'AI emette e che il gate del salvataggio accetta — bastava a far
    perdere la catena: la pagina disegnava una catena orizzontale e la
    variante non veniva nemmeno tentata.

    Non sceglie: appende la variante e lascia decidere alla misura del fit
    (`_figure_width_style`, log `figure_direction_flipped`). Non solleva
    mai e non tocca il sorgente salvato."""
    renderer = REGISTRY.get("mermaid")
    if renderer is None:
        return dict(figures)
    candidates: list[dict[str, str]] = []
    for fmt, asset_id, content in _iter_renderable(assets):
        if fmt != "mermaid":
            continue
        fig = figures.get(asset_id)
        if fig is None or fig.chain_variant is not None:
            continue
        if not _out_of_band(fig, box_mm=box_mm, variant=variant):
            continue
        flipped = vertical_chain_variant(renderer.sanitize(content))
        if flipped is None:
            continue
        candidates.append({"asset_id": asset_id, "format": "mermaid", "content": flipped})
    if not candidates:
        return dict(figures)
    started = time.monotonic()
    rendered = await render_figure_map(candidates, language=language)
    log.info(
        "figure_chain_variants",
        lesson_code=lesson_code,
        variant=variant,
        candidates=[c["asset_id"] for c in candidates],
        rendered=len(rendered),
        elapsed_s=round(time.monotonic() - started, 3),
    )
    return {
        aid: replace(fig, chain_variant=rendered[aid]) if aid in rendered else fig
        for aid, fig in figures.items()
    }


def _changed_assets(
    assets: Sequence[Mapping[str, Any]], previous: Sequence[Mapping[str, Any]] | None
) -> list[tuple[int, Mapping[str, Any]]]:
    """Asset del payload con `(format, content)` diversi da `previous`
    (confronto per `asset_id`, A15): un edit del testo non rivalida i
    diagrammi già in DB."""
    prev: dict[str, tuple[Any, Any]] = {}
    for p in previous or []:
        prev[str(p.get("asset_id") or "")] = (p.get("format"), p.get("content"))
    out: list[tuple[int, Mapping[str, Any]]] = []
    for i, asset in enumerate(assets):
        aid = str(asset.get("asset_id") or "")
        if prev.get(aid) == (asset.get("format"), asset.get("content")):
            continue
        out.append((i, asset))
    return out


async def validate_visual_assets_or_raise(
    assets: Sequence[Mapping[str, Any]],
    *,
    previous: Sequence[Mapping[str, Any]] | None,
    loc_root: str,
    code: str,
) -> None:
    """Gate del PATCH manuale: SOLO sugli asset con `(format, content)`
    cambiati, il tetto di risorsa A1 (`VISUAL_ASSET_CONTENT_MAX_CHARS`, per
    ogni formato: lo schema del PATCH non lo applica, un asset storico più
    lungo e invariato resta salvabile) e, per i renderizzabili, `validate`
    (`deep=False`, in thread); solleva `ValidationAppError` 422 con
    `meta={"errors": [{loc, asset_id, format, msg, type}]}`. Un formato
    assente da `available_formats()` è `figure_format_unavailable`, mai un
    pass-through."""
    formats = available_formats()
    errors: list[dict[str, Any]] = []
    for i, asset in _changed_assets(assets, previous):
        fmt = str(asset.get("format") or "")
        asset_id = str(asset.get("asset_id") or "")
        entry: dict[str, Any] = {
            "loc": [loc_root, i, "content"],
            "asset_id": asset_id,
            "format": fmt,
        }
        content = asset.get("content")
        size = len(content) if isinstance(content, str) else 0
        if size > VISUAL_ASSET_CONTENT_MAX_CHARS:
            errors.append(
                {
                    **entry,
                    "msg": f"contenuto oltre {VISUAL_ASSET_CONTENT_MAX_CHARS} caratteri ({size})",
                    "type": FIGURE_INVALID,
                }
            )
            continue
        if fmt not in RENDERABLE_FORMATS:
            continue
        renderer = REGISTRY.get(fmt)
        if renderer is None or fmt not in formats:
            errors.append(
                {
                    **entry,
                    "msg": f"formato `{fmt}` non disponibile su questo server",
                    "type": FIGURE_FORMAT_UNAVAILABLE,
                }
            )
            continue
        text = content if isinstance(content, str) else ""
        if isinstance(renderer, TikzRenderer):
            # WP6: il controllo statico non basta per TeX, la prova di
            # compilazione sì; i difetti geometrici qui sono solo avvisi.
            ok, msg = await asyncio.to_thread(
                partial(renderer.validate, text, deep=True, strict_geometry=False)
            )
            if not ok and msg.startswith("tikz_busy"):
                # Non è un errore dell'asset: si riprova più tardi.
                raise ConflictError(
                    "Compilazione TikZ occupata: riprova fra poco.", code="tikz_busy"
                )
        else:
            ok, msg = await asyncio.to_thread(renderer.validate, text, deep=False)
        if not ok:
            errors.append({**entry, "msg": msg[:_PAYLOAD_MSG_CAP], "type": error_type_for(msg)})
    if errors:
        log.info("visual_assets_rejected", code=code, errors=len(errors))
        raise ValidationAppError(
            "Uno o più asset visivi non sono validi.",
            code=code,
            meta={"errors": errors},
        )


__all__ = [
    "FIGURE_FORMAT_UNAVAILABLE",
    "FIGURE_INVALID",
    "FUNCTION_SPEC_INVALID",
    "MERMAID_GATE_EMPTY",
    "MERMAID_GATE_HTML",
    "MERMAID_GATE_INIT",
    "MERMAID_GATE_RESOURCE",
    "MERMAID_GATE_TYPE",
    "MERMAID_TYPE_NOT_ALLOWED",
    "PROMPTED_FORMATS",
    "REGISTRY",
    "RENDERABLE_FORMATS",
    "VEGALITE_USE_FUNCTION_FORMAT",
    "DotRenderer",
    "FigureRenderer",
    "FunctionRenderError",
    "FunctionRenderResult",
    "FunctionRenderer",
    "MermaidRenderer",
    "RenderedFigure",
    "VegaLiteRenderer",
    "VisualSvgMap",
    "available_formats",
    "cache_key",
    "clear_svg_cache",
    "error_type_for",
    "figure_asset_context",
    "function_computed_caption",
    "mermaid_declared_type",
    "mermaid_first_meaningful_line",
    "mermaid_static_gate",
    "register_renderer",
    "render_chain_variants",
    "render_figure_map",
    "render_function",
    "render_svg_map",
    "validate_visual_assets_or_raise",
]
