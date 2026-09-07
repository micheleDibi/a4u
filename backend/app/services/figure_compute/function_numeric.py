"""Calcolo numerico del formato `function` (numpy, in thread; Q4).

La figura NON dipende da sympy: campionamento, rami, zeri, punti critici,
flessi, asintoti e integrali sono calcolati qui con numpy a partire
dall'AST già filtrato da `function_parse` (passo 1). `compile_numpy` è un
valutatore ricorsivo dell'AST (`sin → np.sin`, `Pow → np.power` sotto
`errstate(all="ignore")`): niente `eval`, niente `lambdify`. Il calcolo
simbolico del figlio (`function_symbolic`) fornisce solo le forme esatte,
che il servizio riconcilia con i valori numerici di questo modulo.

numpy è importato dentro le funzioni: il modulo resta importabile senza
librerie di calcolo (`figure_render_service` lo importa all'avvio).
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.services.figure_compute.function_parse import ParsedExpr, check_expression

if TYPE_CHECKING:
    from app.schemas.figure_function import FunctionFigureSpec

Array = Any
Evaluator = Callable[[Mapping[str, Any]], Any]

JUMP_FACTOR = 8.0
POLE_MAGNITUDE = 1e6
BISECT_ITERS = 64
GOLDEN_ITERS = 80
INTEGRAL_POINTS = 4001
FILL_POINTS = 400
GRID_POINTS = 160
TAIL_INNER = (1e3, 1e4)
TAIL_OUTER = (1e4, 1e5)

_GOLDEN = (math.sqrt(5) - 1) / 2


@dataclass
class Curve:
    index: int
    label: str
    branches: list[tuple[Array, Array]]
    series: int = 0


@dataclass
class TangentNumeric:
    index: int
    at: float
    y: float
    slope: float
    label: str = ""


@dataclass
class AreaNumeric:
    index: int
    against: int | None
    between: tuple[float, float]
    xs: Array
    lower: Array
    upper: Array
    value: float | None
    label: str = ""


@dataclass
class PointNumeric:
    index: int
    at: float
    y: float
    label: str = ""


@dataclass
class NumericStudy:
    """Esito del calcolo numerico: geometria per il disegno e valori
    approssimati da riconciliare con le forme esatte."""

    xlim: tuple[float, float]
    ylim: tuple[float, float]
    curves: list[Curve] = field(default_factory=list)
    zeros: list[float] = field(default_factory=list)
    critical: list[tuple[float, float, str]] = field(default_factory=list)
    inflection: list[tuple[float, float]] = field(default_factory=list)
    poles: list[float] = field(default_factory=list)
    jumps: list[tuple[float, float]] = field(default_factory=list)
    tails: list[tuple[str, float, float]] = field(default_factory=list)
    tangents: list[TangentNumeric] = field(default_factory=list)
    areas: list[AreaNumeric] = field(default_factory=list)
    points: list[PointNumeric] = field(default_factory=list)
    levels: list[float] | None = None
    grid: tuple[Array, Array, Array] | None = None
    warnings: list[str] = field(default_factory=list)
    scale: float = 1.0
    width: float = 1.0
    fn: Callable[[float], float] | None = None


# ---------------------------------------------------------------------------
# Valutatore AST → numpy
# ---------------------------------------------------------------------------


def compile_numpy(parsed: ParsedExpr) -> Evaluator:
    """Valutatore dell'AST accettato dal passo 1: riceve un ambiente
    `{nome: array | float}` e ritorna un array (o uno scalare numpy) sotto
    `errstate(all="ignore")`: divisioni per zero, logaritmi di numeri
    negativi e overflow producono `inf`/`nan`, che i rami trattano come
    interruzioni."""
    import numpy as np

    unary: dict[str, Callable[[Any], Any]] = {
        "sin": np.sin,
        "cos": np.cos,
        "tan": np.tan,
        "exp": np.exp,
        "log": np.log,
        "sqrt": np.sqrt,
        "abs": np.abs,
        "asin": np.arcsin,
        "acos": np.arccos,
        "atan": np.arctan,
        "sinh": np.sinh,
        "cosh": np.cosh,
        "tanh": np.tanh,
        "floor": np.floor,
    }
    constants = {"pi": np.float64(math.pi), "E": np.float64(math.e)}

    def ev(node: ast.AST, env: Mapping[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError(f"costante non valutabile: {node.value!r}")
            return np.float64(node.value)
        if isinstance(node, ast.Name):
            if node.id in constants:
                return constants[node.id]
            return env[node.id]
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand, env)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp):
            a = ev(node.left, env)
            b = ev(node.right, env)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.Div):
                return np.true_divide(a, b)
            return np.power(a, b)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            args = [ev(a, env) for a in node.args]
            if node.func.id == "log" and len(args) == 2:
                return np.true_divide(np.log(args[0]), np.log(args[1]))
            return unary[node.func.id](args[0])
        raise ValueError(f"nodo non valutabile: {type(node).__name__}")

    body = parsed.tree.body

    def evaluate(env: Mapping[str, Any]) -> Any:
        with np.errstate(all="ignore"):
            return ev(body, env)

    return evaluate


def sample(fn: Evaluator, xs: Array, *, variable: str, env: Mapping[str, float]) -> Array:
    """`f(xs)` come array float della forma di `xs` (le costanti vengono
    propagate)."""
    import numpy as np

    values = np.asarray(fn({**env, variable: xs}), dtype=float)
    if values.shape != np.shape(xs):
        values = np.broadcast_to(values, np.shape(xs)).copy()
    return values


def scalar_function(
    fn: Evaluator, *, variable: str, env: Mapping[str, float]
) -> Callable[[float], float]:
    """`f: float → float` (nan/inf dove non definita)."""
    import numpy as np

    def f(x: float) -> float:
        with np.errstate(all="ignore"):
            value = fn({**env, variable: np.float64(x)})
        try:
            return float(value)
        except (TypeError, ValueError):
            return math.nan

    return f


# ---------------------------------------------------------------------------
# Strumenti di ricerca
# ---------------------------------------------------------------------------


def _bisect(g: Callable[[float], float], a: float, b: float) -> float:
    """Radice di `g` con cambio di segno fra `a` e `b`."""
    ga = g(a)
    for _ in range(BISECT_ITERS):
        m = 0.5 * (a + b)
        gm = g(m)
        if not math.isfinite(gm):
            return m
        if gm == 0.0:
            return m
        if (gm < 0) == (ga < 0):
            a, ga = m, gm
        else:
            b = m
    return 0.5 * (a + b)


def _golden_min(g: Callable[[float], float], a: float, b: float) -> float:
    """Argmin di `g` (unimodale) su `[a, b]`; i valori non finiti valgono +inf."""

    def val(x: float) -> float:
        v = g(x)
        return v if math.isfinite(v) else math.inf

    c = b - _GOLDEN * (b - a)
    d = a + _GOLDEN * (b - a)
    fc, fd = val(c), val(d)
    for _ in range(GOLDEN_ITERS):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - _GOLDEN * (b - a)
            fc = val(c)
        else:
            a, c, fc = c, d, fd
            d = a + _GOLDEN * (b - a)
            fd = val(d)
    return 0.5 * (a + b)


def derivative(f: Callable[[float], float], x: float, *, order: int = 1) -> float:
    """Derivata numerica centrale (ordine 1 o 2)."""
    if order == 1:
        h = 1e-6 * (1.0 + abs(x))
        return (f(x + h) - f(x - h)) / (2.0 * h)
    h = 1e-4 * (1.0 + abs(x))
    return (f(x + h) - 2.0 * f(x) + f(x - h)) / (h * h)


def _dedupe(values: list[float], tol: float) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or abs(v - out[-1]) > tol:
            out.append(v)
    return out


def _in_regions(a: float, b: float, regions: Sequence[tuple[float, float]]) -> bool:
    return any(a <= r1 and b >= r0 for r0, r1 in regions)


def _merge_regions(regions: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(regions):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def data_scale(ys: Array) -> float:
    """Scala tipica di `|y|` (mediana dei valori finiti, almeno 1)."""
    import numpy as np

    finite = ys[np.isfinite(ys)]
    if finite.size == 0:
        return 1.0
    return max(1.0, float(np.median(np.abs(finite))))


# ---------------------------------------------------------------------------
# Rami, zeri, punti critici, flessi, singolarità, code
# ---------------------------------------------------------------------------


def split_branches(
    xs: Array, ys: Array
) -> tuple[list[tuple[Array, Array]], list[tuple[float, float]]]:
    """Spezza il campione dove `y` non è finito o dove `|Δy| > 8·mediana`
    con `|y|` crescente verso il taglio da entrambi i lati (asintoto
    verticale o salto). Ritorna i rami (solo punti finiti, ≥ 2 per ramo)
    e le regioni `[a, b]` dei tagli, fuse quando contigue."""
    import numpy as np

    n = len(xs)
    finite = np.isfinite(ys)
    cut_after = np.zeros(max(n - 1, 0), dtype=bool)
    regions: list[tuple[float, float]] = []
    if n >= 2:
        both = finite[:-1] & finite[1:]
        safe = np.where(finite, ys, 0.0)
        dy = np.abs(np.diff(safe))
        if both.any():
            med = float(np.median(dy[both]))
            threshold = JUMP_FACTOR * max(med, 1e-12 * data_scale(ys))
            ay = np.abs(safe)
            for jump_at in np.flatnonzero(both & (dy > threshold)):
                k = int(jump_at)
                # Servono entrambi i vicini finiti: al bordo del dominio (o
                # accanto a un campione non finito, che taglia da sé) un
                # salto non è mai un asintoto.
                left_grows = k >= 1 and bool(finite[k - 1]) and ay[k] >= ay[k - 1]
                right_grows = k + 2 < n and bool(finite[k + 2]) and ay[k + 1] >= ay[k + 2]
                if left_grows and right_grows:
                    cut_after[k] = True
                    regions.append((float(xs[k]), float(xs[k + 1])))
        for bad_at in np.flatnonzero(~finite):
            j = int(bad_at)
            if j > 0:
                cut_after[j - 1] = True
            if j < n - 1:
                cut_after[j] = True
            regions.append((float(xs[max(j - 1, 0)]), float(xs[min(j + 1, n - 1)])))
    branches: list[tuple[Array, Array]] = []
    start = 0
    for i in range(n):
        if i == n - 1 or cut_after[i]:
            seg_x, seg_y = xs[start : i + 1], ys[start : i + 1]
            mask = np.isfinite(seg_y)
            if int(mask.sum()) >= 2:
                branches.append((seg_x[mask], seg_y[mask]))
            start = i + 1
    return branches, _merge_regions(regions)


def classify_cuts(
    f: Callable[[float], float],
    regions: Sequence[tuple[float, float]],
    *,
    scale: float,
    width: float,
) -> tuple[list[float], list[tuple[float, float]]]:
    """Per ogni regione di taglio: asintoto verticale (|f| → oltre
    `POLE_MAGNITUDE·scale` nel punto di minimo di |1/f|), salto (la
    discontinuità persiste bisecando) oppure nulla (funzione ripida ma
    continua, o bordo del dominio di f). Ritorna `(poli, salti (x, y))`."""
    poles: list[float] = []
    jumps: list[tuple[float, float]] = []
    tol = 1e-9 * width
    for a, b in regions:
        if not b > a:
            continue

        def inv(x: float) -> float:
            v = f(x)
            if math.isnan(v):
                return math.inf
            return abs(1.0 / v) if v != 0.0 else math.inf

        x_star = _golden_min(inv, a, b)
        v_star = f(x_star)
        if not math.isfinite(v_star) or abs(v_star) > POLE_MAGNITUDE * scale:
            poles.append(x_star)
            continue
        fa, fb = f(a), f(b)
        if not (math.isfinite(fa) and math.isfinite(fb)):
            continue
        original = abs(fb - fa)
        lo, hi, flo, fhi = a, b, fa, fb
        while hi - lo > tol:
            mid = 0.5 * (lo + hi)
            fm = f(mid)
            if not math.isfinite(fm):
                break
            if abs(fm - flo) >= abs(fhi - fm):
                hi, fhi = mid, fm
            else:
                lo, flo = mid, fm
        if abs(fhi - flo) >= 0.5 * original and original > 1e-9 * scale:
            jumps.append((0.5 * (lo + hi), 0.5 * (flo + fhi)))
    return _dedupe(poles, tol), jumps


def find_zeros(
    f: Callable[[float], float],
    xs: Array,
    ys: Array,
    regions: Sequence[tuple[float, float]],
    *,
    scale: float,
    width: float,
) -> list[float]:
    """Zeri: cambi di segno (bisezione), campioni esattamente nulli, zeri
    di tangenza (minimi locali di |y| raffinati) ed estremi del dominio."""
    import numpy as np

    n = len(xs)
    finite = np.isfinite(ys)
    tol_val = 1e-9 * scale
    out: list[float] = []
    for i in range(n - 1):
        if not (finite[i] and finite[i + 1]):
            continue
        a, b = float(xs[i]), float(xs[i + 1])
        if _in_regions(a, b, regions):
            continue
        if ys[i] == 0.0:
            out.append(a)
        elif ys[i] * ys[i + 1] < 0:
            x_star = _bisect(f, a, b)
            if abs(f(x_star)) <= 1e-6 * scale:
                out.append(x_star)
    if n and finite[-1] and ys[-1] == 0.0:
        out.append(float(xs[-1]))
    ay = np.abs(np.where(finite, ys, np.inf))
    for i in range(1, n - 1):
        if not (finite[i - 1] and finite[i] and finite[i + 1]):
            continue
        # `<=` a sinistra: su una griglia simmetrica i due campioni più
        # vicini allo zero hanno lo stesso |y| (x² a ±0,00375).
        if ay[i] <= ay[i - 1] and ay[i] < ay[i + 1] and ys[i - 1] * ys[i + 1] > 0:
            a, b = float(xs[i - 1]), float(xs[i + 1])
            if _in_regions(a, b, regions):
                continue
            x_star = _golden_min(lambda x: abs(f(x)), a, b)
            if abs(f(x_star)) <= tol_val:
                out.append(x_star)
    for edge in (float(xs[0]), float(xs[-1])):
        v = f(edge)
        if math.isfinite(v) and abs(v) <= tol_val:
            out.append(edge)
    return _dedupe(out, 1e-9 * width)


def find_critical(
    f: Callable[[float], float],
    xs: Array,
    regions: Sequence[tuple[float, float]],
    *,
    scale: float,
    width: float,
) -> list[tuple[float, float, str]]:
    """Punti critici `(x, y, tipo)`: cambi di segno della derivata centrale
    (bisezione) e stazionari di tangenza; il tipo viene dalla derivata
    seconda numerica (`max`, `min`, `stationary`)."""
    import numpy as np

    n = len(xs)
    d = np.array([derivative(f, float(x)) for x in xs], dtype=float)
    finite = np.isfinite(d)

    def d1(x: float) -> float:
        return derivative(f, x)

    candidates: list[float] = []
    for i in range(n - 1):
        if not (finite[i] and finite[i + 1]):
            continue
        a, b = float(xs[i]), float(xs[i + 1])
        if _in_regions(a, b, regions):
            continue
        if d[i] == 0.0:
            candidates.append(a)
        elif d[i] * d[i + 1] < 0:
            candidates.append(_bisect(d1, a, b))
    ad = np.abs(np.where(finite, d, np.inf))
    for i in range(1, n - 1):
        if not (finite[i - 1] and finite[i] and finite[i + 1]):
            continue
        if ad[i] <= ad[i - 1] and ad[i] < ad[i + 1] and d[i - 1] * d[i + 1] > 0:
            a, b = float(xs[i - 1]), float(xs[i + 1])
            if _in_regions(a, b, regions):
                continue
            x_star = _golden_min(lambda x: abs(d1(x)), a, b)
            if abs(d1(x_star)) <= 1e-7 * scale:
                candidates.append(x_star)
    out: list[tuple[float, float, str]] = []
    for x_star in _dedupe(candidates, 1e-9 * width):
        y_star = f(x_star)
        if not math.isfinite(y_star) or abs(d1(x_star)) > 1e-4 * scale:
            continue
        out.append((x_star, y_star, critical_type(f, x_star, scale=scale)))
    return out


def critical_type(f: Callable[[float], float], x: float, *, scale: float) -> str:
    d2 = derivative(f, x, order=2)
    if not math.isfinite(d2) or abs(d2) <= 1e-6 * scale:
        return "stationary"
    return "max" if d2 < 0 else "min"


def find_inflection(
    f: Callable[[float], float],
    xs: Array,
    regions: Sequence[tuple[float, float]],
    *,
    scale: float,
    width: float,
) -> list[tuple[float, float]]:
    """Flessi `(x, y)`: cambi di segno della derivata seconda centrale con
    ampiezza sopra il rumore numerico, raffinati per bisezione e
    confermati da un cambio di segno persistente attorno al punto."""
    import numpy as np

    n = len(xs)
    d2 = np.array([derivative(f, float(x), order=2) for x in xs], dtype=float)
    finite = np.isfinite(d2)
    floor = 1e-5 * scale

    def g(x: float) -> float:
        return derivative(f, x, order=2)

    out: list[float] = []
    for i in range(n - 1):
        if not (finite[i] and finite[i + 1]) or d2[i] * d2[i + 1] >= 0:
            continue
        if max(abs(d2[i]), abs(d2[i + 1])) < floor:
            continue
        a, b = float(xs[i]), float(xs[i + 1])
        if _in_regions(a, b, regions):
            continue
        x_star = _bisect(g, a, b)
        if inflection_confirmed(f, x_star, width=width):
            out.append(x_star)
    result: list[tuple[float, float]] = []
    for x_star in _dedupe(out, 1e-9 * width):
        y_star = f(x_star)
        if math.isfinite(y_star):
            result.append((x_star, y_star))
    return result


def inflection_confirmed(f: Callable[[float], float], x: float, *, width: float) -> bool:
    delta = 1e-3 * width
    left = derivative(f, x - delta, order=2)
    right = derivative(f, x + delta, order=2)
    return math.isfinite(left) and math.isfinite(right) and left * right < 0


def oblique_or_horizontal(
    fn: Evaluator, *, variable: str, env: Mapping[str, float]
) -> list[tuple[str, float, float]]:
    """Asintoti obliqui/orizzontali stimati per regressione lineare su due
    code (`[1e3, 1e4]` e `[1e4, 1e5]`, per lato): accettati solo se le
    due stime coincidono. Ritorna `(kind, m, q)` senza duplicati."""
    import numpy as np

    out: list[tuple[str, float, float]] = []
    for sign in (1.0, -1.0):
        fits: list[tuple[float, float]] = []
        for lo, hi in (TAIL_INNER, TAIL_OUTER):
            xs = sign * np.geomspace(lo, hi, 48)
            ys = sample(fn, xs, variable=variable, env=env)
            if not np.all(np.isfinite(ys)):
                break
            m, q = np.polyfit(xs, ys, 1)
            resid = float(np.sqrt(np.mean((ys - (m * xs + q)) ** 2)))
            if not (math.isfinite(m) and math.isfinite(q)) or resid > 1e-3 * (1.0 + abs(q)):
                break
            fits.append((float(m), float(q)))
        if len(fits) != 2:
            continue
        (m1, q1), (m2, q2) = fits
        if abs(m1 - m2) > 1e-4 * (1.0 + abs(m2)) or abs(q1 - q2) > 1e-3 * (1.0 + abs(q2)):
            continue
        m, q = m2, q2
        if abs(m) < 1e-6:
            m = 0.0
        kind = "horizontal" if m == 0.0 else "oblique"
        # Stesse rette da entrambi i lati: le stime per regressione
        # differiscono di ~1e-4, la tolleranza di fusione è relativa 1e-3.
        duplicate = any(
            k == kind
            and abs(pm - m) <= 1e-3 * (1.0 + abs(m))
            and abs(pq - q) <= 1e-3 * (1.0 + abs(q))
            for k, pm, pq in out
        )
        if not duplicate:
            out.append((kind, m, q))
    return out


def tail_confirmed(f: Callable[[float], float], m: float, q: float) -> bool:
    """`f(X) − (mX + q) → 0` su X crescente (entrambi i lati basta uno)."""
    for sign in (1.0, -1.0):
        gaps: list[float] = []
        for big in (1e4, 1e6, 1e8):
            x = sign * big
            v = f(x) - (m * x + q)
            if not math.isfinite(v):
                break
            gaps.append(abs(v))
        if len(gaps) == 3 and gaps[2] <= gaps[0] + 1e-12 and gaps[2] < 1e-3 * (1.0 + abs(q)):
            return True
    return False


def simpson(f: Callable[[float], float], a: float, b: float) -> float | None:
    """Integrale composito di Simpson su `INTEGRAL_POINTS` nodi; `None`
    se la funzione non è finita in un nodo."""
    import numpy as np

    xs = np.linspace(a, b, INTEGRAL_POINTS)
    try:
        ys = np.array([f(float(x)) for x in xs], dtype=float)
    except (ZeroDivisionError, OverflowError, ValueError):
        return None
    if not np.all(np.isfinite(ys)):
        return None
    h = (b - a) / (INTEGRAL_POINTS - 1)
    total = ys[0] + ys[-1] + 4.0 * ys[1:-1:2].sum() + 2.0 * ys[2:-1:2].sum()
    return float(total * h / 3.0)


# ---------------------------------------------------------------------------
# Studio completo di una spec
# ---------------------------------------------------------------------------


def _auto_ylim(values: Array, *, include: Sequence[float], span_hint: float) -> tuple[float, float]:
    import numpy as np

    finite = values[np.isfinite(values)]
    if finite.size:
        lo = float(np.percentile(finite, 3))
        hi = float(np.percentile(finite, 97))
    else:
        lo, hi = -1.0, 1.0
    for v in include:
        if math.isfinite(v):
            lo, hi = min(lo, v), max(hi, v)
    lo, hi = min(lo, 0.0), max(hi, 0.0)
    if hi - lo < 1e-9:
        lo, hi = lo - 0.5 * span_hint, hi + 0.5 * span_hint
    pad = 0.08 * (hi - lo)
    return (lo - pad, hi + pad)


def _parsed_expressions(spec: FunctionFigureSpec) -> list[ParsedExpr]:
    declared = spec.declared_symbols()
    return [check_expression(e.expr, free_symbols=declared) for e in spec.expressions]


def analyze(spec: FunctionFigureSpec) -> NumericStudy:
    """Studio numerico completo della spec (già validata)."""
    if spec.kind == "level_curves":
        return _analyze_levels(spec)
    if spec.kind == "family":
        return _analyze_family(spec)
    return _analyze_curves(spec)


def _analyze_curves(spec: FunctionFigureSpec) -> NumericStudy:
    import numpy as np

    lo, hi = spec.domain
    width = hi - lo
    var = spec.variable
    env: dict[str, float] = {}
    parsed = _parsed_expressions(spec)
    evaluators = [compile_numpy(p) for p in parsed]
    xs = np.linspace(lo, hi, spec.sampling.points)
    study = NumericStudy(xlim=(lo, hi), ylim=(-1.0, 1.0), width=width)
    samples: list[Array] = []
    regions_by_expr: list[list[tuple[float, float]]] = []
    for i, fn in enumerate(evaluators):
        ys = sample(fn, xs, variable=var, env=env)
        branches, regions = split_branches(xs, ys)
        samples.append(ys)
        regions_by_expr.append(regions)
        study.curves.append(Curve(index=i, label=spec.expression_label(i), branches=branches))
        if not branches:
            study.warnings.append(f"expression_{i}_undefined")

    include: list[float] = []
    f0 = scalar_function(evaluators[0], variable=var, env=env)
    study.fn = f0
    study.scale = data_scale(samples[0])
    show = set(spec.show)
    regions0 = regions_by_expr[0]
    if show & {"asymptotes", "discontinuities"}:
        study.poles, study.jumps = classify_cuts(f0, regions0, scale=study.scale, width=width)
    if "zeros" in show:
        study.zeros = find_zeros(f0, xs, samples[0], regions0, scale=study.scale, width=width)
    if "critical_points" in show:
        study.critical = find_critical(f0, xs, regions0, scale=study.scale, width=width)
        include.extend(y for _x, y, _t in study.critical)
    if "inflection_points" in show:
        study.inflection = find_inflection(f0, xs, regions0, scale=study.scale, width=width)
        include.extend(y for _x, y in study.inflection)
    if "asymptotes" in show:
        study.tails = oblique_or_horizontal(evaluators[0], variable=var, env=env)

    for ann in spec.annotations:
        fn_i = scalar_function(evaluators[ann.expr_index], variable=var, env=env)
        if ann.kind == "tangent":
            y = fn_i(ann.at)
            slope = derivative(fn_i, ann.at)
            if math.isfinite(y) and math.isfinite(slope):
                study.tangents.append(TangentNumeric(ann.expr_index, ann.at, y, slope, ann.label))
                include.append(y)
            else:
                study.warnings.append("tangent_undefined")
        elif ann.kind == "area":
            a, b = ann.between
            fx = np.linspace(a, b, FILL_POINTS)
            upper = sample(evaluators[ann.expr_index], fx, variable=var, env=env)
            g: Callable[[float], float] = _zero
            if ann.against is not None:
                lower = sample(evaluators[ann.against], fx, variable=var, env=env)
                g = scalar_function(evaluators[ann.against], variable=var, env=env)
            else:
                lower = np.zeros_like(fx)
            value = simpson(_difference(fn_i, g), a, b)
            if value is None:
                study.warnings.append("integral_undefined")
            study.areas.append(
                AreaNumeric(ann.expr_index, ann.against, (a, b), fx, lower, upper, value, ann.label)
            )
        else:
            y = fn_i(ann.at)
            if math.isfinite(y):
                study.points.append(PointNumeric(ann.expr_index, ann.at, y, ann.label))
                include.append(y)
            else:
                study.warnings.append("point_undefined")

    if spec.range is not None:
        study.ylim = spec.range
    else:
        study.ylim = _auto_ylim(np.concatenate(samples), include=include, span_hint=width)
    return study


def _analyze_family(spec: FunctionFigureSpec) -> NumericStudy:
    import numpy as np

    lo, hi = spec.domain
    width = hi - lo
    var = spec.variable
    parsed = _parsed_expressions(spec)
    fn = compile_numpy(parsed[0])
    xs = np.linspace(lo, hi, spec.sampling.points)
    study = NumericStudy(xlim=(lo, hi), ylim=(-1.0, 1.0), width=width)
    assert spec.parameter is not None  # garantito da `check_function_spec`
    name = spec.parameter.name
    samples: list[Array] = []
    for k, value in enumerate(spec.parameter.values):
        ys = sample(fn, xs, variable=var, env={name: value})
        branches, _regions = split_branches(xs, ys)
        samples.append(ys)
        label = f"{name} = {_short_number(value)}"
        study.curves.append(Curve(index=k, label=label, branches=branches, series=k))
        if not branches:
            study.warnings.append(f"series_{k}_undefined")
    study.scale = data_scale(np.concatenate(samples))
    if spec.range is not None:
        study.ylim = spec.range
    else:
        study.ylim = _auto_ylim(np.concatenate(samples), include=[], span_hint=width)
    return study


def _analyze_levels(spec: FunctionFigureSpec) -> NumericStudy:
    import numpy as np

    assert spec.variables is not None  # garantito da `check_function_spec`
    xv, yv = spec.variables
    xlo, xhi = spec.domain
    ylo, yhi = spec.range if spec.range is not None else spec.domain
    parsed = _parsed_expressions(spec)
    fn = compile_numpy(parsed[0])
    gx = np.linspace(xlo, xhi, GRID_POINTS)
    gy = np.linspace(ylo, yhi, GRID_POINTS)
    xx, yy = np.meshgrid(gx, gy)
    with np.errstate(all="ignore"):
        zz = np.asarray(fn({xv: xx, yv: yy}), dtype=float)
    if zz.shape != xx.shape:
        zz = np.broadcast_to(zz, xx.shape).copy()
    study = NumericStudy(xlim=(xlo, xhi), ylim=(ylo, yhi), width=xhi - xlo)
    study.scale = data_scale(zz.ravel())
    finite = zz[np.isfinite(zz)]
    if isinstance(spec.levels, int):
        if finite.size == 0:
            study.warnings.append("levels_undefined")
            levels: list[float] = []
        else:
            zlo, zhi = float(np.percentile(finite, 5)), float(np.percentile(finite, 95))
            if zhi - zlo < 1e-12:
                zlo, zhi = zlo - 1.0, zhi + 1.0
            levels = nice_levels(zlo, zhi, spec.levels)
    else:
        levels = sorted(float(v) for v in (spec.levels or []))
    study.levels = levels
    study.grid = (xx, yy, zz)
    return study


def _short_number(value: float) -> str:
    text = f"{value:.4g}"
    return text.replace("-", "−")


_NICE_STEPS = (1.0, 2.0, 2.5, 5.0, 10.0)


def nice_levels(lo: float, hi: float, n: int) -> list[float]:
    """`n` livelli «tondi» (passo 1·2·2,5·5·10 × potenza di dieci) dentro
    `[lo, hi]`; se nessun passo tondo ne fa entrare `n`, ripiega su
    `linspace`."""
    if n <= 0 or not hi > lo:
        return []
    raw = (hi - lo) / n
    magnitude = 10.0 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    candidates = sorted(
        {s * magnitude / 10 for s in _NICE_STEPS} | {s * magnitude for s in _NICE_STEPS},
        reverse=True,
    )
    for step in candidates:
        start = math.ceil(lo / step) * step
        levels = [start + k * step for k in range(n)]
        if levels[-1] <= hi + 1e-9 * (hi - lo):
            return [float(round(v, 10)) + 0.0 for v in levels]
    return [float(v) for v in ((lo + (hi - lo) * k / (n - 1)) for k in range(n))] if n > 1 else [lo]


def _zero(_x: float) -> float:
    return 0.0


def _difference(
    f: Callable[[float], float], g: Callable[[float], float]
) -> Callable[[float], float]:
    def h(x: float) -> float:
        return f(x) - g(x)

    return h


__all__ = [
    "AreaNumeric",
    "Curve",
    "Evaluator",
    "NumericStudy",
    "PointNumeric",
    "TangentNumeric",
    "analyze",
    "classify_cuts",
    "compile_numpy",
    "critical_type",
    "data_scale",
    "derivative",
    "find_critical",
    "find_inflection",
    "find_zeros",
    "inflection_confirmed",
    "nice_levels",
    "oblique_or_horizontal",
    "sample",
    "scalar_function",
    "simpson",
    "split_branches",
    "tail_confirmed",
]
