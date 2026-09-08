"""Sostituzione chirurgica delle stringhe JSON (`json_spans`, I18N-3).

Il modulo è la ragione per cui la localizzazione di una spec `vegalite` o
`function` non riserializza più il sorgente: qui si verifica che i percorsi
coincidano con quelli degli estrattori, che la formattazione sopravviva,
che una sostituzione identica non tocchi nemmeno un byte e che i sorgenti
non scandibili degradino a `None` invece di produrre JSON rotto.
"""

from __future__ import annotations

import json

import pytest

from app.services.json_spans import replace_strings, string_spans

_SRC = """{
  "title": "Esempio",
  "data": {"values": [{"k": "a"}, {"k": "b"}]},
  "encoding": {
    "x": {"axis": {"title": "categoria"}}
  },
  "flag": true,
  "n": -1.5e3
}"""


def test_string_spans_paths_match_the_extractors():
    spans = string_spans(_SRC)
    assert spans is not None
    assert set(spans) == {
        "title",
        "data.values.0.k",
        "data.values.1.k",
        "encoding.x.axis.title",
    }
    start, end = spans["encoding.x.axis.title"]
    assert _SRC[start:end] == '"categoria"'


def test_replace_keeps_everything_else_byte_for_byte():
    out = replace_strings(_SRC, {"title": "Example", "encoding.x.axis.title": "class"})
    assert out is not None
    assert out == _SRC.replace('"Esempio"', '"Example"').replace('"categoria"', '"class"')
    assert out.count("\n") == _SRC.count("\n")
    assert json.loads(out)["data"] == json.loads(_SRC)["data"]


def test_identical_values_leave_the_source_untouched():
    same = {"title": "Esempio", "encoding.x.axis.title": "categoria"}
    assert replace_strings(_SRC, same) == _SRC
    assert replace_strings(_SRC, {}) == _SRC


def test_a_path_that_is_not_a_string_of_the_document_degrades_to_none():
    """Il chiamante ha già applicato la traduzione alla struttura: se la
    sostituzione chirurgica non sa dove metterla deve dirlo, non perderla
    in silenzio."""
    assert replace_strings(_SRC, {"non.esiste": "x"}) is None
    assert replace_strings(_SRC, {"flag": "x"}) is None  # valore non stringa
    assert replace_strings(_SRC, {"data": "x"}) is None  # oggetto, non stringa


def test_escapes_and_non_ascii_survive_the_round_trip():
    src = '{"a": "riga\\nuno", "b": "vir\\"golette", "c": "caff\\u00e8"}'
    spans = string_spans(src)
    assert spans is not None and set(spans) == {"a", "b", "c"}
    assert replace_strings(src, {k: json.loads(src)[k] for k in "abc"}) == src
    out = replace_strings(src, {"b": 'con "apici" e \\ barra'})
    assert out is not None and json.loads(out)["b"] == 'con "apici" e \\ barra'
    assert json.loads(out)["a"] == "riga\nuno"


@pytest.mark.parametrize(
    "src",
    [
        "non json",
        '{"a": "x"',
        '{"a": }',
        '{"a": "x"} coda',
        '{a: "x"}',
        "[" * 300 + "]" * 300,  # oltre il tetto di annidamento dello scanner
    ],
)
def test_unscannable_sources_degrade_to_none(src: str):
    assert string_spans(src) is None
    assert replace_strings(src, {"a": "y"}) is None


def test_arrays_and_root_scalars():
    spans = string_spans('["a", ["b"], {"k": "c"}]')
    assert spans is not None
    assert set(spans) == {"0", "1.0", "2.k"}
    assert string_spans('"solo una stringa"') == {"": (0, 18)}
    assert string_spans("42") == {}
    assert string_spans("{}") == {}
    assert string_spans("[]") == {}


def test_duplicate_keys_follow_json_loads_last_wins():
    src = '{"a": "primo", "a": "secondo"}'
    spans = string_spans(src)
    assert spans is not None
    start, end = spans["a"]
    assert src[start:end] == '"secondo"'
    out = replace_strings(src, {"a": "terzo"})
    assert out == '{"a": "primo", "a": "terzo"}'
    assert json.loads(out)["a"] == "terzo"
