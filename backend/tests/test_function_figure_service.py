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

import asyncio
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


_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _group(svg: str, gid: str) -> str:
    match = re.search(rf'<g id="{re.escape(gid)}">(.*?)</g>', svg, re.S)
    assert match is not None, gid
    return match.group(1)


def _path_x_range(svg: str, gid: str) -> tuple[float, float]:
    """Estremi x delle coordinate dei path del gruppo `gid` (coordinate
    assolute nelle unità del viewBox: matplotlib scrive i `PathPatch`
    senza `transform`)."""
    xs: list[float] = []
    for d in re.findall(r'\sd="([^"]+)"', _group(svg, gid)):
        numbers = [float(t) for t in _NUMBER_RE.findall(d)]
        xs.extend(numbers[0::2])
    assert xs, gid
    return (min(xs), max(xs))


def _view_box_width(svg: str) -> float:
    match = re.search(r'viewBox="0 0 ([\d.]+) [\d.]+"', svg)
    assert match is not None
    return float(match.group(1))


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


def test_parser_measures_flat_chains_by_size_not_by_nesting():
    """Una catena associativa (`x**11 + … + 1`, `(x-1)*…*(x-12)`) è piatta per
    chi legge: contarne i livelli la faceva rifiutare come «troppo annidata»
    e il messaggio arrivava al docente e al fix AI. E il conteggio
    preliminare includeva i nodi operatore, che la visita non conta: il
    tetto effettivo era ~40 nodi invece degli 80 dichiarati (COR-3)."""
    poly = " + ".join(f"x**{k}" for k in range(11, 0, -1)) + " + 1"
    product = "*".join(f"(x-{k})" for k in range(1, 13))
    taylor = " + ".join(
        ["1", "x"] + [f"x**{k}/{__import__('math').factorial(k)}" for k in range(2, 12)]
    )
    for src in (poly, product, taylor):
        parsed = fparse.check_expression(src, free_symbols=["x"])
        assert parsed.node_count <= fparse.MAX_NODES
        assert parsed.depth <= fparse.MAX_DEPTH
    # L'annidamento VERO resta il criterio della profondità.
    with pytest.raises(fparse.ExprError, match="troppo annidata"):
        fparse.check_expression("sqrt(" * 13 + "x" + ")" * 13, free_symbols=["x"])


