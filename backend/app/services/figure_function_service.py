"""Motore del formato `function` (Q4): orchestrazione, riconciliazione, cache.

`render_function_sync(spec, language=…)` è la sequenza completa, bloccante
(il chiamante la avvolge in `asyncio.to_thread`):

1. calcolo numerico (`figure_compute.function_numeric`, numpy nel thread):
   rami, zeri, punti critici, flessi, singolarità, code, tangenti,
   integrali — la figura non dipende da sympy;
2. calcolo simbolico nel processo figlio `spawn`
   (`figure_compute.function_symbolic` via `isolated.run_isolated`, timeout
   `settings.figure_function_timeout_seconds`): forme esatte in LaTeX;
3. riconciliazione: ogni valore numerico è sostituito dall'esatto entro
   `1e-6·ampiezza` (tangenti e integrali entro una tolleranza relativa); un
   esatto senza riscontro numerico entra SOLO se una verifica numerica
   diretta con tolleranza locale lo conferma (`f(x*) ≈ 0`, `f'(x*) ≈ 0`,
   `f` non finita nel punto, `f(X) − (mX + q) → 0` sulle code), altrimenti
   è ignorato: nessun numero «plausibile» nella figura; ogni categoria di
   punti notevoli resta entro `function_numeric.MAX_NOTABLE_POINTS` voci
   (le prime da sinistra) e le categorie troncate sono elencate in
   `computed["truncated"]` con l'avvertenza `too_many_points`;
4. disegno (`figure_compute.function_plot`) → `normalize_svg`;
5. didascalia calcolata `figure_theme.function_caption(computed, language)`,
   mai persistita.

Timeout o errore del figlio → `approximate=True`, avvertenza
`symbolic_timeout` / `symbolic_failed`, SVG comunque prodotto con i valori
numerici. Errore del calcolo numerico o del disegno → `FunctionRenderError`
(dettaglio nel log `function_render_failed`, mai nel messaggio al client).
`deadline` (tempo monotono) è la scadenza complessiva dell'endpoint: il
thread non è interrompibile, ma il motore la controlla fra un passo e
l'altro e si ferma al primo controllo con `FunctionRenderError`.

Il registro (`figure_render_service.FunctionRenderer`) e l'endpoint
`render-function` passano da qui; SVG e `computed` (che non dipendono
dalla lingua) sono in una cache LRU per `(sha256 della spec canonica,
THEME_VERSION)`; la didascalia è composta per lingua a ogni chiamata
(microsecondi). Questo modulo NON importa `figure_render_service` (che lo
importa all'avvio).
"""

from __future__ import annotations

import importlib.util
import json
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.figure_function import ANALYSIS_ITEMS, FunctionFigureSpec
from app.services.figure_compute import function_numeric, function_plot
from app.services.figure_compute.function_numeric import (
    CATEGORY_ASYMPTOTES,
    CATEGORY_CRITICAL,
    CATEGORY_DISCONTINUITIES,
    CATEGORY_INFLECTION,
    CATEGORY_ZEROS,
    MAX_NOTABLE_POINTS,
    NumericStudy,
)
from app.services.figure_compute.isolated import (
    FigureComputeError,
    FigureTimeoutError,
    run_isolated,
)
from app.services.figure_theme import THEME_VERSION, format_number, function_caption
from app.services.svg_normalize import SvgRejectedError, normalize_svg

log = get_logger("app.figure_function")

FUNCTION_SPEC_INVALID = "function_spec_invalid"
SYMBOLIC_TARGET = "app.services.figure_compute.function_symbolic:analyze_symbolic"
_DEPENDENCIES = ("numpy", "matplotlib", "sympy")

# `(sha256 della spec canonica, THEME_VERSION)`: SVG e `computed` non
# dipendono dalla lingua.
ResultKey = tuple[str, str]


class FunctionRenderError(RuntimeError):
    """Calcolo numerico o disegno falliti (o scadenza complessiva superata):
    la figura non può essere prodotta."""


@dataclass(frozen=True)
class FunctionRenderResult:
    svg: str
    computed: dict[str, Any]
    latex: list[str]
    warnings: list[str]
    approximate: bool
    computed_caption: str
    content_hash: str


def _check_deadline(deadline: float | None, step: str) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise FunctionRenderError(f"scadenza superata prima del passo «{step}»")


def dependencies_available() -> bool:
    """numpy, matplotlib e sympy importabili (senza importarli)."""
    return all(importlib.util.find_spec(name) is not None for name in _DEPENDENCIES)


