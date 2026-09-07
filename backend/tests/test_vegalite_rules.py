"""Regole D5 ed euristica del criterio 10 di `figure_compute.vegalite_rules`.

Test puro (nessuna libreria, nessuna rete). L'ereditarietà dei campi
`sequence` (difetto della prima stesura, corretto in WP2b) è verificata alla
radice e nei figli `layer` / `vconcat` / `concat` / `hconcat` / `spec`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.figure_compute.vegalite_rules import (
    MAX_DEPTH,
    MAX_NESTING,
    MAX_SEQUENCE_STEPS,
    MAX_TITLE_CHARS,
    MAX_VALUES_ROWS,
    USE_FUNCTION_FORMAT,
    check_vegalite_rules,
    nesting_depth,
    nesting_violation,
)

_QUANT_XY = {
    "x": {"field": "x", "type": "quantitative", "scale": {"domain": [0, 10]}},
    "y": {"field": "y", "type": "quantitative", "scale": {"domain": [-1, 1]}},
}


def _bar() -> dict[str, Any]:
    return {
        "data": {"values": [{"k": "a", "v": 1}, {"k": "b", "v": 2}]},
        "mark": "bar",
        "encoding": {
            "x": {"field": "k", "type": "nominal"},
            "y": {"field": "v", "type": "quantitative", "scale": {"domain": [0, 3]}},
        },
    }


def _line_view(calculate: str) -> dict[str, Any]:
    return {
        "transform": [{"calculate": calculate, "as": "y"}],
        "mark": {"type": "line", "clip": True},
        "encoding": json.loads(json.dumps(_QUANT_XY)),
    }


def _sequence() -> dict[str, Any]:
    return {"sequence": {"start": 0, "stop": 10, "step": 0.1, "as": "x"}}


def _use_function(errors: list[str]) -> bool:
    return bool(errors) and errors[0].startswith(USE_FUNCTION_FORMAT)


# ---------------------------------------------------------------------------
# D5
# ---------------------------------------------------------------------------


def test_conforming_bar_chart_has_no_violations():
    assert check_vegalite_rules(_bar()) == []


def test_data_url_and_name_without_datasets_are_rejected():
    spec = _bar()
    spec["data"] = {"url": "data.csv"}
    assert any("data.url" in e for e in check_vegalite_rules(spec))
    spec["data"] = {"name": "tab"}
    assert any("data.name" in e for e in check_vegalite_rules(spec))
    spec["datasets"] = {"tab": [{"k": "a", "v": 1}]}
    assert not any("data.name" in e for e in check_vegalite_rules(spec))


def test_data_url_is_rejected_inside_a_nested_layer():
    spec = {"layer": [{"data": {"url": "x.csv"}, "mark": "bar"}]}
    errors = check_vegalite_rules(spec)
    assert any("data.url" in e and "profondità 1" in e for e in errors)


def _lookup(data: dict[str, Any]) -> dict[str, Any]:
    """Vista con un `transform.lookup` il cui `from.data` è `data`."""
    spec = _bar()
    spec["transform"] = [{"lookup": "k", "from": {"data": data, "key": "k", "fields": ["z"]}}]
    return spec


@pytest.mark.parametrize(
    "url", ["https://example.org/x.json", "file:///etc/hosts", "/etc/hosts", "data/x.json"]
)
def test_data_url_hidden_in_a_lookup_transform_is_rejected(url: str):
    """`allowed_base_urls=[]` di vl-convert ferma solo gli URL http(s) e
    `file://` produce un join vuoto in silenzio: il gate statico deve
    vedere ogni oggetto `data` della vista, non solo quello di primo livello."""
    errors = check_vegalite_rules(_lookup({"url": url}))
    assert any("transform[0].from.data.url" in e for e in errors), errors


def test_data_name_hidden_in_a_lookup_transform_requires_datasets():
    spec = _lookup({"name": "tab"})
    assert any("transform[0].from.data.name" in e for e in check_vegalite_rules(spec))
    spec["datasets"] = {"tab": [{"k": "a", "z": 1}]}
    assert check_vegalite_rules(spec) == []


def test_lookup_data_rules_apply_inside_a_layer_and_cap_values():
    nested = {"layer": [_lookup({"url": "x.csv"})]}
    errors = check_vegalite_rules(nested)
    assert any("profondità 1" in e and "transform[0].from.data.url" in e for e in errors)
    big = _lookup({"values": [{"k": i, "z": i} for i in range(MAX_VALUES_ROWS + 1)]})
    assert any("transform[0].from.data.values oltre" in e for e in check_vegalite_rules(big))
    small = _lookup({"values": [{"k": "a", "z": 1}]})
    assert check_vegalite_rules(small) == []


def test_mark_image_is_rejected_in_both_forms():
    spec = _bar()
    spec["mark"] = "image"
    assert any("mark image" in e for e in check_vegalite_rules(spec))
    spec["mark"] = {"type": "image"}
    assert any("mark image" in e for e in check_vegalite_rules(spec))


@pytest.mark.parametrize("mark", ["line", "area", "point", "trail"])
def test_clip_true_is_mandatory_on_line_like_marks(mark: str):
    spec = {"data": _sequence(), "mark": mark, "encoding": _QUANT_XY}
    errors = check_vegalite_rules(spec)
    assert any("clip:true" in e for e in errors), errors
    spec["mark"] = {"type": mark, "clip": True}
    assert not any("clip" in e for e in check_vegalite_rules(spec))
    spec["mark"] = {"type": mark, "clip": False}
    assert any("clip:true" in e for e in check_vegalite_rules(spec))


def test_quantitative_axes_require_a_numeric_domain():
    spec = _bar()
    del spec["encoding"]["y"]["scale"]
    assert any("scale.domain" in e for e in check_vegalite_rules(spec))
    spec["encoding"]["y"]["scale"] = {"domain": [3, 0]}  # min >= max
    assert any("scale.domain" in e for e in check_vegalite_rules(spec))
    spec["encoding"]["y"]["scale"] = {"domain": [True, 3]}  # bool non è un numero
    assert any("scale.domain" in e for e in check_vegalite_rules(spec))


def test_axis_format_is_restricted_to_short_d3_specifiers():
    spec = _bar()
    spec["encoding"]["y"]["axis"] = {"format": ".1f"}
    assert check_vegalite_rules(spec) == []
    spec["encoding"]["y"]["axis"] = {"format": "%Y-%m-%dT%H:%M:%S"}
    assert any("axis.format" in e for e in check_vegalite_rules(spec))


def test_title_only_at_root_string_and_bounded():
    spec = _bar()
    spec["title"] = "x" * (MAX_TITLE_CHARS + 1)
    assert any("title oltre" in e for e in check_vegalite_rules(spec))
    spec["title"] = {"text": "obj"}
    assert any("title deve essere una stringa" in e for e in check_vegalite_rules(spec))
    nested = {"layer": [dict(_bar(), title="Sotto")]}
    assert any("title ammesso solo" in e for e in check_vegalite_rules(nested))


def test_values_rows_are_capped_also_in_datasets():
    spec = _bar()
    spec["data"] = {"values": [{"k": i, "v": i} for i in range(MAX_VALUES_ROWS + 1)]}
    assert any("data.values oltre" in e for e in check_vegalite_rules(spec))
    spec = _bar()
    spec["datasets"] = {"big": [{"k": i} for i in range(MAX_VALUES_ROWS + 1)]}
    assert any("datasets.big oltre" in e for e in check_vegalite_rules(spec))
    spec = _bar()
    spec["data"] = {"values": "k,v\n" + "\n".join(f"{i},{i}" for i in range(MAX_VALUES_ROWS + 1))}
    assert any("data.values oltre" in e for e in check_vegalite_rules(spec))


def test_sequence_requires_numbers_positive_step_and_bounded_length():
    for seq, needle in (
        ({"start": "0", "stop": 1, "step": 1}, "numerici"),
        ({"start": 0, "stop": 1, "step": 0}, "step deve essere > 0"),
        ({"start": 0, "stop": MAX_SEQUENCE_STEPS + 10, "step": 1}, "oltre"),
    ):
        spec = {"data": {"sequence": seq}, "mark": "bar"}
        assert any(needle in e for e in check_vegalite_rules(spec)), (seq, needle)


@pytest.mark.parametrize(
    "key", ["selection", "params", "interactive", "config", "usermeta", "tooltip"]
)
def test_forbidden_view_keys(key: str):
    spec = _bar()
    spec[key] = {}
    assert any(f"{key} non ammesso" in e for e in check_vegalite_rules(spec))


def test_tooltip_and_href_inside_mark_and_encoding_are_rejected():
    spec = _bar()
    spec["mark"] = {"type": "bar", "tooltip": True}
    assert any("mark.tooltip" in e for e in check_vegalite_rules(spec))
    spec = _bar()
    spec["encoding"]["href"] = {"field": "u"}
    spec["encoding"]["tooltip"] = {"field": "v"}
    errors = check_vegalite_rules(spec)
    assert any("encoding.href" in e for e in errors)
    assert any("encoding.tooltip" in e for e in errors)


def test_composition_depth_is_bounded():
    spec: dict[str, Any] = _bar()
    for _ in range(MAX_DEPTH + 1):
        spec = {"layer": [spec]}
    assert any("composizione oltre" in e for e in check_vegalite_rules(spec))


def test_json_nesting_is_measured_iteratively_and_capped():
    """`nesting_depth` non ricorre: un annidamento oltre il limite
    dell'interprete non solleva; `check_vegalite_rules` rifiuta oltre
    `MAX_NESTING` prima di ogni visita ricorsiva (jsonschema cade a ~100
    livelli di `and`, misurato)."""
    assert nesting_depth(1) == 0 and nesting_depth({}) == 1 and nesting_depth([[]]) == 2
    assert nesting_depth(_bar()) == 5  # encoding.y.scale.domain
    assert nesting_violation(_bar()) is None
    deep: Any = 1
    for _ in range(5_000):
        deep = [deep]
    assert nesting_depth(deep) == 5_000
    predicate: dict[str, Any] = {"field": "v", "gt": 0}
    for _ in range(MAX_NESTING):
        predicate = {"and": [predicate]}
    spec = {**_bar(), "transform": [{"filter": predicate}]}
    (error,) = check_vegalite_rules(spec)
    assert error.startswith(f"spec annidata oltre {MAX_NESTING} livelli")
    assert nesting_violation(spec) == error
    ok: dict[str, Any] = _bar()
    for _ in range(MAX_DEPTH):
        ok = {"layer": [ok]}
    assert check_vegalite_rules(ok) == []  # composizione al massimo: sotto il cap


# ---------------------------------------------------------------------------
# Criterio 10: H1-H3 alla radice e nei figli (ereditarietà di `sequence`)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("calculate", "reason"),
    [
        ("sin(datum.x)", "trascendente"),
        ("exp(-datum.x*datum.x)", "trascendente"),
        ("1 / datum.x", "razionale"),
        ("1/(2*datum.x + 1)", "razionale"),
        ("1/(-datum.x)", "razionale"),  # segno prima di datum
        ("1/(2*(datum.x+1))", "razionale"),  # parentesi annidata
        ("datum.x ** 2", "potenza"),
        ("datum.x^3", "potenza"),
        ("sqrt(datum['x'])", "trascendente"),  # forma datum["campo"]
        # Costanti simboliche di Vega davanti a `datum` a denominatore.
        ("1/(PI*datum.x)", "razionale"),
        ("1/(2*PI*datum.x)", "razionale"),
        ("1 / (E - datum.x)", "razionale"),
        ("1/(LN2 + 3*datum.x)", "razionale"),
    ],
)
def test_criterion_10_at_root(calculate: str, reason: str):
    spec = {"data": _sequence(), **_line_view(calculate)}
    errors = check_vegalite_rules(spec)
    assert _use_function(errors), errors
    assert reason in errors[0] and 'format="function"' in errors[0]


@pytest.mark.parametrize(
    "calculate", ["2*datum.x + 1", "datum.x*datum.x - 3*datum.x", "datum.x", "datum.x / 2"]
)
def test_lines_and_polynomials_written_with_star_are_allowed(calculate: str):
    spec = {"data": _sequence(), **_line_view(calculate)}
    assert not _use_function(check_vegalite_rules(spec))


def test_alias_of_a_sequence_field_is_still_a_sequence_field():
    """`{"calculate": "datum.x", "as": "t"}` seguito da `sin(datum.t)` è
    `sin(datum.x)`: l'alias eredita la natura di campo `sequence`."""
    spec = {"data": _sequence(), **_line_view("sin(datum.t)")}
    spec["transform"].insert(0, {"calculate": "datum.x", "as": "t"})
    errors = check_vegalite_rules(spec)
    assert _use_function(errors) and "transform[1]" in errors[0], errors
    # Un alias definito in una vista padre vale anche nei figli.
    parent = {
        "data": _sequence(),
        "transform": [{"calculate": "2*datum.x", "as": "t"}],
        "layer": [_line_view("exp(datum.t)")],
    }
    errors = check_vegalite_rules(parent)
    assert _use_function(errors) and "profondità 1" in errors[0], errors
    # Un `calculate` che non referenzia la sequenza non crea un alias.
    spec = {"data": _sequence(), **_line_view("sin(datum.c)")}
    spec["transform"].insert(0, {"calculate": "2", "as": "c"})
    assert not _use_function(check_vegalite_rules(spec))