def test_parser_rejects_a_constant_exponent_that_is_not_computable():
    """`-x**(1/0)` passava il passo 1 perché `constant_value` nasconde la
    `ZeroDivisionError`: numpy calcolava `x**inf` e la figura usciva con una
    didascalia matematicamente falsa (COR-2)."""
    with pytest.raises(fparse.ExprError, match="esponente costante"):
        fparse.check_expression("-x**(1/0)", free_symbols=["x"])
    # `constant_value` resta un ripiegatore, non un validatore.
    import ast

    assert fparse.constant_value(ast.parse("1/0", mode="eval").body) is None


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
        # `E` è la costante di Nepero per il passo 1 e per numpy, un simbolo
        # per sympy: la figura sarebbe incoerente con la formula.
        (
            {
                "kind": "function_study",
                "expressions": [{"expr": "E**2"}],
                "domain": [0, 1],
                "variable": "E",
            },
            ["variable"],
            "costante di Nepero",
        ),
        (
            {
                "kind": "family",
                "expressions": [{"expr": "E*x"}],
                "domain": [0, 1],
                "parameter": {"name": "E", "values": [1, 2, 3]},
            },
            ["parameter", "name"],
            "costante di Nepero",
        ),
        (
            {
                "kind": "level_curves",
                "expressions": [{"expr": "E + y"}],
                "domain": [0, 1],
                "variables": ["E", "y"],
                "levels": 3,
            },
            ["variables", 0],
            "costante di Nepero",
        ),
        (
            {
                "kind": "family",
                "expressions": [{"expr": "a*x"}],
                "domain": [0, 1],
                "parameter": {"name": "a", "values": [1, 1, 1]},
            },
            ["parameter", "values"],
            "valori duplicati",
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
    poles, jumps, more = fnum.classify_cuts(f, regions, scale=fnum.data_scale(ys), width=14.0)
    assert len(poles) == 1 and abs(poles[0] - 2.0) < 1e-6
    assert jumps == [] and more is False


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
    found = fnum.find_zeros(f, xs, ys, [], width=hi - lo)
    assert len(found.zeros) == 5 and found.intervals == [] and found.truncated is False
    for z, k in zip(found.zeros, range(-2, 3), strict=True):
        assert abs(z - k * math.pi) < 1e-9


@needs_deps
def test_touching_zero_and_critical_points_are_found():
    import numpy as np

    parsed = fparse.check_expression("x**2", free_symbols=["x"])
    fn = fnum.compile_numpy(parsed)
    xs = np.linspace(-3, 3, 800)
    ys = fnum.sample(fn, xs, variable="x", env={})
    f = fnum.scalar_function(fn, variable="x", env={})
    zeros = fnum.find_zeros(f, xs, ys, [], width=6.0).zeros
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
    poles, jumps, _more = fnum.classify_cuts(f, regions, scale=1.0, width=5.0)
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
    assert not fnum.tail_confirmed(fr, 1.0, 2.005)
    # Discontinuità eliminabile: a X = 1e8 la cancellazione supererebbe lo
    # scarto reale (nullo) e l'esatto `y = x + 1` verrebbe scartato.
    removable = fnum.compile_numpy(fparse.check_expression("(x**2-1)/(x-1)", free_symbols=["x"]))
    assert fnum.tail_confirmed(fnum.scalar_function(removable, variable="x", env={}), 1.0, 1.0)


@needs_deps
def test_local_tolerances_reject_false_zeros_and_boundary_poles():
    """Tolleranze locali, non relative alla mediana globale di |y|:
    `x**10 + 1` non ha zeri su [-100, 100] (scale 1e17 → tol 1e8 con la
    vecchia regola), `exp(x)` non si annulla in −5 e non ha un polo dove
    va in overflow al bordo del dominio."""
    big = ffs.render_function_sync(
        _spec(
            {
                "kind": "function_study",
                "expressions": [{"expr": "x**10 + 1"}],
                "domain": [-100, 100],
            }
        ),
        language="it",
    )
    assert big.computed["zeros"] == []
    assert [round(c["x"], 6) for c in big.computed["critical_points"]] == [0.0]
    assert big.computed_caption == "Punti critici in x = 0."

    exp_small = ffs.render_function_sync(
        _spec({"kind": "function_study", "expressions": [{"expr": "exp(x)"}], "domain": [-5, 40]}),
        language="it",
    )
    assert exp_small.computed["zeros"] == [] and exp_small.computed["critical_points"] == []
    assert exp_small.computed_caption == "Asintoto orizzontale y = 0."

    exp_big = ffs.render_function_sync(
        _spec({"kind": "function_study", "expressions": [{"expr": "exp(x)"}], "domain": [0, 1000]}),
        language="it",
    )
    assert exp_big.computed["zeros"] == []
    assert [a["kind"] for a in exp_big.computed["asymptotes"]] == ["horizontal"]
    assert exp_big.computed_caption == "Asintoto orizzontale y = 0."

    # Il polo di log(x) in 0 (bordo del dominio numerico) arriva dal figlio
    # sympy e passa la verifica numerica diretta (`f(0)` non finita).
    log = ffs.render_function_sync(
        _spec(
            {
                "kind": "function_study",
                "expressions": [{"expr": "log(x)"}],
                "domain": [-1, 1],
                "show": ["zeros", "asymptotes", "formula"],
            }
        ),
        language="it",
    )
    assert log.computed_caption == "Zeri in x = 1. Asintoto verticale x = 0."


@needs_deps
def test_plateaus_collapse_to_intervals_and_stationary_warnings():
    """Sequenze di campioni nulli → un intervallo (non un punto per
    campione); `f' ≡ 0` → nessun punto critico e avvertenza; il plateau
    da sottoflusso di `x**20736` è un solo zero di tangenza (esatto 0)."""
    show = ["zeros", "critical_points", "asymptotes", "discontinuities", "formula"]
    floor = ffs.render_function_sync(
        _spec(
            {
                "kind": "function_study",
                "expressions": [{"expr": "floor(x)"}],
                "domain": [-3, 3],
                "show": show,
            }
        ),
        language="it",
    )
    assert floor.computed["zeros"] == []
    [(a, b)] = floor.computed["zero_intervals"]
    assert abs(a) < 1e-9 and abs(b - 1.0) < 1e-9
    assert floor.computed["critical_points"] == []
    assert len(floor.computed["discontinuities"]) == 5
    assert {"zero_interval", "stationary_interval"} <= set(floor.warnings)
    assert floor.computed_caption == "Si annulla su [0, 1]. Valori approssimati."
    assert "zero-interval-0" in _ids(floor.svg) and "zero-0" not in _ids(floor.svg)

    constant = ffs.render_function_sync(
        _spec({"kind": "function_study", "expressions": [{"expr": "3"}], "domain": [-2, 2]}),
        language="it",
    )
    assert constant.computed["critical_points"] == [] and constant.computed["zeros"] == []
    assert "stationary_interval" in constant.warnings
    assert len(constant.svg) < 40_000

    underflow = ffs.render_function_sync(
        _spec(
            {
                "kind": "function_study",
                "expressions": [{"expr": "(((x**12)**12)**12)**12"}],
                "domain": [-2, 2],
                "show": show,
            }
        ),
        language="it",
    )
    assert [z["exact"] for z in underflow.computed["zeros"]] == ["0"]
    assert underflow.computed["zero_intervals"] == []
    assert underflow.computed_caption == "Zeri in x = 0. Punti critici in x = 0."


@needs_deps
def test_notable_points_are_capped_and_the_render_stays_bounded():
    """`sin(50*x)` su [-10, 10] ha 318 zeri e 319 punti critici: senza il
    tetto costava decine di secondi di CPU nel thread (un `TextPath` e un
    parse mathtext per etichetta), un SVG oltre il limite e una didascalia
    di migliaia di caratteri."""
    spec = _spec(
        {"kind": "function_study", "expressions": [{"expr": "sin(50*x)"}], "domain": [-10, 10]}
    )
    t0 = time.perf_counter()
    study = fnum.analyze(spec)
    computed, _approx = ffs.build_computed(spec, study, None)
    fplot.render_svg(spec, study, computed, content_hash=spec.content_hash())
    in_thread = time.perf_counter() - t0
    assert in_thread < 2.0, in_thread  # solo numerico + disegno (il figlio sympy è a parte)
    assert study.truncated == {"zeros", "critical_points"}
    assert len(study.zeros) == fnum.MAX_NOTABLE_POINTS == 12

    t0 = time.perf_counter()
    result = ffs.render_function_sync(spec, language="it")
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0, elapsed  # percorso completo, figlio sympy compreso (era 126 s)
    assert len(result.svg) < 300_000
    assert len(result.computed["zeros"]) == 12 and len(result.computed["critical_points"]) == 12
    assert result.computed["truncated"] == ["critical_points", "zeros"]
    assert "too_many_points" in result.warnings
    assert (
        result.computed_caption.endswith("Elenco troncato ai primi 12 punti per categoria.")
        and len(result.computed_caption) < 600
    )
    ids = _ids(result.svg)
    assert "zero-11" in ids and "zero-12" not in ids and "critical-12" not in ids
    # Gli zeri mantenuti sono i primi da sinistra, esatti (k·π/50).
    xs = [z["x"] for z in result.computed["zeros"]]
    assert xs == sorted(xs) and all(
        abs(x / (math.pi / 50) - round(x / (math.pi / 50))) < 1e-6 for x in xs
    )


@needs_deps
def test_removable_discontinuity_keeps_the_exact_oblique_asymptote():
    result = ffs.render_function_sync(
        _spec(
            {
                "kind": "function_study",
                "expressions": [{"expr": "(x**2-1)/(x-1)"}],
                "domain": [-3, 3],
                "show": ["zeros", "asymptotes", "discontinuities", "formula"],
            }
        ),
        language="it",
    )
    assert result.approximate is False
    assert [(a["kind"], a["latex"]) for a in result.computed["asymptotes"]] == [
        ("oblique", "y = x + 1")
    ]
    assert [round(x, 9) for x in result.computed["discontinuities"]] == [1.0]
    assert result.computed_caption == "Zeri in x = −1. Asintoto obliquo y = x + 1."


def _scalar(src: str) -> Any:
    fn = fnum.compile_numpy(fparse.check_expression(src, free_symbols=["x"]))
    return fnum.scalar_function(fn, variable="x", env={})


@needs_deps
def test_edge_poles_are_found_without_sympy():
    """`tan(x)` su [−π/2, π/2] e `1/x` su [0, 1]: il polo coincide con un
    estremo del dominio. Nel percorso approssimato (figlio sympy fallito)
    le regioni al bordo erano ignorate in blocco e l'asintoto verticale
    spariva; `exp(x)` in overflow al bordo resta senza polo, `log(x)` in 0
    cresce troppo lentamente per la soglia numerica (con sympy arriva
    dal figlio)."""
    half_pi = math.pi / 2
    boom = "tests.helpers.slow_target:boom"
    tan = ffs.render_function_sync(
        _spec(
            {
                "kind": "function_study",
                "expressions": [{"expr": "tan(x)"}],
                "domain": [-half_pi, half_pi],
                "show": ["zeros", "asymptotes"],
            }
        ),
        language="it",
        symbolic_target=boom,
    )
    assert tan.approximate is True
    assert [(a["kind"], round(a["x"], 6)) for a in tan.computed["asymptotes"]] == [
        ("vertical", -1.570796),
        ("vertical", 1.570796),
    ]
    assert tan.computed_caption == (
        "Zeri in x = 0. Asintoto verticale x = −1.571. Asintoto verticale x = 1.571. "
        "Valori approssimati."
    )
    assert {"asymptote-0", "asymptote-1"} <= _ids(tan.svg)

    inverse = ffs.render_function_sync(
        _spec(
            {
                "kind": "function_study",
                "expressions": [{"expr": "1/x"}],
                "domain": [0, 1],
                "show": ["zeros", "asymptotes"],
            }
        ),
        language="it",
        symbolic_target=boom,
    )
    assert [(a["kind"], a["expr"]) for a in inverse.computed["asymptotes"]] == [
        ("vertical", "x = 0"),
        ("horizontal", "y = 0"),
    ]

    assert fnum.edge_pole(_scalar("tan(x)"), half_pi, -1.0, scale=1.0, width=math.pi)
    assert fnum.edge_pole(_scalar("1/x"), 0.0, 1.0, scale=2.0, width=1.0)
    assert fnum.edge_pole(_scalar("1/(x-1)**2"), 1.0, -1.0, scale=1.0, width=1.0)
    assert not fnum.edge_pole(_scalar("exp(x)"), 1000.0, -1.0, scale=1.0, width=1000.0)
    assert not fnum.edge_pole(_scalar("exp(x)"), 709.5, -1.0, scale=1.0, width=1000.0)
    assert not fnum.edge_pole(_scalar("log(x)"), 0.0, 1.0, scale=1.0, width=1.0)
    assert not fnum.edge_pole(_scalar("x**10"), 100.0, -1.0, scale=1.0, width=200.0)
    assert not fnum.edge_pole(_scalar("sin(x)/x"), 0.0, 1.0, scale=1.0, width=10.0)


@needs_deps
def test_caption_and_asymptotes_follow_the_spec_variable():
    """`variable: "t"`: la didascalia, gli asintoti e l'asse usano `t`, non
    una «x» cablata che contraddiceva formula e asse."""

    def study(var: str) -> dict[str, Any]:
        return {
            "kind": "tangent",
            "expressions": [{"expr": f"({var}**2 - 1)/({var} - 2)"}],
            "variable": var,
            "domain": [-6, 8],
            "annotations": [{"kind": "tangent", "at": 3}],
            "show": ["zeros", "critical_points", "asymptotes", "formula"],
        }

    in_x = ffs.render_function_sync(_spec(study("x")), language="it")
    in_t = ffs.render_function_sync(_spec(study("t")), language="it")
    assert in_t.computed["variable"] == "t" and in_x.computed["variable"] == "x"
    assert in_x.computed_caption.startswith("Zeri in x = −1, 1. Punti critici in x = 2 − √3, ")
    assert "Asintoto verticale x = 2. Asintoto obliquo y = x + 2. Tangente in x = 3" in (
        in_x.computed_caption
    )
    assert in_t.computed_caption == in_x.computed_caption.replace("x =", "t =").replace(
        "y = x + 2", "y = t + 2"
    )
    assert [a["expr"] for a in in_t.computed["asymptotes"]] == ["t = 2", "y = t + 2"]
    assert in_t.latex == [r"\frac{t^{2} - 1}{t - 2}"]
    approx = ffs.render_function_sync(
        _spec(study("t")), language="en", symbolic_target="tests.helpers.slow_target:boom"
    )
    assert approx.warnings == in_t.warnings and not approx.approximate  # cache: stesso hash
    ffs.clear_result_cache()
    approx = ffs.render_function_sync(
        _spec(study("t")), language="en", symbolic_target="tests.helpers.slow_target:boom"
    )
    assert approx.computed_caption.startswith("Zeros at t = −1, 1. Critical points at t = ")
    assert "Vertical asymptote t = 2. Oblique asymptote y = t + 2. Tangent at t = 3" in (
        approx.computed_caption
    )


@needs_deps
def test_concurrent_draws_match_serial_and_leave_rcparams_clean():
    """`rc_context` tocca `matplotlib.rcParams` (globale): senza il lock di
    modulo due disegni concorrenti si scambiavano `svg.hashsalt` e gli
    altri parametri e lasciavano rcParams inquinati."""
    import threading

    import matplotlib

    specs = [
        _spec({"kind": "function_study", "expressions": [{"expr": "x**2 - 1"}], "domain": [-2, 2]}),
        _spec({"kind": "function_study", "expressions": [{"expr": "sin(x)"}], "domain": [-3, 3]}),
    ]
    studies = {s.content_hash(): fnum.analyze(s) for s in specs}

    def draw(spec: FunctionFigureSpec) -> str:
        study = studies[spec.content_hash()]
        computed, _ = ffs.build_computed(spec, study, None)
        return fplot.render_svg(spec, study, computed, content_hash=spec.content_hash())[0]

    def worker(spec: FunctionFigureSpec, out: dict[str, str]) -> None:
        out[spec.content_hash()] = draw(spec)

    before = (matplotlib.rcParams["svg.hashsalt"], matplotlib.rcParams["svg.fonttype"])
    serial = {s.content_hash(): draw(s) for s in specs}
    for _round in range(6):
        out: dict[str, str] = {}
        threads = [threading.Thread(target=worker, args=(s, out)) for s in specs * 2]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert out == serial
    assert (matplotlib.rcParams["svg.hashsalt"], matplotlib.rcParams["svg.fonttype"]) == before


@needs_deps
def test_result_cache_is_shared_across_languages_and_deadline_stops_the_engine(monkeypatch):
    spec = _spec(
        {"kind": "function_study", "expressions": [{"expr": "x**2 - 1"}], "domain": [-2, 2]}
    )
    first = ffs.render_function_sync(spec, language="it")
    calls: list[str] = []
    monkeypatch.setattr(ffs.function_numeric, "analyze", lambda s: calls.append("analyze"))
    english = ffs.render_function_sync(spec, language="en")
    assert calls == []  # nessun ricalcolo: SVG e computed non dipendono dalla lingua
    assert english.svg == first.svg and english.computed == first.computed
    assert english.computed_caption == "Zeros at x = −1, 1. Critical points at x = 0."
    assert len(ffs._result_cache) == 1
    monkeypatch.undo()
    # Scadenza già passata: il motore si ferma al primo controllo, senza
    # calcolare né disegnare (il thread orfano dell'endpoint termina subito).
    ffs.clear_result_cache()
    with pytest.raises(ffs.FunctionRenderError, match="scadenza"):
        ffs.render_function_sync(spec, language="it", deadline=time.monotonic() - 1)


@needs_deps
async def test_render_function_timeout_releases_and_the_thread_ends(monkeypatch):
    from app.core import config

    settings = config.get_settings().model_copy(update={"figure_render_timeout_seconds": 1})
    monkeypatch.setattr(frs, "get_settings", lambda: settings)
    spec = _spec(
        {
            "kind": "function_study",
            "expressions": [{"expr": "sin(1000*x)"}],
            "domain": [-10, 10],
            "sampling": {"points": 2000},
            "show": ["zeros", "critical_points", "inflection_points", "asymptotes", "formula"],
        }
    )
    import threading

    finished = threading.Event()
    outcome: list[BaseException | None] = []
    drawn: list[str] = []
    original = ffs.render_function_sync

    def tracked(*args: Any, **kwargs: Any) -> Any:
        try:
            return original(*args, **kwargs)
        except BaseException as exc:
            outcome.append(exc)
            raise
        finally:
            finished.set()

    monkeypatch.setattr(frs.figure_function_service, "render_function_sync", tracked)
    monkeypatch.setattr(
        ffs.function_plot, "render_svg", lambda *a, **k: drawn.append("draw") or ("", [])
    )
    t0 = time.perf_counter()
    with pytest.raises(frs.FigureTimeoutError):
        await frs.render_function(spec, language="it")
    assert time.perf_counter() - t0 < 3.0
    # Il thread (non interrompibile) riceve la scadenza: il figlio sympy è
    # ucciso entro la stessa scadenza e il motore si ferma al controllo
    # successivo senza disegnare, invece di completare il lavoro a vuoto.
    assert await asyncio.to_thread(finished.wait, 6.0)
    assert drawn == []
    assert len(outcome) == 1 and isinstance(outcome[0], ffs.FunctionRenderError)
    assert "scadenza" in str(outcome[0])
    assert len(ffs._result_cache) == 0


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
    # Cache dei risultati: stesso esito; lingua diversa: stesso SVG, altra coda.
    assert ffs.render_function_sync(spec, language="it") == first
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
    # Senza il LaTeX del figlio la formula è scritta dall'AST (`x^{2} - 2`
    # come geometria), mai in sintassi Python.
    assert "formula_not_mathtext" not in result.warnings and "x**2" not in result.svg
    assert '<g id="formula">' in result.svg
    assert (
        result.computed_caption
        == "Zeri in x = −1.414, 1.414. Punti critici in x = 0. Valori approssimati."
    )
    ffs.clear_result_cache()
    failed = ffs.render_function_sync(
        spec, language="it", symbolic_target="tests.helpers.slow_target:boom"
    )
    assert failed.approximate is True and "symbolic_failed" in failed.warnings


def test_run_isolated_start_failure_is_a_compute_error(monkeypatch):
    """Un errore di `Process.start()` (descrittori esauriti, bootstrap del
    processo principale non concluso) era un'`OSError`/`RuntimeError` nuda
    che risaliva intatta dal motore fino all'endpoint (500) e dal renderer
    nel worker: ora è `FigureComputeError` come ogni altro fallimento del
    figlio."""
    from app.services.figure_compute import isolated

    real_ctx = isolated.multiprocessing.get_context("spawn")

    class _Broken:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def start(self) -> None:
            raise OSError(24, "Too many open files")

    class _Ctx:
        Pipe = staticmethod(real_ctx.Pipe)
        Process = _Broken

    monkeypatch.setattr(isolated.multiprocessing, "get_context", lambda _name: _Ctx())
    with pytest.raises(isolated.FigureComputeError, match="avvio del processo figlio fallito"):
        isolated.run_isolated("tests.helpers.slow_target:echo", {"a": 1}, timeout=1)


@needs_deps
def test_engine_degrades_to_symbolic_failed_on_any_child_error(monkeypatch):
    spec = _spec(
        {"kind": "function_study", "expressions": [{"expr": "x**2 - 2"}], "domain": [-3, 3]}
    )

    def broken_start(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("bootstrapping phase")

    monkeypatch.setattr(ffs, "run_isolated", broken_start)
    result = ffs.render_function_sync(spec, language="it")
    assert result.approximate is True and "symbolic_failed" in result.warnings
    assert "zero-0" in _ids(result.svg)


@needs_deps
def test_formula_and_large_numbers_stay_inside_the_figure():
    """Casi del verificatore (giro 2). `((10**12)**12)**12*x`: LaTeX di
    1.731 cifre dal figlio sympy, SVG di 1,4 MB con la formula da
    x = −9.573 pt; `(10**12)**12*x`: didascalia e tick con 145 cifre; un
    polinomio di grado 11 con coefficienti decimali usciva dal viewBox a
    sinistra (x = −64,6 pt su 374,4). Ora ogni testo è bounded: LaTeX
    oltre 160 caratteri omesso (formula dall'AST), notazione scientifica da
    1e6 in modulo, formula misurata e ridotta di corpo, altrimenti `f(x)`
    con `formula_too_wide`."""
    huge = ffs.render_function_sync(
        _spec(
            {
                "kind": "function_study",
                "expressions": [{"expr": "((10**12)**12)**12*x"}],
                "domain": [-1, 1],
            }
        ),
        language="it",
    )
    width = _view_box_width(huge.svg)
    lo, hi = _path_x_range(huge.svg, "formula")
    assert 0.0 <= lo < hi <= width, (lo, hi, width)
    assert len(huge.svg) < 60_000
    assert huge.latex == [""] and "symbolic_latex_too_long" in huge.warnings
    assert fplot.FORMULA_TOO_WIDE not in huge.warnings  # `((10^{12})^{12})^{12}\,x` entra

    big = ffs.render_function_sync(
        _spec(
            {
                "kind": "function_study",
                "expressions": [{"expr": "(10**12)**12*x"}],
                "domain": [-1, 1],
            }
        ),
        language="it",
    )
    assert big.computed_caption == (
        "Zeri in x = 0. Asintoto obliquo y = 1×10¹⁴⁴ x. Valori approssimati."
    )
    assert all(len(a["expr"]) < 40 and not a["latex"] for a in big.computed["asymptotes"])
    assert not re.search(r"\d{20}", big.svg)  # né tick né etichette con decine di cifre
    assert "×10" in big.svg  # tick dell'asse y in notazione scientifica
    lo, hi = _path_x_range(big.svg, "formula")
    assert 0.0 <= lo < hi <= width and len(big.svg) < 60_000

    poly = ffs.render_function_sync(
        _spec(
            {
                "kind": "function_study",
                "expressions": [
                    {
                        "expr": (
                            "123456.789*x**11 + 98765.4321*x**10 - 55555.5555*x**9"
                            " + 4444.4444*x**8 - 333.333*x**7 + 22.22*x**6"
                        )
                    }
                ],
                "domain": [-1, 1],
            }
        ),
        language="it",
    )
    lo, hi = _path_x_range(poly.svg, "formula")
    assert 0.0 <= lo < hi <= width, (lo, hi)
    assert fplot.FORMULA_TOO_WIDE not in poly.warnings  # ridotta di corpo, non omessa

    # Nove funzioni distinte (sympy non le compatta; ≤ 80 nodi e ≤ 12
    # livelli del parser): ~480 pt al corpo di 9 pt, oltre il minimo anche
    # ridotta (6,1 pt < 6,5).
    too_long = " + ".join(
        f"{fn}({m})"
        for m, fns in (
            ("x", ("asin", "acos", "atan", "sinh", "cosh", "tanh")),
            ("2*x", ("asin", "acos", "atan")),
        )
        for fn in fns
    )
    assert len(too_long) <= 200
    wide = ffs.render_function_sync(
        _spec(
            {
                "kind": "function_study",
                "expressions": [{"expr": too_long}],
                "domain": [-1, 1],
                "show": ["formula"],
            }
        ),
        language="it",
    )
    assert fplot.FORMULA_TOO_WIDE in wide.warnings
    formula = _group(wide.svg, "formula")
    assert "f(x)" in formula and "<path" not in formula  # solo il nome, come `<text>`

    # Le costanti dell'AST fuori da [1e-3, 1e6) sono scritte in notazione scientifica.
    assert fplot.expr_to_mathtext("2e-05*x + 3000000*x**2") == (
        r"2 \cdot 10^{-5}\,x + 3 \cdot 10^{6}\,x^{2}"
    )
    text = r"f(x) = $x^{2} + 2\,x + 1$"
    natural = fplot.text_width_pt(text, size=fplot.MATH_SIZE_PT)
    assert fplot.fit_size(text, available_pt=natural) == fplot.MATH_SIZE_PT
    assert fplot.fit_size(text, available_pt=0.5 * natural) is None
    reduced = fplot.fit_size(text, available_pt=0.85 * natural)
    assert reduced is not None and fplot.MIN_MATH_SIZE_PT <= reduced < fplot.MATH_SIZE_PT
    assert fplot.text_width_pt(text, size=reduced) <= 0.85 * natural + 1e-6


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


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("x**2 - 2", "x^{2} - 2"),
        ("(x**2 - 1)/(x - 2)", r"\frac{x^{2} - 1}{x - 2}"),
        ("2*x", r"2\,x"),
        ("x*2", r"x \cdot 2"),
        ("sin(x)**2 + 2*x", r"\sin(x)^{2} + 2\,x"),
        ("log(x, 2)", r"\log_{2}(x)"),
        ("exp(-x**2)", "e^{-x^{2}}"),
        ("abs(x - 1)", "|x - 1|"),
        ("floor(x)/pi", r"\frac{\lfloor x \rfloor}{\pi}"),
        ("x - -2", "x - (-2)"),
        ("x**(1/3)", r"x^{\frac{1}{3}}"),
        ("E**x", "e^{x}"),
        ("sqrt(x**2 + 1)", r"\sqrt{x^{2} + 1}"),
        ("(x + 1)**2", "(x + 1)^{2}"),
        ("-(x + 1)", "-(x + 1)"),
    ],
)
def test_expr_to_mathtext_writes_the_formula_from_the_ast(src: str, expected: str):
    pytest.importorskip("matplotlib")
    assert fplot.expr_to_mathtext(src) == expected
    assert fplot.expr_to_mathtext("x +") is None


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
    # Contenuto con fence: estrazione e applicazione passano dalla stessa
    # sanificazione (prima la traduzione andava persa in silenzio).
    fenced = "```json\n" + content + "\n```"
    assert renderer.extract_translatable(fenced) == fields
    translated = json.loads(renderer.apply_translations(fenced, {"expressions.0.label": "speed"}))
    assert translated["expressions"][0]["label"] == "speed"