# ---------------------------------------------------------------------------
# Cache LRU dei risultati
# ---------------------------------------------------------------------------

_result_cache: OrderedDict[ResultKey, FunctionRenderResult] = OrderedDict()
_result_lock = threading.Lock()


def result_key(spec: FunctionFigureSpec) -> ResultKey:
    return (spec.content_hash(), THEME_VERSION)


def _cache_get(key: ResultKey) -> FunctionRenderResult | None:
    with _result_lock:
        hit = _result_cache.get(key)
        if hit is not None:
            _result_cache.move_to_end(key)
        return hit


def _cache_put(key: ResultKey, result: FunctionRenderResult) -> None:
    size = max(1, int(get_settings().figure_svg_cache_size))
    with _result_lock:
        _result_cache[key] = result
        _result_cache.move_to_end(key)
        while len(_result_cache) > size:
            _result_cache.popitem(last=False)


def clear_result_cache() -> None:
    with _result_lock:
        _result_cache.clear()


def cached_result(spec: FunctionFigureSpec, *, language: str | None) -> FunctionRenderResult | None:
    """Risultato già calcolato per la spec (con la didascalia nella lingua
    richiesta) oppure `None`. Non calcola mai: serve al render dei PDF, che
    legge la coda della didascalia dopo `render_svg_map` senza rieseguire
    il motore nel thread di composizione dell'HTML."""
    hit = _cache_get(result_key(spec))
    return _with_caption(hit, language) if hit is not None else None


# ---------------------------------------------------------------------------
# Passo simbolico nel figlio
# ---------------------------------------------------------------------------


def _symbolic_payload(spec: FunctionFigureSpec) -> dict[str, Any]:
    tasks = sorted(set(spec.show) & ANALYSIS_ITEMS) if spec.kind not in ("family",) else []
    study_index: int | None = 0 if spec.kind not in ("family", "level_curves") else None
    tangents: list[dict[str, Any]] = []
    integrals: list[dict[str, Any]] = []
    for ann in spec.annotations:
        if ann.kind == "tangent":
            tangents.append({"index": ann.expr_index, "at": ann.at})
        elif ann.kind == "area":
            integrals.append(
                {"index": ann.expr_index, "against": ann.against, "between": list(ann.between)}
            )
    return {
        "expressions": [e.expr for e in spec.expressions],
        "declared": list(spec.declared_symbols()),
        "variable": spec.variables[0] if spec.variables else spec.variable,
        "domain": list(spec.domain),
        "study_index": study_index,
        "tasks": tasks,
        "tangents": tangents,
        "integrals": integrals,
    }


def _run_symbolic(
    spec: FunctionFigureSpec, *, timeout: float, target: str
) -> tuple[dict[str, Any] | None, list[str]]:
    """`(esito del figlio | None, avvertenze)`; mai solleva: anche un
    errore di avvio del processo figlio (spawn impossibile, pipe esaurite)
    degrada a `symbolic_failed`, la figura resta numerica."""
    if importlib.util.find_spec("sympy") is None:
        return (None, ["symbolic_unavailable"])
    try:
        result = run_isolated(target, _symbolic_payload(spec), timeout=timeout)
    except FigureTimeoutError:
        log.warning("function_symbolic_timeout", timeout=timeout, hash=spec.content_hash()[:12])
        return (None, ["symbolic_timeout"])
    except FigureComputeError as exc:
        log.warning("function_symbolic_failed", reason=str(exc)[:300])
        return (None, ["symbolic_failed"])
    except Exception as exc:  # avvio del figlio o pickling: mai oltre il motore
        log.warning("function_symbolic_failed", reason=f"{type(exc).__name__}: {exc}"[:300])
        return (None, ["symbolic_failed"])
    if not isinstance(result, dict):
        return (None, ["symbolic_failed"])
    return (result, [str(w) for w in result.get("warnings") or []])


# ---------------------------------------------------------------------------
# Riconciliazione numerico ↔ esatto
# ---------------------------------------------------------------------------


def _entries(sym: Mapping[str, Any] | None, key: str) -> list[Mapping[str, Any]]:
    if sym is None:
        return []
    value = sym.get(key)
    if not isinstance(value, list):
        return []
    return [e for e in value if isinstance(e, Mapping) and isinstance(e.get("x"), (int, float))]


