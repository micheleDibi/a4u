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
- `render_svg_map(assets, *, language)`: l'UNICO punto in cui compaiono
  `asyncio.to_thread`, `asyncio.wait_for` e il semaforo dei render
  CPU-bound; raggruppa per formato, una `render_svg_batch` per formato,
  cache LRU degli SVG e cache negativa dei render falliti; non solleva
  mai (le chiavi assenti attivano il fallback del partial);
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
"""

from __future__ import annotations

import asyncio
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
from collections.abc import Iterator, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from app.core.config import get_settings
from app.core.errors import ValidationAppError
from app.core.logging import get_logger
from app.schemas.figure_function import FunctionFigureSpec, format_issues, parse_function_spec
from app.services import figure_function_service
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
from app.services.figure_theme import (
    MERMAID_ALLOWED_TYPES,
    THEME_VERSION,
    VEGALITE_THEME_CONFIG,
    dot_defaults_prelude,
)
from app.services.mermaid_prerender import (
    _prerender_mermaid_to_svg_batch_sync,
    _sanitize_mermaid_code,
    _strip_mermaid_max_width,
)
from app.services.svg_normalize import SvgRejectedError, normalize_svg

log = get_logger("app.figure_render")

RENDERABLE_FORMATS: tuple[str, ...] = ("mermaid", "vegalite", "dot", "function")

# Tipi di errore del payload 422 (`meta.errors[].type`), stessa forma
# `loc/msg/type` del handler Pydantic (`core/errors.py`).
FIGURE_INVALID = "figure_invalid"
FIGURE_FORMAT_UNAVAILABLE = "figure_format_unavailable"
MERMAID_TYPE_NOT_ALLOWED = "mermaid_type_not_allowed"
VEGALITE_USE_FUNCTION_FORMAT = USE_FUNCTION_FORMAT
# `FUNCTION_SPEC_INVALID` è importato da `figure_function_service`.

# Un messaggio di `validate` che inizia con uno di questi prefissi viene
# classificato con quel `type`; tutto il resto è `figure_invalid`.
_TYPED_PREFIXES: tuple[str, ...] = (
    MERMAID_TYPE_NOT_ALLOWED,
    VEGALITE_USE_FUNCTION_FORMAT,
    FUNCTION_SPEC_INVALID,
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


# ---------------------------------------------------------------------------
# Cache LRU degli SVG + cache negativa
# ---------------------------------------------------------------------------

CacheKey = tuple[str, str, str]

_svg_cache: OrderedDict[CacheKey, str] = OrderedDict()
_negative_cache: dict[CacheKey, float] = {}
_cache_lock = threading.Lock()


def cache_key(fmt: str, sanitized: str) -> CacheKey:
    digest = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
    return (fmt, digest, THEME_VERSION)


def _cache_get(key: CacheKey) -> str | None:
    with _cache_lock:
        svg = _svg_cache.get(key)
        if svg is not None:
            _svg_cache.move_to_end(key)
        return svg


def _cache_put(key: CacheKey, svg: str) -> None:
    size = max(1, int(get_settings().figure_svg_cache_size))
    with _cache_lock:
        _svg_cache[key] = svg
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
# Tag HTML nelle label (`<br>`, `<b>`, `<script>`, `<table>`, ...): con
# `htmlLabels: false` finirebbero in chiaro nel `<text>`. Regola generica,
# non un elenco di tag: `<` seguito da un nome di elemento e chiuso da `>`
# sulla stessa riga. Non sono tag e passano: le frecce (`-->`, `<|--`,
# `->>`, `<-->`), le annotazioni `<<interface>>` (doppio `<`), un `<`
# isolato (`a < b`) e un `<b` non chiuso (`A[x <b] --> B`: la sezione degli
# attributi non attraversa `]`, `)`, `}`).
_HTML_TAG_RE = re.compile(r"(?<!<)</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^>\n\]\)\}]*)?/?>(?!>)")

# Esiti del gate statico (condivisi con `scripts/revalidate_mermaid_assets.py`).
MERMAID_GATE_EMPTY = "mermaid_empty"
MERMAID_GATE_TYPE = MERMAID_TYPE_NOT_ALLOWED
MERMAID_GATE_INIT = "mermaid_init_directive"
MERMAID_GATE_HTML = "mermaid_html_in_label"

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


def mermaid_static_gate(code: str) -> tuple[str, str]:
    """Gate statico D8 su un sorgente già sanificato: `("", "")` se passa,
    altrimenti `(esito, dettaglio)` con esito in `MERMAID_GATE_*` e
    dettaglio = tipo dichiarato (`?` se assente), motivo della direttiva o
    tag trovato. Unico punto del gate: `MermaidRenderer.validate` e lo
    script di rivalidazione lo consumano con messaggi propri."""
    if not code.strip():
        return (MERMAID_GATE_EMPTY, "")
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
    # Solo le righe del corpo che non sono commenti `%%`: un tag in un
    # commento o nel frontmatter non viene renderizzato.
    for line in body:
        if line.lstrip().startswith("%%"):
            continue
        m = _HTML_TAG_RE.search(line)
        if m:
            return (MERMAID_GATE_HTML, m.group(0))
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
        if outcome == MERMAID_GATE_HTML:
            kind = mermaid_declared_type(code)
            hint = ""
            if _MERMAID_TYPE_ALIASES.get(kind, kind) == "classDiagram":
                hint = "; per i tipi generici usa `List~int~`, non `List<int>`"
            return (
                False,
                f"{MERMAID_TYPE_NOT_ALLOWED}: HTML nelle label non ammesso ({detail[:40]}){hint}",
            )
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
        codes = [self.sanitize(c) for c in contents]
        svgs = _prerender_mermaid_to_svg_batch_sync(codes)
        # `_strip_mermaid_max_width` è già applicato dal pre-render ed è
        # idempotente: qui rende esplicito il contratto del registro.
        return [_strip_mermaid_max_width(s) if s else None for s in svgs]

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
        spec, _err = _parse_vegalite(self.sanitize(content))
        if spec is None or not tr:
            return content
        for path, value in tr.items():
            try:
                _set_by_path(spec, path, value)
            except (KeyError, IndexError, ValueError):
                continue
        return json.dumps(spec, ensure_ascii=False)


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
    commenti `/* */`, `//` e righe `#` (preprocessore, a colonna 0)."""
    n = len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            i += 1
        elif src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif src.startswith("//", i) or (ch == "#" and (i == 0 or src[i - 1] == "\n")):
            end = src.find("\n", i)
            i = n if end < 0 else end
        else:
            break
    return i


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
        if ch == '"':
            text, i = _dot_read_qstring(src, i)
            while True:
                j = _dot_skip_blank(src, i)
                if j < n and src[j] == "+":
                    k = _dot_skip_blank(src, j + 1)
                    if k < n and src[k] == '"':
                        more, i = _dot_read_qstring(src, k)
                        text += more
                        continue
                break
            yield ("str", text)
            continue
        if ch == "<":
            depth, j = 0, i
            while j < n:
                if src[j] == "<":
                    depth += 1
                elif src[j] == ">":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            yield ("html", src[i + 1 : j])
            i = j + 1
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
    file, numero di archi) e, profonda, la prova di render con il tema
    iniettato; `dot` assente → `dot_unavailable`, non fixable."""

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
        if deep:
            try:
                svg = self._render_or_raise(src)
            except (_DotError, SvgRejectedError) as exc:
                return (False, str(exc)[:_ERROR_CAP])
            _cache_put(cache_key(self.fmt, src), svg)
        return (True, "")

    def _render_or_raise(self, sanitized: str) -> str:
        raw = _run_dot(_dot_with_theme(sanitized))
        return normalize_svg(raw, max_bytes=get_settings().figure_svg_max_bytes).svg

    def render_svg(self, content: str, *, asset_id: str = "") -> str | None:
        sanitized = self.sanitize(content)
        key = cache_key(self.fmt, sanitized)
        hit = _cache_get(key)
        if hit is not None:
            return hit
        try:
            svg = self._render_or_raise(sanitized)
        except (_DotError, SvgRejectedError) as exc:
            _cache_negative(key)
            _render_failed(self.fmt, asset_id, str(exc))
            return None
        _cache_put(key, svg)
        return svg

    def render_svg_batch(self, contents: list[str], *, asset_ids: list[str]) -> list[str | None]:
        return [
            self.render_svg(c, asset_id=aid) for c, aid in zip(contents, asset_ids, strict=True)
        ]

    def extract_translatable(self, content: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for n, m in enumerate(_DOT_LABEL_RE.finditer(self.sanitize(content))):
            value = m.group(2)
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
            escaped = tr[key].replace("\\", "\\\\").replace('"', '\\"')
            return f'{m.group(1)}="{escaped}"'

        return _DOT_LABEL_RE.sub(_replace, content)


# ---------------------------------------------------------------------------
# function — Pydantic + AST offline, numerico/matplotlib in thread, sympy nel figlio
# ---------------------------------------------------------------------------


class FunctionRenderer:
    """Validazione: `parse_function_spec` (struttura Pydantic + controlli
    semantici + passo 1 dell'AST, senza sympy); profonda: anche il render
    completo, il cui SVG entra in cache. Render: `render_function_sync`
    del motore (numerico, simbolico nel figlio con timeout, disegno,
    `normalize_svg`). L'SVG non dipende dalla lingua: la didascalia
    calcolata è composta fuori dall'SVG (`function_caption`)."""

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
            _cache_put(cache_key(self.fmt, sanitized), result.svg)
        return (True, "")

    def render_svg(self, content: str, *, asset_id: str = "") -> str | None:
        sanitized = self.sanitize(content)
        key = cache_key(self.fmt, sanitized)
        hit = _cache_get(key)
        if hit is not None:
            return hit
        spec, issues = parse_function_spec(sanitized)
        if spec is None:
            _render_failed(self.fmt, asset_id, format_issues(issues))
            return None
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

    def computed_caption(self, content: str, *, language: str | None) -> str:
        """Coda della didascalia calcolata (D9) nella lingua richiesta,
        letta dalla cache dei risultati del motore popolata da
        `render_svg`/`validate(deep=True)`; mai calcolata qui. Vuota se la
        spec non è valida o il risultato non è (più) in cache."""
        spec, _issues = parse_function_spec(self.sanitize(content))
        if spec is None:
            return ""
        hit = figure_function_service.cached_result(spec, language=language)
        return hit.computed_caption if hit is not None else ""


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------

REGISTRY: dict[str, FigureRenderer] = {
    "mermaid": MermaidRenderer(),
    "vegalite": VegaLiteRenderer(),
    "dot": DotRenderer(),
    "function": FunctionRenderer(),
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
_BATCH_TIMEOUT_FLOOR_S: dict[str, float] = {"mermaid": 60.0}
# Formati per cui un timeout dell'INTERO batch non entra in cache negativa:
# il tempo è dominato dal costo fisso, non attribuibile alle singole figure
# (un render fallito per figura, `None` nella lista, resta in cache negativa).
_NO_NEGATIVE_CACHE_ON_BATCH_TIMEOUT: frozenset[str] = frozenset({"mermaid"})


def _batch_timeout(fmt: str, base: float) -> float:
    return max(base, _BATCH_TIMEOUT_FLOOR_S.get(fmt, 0.0))


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


def function_computed_caption(content: str, *, language: str | None) -> str:
    """Coda della didascalia di una figura `function` già renderizzata
    (`FunctionRenderer.computed_caption`): il PDF la passa al partial come
    `extra_caption`. Stringa vuota per ogni altro caso."""
    renderer = REGISTRY.get("function")
    if not isinstance(renderer, FunctionRenderer):
        return ""
    return renderer.computed_caption(content, language=language)


def _iter_renderable(assets: Sequence[Mapping[str, Any]]) -> Iterator[tuple[str, str, str]]:
    for asset in assets:
        fmt = str(asset.get("format") or "")
        asset_id = str(asset.get("asset_id") or "")
        content = asset.get("content")
        if fmt in RENDERABLE_FORMATS and asset_id and isinstance(content, str) and content.strip():
            yield fmt, asset_id, content


async def render_svg_map(assets: Sequence[Mapping[str, Any]], *, language: str) -> dict[str, str]:
    """`{asset_id: svg}` per gli asset renderizzabili di una lezione.

    Unico punto di `asyncio.to_thread` + `asyncio.wait_for` + semaforo
    (tenuto dal chiamante async, rilasciato anche su timeout). Raggruppa
    per formato e chiama UNA `render_svg_batch` per formato; serve dalla
    cache LRU e salta le chiavi in cache negativa (render fallito negli
    ultimi 60 s). Non solleva mai: timeout ed eccezioni producono
    `figure_render_failed` e la chiave resta assente (fallback del
    partial). `language` è solo contesto di log (vedi la docstring del
    modulo sulla chiave di cache).
    """
    settings = get_settings()
    timeout = float(settings.figure_render_timeout_seconds)
    formats = available_formats()
    result: dict[str, str] = {}
    pending: dict[str, list[tuple[str, str, CacheKey]]] = {}

    for fmt, asset_id, content in _iter_renderable(assets):
        renderer = REGISTRY.get(fmt)
        if renderer is None or fmt not in formats:
            _render_failed(fmt, asset_id, "format_unavailable")
            continue
        sanitized = renderer.sanitize(content)
        key = cache_key(fmt, sanitized)
        hit = _cache_get(key)
        if hit is not None:
            result[asset_id] = hit
            continue
        if _cache_is_negative(key):
            _render_failed(fmt, asset_id, "negative_cache")
            continue
        pending.setdefault(fmt, []).append((asset_id, sanitized, key))

    sem = _render_semaphore()
    for fmt, items in pending.items():
        renderer = REGISTRY[fmt]
        ids = [aid for aid, _s, _k in items]
        contents = [s for _a, s, _k in items]
        fmt_timeout = _batch_timeout(fmt, timeout)
        async with sem:
            try:
                svgs = await asyncio.wait_for(
                    asyncio.to_thread(renderer.render_svg_batch, contents, asset_ids=ids),
                    timeout=fmt_timeout,
                )
            except TimeoutError:
                for aid, _s, key in items:
                    if fmt not in _NO_NEGATIVE_CACHE_ON_BATCH_TIMEOUT:
                        _cache_negative(key)
                    _render_failed(fmt, aid, f"timeout dopo {fmt_timeout:g} s")
                continue
            except Exception as exc:  # il renderer non deve sollevare; difesa in profondità
                for aid, _s, key in items:
                    _cache_negative(key)
                    _render_failed(fmt, aid, f"{type(exc).__name__}: {exc}")
                continue
        for (aid, _s, key), svg in zip(items, svgs, strict=True):
            if svg:
                _cache_put(key, svg)
                result[aid] = svg
            else:
                _cache_negative(key)
    log.info(
        "figure_render_map",
        language=language,
        requested=sum(len(v) for v in pending.values()),
        rendered=len(result),
    )
    return result


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
    """Gate del PATCH manuale: valida (`deep=False`, in thread) SOLO gli
    asset renderizzabili con `(format, content)` cambiati e solleva
    `ValidationAppError` 422 con `meta={"errors": [{loc, asset_id, format,
    msg, type}]}`. Un formato assente da `available_formats()` è
    `figure_format_unavailable`, mai un pass-through."""
    formats = available_formats()
    errors: list[dict[str, Any]] = []
    for i, asset in _changed_assets(assets, previous):
        fmt = str(asset.get("format") or "")
        if fmt not in RENDERABLE_FORMATS:
            continue
        asset_id = str(asset.get("asset_id") or "")
        entry: dict[str, Any] = {
            "loc": [loc_root, i, "content"],
            "asset_id": asset_id,
            "format": fmt,
        }
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
        content = asset.get("content")
        ok, msg = await asyncio.to_thread(
            renderer.validate, content if isinstance(content, str) else "", deep=False
        )
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
    "MERMAID_GATE_TYPE",
    "MERMAID_TYPE_NOT_ALLOWED",
    "REGISTRY",
    "RENDERABLE_FORMATS",
    "VEGALITE_USE_FUNCTION_FORMAT",
    "DotRenderer",
    "FigureRenderer",
    "FunctionRenderError",
    "FunctionRenderResult",
    "FunctionRenderer",
    "MermaidRenderer",
    "VegaLiteRenderer",
    "available_formats",
    "cache_key",
    "clear_svg_cache",
    "error_type_for",
    "function_computed_caption",
    "mermaid_declared_type",
    "mermaid_first_meaningful_line",
    "mermaid_static_gate",
    "register_renderer",
    "render_function",
    "render_svg_map",
    "validate_visual_assets_or_raise",
]
