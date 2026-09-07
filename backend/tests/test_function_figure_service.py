"""Formato `function` (WP7, Q4): parser a due passi, calcolo numerico,
calcolo simbolico nel figlio, disegno, registro ed endpoint.

Struttura:
- parser (passo 1, senza sympy): rifiuti con i messaggi per il docente,
  accettazioni, limiti;
- schema: `parse_function_spec` con `loc` per campo, hash canonico;
- numerico: valutatore AST→numpy, rami di `(x**2-1)/(x-2)`, zeri di
  `sin`, punti critici, poli e salti, code, livelli «tondi»;
- simbolico (`importorskip("sympy")`): i sei casi del `global_dict`
  ristretto verificati su sympy 1.14 (documento 17, §4.2) e lo studio
  esatto in-process;
- render (numpy + matplotlib + sympy nel figlio `spawn`): id degli
  elementi, niente `<foreignObject>`/`<image>`, font ⊆ {Noto Sans, DejaVu
  Sans}, determinismo byte-identico, forme esatte riconciliate, timeout
  reale con `tests/helpers/slow_target.py`;
- registro (`FunctionRenderer`) e gate 422 del PATCH;
- endpoint `render-function`: 200, 422 semantico, 422 Pydantic, 403.

I test che disegnano o avviano il figlio saltano se mancano numpy,
matplotlib o sympy (mai falliscono per l'ambiente).
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.errors import ValidationAppError
from app.core.permissions import R
from app.models.course import Course
from app.schemas.figure_function import (
    DEFAULT_SHOW,
    FunctionFigureSpec,
    check_function_spec,
    parse_function_spec,
)
from app.services import figure_function_service as ffs
from app.services import figure_render_service as frs
from app.services.figure_compute import function_numeric as fnum
from app.services.figure_compute import function_parse as fparse
from app.services.figure_compute import function_plot as fplot
from app.services.figure_theme import FONT_ALLOWED
from tests.test_admin_user_management import _bearer
from tests.test_permissions import _setup_user_membership

pytestmark = pytest.mark.asyncio

_DEPS = ffs.dependencies_available()
needs_deps = pytest.mark.skipif(not _DEPS, reason="numpy, matplotlib o sympy assenti")

RATIONAL = {
    "kind": "function_study",
    "expressions": [{"expr": "(x**2 - 1)/(x - 2)"}],
    "domain": [-6, 8],
    "show": ["zeros", "critical_points", "asymptotes", "discontinuities", "formula"],
}
SIN = {
    "kind": "function_study",
    "expressions": [{"expr": "sin(x)", "label": "seno"}],
    "domain": [-2 * math.pi, 2 * math.pi],
    "show": ["zeros", "critical_points", "inflection_points", "formula"],
}
AREA = {
    "kind": "area",
    "expressions": [{"expr": "x**2"}],
    "domain": [-1, 2],
    "annotations": [{"kind": "area", "between": [0, 1]}],
    "show": ["formula"],
}
TANGENT = {
    "kind": "tangent",
    "expressions": [{"expr": "x**3 - 3*x"}],
    "domain": [-3, 3],
    "annotations": [{"kind": "tangent", "at": 1.5}, {"kind": "point", "at": -1, "label": "P"}],
    "show": ["critical_points", "inflection_points", "formula"],
}
FAMILY = {
    "kind": "family",
    "expressions": [{"expr": "a*x**2"}],
    "domain": [-2, 2],
    "parameter": {"name": "a", "values": [0.5, 1, 2]},
    "show": ["formula"],
}
LEVELS = {
    "kind": "level_curves",
    "expressions": [{"expr": "x**2 + y**2"}],
    "domain": [-2, 2],
    "variables": ["x", "y"],
    "levels": 4,
    "show": ["formula"],
}


def _spec(data: dict[str, Any]) -> FunctionFigureSpec:
    spec, issues = parse_function_spec(json.dumps(data))
    assert spec is not None, issues
    return spec


def _ids(svg: str) -> set[str]:
    return set(re.findall(r'id="([^"]+)"', svg))


def _font_families(svg: str) -> set[str]:
    return {f.strip() for f in re.findall(r"font-family:\s*'?([^;'\"]+)'?", svg)}


@pytest.fixture(autouse=True)
def _clean_caches():
    ffs.clear_result_cache()
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()
    yield
    ffs.clear_result_cache()
    frs.clear_svg_cache()
    frs.available_formats.cache_clear()


# ---------------------------------------------------------------------------
# Passo 1: parser AST senza sympy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "needle", "etype"),
    [
        ("__import__('os').system('id')", "chiamata non ammessa", fparse.EXPR_FORBIDDEN),
        ("open('/etc/hosts').read()", "chiamata non ammessa", fparse.EXPR_FORBIDDEN),
        ("open('x')", "funzione non ammessa: open", fparse.EXPR_FORBIDDEN),
        ("x.__class__", "costrutto non ammesso: Attribute", fparse.EXPR_FORBIDDEN),
        ("x[0]", "costrutto non ammesso: Subscript", fparse.EXPR_FORBIDDEN),
        ("(lambda: 1)()", "chiamata non ammessa", fparse.EXPR_FORBIDDEN),
        ("x if x else 1", "costrutto non ammesso: IfExp", fparse.EXPR_FORBIDDEN),
        ("[1, 2]", "costrutto non ammesso: List", fparse.EXPR_FORBIDDEN),
        ("y + x", "simbolo non dichiarato: y", fparse.EXPR_SYMBOL),
        ("2x", "moltiplicazione implicita non ammessa: scrivi 2*x", fparse.EXPR_SYNTAX),
        ("2 x", "moltiplicazione implicita non ammessa: scrivi 2*x", fparse.EXPR_SYNTAX),
        ("2(x + 1)", "moltiplicazione implicita non ammessa", fparse.EXPR_SYNTAX),
        ("x(x + 1)", "moltiplicazione implicita non ammessa: scrivi x*(...)", fparse.EXPR_SYNTAX),
        ("x^2", "usa ** per la potenza", fparse.EXPR_SYNTAX),
        ("2**1000000", "esponente costante oltre", fparse.EXPR_LIMIT),
        ("2**12**12", "esponente costante oltre", fparse.EXPR_LIMIT),
        ("x % 2", "operatore non ammesso: Mod", fparse.EXPR_FORBIDDEN),
        ("x // 2", "operatore non ammesso: FloorDiv", fparse.EXPR_FORBIDDEN),
        ("True", "costante non ammessa", fparse.EXPR_FORBIDDEN),
        ("1j * x", "costante non ammessa", fparse.EXPR_FORBIDDEN),
        ("sin", "funzione senza argomento: sin", fparse.EXPR_FORBIDDEN),
        ("log(x, 2, 3)", "log accetta 1 o 2 argomenti", fparse.EXPR_FORBIDDEN),
        ("sin(x, 1)", "sin accetta un argomento", fparse.EXPR_FORBIDDEN),
        ("sin(x=1)", "argomenti con nome", fparse.EXPR_FORBIDDEN),
        ("", "espressione vuota", fparse.EXPR_SYNTAX),
        ("x +", "sintassi non valida", fparse.EXPR_SYNTAX),
        ("x" * 201, "oltre 200 caratteri", fparse.EXPR_LIMIT),
    ],
)
def test_parser_rejects_with_teacher_messages(src: str, needle: str, etype: str):
    with pytest.raises(fparse.ExprError) as info:
        fparse.check_expression(src, free_symbols=["x"])
    assert needle in info.value.msg, info.value.msg
    assert info.value.type == etype
    assert info.value.loc_suffix == ("expr",)


@pytest.mark.parametrize(
    ("src", "declared", "names"),
    [
        ("(x**2 - 1)/(x - 2)", ["x"], {"x"}),
        ("sin(x)**2 + 2*x", ["x"], {"x"}),
        ("log(x, 2) + log(x)", ["x"], {"x"}),
        ("a*x**2 + pi*E", ["x", "a"], {"x", "a"}),
        ("x**(1/3) + 2**x + x**-2", ["x"], {"x"}),
        ("-(-x) + abs(x) + floor(x)", ["x"], {"x"}),
        ("3", ["x"], set()),
        ("sqrt(x**2 + y**2)", ["x", "y"], {"x", "y"}),
    ],
)
def test_parser_accepts_formulas(src: str, declared: list[str], names: set[str]):
    parsed = fparse.check_expression(src, free_symbols=declared)
    assert parsed.source == src
    assert parsed.names == frozenset(names)
    assert parsed.node_count <= fparse.MAX_NODES
    assert parsed.depth <= fparse.MAX_DEPTH


def test_parser_limits_nodes_and_depth():
    with pytest.raises(fparse.ExprError, match="troppo lunga") as info:
        fparse.check_expression("+".join(["x"] * 42), free_symbols=["x"])  # 83 nodi, 83 caratteri
    assert info.value.type == fparse.EXPR_LIMIT
    with pytest.raises(fparse.ExprError, match="troppo annidata"):
        fparse.check_expression("-" * 14 + "x", free_symbols=["x"])
    # Le parentesi non contano come nodi: 12 livelli di unario passano.
    fparse.check_expression("-" * 10 + "x", free_symbols=["x"])


def test_constant_value_folds_only_constants():
    import ast

    tree = ast.parse("-(1/3) + 2**3", mode="eval")
    assert fparse.constant_value(tree.body) == pytest.approx(-1 / 3 + 8)
    assert fparse.constant_value(ast.parse("x + 1", mode="eval").body) is None
    assert fparse.constant_value(ast.parse("1/0", mode="eval").body) is None


# ---------------------------------------------------------------------------
# Schema: struttura + semantica con `loc` per campo
# ---------------------------------------------------------------------------


def test_parse_function_spec_accepts_the_examples():
    for data in (RATIONAL, SIN, AREA, TANGENT, FAMILY, LEVELS):
        spec = _spec(data)
        assert check_function_spec(spec) == []
    spec = _spec({"kind": "function_study", "expressions": [{"expr": "x"}], "domain": [0, 1]})
    assert tuple(spec.show) == DEFAULT_SHOW
    assert spec.sampling.points == 800
    assert spec.expression_label(0) == "f"


@pytest.mark.parametrize(
    ("data", "loc", "needle"),
    [
        ({"kind": "spiral", "expressions": [{"expr": "x"}], "domain": [0, 1]}, ["kind"], "Input"),
        (
            {"kind": "function_study", "expressions": [{"expr": "x"}], "domain": [0, 1], "z": 1},
            ["z"],
            "Extra",
        ),
        (
            {"kind": "function_study", "expressions": [{"expr": "y + x"}], "domain": [0, 1]},
            ["expressions", 0, "expr"],
            "simbolo non dichiarato: y",
        ),
        (
            {"kind": "function_study", "expressions": [{"expr": "2x"}], "domain": [0, 1]},
            ["expressions", 0, "expr"],
            "scrivi 2*x",
        ),
        (
            {"kind": "function_study", "expressions": [{"expr": "x"}], "domain": [1, 0]},
            ["domain"],
            "minore del massimo",
        ),
        (
            {"kind": "function_study", "expressions": [{"expr": "x"}], "domain": [0, 1e5]},
            ["domain"],
            "ampiezza",
        ),
        (
            {
                "kind": "function_study",
                "expressions": [{"expr": "x"}],
                "domain": [0, 1],
                "range": [2, 2],
            },
            ["range"],
            "minore del massimo",
        ),
        (
            {"kind": "tangent", "expressions": [{"expr": "x"}], "domain": [0, 1]},
            ["annotations"],
            "kind=tangent richiede",
        ),
        (
            {
                "kind": "area",
                "expressions": [{"expr": "x"}],
                "domain": [0, 1],
                "annotations": [{"kind": "area", "between": [0, 2]}],
            },
            ["annotations", 0, "between"],
            "fuori dal dominio",
        ),
        (
            {
                "kind": "function_study",
                "expressions": [{"expr": "x"}],
                "domain": [0, 1],
                "annotations": [{"kind": "point", "at": 0.5, "expr_index": 2}],
            },
            ["annotations", 0, "expr_index"],
            "fuori da expressions",
        ),
        (
            {
                "kind": "function_study",
                "expressions": [{"expr": "x"}],
                "domain": [0, 1],
                "annotations": [{"kind": "tangent", "at": 5}],
            },
            ["annotations", 0, "at"],
            "fuori dal dominio",
        ),
        (
            {"kind": "family", "expressions": [{"expr": "x"}], "domain": [0, 1]},
            ["parameter"],
            "kind=family richiede",
        ),
        (
            {
                "kind": "family",
                "expressions": [{"expr": "a*x"}],
                "domain": [0, 1],
                "parameter": {"name": "x", "values": [1]},
            },
            ["parameter", "name"],
            "coincidere",
        ),
        (
            {"kind": "level_curves", "expressions": [{"expr": "x"}], "domain": [0, 1], "levels": 3},
            ["variables"],
            "richiede `variables`",
        ),
        (
            {
                "kind": "level_curves",
                "expressions": [{"expr": "x"}],
                "domain": [0, 1],
                "variables": ["x", "x"],
                "levels": 3,
            },
            ["variables"],
            "distinte",
        ),
        (
            {
                "kind": "function_study",
                "expressions": [{"expr": "x"}],
                "domain": [0, 1],
                "levels": 3,
            },
            ["levels"],
            "solo con kind=level_curves",
        ),
        (
            {
                "kind": "level_curves",
                "expressions": [{"expr": "x + y"}],
                "domain": [0, 1],
                "variables": ["x", "y"],
                "levels": 1,
            },
            ["levels"],
            "numero di livelli",
        ),
        (
            {
                "kind": "function_study",
                "expressions": [{"expr": "x", "label": "f"}, {"expr": "2*x", "label": "f"}],
                "domain": [0, 1],
            },
            ["expressions", 1, "label"],
            "duplicata",
        ),
        (
            {
                "kind": "function_study",
                "expressions": [{"expr": "x"}],
                "domain": [0, 1],
                "show": ["zeros", "zeros"],
            },
            ["show"],
            "duplicate",
        ),
    ],
)
def test_parse_function_spec_reports_the_field(data: dict[str, Any], loc: list[Any], needle: str):
    spec, issues = parse_function_spec(json.dumps(data))
    assert spec is None
    assert any(i["loc"] == loc and needle in i["msg"] for i in issues), issues


def test_parse_function_spec_handles_non_json_and_empty():
    spec, issues = parse_function_spec("")
    assert spec is None and issues[0]["msg"] == "spec vuota"
    spec, issues = parse_function_spec("{not json")
    assert spec is None and issues[0]["type"] == "json_invalid"
    spec, issues = parse_function_spec("[1, 2]")
    assert spec is None and issues


def test_canonical_json_and_hash_include_defaults():
    a = _spec({"kind": "function_study", "expressions": [{"expr": "x"}], "domain": [0, 1]})
    b = _spec(
        {
            "kind": "function_study",
            "expressions": [{"expr": "x", "label": ""}],
            "domain": [0, 1],
            "sampling": {"points": 800},
            "show": list(DEFAULT_SHOW),
        }
    )
    c = _spec({"kind": "function_study", "expressions": [{"expr": "x"}], "domain": [0, 2]})
    assert a.canonical_json() == b.canonical_json()
    assert a.content_hash() == b.content_hash() and len(a.content_hash()) == 64
    assert a.content_hash() != c.content_hash()
    assert json.loads(a.canonical_json())["sampling"] == {"points": 800}


# ---------------------------------------------------------------------------
# Numerico (numpy in thread)
# ---------------------------------------------------------------------------


@needs_deps
@pytest.mark.parametrize(
    "src",
    [
        "(x**2 - 1)/(x - 2)",
        "sin(x)**2 + 2*x",
        "log(x, 2)",
        "sqrt(x) + abs(x - 1)",
        "2**x - E",
        "floor(x)/pi",
    ],
)
def test_compile_numpy_matches_numpy_reference(src: str):
    import numpy as np

    parsed = fparse.check_expression(src, free_symbols=["x"])
    fn = fnum.compile_numpy(parsed)
    xs = np.linspace(-3, 3, 97)
    got = fnum.sample(fn, xs, variable="x", env={})
    reference = {
        "(x**2 - 1)/(x - 2)": (xs**2 - 1) / (xs - 2),
        "sin(x)**2 + 2*x": np.sin(xs) ** 2 + 2 * xs,
        "log(x, 2)": np.log(xs) / np.log(2),
        "sqrt(x) + abs(x - 1)": np.sqrt(xs) + np.abs(xs - 1),
        "2**x - E": 2**xs - math.e,
        "floor(x)/pi": np.floor(xs) / math.pi,
    }
    with np.errstate(all="ignore"):
        expected = reference[src]
    assert got.shape == xs.shape
    assert np.allclose(got, expected, equal_nan=True)


@needs_deps
def test_compile_numpy_never_evaluates_source():
    parsed = fparse.check_expression("2*x", free_symbols=["x"])
    fn = fnum.compile_numpy(parsed)
    assert float(fn({"x": 3.0})) == 6.0
    # Il valutatore lavora sull'AST accettato: nessuna chiamata a `eval`
    # né a `lambdify` nel modulo (le citazioni nelle docstring non contano).
    import inspect

    source = inspect.getsource(fnum).replace("evaluate(", "")
    assert not re.search(r"\beval\(", source) and not re.search(r"\blambdify\(", source)


@needs_deps
def test_split_branches_never_crosses_the_pole():
    import numpy as np

    parsed = fparse.check_expression("(x**2 - 1)/(x - 2)", free_symbols=["x"])
    fn = fnum.compile_numpy(parsed)
    xs = np.linspace(-6, 8, 800)
    ys = fnum.sample(fn, xs, variable="x", env={})
    branches, regions = fnum.split_branches(xs, ys)
    assert len(branches) >= 2
    for seg_x, _seg_y in branches:
        assert (seg_x < 2).all() or (seg_x > 2).all()
    assert any(a <= 2 <= b for a, b in regions)
    f = fnum.scalar_function(fn, variable="x", env={})
    poles, jumps = fnum.classify_cuts(f, regions, scale=fnum.data_scale(ys), width=14.0)
    assert len(poles) == 1 and abs(poles[0] - 2.0) < 1e-6
    assert jumps == []


@needs_deps
def test_split_branches_keeps_steep_but_continuous_functions_whole():
    import numpy as np

    parsed = fparse.check_expression("exp(x)", free_symbols=["x"])
    fn = fnum.compile_numpy(parsed)
    xs = np.linspace(-5, 5, 800)
    branches, regions = fnum.split_branches(xs, fnum.sample(fn, xs, variable="x", env={}))
    assert len(branches) == 1 and regions == []


@needs_deps
def test_find_zeros_of_sin_are_multiples_of_pi():
    import numpy as np

    parsed = fparse.check_expression("sin(x)", free_symbols=["x"])
    fn = fnum.compile_numpy(parsed)
    lo, hi = -2 * math.pi, 2 * math.pi
    xs = np.linspace(lo, hi, 800)
    ys = fnum.sample(fn, xs, variable="x", env={})
    f = fnum.scalar_function(fn, variable="x", env={})
    zeros = fnum.find_zeros(f, xs, ys, [], scale=1.0, width=hi - lo)
    assert len(zeros) == 5
    for z, k in zip(zeros, range(-2, 3), strict=True):
        assert abs(z - k * math.pi) < 1e-9


@needs_deps
def test_touching_zero_and_critical_points_are_found():
    import numpy as np

    parsed = fparse.check_expression("x**2", free_symbols=["x"])
    fn = fnum.compile_numpy(parsed)
    xs = np.linspace(-3, 3, 800)
    ys = fnum.sample(fn, xs, variable="x", env={})
    f = fnum.scalar_function(fn, variable="x", env={})
    zeros = fnum.find_zeros(f, xs, ys, [], scale=1.0, width=6.0)
    assert len(zeros) == 1 and abs(zeros[0]) < 1e-6
    study = fnum.analyze(_spec(TANGENT))
    kinds = [(round(x, 6), t) for x, _y, t in study.critical]
    assert kinds == [(-1.0, "max"), (1.0, "min")]
    assert len(study.inflection) == 1 and abs(study.inflection[0][0]) < 1e-6
    assert len(study.tangents) == 1 and abs(study.tangents[0].slope - 3.75) < 1e-6
    assert len(study.points) == 1 and study.points[0].label == "P"


@needs_deps
def test_jumps_poles_and_tails():
    import numpy as np

    floor = fnum.compile_numpy(fparse.check_expression("floor(x)", free_symbols=["x"]))
    xs = np.linspace(-2, 3, 800)
    ys = fnum.sample(floor, xs, variable="x", env={})
    _branches, regions = fnum.split_branches(xs, ys)
    f = fnum.scalar_function(floor, variable="x", env={})
    poles, jumps = fnum.classify_cuts(f, regions, scale=1.0, width=5.0)
    assert poles == []
    assert [round(x, 6) for x, _y in jumps] == [-1.0, 0.0, 1.0, 2.0, 3.0][: len(jumps)]
    assert len(jumps) >= 4

    rational = fnum.compile_numpy(fparse.check_expression("(x**2 - 1)/(x - 2)", free_symbols=["x"]))
    tails = fnum.oblique_or_horizontal(rational, variable="x", env={})
    assert len(tails) == 1
    kind, m, q = tails[0]
    assert kind == "oblique" and abs(m - 1) < 1e-3 and abs(q - 2) < 1e-2
    exp = fnum.compile_numpy(fparse.check_expression("exp(x)", free_symbols=["x"]))
    assert fnum.oblique_or_horizontal(exp, variable="x", env={}) == [("horizontal", 0.0, 0.0)]
    sin = fnum.compile_numpy(fparse.check_expression("sin(x)", free_symbols=["x"]))
    assert fnum.oblique_or_horizontal(sin, variable="x", env={}) == []
    fr = fnum.scalar_function(rational, variable="x", env={})
    assert fnum.tail_confirmed(fr, 1.0, 2.0) and not fnum.tail_confirmed(fr, 1.0, 3.0)


@needs_deps
def test_simpson_levels_and_family():
    assert fnum.simpson(lambda x: x * x, 0.0, 1.0) == pytest.approx(1 / 3, abs=1e-9)
    assert fnum.simpson(lambda x: 1 / x, -1.0, 1.0) is None
    levels = fnum.nice_levels(0.26, 5.81, 4)
    assert len(levels) == 4 and levels == sorted(levels)
    assert all(0.26 <= v <= 5.81 + 1e-9 for v in levels)
    assert all(abs(v * 10 - round(v * 10)) < 1e-9 for v in levels)  # multipli di 0,1
    study = fnum.analyze(_spec(LEVELS))
    assert study.levels is not None and len(study.levels) == 4 and study.grid is not None
    family = fnum.analyze(_spec(FAMILY))
    assert [c.label for c in family.curves] == ["a = 0.5", "a = 1", "a = 2"]
    assert all(len(c.branches) == 1 for c in family.curves)


# ---------------------------------------------------------------------------
# Simbolico (sympy): `global_dict` ristretto e studio esatto in-process
# ---------------------------------------------------------------------------


def test_restricted_global_dict_cases_on_sympy():
    """I sei casi verificati su sympy 1.14 (documento 17, §4.2)."""
    pytest.importorskip("sympy")
    from app.services.figure_compute import function_symbolic as fsym

    with pytest.raises(NameError, match="Function"):
        fsym.parse_sympy("__import__('os').system('id')", ["x"])
    with pytest.raises(NameError, match="Function"):
        fsym.parse_sympy("open('/etc/hosts').read()", ["x"])
    # Lambda, attributi e subscript NON sono fermati dal `global_dict`: li
    # ferma solo il passo 1 (`check_expression`), da cui l'obbligo dei due passi.
    assert fsym.parse_sympy("(lambda: 1)()", ["x"]) == 1
    for src in ("x.__class__.__mro__", "[].__class__.__base__.__subclasses__()"):
        with pytest.raises(fparse.ExprError):
            fparse.check_expression(src, free_symbols=["x"])
    with pytest.raises(ValueError, match="simboli non dichiarati: y"):
        fsym.parse_sympy("y + x", ["x"])
    # `2**1000000` viene calcolato come Integer; è la conversione in
    # stringa (latex, str) a superare il limite di 4300 cifre. Lo ferma il
    # passo 1 con il limite sull'esponente.
    huge = fsym.parse_sympy("2**1000000", ["x"])
    with pytest.raises(ValueError, match="Exceeds the limit"):
        str(huge)
    with pytest.raises(fparse.ExprError, match="esponente costante oltre"):
        fparse.check_expression("2**1000000", free_symbols=["x"])
    t0 = time.perf_counter()
    expr = fsym.parse_sympy("sin(x)**2 + 2*x", ["x"])
    assert time.perf_counter() - t0 < 1.0
    assert str(expr) == "2*x + sin(x)**2"
    assert "__builtins__" in fsym.sympy_namespace() and fsym.sympy_namespace()["__builtins__"] == {}


def test_exact_forms_and_symbolic_study_in_process():
    sp = pytest.importorskip("sympy")
    from app.services.figure_compute import function_symbolic as fsym

    assert fsym.exact_form(sp.Float(0.5)) == (0.5, r"\frac{1}{2}")
    assert fsym.exact_form(sp.pi / 4)[1] == r"\frac{\pi}{4}"
    assert fsym.exact_form(sp.Float(0.123456789123))[1] is None
    assert fsym.exact_form(sp.Symbol("x")) == (None, None)

    spec = _spec(RATIONAL)
    payload = ffs._symbolic_payload(spec)
    payload["integrals"] = [{"index": 0, "against": None, "between": [0, 1]}]
    out = fsym.analyze_symbolic(payload)
    assert out["latex"] == [r"\frac{x^{2} - 1}{x - 2}"]
    assert [z["exact"] for z in out["zeros"]] == ["-1", "1"]
    assert [c["exact_x"] for c in out["critical_points"]] == [r"2 - \sqrt{3}", r"\sqrt{3} + 2"]
    assert [c["type"] for c in out["critical_points"]] == ["max", "min"]
    assert out["vertical_asymptotes"][0]["latex"] == "x = 2"
    assert out["tail_asymptotes"] == [
        {"kind": "oblique", "m": 1.0, "q": 2.0, "expr": "y = x + 2", "latex": "y = x + 2"}
    ]
    assert out["discontinuities"] == []

    area = fsym.analyze_symbolic(ffs._symbolic_payload(_spec(AREA)))
    assert area["integrals"] == [
        {"index": 0, "value": pytest.approx(1 / 3), "exact": r"\frac{1}{3}"}
    ]
    sin = fsym.analyze_symbolic(ffs._symbolic_payload(_spec(SIN)))
    assert [z["exact"] for z in sin["zeros"]] == [r"- 2 \pi", r"- \pi", "0", r"\pi", r"2 \pi"]


# ---------------------------------------------------------------------------
# Render completo (numerico + figlio sympy + matplotlib)
# ---------------------------------------------------------------------------


@needs_deps
def test_render_rational_study_is_exact_deterministic_and_vector_only():
    spec = _spec(RATIONAL)
    t0 = time.perf_counter()
    first = ffs.render_function_sync(spec, language="it")
    elapsed = time.perf_counter() - t0
    assert first.approximate is False and first.warnings == []
    assert first.content_hash == spec.content_hash()
    computed = first.computed
    assert [z["exact"] for z in computed["zeros"]] == ["-1", "1"]
    assert [c["exact_x"] for c in computed["critical_points"]] == [r"2 - \sqrt{3}", r"\sqrt{3} + 2"]
    assert [c["type"] for c in computed["critical_points"]] == ["max", "min"]
    kinds = {(a["kind"], a["latex"]) for a in computed["asymptotes"]}
    assert kinds == {("vertical", "x = 2"), ("oblique", "y = x + 2")}
    assert computed["inflection_points"] is None and computed["integral"] is None
    assert first.latex == [r"\frac{x^{2} - 1}{x - 2}"]
    assert first.computed_caption == (
        "Zeri in x = −1, 1. Punti critici in x = 2 − √3, √3 + 2. "
        "Asintoto verticale x = 2. Asintoto obliquo y = x + 2."
    )
    svg = first.svg
    assert svg.startswith("<svg") and 'preserveAspectRatio="xMidYMid meet"' in svg
    assert "<foreignObject" not in svg and "<image" not in svg and "<script" not in svg
    assert "<metadata" not in svg
    assert {
        "formula",
        "zero-0",
        "zero-1",
        "branch-0-0",
        "branch-0-1",
        "critical-0",
        "asymptote-0",
    } <= _ids(svg)
    assert _font_families(svg) <= FONT_ALLOWED
    # Cache dei risultati: stesso oggetto; lingua diversa: stesso SVG, altra coda.
    assert ffs.render_function_sync(spec, language="it") is first
    english = ffs.render_function_sync(spec, language="en")
    assert english.svg == svg and english.computed_caption.startswith("Zeros at x = −1, 1.")
    ffs.clear_result_cache()
    again = ffs.render_function_sync(spec, language="it")
    assert again.svg == svg  # byte-identico (hashsalt fisso, metadati neutri)
    assert elapsed < 20, elapsed


@needs_deps
def test_render_sin_area_tangent_family_and_levels():
    sin = ffs.render_function_sync(_spec(SIN), language="it")
    assert [z["exact"] for z in sin.computed["zeros"]] == [
        r"- 2 \pi",
        r"- \pi",
        "0",
        r"\pi",
        r"2 \pi",
    ]
    assert len(sin.computed["critical_points"]) == 4 and len(sin.computed["inflection_points"]) == 5
    assert all("type" not in p for p in sin.computed["inflection_points"])
    assert sin.computed_caption.startswith(
        "Zeri in x = −2π, −π, 0, π, 2π. Punti critici in x = −3π/2, −π/2, π/2, 3π/2."
    )

    area = ffs.render_function_sync(_spec(AREA), language="it")
    assert area.computed["integral"] == {
        "between": [0.0, 1.0],
        "value": pytest.approx(1 / 3),
        "exact": r"\frac{1}{3}",
    }
    assert area.computed_caption == "Integrale su [0, 1] pari a 1/3."
    assert "area-0" in _ids(area.svg)

    tangent = ffs.render_function_sync(_spec(TANGENT), language="en")
    assert tangent.computed["tangents"] == [
        {"at": 1.5, "slope": pytest.approx(3.75, abs=1e-6), "exact_slope": r"\frac{15}{4}"}
    ]
    assert "Tangent at x = 1.5 with slope 15/4." in tangent.computed_caption
    assert {"tangent-0", "tangent-point-0", "point-0", "inflection-0"} <= _ids(tangent.svg)

    family = ffs.render_function_sync(_spec(FAMILY), language="it")
    assert {"branch-0-0", "branch-1-0", "branch-2-0", "formula"} <= _ids(family.svg)
    assert family.computed_caption == "" and family.computed["zeros"] is None
    assert "a = 0.5" in family.svg  # legenda con più serie

    levels = ffs.render_function_sync(_spec(LEVELS), language="it")
    assert len(levels.computed["levels"]) == 4
    assert "levels" in _ids(levels.svg) and "<image" not in levels.svg
    assert levels.computed_caption.startswith("Curve di livello per z = ")
    for result in (sin, area, tangent, family, levels):
        assert _font_families(result.svg) <= FONT_ALLOWED
        assert "<foreignObject" not in result.svg and "<image" not in result.svg


@needs_deps
def test_render_survives_symbolic_timeout_and_failure():
    spec = _spec(
        {"kind": "function_study", "expressions": [{"expr": "x**2 - 2"}], "domain": [-3, 3]}
    )
    t0 = time.perf_counter()
    result = ffs.render_function_sync(
        spec,
        language="it",
        symbolic_timeout=1,
        symbolic_target="tests.helpers.slow_target:sleep_forever",
    )
    elapsed = time.perf_counter() - t0
    assert 0.9 < elapsed < 8.0, elapsed
    assert result.approximate is True
    assert "symbolic_timeout" in result.warnings
    assert "branch-0-0" in _ids(result.svg) and "zero-0" in _ids(result.svg)
    assert result.computed["zeros"][0]["exact"] is None
    assert (
        result.computed_caption
        == "Zeri in x = −1.414, 1.414. Punti critici in x = 0. Valori approssimati."
    )
    ffs.clear_result_cache()
    failed = ffs.render_function_sync(
        spec, language="it", symbolic_target="tests.helpers.slow_target:boom"
    )
    assert failed.approximate is True and "symbolic_failed" in failed.warnings


def test_to_mathtext_rewrites_and_rejects():
    pytest.importorskip("matplotlib")
    assert fplot.to_mathtext(r"\left(x \right)") == "(x)"
    assert fplot.to_mathtext(r"\lvert x \rvert") == "|x|"
    assert fplot.to_mathtext(r"\tfrac{1}{2}") == r"\frac{1}{2}"
    assert fplot.to_mathtext(r"\operatorname{asin}{\left(x \right)}") == r"\mathrm{asin}{(x)}"
    assert fplot.to_mathtext(r"\begin{aligned}x\end{aligned}") is None
    assert fplot.to_mathtext(r"{a \over b}") is None
    assert fplot.to_mathtext(r"\frac{x^{2} - 1}{x - 2}") == r"\frac{x^{2} - 1}{x - 2}"
    assert fplot.to_mathtext("") is None


# ---------------------------------------------------------------------------
# Registro e gate 422 del PATCH
# ---------------------------------------------------------------------------


def test_function_renderer_is_registered_and_validates_offline():
    renderer = frs.REGISTRY["function"]
    assert renderer.fmt == "function"
    ok, msg = renderer.validate(
        json.dumps({"kind": "function_study", "expressions": [{"expr": "2x"}], "domain": [0, 1]})
    )
    assert ok is False and msg.startswith(
        "function_spec_invalid: expressions.0.expr: moltiplicazione"
    )
    assert frs.error_type_for(msg) == frs.FUNCTION_SPEC_INVALID
    assert renderer.validate("```json\n" + json.dumps(RATIONAL) + "\n```") == (True, "")
    assert renderer.validate("") == (False, "function_spec_invalid: spec vuota")
    assert renderer.render_svg("{}", asset_id="A1") is None
    if _DEPS:
        assert "function" in frs.available_formats()


@needs_deps
def test_function_renderer_deep_validation_and_render_use_the_svg_cache():
    renderer = frs.REGISTRY["function"]
    content = json.dumps(AREA)
    assert renderer.validate(content, deep=True) == (True, "")
    cached = frs._cache_get(frs.cache_key("function", content))
    assert cached is not None and "area-0" in _ids(cached)
    assert renderer.render_svg(content, asset_id="A1") == cached
    assert renderer.render_svg_batch([content, "{}"], asset_ids=["A1", "A2"]) == [cached, None]


def test_function_translatable_labels_round_trip():
    renderer = frs.REGISTRY["function"]
    content = json.dumps(
        {
            "kind": "function_study",
            "expressions": [{"expr": "x", "label": "velocità"}, {"expr": "2*x"}],
            "domain": [0, 1],
            "annotations": [{"kind": "point", "at": 0.5, "label": "punto"}],
        }
    )
    fields = renderer.extract_translatable(content)
    assert fields == {"expressions.0.label": "velocità", "annotations.0.label": "punto"}
    out = renderer.apply_translations(
        content, {"expressions.0.label": "speed", "annotations.0.label": "point"}
    )
    data = json.loads(out)
    assert data["expressions"][0]["label"] == "speed" and data["annotations"][0]["label"] == "point"
    assert data["expressions"][1] == {"expr": "2*x"} and data["domain"] == [0, 1]
    assert renderer.apply_translations("non json", {"expressions.0.label": "x"}) == "non json"
    assert renderer.extract_translatable("[1]") == {}


async def test_patch_gate_reports_function_spec_errors():
    assets = [
        {
            "asset_id": "A1",
            "format": "function",
            "content": json.dumps(
                {"kind": "function_study", "expressions": [{"expr": "y"}], "domain": [0, 1]}
            ),
            "caption": "c",
        }
    ]
    if not _DEPS:
        pytest.skip("renderer function non disponibile")
    with pytest.raises(ValidationAppError) as info:
        await frs.validate_visual_assets_or_raise(
            assets,
            previous=None,
            loc_root="visual_assets",
            code="lesson_content_invalid_visual_asset",
        )
    errors = info.value.meta["errors"]
    assert errors[0]["loc"] == ["visual_assets", 0, "content"]
    assert errors[0]["type"] == "function_spec_invalid"
    assert "simbolo non dichiarato: y" in errors[0]["msg"]


# ---------------------------------------------------------------------------
# Endpoint render-function
# ---------------------------------------------------------------------------


async def _course_for(db, *, role_code: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    user, org, _m = await _setup_user_membership(db, role_code=role_code)
    course = Course(
        organization_id=org.id,
        title="Corso di prova",
        objectives="Obiettivi di prova.",
        language_code="en",
        cfu=6,
        modules_count=1,
        lessons_per_module=1,
        lesson_duration_minutes=45,
        assessment_lesson_enabled=False,
        multiple_choice_questions_count=0,
        open_questions_count=0,
        assignee_user_id=user.id,
        created_by_user_id=user.id,
        status="lessons_structure_approved",
        didactic_setup_confirmed_at=datetime.now(UTC),
    )
    db.add(course)
    await db.commit()
    return user.id, org.id, course.id


def _url(org_id: uuid.UUID, course_id: uuid.UUID) -> str:
    return f"/api/v1/orgs/{org_id}/courses/{course_id}/lesson-assets/render-function"


@needs_deps
async def test_endpoint_renders_for_editors(client, seeded_db):
    user_id, org_id, course_id = await _course_for(seeded_db, role_code=R.MANAGER)
    res = await client.post(_url(org_id, course_id), json=AREA, headers=_bearer(user_id))
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"svg", "computed", "latex", "warnings", "computed_caption", "content_hash"}
    assert body["svg"].startswith("<svg") and "area-0" in _ids(body["svg"])
    assert body["latex"] == ["x^{2}"]
    assert body["computed"]["integral"]["exact"] == r"\frac{1}{3}"
    assert body["computed_caption"] == "Integral over [0, 1] equal to 1/3."  # lingua del corso
    assert body["content_hash"] == _spec(AREA).content_hash()


async def test_endpoint_422_for_semantic_errors_with_field_loc(client, seeded_db):
    user_id, org_id, course_id = await _course_for(seeded_db, role_code=R.MANAGER)
    bad = {"kind": "function_study", "expressions": [{"expr": "y + x"}], "domain": [0, 1]}
    res = await client.post(_url(org_id, course_id), json=bad, headers=_bearer(user_id))
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["code"] == "function_spec_invalid"
    assert body["meta"]["errors"] == [
        {
            "loc": ["expressions", 0, "expr"],
            "msg": "simbolo non dichiarato: y",
            "type": "expr_symbol",
        }
    ]


async def test_endpoint_422_pydantic_for_unknown_kind(client, seeded_db):
    user_id, org_id, course_id = await _course_for(seeded_db, role_code=R.MANAGER)
    bad = {"kind": "spiral", "expressions": [{"expr": "x"}], "domain": [0, 1]}
    res = await client.post(_url(org_id, course_id), json=bad, headers=_bearer(user_id))
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["code"] == "validation_error"
    assert body["meta"]["errors"][0]["loc"][:2] == ["body", "kind"]


async def test_endpoint_403_without_course_edit(client, seeded_db):
    user_id, org_id, course_id = await _course_for(seeded_db, role_code=R.MEMBER)
    res = await client.post(_url(org_id, course_id), json=AREA, headers=_bearer(user_id))
    assert res.status_code == 403, res.text