def test_function_translation_keeps_source_formatting():
    """I18N-3: la spec non viene riserializzata. La formattazione scritta
    dal docente resta e il round-trip con traduzioni identiche è
    byte-identico."""
    renderer = frs.REGISTRY["function"]
    content = (
        '{\n  "kind": "function_study",\n'
        '  "expressions": [{"expr": "x**2", "label": "parabola"}],\n'
        '  "domain": [-3, 3],\n'
        '  "annotations": [{"kind": "point", "at": 1, "label": "vertice"}]\n}'
    )
    assert renderer.apply_translations(content, renderer.extract_translatable(content)) == content
    out = renderer.apply_translations(content, {"expressions.0.label": "parabola (rossa)"})
    assert out == content.replace('"parabola"', '"parabola (rossa)"')
    assert out.count("\n") == content.count("\n")
    # Una label che nel sorgente non esiste non ha una posizione da
    # sostituire: si ricade sulla riserializzazione, senza perderla.
    senza = (
        '{\n  "kind": "function_study",\n  "expressions": [{"expr": "x"}],\n  "domain": [0, 1]\n}'
    )
    nuova = json.loads(renderer.apply_translations(senza, {"expressions.0.label": "retta"}))
    assert nuova["expressions"][0] == {"expr": "x", "label": "retta"}


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