def _take_closest(
    entries: list[Mapping[str, Any]], x: float, tol: float, used: set[int]
) -> Mapping[str, Any] | None:
    best: int | None = None
    best_gap = tol
    for i, entry in enumerate(entries):
        if i in used:
            continue
        gap = abs(float(entry["x"]) - x)
        if gap <= best_gap:
            best, best_gap = i, gap
    if best is None:
        return None
    used.add(best)
    return entries[best]


def _in_domain(spec: FunctionFigureSpec, x: float, tol: float) -> bool:
    lo, hi = spec.domain
    return lo - tol <= x <= hi + tol


def _axis_names(spec: FunctionFigureSpec) -> tuple[str, str]:
    """`(variabile, nome dell'ordinata)` come li scrive `function_plot`
    (`y`, oppure `z` se la variabile è `y`)."""
    var = spec.variable
    return (var, "y" if var != "y" else "z")


def _line_expr(m: float, q: float, *, names: tuple[str, str] = ("x", "y")) -> str:
    """`y = m x + q` con i coefficienti numerici (`format_number`: notazione
    scientifica fuori da [1e-3, 1e6) in modulo) e i nomi degli assi."""
    var, y_name = names
    # Decisioni sul valore formattato: una pendenza di regressione 1,00001
    # si legge «y = t + 2», non «y = 1 t + 2».
    m_text, q_text = format_number(m), format_number(abs(q))
    if m_text == "0":
        return f"{y_name} = {format_number(q)}"
    if m_text == "1":
        head = var
    elif m_text == "−1":
        head = f"−{var}"
    else:
        head = f"{m_text} {var}"
    if q_text == "0":
        return f"{y_name} = {head}"
    sign = "+" if q > 0 else "−"
    return f"{y_name} = {head} {sign} {q_text}"


