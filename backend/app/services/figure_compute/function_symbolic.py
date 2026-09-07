"""Calcolo simbolico del formato `function` (sympy, SOLO nel processo figlio).

`analyze_symbolic(payload)` è il bersaglio di `isolated.run_isolated`:
`sympy.solve` non è interrompibile da un thread, quindi il calcolo gira in
un processo `spawn` ucciso allo scadere di
`settings.figure_function_timeout_seconds`. Il modulo importa sympy solo
dentro le funzioni e non importa nulla dell'applicazione: il figlio lo
carica dal disco con `sys.path` del padre.

Passo 2 del parsing: `parse_expr` con `transformations=
standard_transformations` e `global_dict` ristretto (`Integer`, `Float`,
`Rational`, `Symbol`, `pi`, `E`, le funzioni della whitelist, `abs` →
`Abs`, `__builtins__` vuoto) sulla STESSA stringa già accettata dal passo
1 (`function_parse`); controllo finale `free_symbols ⊆ dichiarati`. I
casi verificati su sympy 1.14 sono fissati da
`tests/test_function_figure_service.py` (documento 17, §4.2).

Ogni risultato porta il valore numerico e, quando esiste una forma chiusa
breve, la forma esatta in LaTeX (`nsimplify` per i Float + `latex(...,
ln_notation=True, fold_short_frac=False)`), che il servizio riconcilia con
il calcolo numerico del padre: nessun numero entra nella figura senza
riscontro numerico.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

MAX_EXACT_LATEX = 80
MAX_RATIONAL_DENOMINATOR = 10_000
MAX_EXACT_OPS = 12
NSIMPLIFY_TOL = 1e-9
INTERVAL_EPS = 1e-9

_SYMPY_FUNCTIONS: tuple[str, ...] = (
    "sin",
    "cos",
    "tan",
    "exp",
    "log",
    "sqrt",
    "asin",
    "acos",
    "atan",
    "sinh",
    "cosh",
    "tanh",
    "floor",
)


def sympy_namespace() -> dict[str, Any]:
    """`global_dict` ristretto di `parse_expr` (D9)."""
    import sympy as sp

    namespace: dict[str, Any] = {
        "__builtins__": {},
        "Integer": sp.Integer,
        "Float": sp.Float,
        "Rational": sp.Rational,
        "Symbol": sp.Symbol,
        "pi": sp.pi,
        "E": sp.E,
        "abs": sp.Abs,
    }
    for name in _SYMPY_FUNCTIONS:
        namespace[name] = getattr(sp, name)
    return namespace


def parse_sympy(src: str, declared: Iterable[str]) -> Any:
    """Passo 2: espressione sympy con i soli simboli dichiarati (reali).
    Solleva `ValueError` se compaiono simboli liberi non dichiarati; ogni
    altro errore di `parse_expr` (NameError, SyntaxError, TypeError)
    risale al chiamante."""
    import sympy as sp
    from sympy.parsing.sympy_parser import parse_expr, standard_transformations

    local_dict = {name: sp.Symbol(name, real=True) for name in declared}
    expr = parse_expr(
        src,
        transformations=standard_transformations,
        global_dict=sympy_namespace(),
        local_dict=local_dict,
        evaluate=True,
    )
    expr = sp.sympify(expr)
    free = set(getattr(expr, "free_symbols", set()))
    extra = free - set(local_dict.values())
    if extra:
        raise ValueError("simboli non dichiarati: " + ", ".join(sorted(str(s) for s in extra)))
    return expr


def _latex(value: Any) -> str:
    import sympy as sp

    return str(sp.latex(value, ln_notation=True, fold_short_frac=False))


def _is_real_number(value: Any) -> bool:
    return not getattr(value, "free_symbols", set()) and bool(getattr(value, "is_real", False))


def _to_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def exact_form(value: Any) -> tuple[float | None, str | None]:
    """`(valore numerico, LaTeX esatto | None)`: il LaTeX c'è solo per forme
    chiuse brevi; i Float passano da `nsimplify` e sono accettati solo se
    coincidono entro `NSIMPLIFY_TOL`."""
    import sympy as sp

    if getattr(value, "free_symbols", set()):
        return (None, None)
    f = _to_float(value)
    if f is None:
        return (None, None)
    exact = value
    if exact.has(sp.Float):
        try:
            candidate = sp.nsimplify(exact, [sp.pi, sp.E], rational=True, tolerance=NSIMPLIFY_TOL)
        except (ValueError, TypeError, RecursionError):
            return (f, None)
        cf = _to_float(candidate)
        if candidate.has(sp.Float) or cf is None or abs(cf - f) >= NSIMPLIFY_TOL:
            return (f, None)
        # `nsimplify` trova sempre un razionale entro la tolleranza: una
        # frazione con denominatore enorme o una combinazione lunga di π ed
        # e non è una forma «esatta» ma un'approssimazione travestita.
        if candidate.is_Rational and int(candidate.q) > MAX_RATIONAL_DENOMINATOR:
            return (f, None)
        if int(sp.count_ops(candidate)) > MAX_EXACT_OPS:
            return (f, None)
        exact = candidate
    if exact.has(sp.CRootOf) or exact.has(sp.RootSum):
        return (f, None)
    try:
        text = _latex(exact)
    except (ValueError, TypeError, RecursionError):
        return (f, None)
    if len(text) > MAX_EXACT_LATEX:
        return (f, None)
    return (f, text)


def _simplified(value: Any) -> Any:
    """`simplify` guardato: i valori di `subs`/`integrate` escono spesso in
    forma non ridotta (`-√3·(−1 + (2 − √3)²)/3` invece di `4 − 2√3`)."""
    import sympy as sp

    try:
        return sp.simplify(value)
    except (ValueError, TypeError, RecursionError):
        return value


def _exactify(value: float) -> Any:
    """Numero esatto per un float «notevole» (`0.5 → 1/2`, `3.14159… → π`),
    altrimenti `Float`."""
    import sympy as sp

    try:
        candidate = sp.nsimplify(value, [sp.pi, sp.E], rational=True, tolerance=1e-12)
        cf = _to_float(candidate)
        if cf is not None and abs(cf - value) <= 1e-12 * (1.0 + abs(value)):
            return candidate
    except (ValueError, TypeError, RecursionError):
        pass
    return sp.Float(value)


def _solve_in(expr: Any, x: Any, lo: float, hi: float, warnings: list[str], what: str) -> list[Any]:
    """Soluzioni reali di `expr = 0` in `[lo, hi]` (bordi allargati di
    `INTERVAL_EPS·ampiezza` per non perdere gli zeri agli estremi)."""
    import sympy as sp

    eps = INTERVAL_EPS * (hi - lo)
    interval = sp.Interval(lo - eps, hi + eps)
    try:
        solutions = sp.solveset(expr, x, interval)
    except (NotImplementedError, ValueError, TypeError, RecursionError):
        solutions = None
    items: list[Any] = []
    if isinstance(solutions, sp.FiniteSet):
        items = list(solutions)
    else:
        try:
            for s in sp.solve(expr, x):
                if _is_real_number(s):
                    v = _to_float(s)
                    if v is not None and lo - eps <= v <= hi + eps:
                        items.append(s)
        except (NotImplementedError, ValueError, TypeError, RecursionError):
            warnings.append(f"symbolic_{what}_unsupported")
            return []
    real_items = [s for s in items if _is_real_number(s) and _to_float(s) is not None]
    return sorted(real_items, key=lambda s: float(s))


def _singular_points(expr: Any, x: Any, lo: float, hi: float, warnings: list[str]) -> list[Any]:
    import sympy as sp

    eps = INTERVAL_EPS * (hi - lo)
    interval = sp.Interval(lo - eps, hi + eps)
    try:
        found = sp.singularities(expr, x, interval)
    except (NotImplementedError, ValueError, TypeError, RecursionError):
        found = None
    if isinstance(found, sp.FiniteSet):
        return sorted(
            (s for s in found if _is_real_number(s) and _to_float(s) is not None),
            key=lambda s: float(s),
        )
    if found is not None and found == sp.S.EmptySet:
        return []
    try:
        denominator = sp.denom(sp.together(expr))
        return _solve_in(denominator, x, lo, hi, warnings, "singularities")
    except (NotImplementedError, ValueError, TypeError, RecursionError):
        warnings.append("symbolic_singularities_unsupported")
        return []


def _is_infinite(value: Any) -> bool:
    import sympy as sp

    return value in (sp.oo, -sp.oo, sp.zoo) or bool(getattr(value, "is_infinite", False))


def _finite_real(value: Any) -> bool:
    return bool(getattr(value, "is_real", False)) and bool(getattr(value, "is_finite", False))


def _limit(expr: Any, x: Any, point: Any, direction: str = "+") -> Any:
    import sympy as sp

    try:
        return sp.limit(expr, x, point, direction)
    except (NotImplementedError, ValueError, TypeError, RecursionError):
        return sp.nan


def _tail(expr: Any, x: Any, y: Any, direction: Any) -> dict[str, Any] | None:
    import sympy as sp

    m = _limit(expr / x, x, direction)
    if not _finite_real(m):
        return None
    q = _limit(expr - m * x, x, direction)
    if not _finite_real(q):
        return None
    mf, qf = _to_float(m), _to_float(q)
    if mf is None or qf is None:
        return None
    kind = "horizontal" if m == 0 else "oblique"
    return {
        "kind": kind,
        "m": mf,
        "q": qf,
        "expr": f"y = {sp.sstr(m * x + q)}",
        "latex": _latex(sp.Eq(y, m * x + q)),
    }


def _critical_type(expr: Any, x: Any, point: Any) -> str:
    import sympy as sp

    try:
        d2 = sp.diff(expr, x, 2).subs(x, point)
        v = _to_float(d2.evalf())
    except (ValueError, TypeError, RecursionError):
        return "stationary"
    if v is None or abs(v) < 1e-12:
        return "stationary"
    return "max" if v < 0 else "min"


def _points(expr: Any, x: Any, solutions: list[Any], *, with_type: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in solutions:
        xf, exact_x = exact_form(s)
        if xf is None:
            continue
        try:
            y_val = _simplified(expr.subs(x, s))
            yf, exact_y = exact_form(y_val)
        except (ValueError, TypeError, RecursionError):
            yf, exact_y = None, None
        entry: dict[str, Any] = {"x": xf, "y": yf, "exact_x": exact_x, "exact_y": exact_y}
        if with_type:
            entry["type"] = _critical_type(expr, x, s)
        out.append(entry)
    return out


def _study(payload: dict[str, Any], exprs: list[Any], x: Any, out: dict[str, Any]) -> None:
    import sympy as sp

    index = payload.get("study_index")
    if index is None or index >= len(exprs) or exprs[index] is None:
        return
    f = exprs[index]
    lo, hi = float(payload["domain"][0]), float(payload["domain"][1])
    tasks = set(payload.get("tasks") or ())
    warnings: list[str] = out["warnings"]
    if "zeros" in tasks:
        zeros: list[dict[str, Any]] = []
        for s in _solve_in(f, x, lo, hi, warnings, "zeros"):
            xf, exact = exact_form(s)
            if xf is not None:
                zeros.append({"x": xf, "exact": exact})
        out["zeros"] = zeros
    if "critical_points" in tasks:
        d1 = sp.diff(f, x)
        out["critical_points"] = _points(
            f, x, _solve_in(d1, x, lo, hi, warnings, "critical"), with_type=True
        )
    if "inflection_points" in tasks:
        d2 = sp.diff(f, x, 2)
        out["inflection_points"] = _points(
            f, x, _solve_in(d2, x, lo, hi, warnings, "inflection"), with_type=False
        )
    if tasks & {"asymptotes", "discontinuities"}:
        vertical: list[dict[str, Any]] = []
        discontinuities: list[dict[str, Any]] = []
        for s in _singular_points(f, x, lo, hi, warnings):
            xf, exact = exact_form(s)
            if xf is None:
                continue
            left = _limit(f, x, s, "-")
            right = _limit(f, x, s, "+")
            if _is_infinite(left) or _is_infinite(right):
                vertical.append(
                    {
                        "x": xf,
                        "exact": exact,
                        "expr": f"x = {sp.sstr(s)}",
                        "latex": _latex(sp.Eq(x, s)),
                    }
                )
            else:
                discontinuities.append({"x": xf, "exact": exact})
        out["vertical_asymptotes"] = vertical
        out["discontinuities"] = discontinuities
    if "asymptotes" in tasks:
        y = sp.Symbol("y" if str(x) != "y" else "z")
        tails: list[dict[str, Any]] = []
        for direction in (sp.oo, -sp.oo):
            tail = _tail(f, x, y, direction)
            if tail is not None and not any(
                t["kind"] == tail["kind"]
                and abs(t["m"] - tail["m"]) < 1e-12
                and abs(t["q"] - tail["q"]) < 1e-12
                for t in tails
            ):
                tails.append(tail)
        out["tail_asymptotes"] = tails


def _tangents(payload: dict[str, Any], exprs: list[Any], x: Any, out: dict[str, Any]) -> None:
    import sympy as sp

    for item in payload.get("tangents") or []:
        index = int(item["index"])
        if index >= len(exprs) or exprs[index] is None:
            continue
        f = exprs[index]
        a = _exactify(float(item["at"]))
        try:
            slope = _simplified(sp.diff(f, x).subs(x, a))
            y_val = _simplified(f.subs(x, a))
        except (ValueError, TypeError, RecursionError):
            continue
        sf, exact_slope = exact_form(slope)
        yf, exact_y = exact_form(y_val)
        out["tangents"].append(
            {
                "index": index,
                "at": float(item["at"]),
                "slope": sf,
                "exact_slope": exact_slope,
                "y": yf,
                "exact_y": exact_y,
            }
        )


def _integrals(payload: dict[str, Any], exprs: list[Any], x: Any, out: dict[str, Any]) -> None:
    import sympy as sp

    for item in payload.get("integrals") or []:
        index = int(item["index"])
        against = item.get("against")
        if index >= len(exprs) or exprs[index] is None:
            continue
        integrand = exprs[index]
        if against is not None:
            if against >= len(exprs) or exprs[against] is None:
                continue
            integrand = integrand - exprs[against]
        a = _exactify(float(item["between"][0]))
        b = _exactify(float(item["between"][1]))
        try:
            result = sp.integrate(integrand, (x, a, b))
        except (NotImplementedError, ValueError, TypeError, RecursionError):
            out["warnings"].append("symbolic_integral_unsupported")
            continue
        if result.has(sp.Integral):
            out["warnings"].append("symbolic_integral_unsupported")
            continue
        vf, exact = exact_form(_simplified(result))
        out["integrals"].append({"index": index, "value": vf, "exact": exact})


def analyze_symbolic(payload: dict[str, Any]) -> dict[str, Any]:
    """Bersaglio del figlio. `payload = {"expressions": [str], "declared":
    [str], "variable": str, "domain": [lo, hi], "study_index": int | None,
    "tasks": [str], "tangents": [{index, at}], "integrals": [{index,
    against, between}]}` → dizionario JSON con `latex` per espressione
    (None se il parse fallisce), `zeros`, `critical_points`,
    `inflection_points`, `vertical_asymptotes`, `discontinuities`,
    `tail_asymptotes`, `tangents`, `integrals`, `warnings`."""
    import sympy as sp

    declared = [str(s) for s in payload.get("declared") or [payload["variable"]]]
    x = sp.Symbol(str(payload["variable"]), real=True)
    out: dict[str, Any] = {
        "latex": [],
        "zeros": None,
        "critical_points": None,
        "inflection_points": None,
        "vertical_asymptotes": None,
        "discontinuities": None,
        "tail_asymptotes": None,
        "tangents": [],
        "integrals": [],
        "warnings": [],
    }
    exprs: list[Any] = []
    for src in payload.get("expressions") or []:
        try:
            expr = parse_sympy(str(src), declared)
            exprs.append(expr)
            out["latex"].append(_latex(expr))
        except Exception as exc:  # il parse del figlio non deve fermare le altre espressioni
            exprs.append(None)
            out["latex"].append(None)
            out["warnings"].append(f"symbolic_parse_failed: {type(exc).__name__}")
    _study(payload, exprs, x, out)
    _tangents(payload, exprs, x, out)
    _integrals(payload, exprs, x, out)
    return out


__all__ = ["analyze_symbolic", "exact_form", "parse_sympy", "sympy_namespace"]