# ---------------------------------------------------------------------------
# Leggibilità del disegno (Fase D: TIP-3, TIP-4, TIP-5)
# ---------------------------------------------------------------------------


_TEXT_RE = re.compile(r'<text[^>]*\sx="([-\d.]+)"[^>]*\sy="([-\d.]+)"[^>]*>([^<]*)</text>')


def _texts(svg: str) -> list[tuple[float, float, str]]:
    return [(float(m.group(1)), float(m.group(2)), m.group(3)) for m in _TEXT_RE.finditer(svg)]


def _path_y_range(svg: str, gid: str) -> tuple[float, float]:
    ys: list[float] = []
    for d in re.findall(r'\sd="([^"]+)"', _group(svg, gid)):
        numbers = [float(t) for t in _NUMBER_RE.findall(d)]
        ys.extend(numbers[1::2])
    assert ys, gid
    return (min(ys), max(ys))


@needs_deps
def test_axis_name_sits_above_the_last_tick_label():
    """Il nome dell'asse x era ancorato alla punta della freccia con
    `va="top"` e 2 pt di scarto: sulla stessa riga del tick dell'estremo
    destro, che è centrato sulla fine dell'asse, i due si leggevano come un
    unico token («8x»). Ora sta sopra la freccia (TIP-4)."""
    result = ffs.render_function_sync(
        _spec(
            {
                "kind": "function_study",
                "expressions": [{"expr": "(x**2 - 1)/(x - 2)"}],
                "domain": [-6, 8],
            }
        ),
        language="it",
    )
    texts = _texts(result.svg)
    name = next(t for t in texts if t[2] == "x")
    tick = max((t for t in texts if t[2] == "8"), key=lambda t: t[0])
    assert abs(tick[0] - name[0]) < 12.0, (name, tick)  # il tick è all'estremo
    assert name[1] <= tick[1] - 8.0, (name, tick)  # su una riga più alta