class _Reconciler:
    def __init__(self, spec: FunctionFigureSpec, study: NumericStudy, sym: dict[str, Any] | None):
        self.spec = spec
        self.study = study
        self.sym = sym
        self.tol = 1e-6 * study.width
        self.f: Callable[[float], float] | None = study.fn
        self.approximate = sym is None
        self.truncated: set[str] = set(study.truncated)
        self.names = _axis_names(spec)

    def _note_exact(self, exact: Any) -> Any:
        if not isinstance(exact, str) or not exact.strip():
            self.approximate = True
            return None
        return exact

    def _finish(
        self, category: str, out: list[dict[str, Any]], *, exact_key: str
    ) -> list[dict[str, Any]]:
        """Ordina per `x`, tronca a `MAX_NOTABLE_POINTS` (le prime voci da
        sinistra) e registra le forme esatte SOLO delle voci mantenute:
        `approximate` riguarda ciò che entra nella figura."""
        out.sort(key=lambda e: e["x"])
        if len(out) > MAX_NOTABLE_POINTS:
            self.truncated.add(category)
            out = out[:MAX_NOTABLE_POINTS]
        for entry in out:
            entry[exact_key] = self._note_exact(entry.get(exact_key))
        return out

    def _in_zero_interval(self, x: float) -> bool:
        return any(a - self.tol <= x <= b + self.tol for a, b in self.study.zero_intervals)

    def zeros(self) -> list[dict[str, Any]]:
        entries = _entries(self.sym, "zeros")
        used: set[int] = set()
        out: list[dict[str, Any]] = []
        for xn in self.study.zeros:
            match = _take_closest(entries, xn, self.tol, used)
            x = float(match["x"]) if match else xn
            out.append({"x": x, "exact": match.get("exact") if match else None})
        for i, entry in enumerate(entries):
            x = float(entry["x"])
            if i in used or not _in_domain(self.spec, x, self.tol) or self.f is None:
                continue
            # Un esatto dentro un plateau di zeri è già rappresentato
            # dall'intervallo; fuori entra solo con `f(x) ≈ 0` locale.
            if self._in_zero_interval(x):
                continue
            if function_numeric.is_zero_at(self.f, x, width=self.study.width):
                out.append({"x": x, "exact": entry.get("exact")})
        return self._finish(CATEGORY_ZEROS, out, exact_key="exact")

    def zero_intervals(self) -> list[list[float]]:
        if self.study.zero_intervals:
            self.approximate = True
        return [[a, b] for a, b in self.study.zero_intervals]

    def critical(self) -> list[dict[str, Any]]:
        entries = _entries(self.sym, "critical_points")
        used: set[int] = set()
        out: list[dict[str, Any]] = []
        for xn, yn, kind in self.study.critical:
            match = _take_closest(entries, xn, self.tol, used)
            out.append(self._point(xn, yn, kind, match))
        for i, entry in enumerate(entries):
            x = float(entry["x"])
            if i in used or not _in_domain(self.spec, x, self.tol) or self.f is None:
                continue
            y = self.f(x)
            if math.isfinite(y) and function_numeric.is_stationary_at(
                self.f, x, width=self.study.width
            ):
                kind = function_numeric.critical_type(self.f, x, width=self.study.width)
                out.append(self._point(x, y, kind, entry))
        return self._finish(CATEGORY_CRITICAL, out, exact_key="exact_x")

    def inflection(self) -> list[dict[str, Any]]:
        entries = _entries(self.sym, "inflection_points")
        used: set[int] = set()
        out: list[dict[str, Any]] = []
        for xn, yn in self.study.inflection:
            match = _take_closest(entries, xn, self.tol, used)
            out.append(self._point(xn, yn, None, match))
        for i, entry in enumerate(entries):
            x = float(entry["x"])
            if i in used or not _in_domain(self.spec, x, self.tol) or self.f is None:
                continue
            y = self.f(x)
            if math.isfinite(y) and function_numeric.inflection_confirmed(
                self.f, x, width=self.study.width
            ):
                out.append(self._point(x, y, None, entry))
        return self._finish(CATEGORY_INFLECTION, out, exact_key="exact_x")

    def _point(
        self, xn: float, yn: float, kind: str | None, match: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        x = float(match["x"]) if match else xn
        y_sym = match.get("y") if match else None
        y = float(y_sym) if isinstance(y_sym, (int, float)) else yn
        entry: dict[str, Any] = {
            "x": x,
            "y": y,
            "exact_x": match.get("exact_x") if match else None,
            "exact_y": match.get("exact_y") if match else None,
        }
        if kind is not None:
            entry["type"] = kind
        elif match is not None and isinstance(match.get("type"), str):
            entry["type"] = str(match["type"])
        return entry

    def _vertical(self, x: float, entry: Mapping[str, Any] | None) -> dict[str, Any]:
        """Voce di asintoto verticale: `expr`/`latex` esatti del figlio se
        brevi, altrimenti `x = <valore>` con il nome della variabile."""
        expr = entry.get("expr") if entry else None
        if not (isinstance(expr, str) and expr):
            expr = f"{self.names[0]} = {format_number(x)}"
        return {
            "kind": "vertical",
            "x": x,
            "expr": str(expr),
            "latex": entry.get("latex") if entry else None,
        }

    def asymptotes(self) -> list[dict[str, Any]]:
        vertical_out: list[dict[str, Any]] = []
        vertical = _entries(self.sym, "vertical_asymptotes")
        used: set[int] = set()
        for xn in self.study.poles:
            match = _take_closest(vertical, xn, self.tol, used)
            if match:
                vertical_out.append(self._vertical(float(match["x"]), match))
            else:
                vertical_out.append(self._vertical(xn, None))
        for i, entry in enumerate(vertical):
            x = float(entry["x"])
            if i in used or not _in_domain(self.spec, x, self.tol) or self.f is None:
                continue
            probe = self.f(x)
            near = max(
                abs(self.f(x - 1e-9 * self.study.width)), abs(self.f(x + 1e-9 * self.study.width))
            )
            if (
                not math.isfinite(probe)
                or near > function_numeric.POLE_MAGNITUDE * self.study.scale
            ):
                vertical_out.append(self._vertical(x, entry))
        out = self._finish(CATEGORY_ASYMPTOTES, vertical_out, exact_key="latex")
        tails_sym = self.sym.get("tail_asymptotes") if self.sym else None
        confirmed: list[tuple[str, float, float]] = []
        if isinstance(tails_sym, list) and self.f is not None:
            for entry in tails_sym:
                if not isinstance(entry, Mapping):
                    continue
                m, q = entry.get("m"), entry.get("q")
                if not (isinstance(m, (int, float)) and isinstance(q, (int, float))):
                    continue
                if function_numeric.tail_confirmed(self.f, float(m), float(q)):
                    confirmed.append((str(entry.get("kind")), float(m), float(q)))
                    expr = entry.get("expr")
                    out.append(
                        {
                            "kind": str(entry.get("kind")),
                            "m": float(m),
                            "q": float(q),
                            "expr": (
                                str(expr)
                                if isinstance(expr, str) and expr
                                else _line_expr(float(m), float(q), names=self.names)
                            ),
                            "latex": self._note_exact(entry.get("latex")),
                        }
                    )
        for kind, m, q in self.study.tails:
            if any(
                k == kind
                and abs(cm - m) <= 1e-2 * (1 + abs(m))
                and abs(cq - q) <= 1e-2 * (1 + abs(q))
                for k, cm, cq in confirmed
            ):
                continue
            self.approximate = True
            out.append(
                {
                    "kind": kind,
                    "m": m,
                    "q": q,
                    "expr": _line_expr(m, q, names=self.names),
                    "latex": None,
                }
            )
        return out

    def discontinuities(self) -> list[float]:
        values = [x for x, _y in self.study.jumps]
        for entry in _entries(self.sym, "discontinuities"):
            x = float(entry["x"])
            if self.f is None or not _in_domain(self.spec, x, self.tol):
                continue
            if any(abs(x - v) <= self.tol for v in values):
                continue
            if not math.isfinite(self.f(x)):
                values.append(x)
        values.sort()
        if len(values) > MAX_NOTABLE_POINTS:
            self.truncated.add(CATEGORY_DISCONTINUITIES)
        return values[:MAX_NOTABLE_POINTS]

    def tangents(self) -> list[dict[str, Any]]:
        sym_list = self.sym.get("tangents") if self.sym else None
        out: list[dict[str, Any]] = []
        for k, t in enumerate(self.study.tangents):
            exact: Any = None
            if (
                isinstance(sym_list, list)
                and k < len(sym_list)
                and isinstance(sym_list[k], Mapping)
            ):
                s = sym_list[k].get("slope")
                if isinstance(s, (int, float)) and abs(float(s) - t.slope) <= 1e-6 * (
                    1 + abs(t.slope)
                ):
                    exact = sym_list[k].get("exact_slope")
            out.append({"at": t.at, "slope": t.slope, "exact_slope": self._note_exact(exact)})
        return out

    def integral(self) -> dict[str, Any] | None:
        if not self.study.areas:
            return None
        area = self.study.areas[0]
        if area.value is None:
            self.approximate = True
            return None
        sym_list = self.sym.get("integrals") if self.sym else None
        exact: Any = None
        if isinstance(sym_list, list) and sym_list and isinstance(sym_list[0], Mapping):
            v = sym_list[0].get("value")
            if isinstance(v, (int, float)) and abs(float(v) - area.value) <= 1e-5 * (
                1 + abs(area.value)
            ):
                exact = sym_list[0].get("exact")
        return {
            "between": [area.between[0], area.between[1]],
            "value": area.value,
            "exact": self._note_exact(exact),
        }

    def latex(self) -> list[str]:
        values = self.sym.get("latex") if self.sym else None
        out: list[str] = []
        if isinstance(values, list):
            out = [v if isinstance(v, str) else "" for v in values]
        while len(out) < len(self.spec.expressions):
            out.append("")
        return out


def build_computed(
    spec: FunctionFigureSpec, study: NumericStudy, sym: dict[str, Any] | None
) -> tuple[dict[str, Any], bool]:
    """`(computed, approximate)`: il dizionario del contratto D9 con le
    forme esatte riconciliate."""
    r = _Reconciler(spec, study, sym)
    show = set(spec.show)
    analysed = spec.kind not in ("family", "level_curves")
    computed: dict[str, Any] = {
        "approximate": False,
        "variable": spec.variable,
        "latex": r.latex(),
        "zeros": r.zeros() if analysed and "zeros" in show else None,
        "zero_intervals": r.zero_intervals() if analysed and "zeros" in show else None,
        "critical_points": r.critical() if analysed and "critical_points" in show else None,
        "inflection_points": (r.inflection() if analysed and "inflection_points" in show else None),
        "asymptotes": r.asymptotes() if analysed and "asymptotes" in show else None,
        "discontinuities": (
            r.discontinuities() if analysed and "discontinuities" in show else None
        ),
        "integral": r.integral() if analysed else None,
        "tangents": r.tangents() if analysed else [],
        "levels": list(study.levels) if study.levels is not None else None,
        "truncated": sorted(r.truncated),
        "warnings": [],
    }
    computed["approximate"] = r.approximate
    return computed, r.approximate


# ---------------------------------------------------------------------------
# Sequenza completa
# ---------------------------------------------------------------------------


def _with_caption(base: FunctionRenderResult, language: str | None) -> FunctionRenderResult:
    return replace(base, computed_caption=function_caption(base.computed, language))


def _failed(reason: str, spec: FunctionFigureSpec, exc: BaseException) -> FunctionRenderError:
    """`FunctionRenderError` con il dettaglio nel log e un messaggio breve
    per il chiamante (il client riceve un testo fisso)."""
    detail = f"{type(exc).__name__}: {exc}"
    log.warning(
        "function_render_failed",
        reason=reason,
        detail=detail[:300],
        hash=spec.content_hash()[:12],
    )
    return FunctionRenderError(f"{reason}: {detail}")


def render_function_sync(
    spec: FunctionFigureSpec,
    *,
    language: str | None,
    symbolic_timeout: float | None = None,
    symbolic_target: str = SYMBOLIC_TARGET,
    deadline: float | None = None,
) -> FunctionRenderResult:
    """Render completo di una spec valida (bloccante). `symbolic_timeout` e
    `symbolic_target` sono parametrizzabili per i test del timeout reale;
    `deadline` (tempo monotono) è la scadenza complessiva del chiamante,
    controllata fra un passo e l'altro."""
    key = result_key(spec)
    hit = _cache_get(key)
    if hit is not None:
        return _with_caption(hit, language)
    settings = get_settings()
    timeout = float(
        symbolic_timeout
        if symbolic_timeout is not None
        else settings.figure_function_timeout_seconds
    )
    _check_deadline(deadline, "numerico")
    try:
        study = function_numeric.analyze(spec)
    except Exception as exc:  # numpy: overflow, forme incompatibili, ...
        raise _failed("calcolo numerico fallito", spec, exc) from exc
    _check_deadline(deadline, "simbolico")
    if deadline is not None:
        timeout = max(0.0, min(timeout, deadline - time.monotonic()))
    sym, warnings = _run_symbolic(spec, timeout=timeout, target=symbolic_target)
    computed, approximate = build_computed(spec, study, sym)
    warnings = [*study.warnings, *warnings]
    _check_deadline(deadline, "disegno")
    try:
        raw_svg, plot_warnings = function_plot.render_svg(
            spec, study, computed, content_hash=spec.content_hash()
        )
        svg = normalize_svg(raw_svg, max_bytes=settings.figure_svg_max_bytes).svg
    except SvgRejectedError as exc:
        raise _failed("svg rifiutato", spec, exc) from exc
    except Exception as exc:  # matplotlib
        raise _failed("disegno fallito", spec, exc) from exc
    warnings.extend(plot_warnings)
    computed["warnings"] = list(dict.fromkeys(warnings))
    base = FunctionRenderResult(
        svg=svg,
        computed=computed,
        latex=list(computed["latex"]),
        warnings=list(computed["warnings"]),
        approximate=approximate,
        computed_caption="",
        content_hash=spec.content_hash(),
    )
    _cache_put(key, base)
    return _with_caption(base, language)


# ---------------------------------------------------------------------------
# Localizzazione D7: label delle espressioni e delle annotazioni
# ---------------------------------------------------------------------------


def _load_json_object(content: str) -> dict[str, Any] | None:
    try:
        data = json.loads(content)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def extract_translatable(content: str) -> dict[str, str]:
    """`{"expressions.0.label": ..., "annotations.1.label": ...}` per le
    label non vuote (contenuto JSON anche non ancora valido come spec)."""
    data = _load_json_object(content)
    if data is None:
        return {}
    out: dict[str, str] = {}
    for key in ("expressions", "annotations"):
        items = data.get(key)
        if not isinstance(items, list):
            continue
        for i, item in enumerate(items):
            if isinstance(item, dict):
                label = item.get("label")
                if isinstance(label, str) and label.strip():
                    out[f"{key}.{i}.label"] = label
    return out


def apply_translations(content: str, tr: Mapping[str, str]) -> str:
    data = _load_json_object(content)
    if data is None or not tr:
        return content
    for path, value in tr.items():
        parts = path.split(".")
        if len(parts) != 3 or parts[2] != "label" or parts[0] not in ("expressions", "annotations"):
            continue
        items = data.get(parts[0])
        try:
            index = int(parts[1])
        except ValueError:
            continue
        if isinstance(items, list) and 0 <= index < len(items) and isinstance(items[index], dict):
            items[index]["label"] = value
    return json.dumps(data, ensure_ascii=False)


__all__ = [
    "FUNCTION_SPEC_INVALID",
    "SYMBOLIC_TARGET",
    "FunctionRenderError",
    "FunctionRenderResult",
    "apply_translations",
    "build_computed",
    "cached_result",
    "clear_result_cache",
    "dependencies_available",
    "extract_translatable",
    "render_function_sync",
    "result_key",
]