def test_calculate_without_sequence_is_not_a_function():
    spec = {"data": {"values": [{"x": 1, "y": 2}]}, **_line_view("sin(datum.x)")}
    assert not _use_function(check_vegalite_rules(spec))


def test_calculate_on_a_non_sequence_field_is_allowed():
    spec = {"data": _sequence(), **_line_view("sin(datum.altro)")}
    assert not _use_function(check_vegalite_rules(spec))


@pytest.mark.parametrize("container", ["layer", "vconcat", "hconcat", "concat"])
def test_criterion_10_is_inherited_by_composition_children(container: str):
    spec = {"data": _sequence(), container: [_line_view("sin(datum.x)")]}
    errors = check_vegalite_rules(spec)
    assert _use_function(errors), (container, errors)
    assert "profondità 1" in errors[0]


def test_criterion_10_is_inherited_through_spec_and_deeper_levels():
    spec = {"data": _sequence(), "spec": {"layer": [_line_view("cos(datum.x)")]}}
    errors = check_vegalite_rules(spec)
    assert _use_function(errors), errors
    assert "profondità 2" in errors[0]


def test_sequence_declared_in_a_child_applies_to_that_subtree_only():
    child = {"data": _sequence(), **_line_view("tan(datum.x)")}
    sibling = _line_view("tan(datum.x)")  # senza sequenza ereditata: dati inline
    sibling["data"] = {"values": [{"x": 1, "y": 1}]}
    spec = {"vconcat": [child, sibling]}
    errors = [e for e in check_vegalite_rules(spec) if e.startswith(USE_FUNCTION_FORMAT)]
    assert len(errors) == 1 and "profondità 1" in errors[0]


def test_use_function_error_is_sorted_first():
    spec = {"data": {"url": "x.csv", **_sequence()}, **_line_view("sin(datum.x)")}
    errors = check_vegalite_rules(spec)
    assert len(errors) >= 2 and errors[0].startswith(USE_FUNCTION_FORMAT)