@needs_deps
def test_tick_labels_are_drawn_over_the_curves_with_a_white_backdrop():
    """Con le spine a zero i tick stanno dentro l'area dati e matplotlib
    disegna l'asse SOTTO le curve: un ramo che passa per un tick lo
    cancellava (le ellissi di livello passano esattamente per ±1, ±2). Ora
    ogni etichetta ha un riquadro bianco e resta `<text>` (TIP-5, A14)."""
    result = ffs.render_function_sync(
        _spec(
            {
                "kind": "level_curves",
                "expressions": [{"expr": "x**2 + y**2"}],
                "variables": ["x", "y"],
                "domain": [-3, 3],
                "range": [-3, 3],
                "levels": [1, 2, 4],
            }
        ),
        language="it",
    )
    svg = result.svg
    assert "<image" not in svg  # nessun raster: l'alone è geometria
    minus_two = next(m.start() for m in re.finditer(r">−2</text>", svg))
    backdrop = svg.rfind('style="fill: #ffffff; opacity:', 0, minus_two)
    assert backdrop > 0 and minus_two - backdrop < 400, "riquadro bianco assente sotto il tick"
    # I tick sono disegnati dopo le curve: l'ordine del documento è
    # l'ordine di pittura.
    assert svg.index('id="levels"') < minus_two


@needs_deps
def test_exact_zero_labels_never_duplicate_or_overlap_a_tick():
    """`0` sopra il tick `0` («0₀») e `−π` sopra il tick `−3` («−3π»): la
    prima è informazione duplicata, la seconda una lettura falsa. Ora
    l'etichetta identica al tick è omessa e quella diversa scende di una
    riga (TIP-5)."""
    parabola = ffs.render_function_sync(
        _spec(
            {
                "kind": "function_study",
                "expressions": [{"expr": "x**2 - 2*x"}],
                "domain": [-3, 3],
            }
        ),
        language="it",
    )
    # Zeri in 0 e 2, entrambi su un tick con lo stesso testo: nessuna
    # etichetta esatta, i marcatori restano.
    assert not [i for i in _ids(parabola.svg) if i.startswith("zero-label-")]
    assert "zero-0" in _ids(parabola.svg)

    tangente = ffs.render_function_sync(
        _spec(
            {
                "kind": "function_study",
                "expressions": [{"expr": "tan(x)"}],
                "domain": [-4.5, 4.5],
                "range": [-6, 6],
            }
        ),
        language="it",
    )
    svg = tangente.svg
    assert svg.count(">0</text>") == 1  # solo il tick, non anche lo zero esatto
    top, _bottom = _path_y_range(svg, "zero-label-0")
    tick = next(t for t in _texts(svg) if t[2] == "−3")
    assert top > tick[1], (top, tick)  # seconda riga, sotto il tick


@needs_deps
def test_formula_and_point_labels_are_painted_over_the_curves_with_a_halo():
    """I `PathPatch` di matplotlib stanno a zorder 1, le linee a 2: la curva
    passava sopra «f(x) = eˣ» e sopra le coordinate esatte dei punti
    critici. Ora il testo è sopra, con un alone bianco (TIP-3)."""
    result = ffs.render_function_sync(
        _spec({"kind": "function_study", "expressions": [{"expr": "exp(x)"}], "domain": [-2, 3]}),
        language="it",
    )
    svg = result.svg
    assert svg.index('id="branch-0-0"') < svg.index('id="formula"')
    formula = _group(svg, "formula")
    assert formula.count("<path") == 2  # alone + glifi
    assert "stroke: #ffffff" in formula


@needs_deps
@pytest.mark.parametrize(
    ("expr", "domain"),
    [("x/0", [-2, 2]), ("sqrt(x)", [-2, -1]), ("log(x)", [-3, -1]), ("asin(x)", [2, 3])],
)
def test_deep_validation_rejects_an_expression_undefined_on_the_whole_domain(
    expr: str, domain: list[float]
):
    """Senza campioni finiti la figura esce senza rami, `approximate=False` e
    con la didascalia «Nessun punto notevole nel dominio considerato»: il
    check del worker era verde, il fix AI non partiva e la lezione andava
    `ready` con una figura vuota in dispensa, slide e frame (COR-2)."""
    content = json.dumps(
        {"kind": "function_study", "expressions": [{"expr": expr}], "domain": domain}
    )
    ok, err = frs.REGISTRY["function"].validate(content, deep=True)
    assert ok is False and "indefinita" in err, err
    # La spec resta valida a `deep=False` (gate del PATCH, A15) e
    # l'anteprima dell'editor continua a rispondere: il docente vede il
    # dominio sbagliato invece di un errore secco.
    assert frs.REGISTRY["function"].validate(content) == (True, "")


@needs_deps
def test_deep_validation_keeps_accepting_informative_warnings():
    """Le avvertenze informative (`zero_interval`, `symbolic_*`) non sono
    errori: solo l'assenza totale di rami lo è."""
    content = json.dumps(
        {"kind": "function_study", "expressions": [{"expr": "1/(x - 0.5)"}], "domain": [-2, 2]}
    )
    assert frs.REGISTRY["function"].validate(content, deep=True) == (True, "")
