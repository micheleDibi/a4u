"""`figure_numbering` (D4, Q2): casi della fixture condivisa con il frontend
(`tests/fixtures/figure_numbering_cases.json`, specchio di
`lib/figureNumbering.ts`) e proprietà del modulo.

- prima citazione → N crescente; citazioni ripetute → stesso N;
- id senza asset → nessun numero consumato;
- `FIG` case-sensitive (`[fig:x]` ignorato), id case-insensitive;
- asset non citati accodati dopo il testo in ordine di array (A12) e
  numerati dopo le citate;
- `strip_figure_prefix` con cifra obbligatoria, solo a render.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import figure_numbering as fn

_FIXTURE = Path(__file__).parent / "fixtures" / "figure_numbering_cases.json"
_DATA = json.loads(_FIXTURE.read_text(encoding="utf-8"))
_CASES = _DATA["cases"]
_STRIP = _DATA["strip_prefix"]


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_fixture_case(case: dict) -> None:
    appended = fn.append_uncited_figure_refs(case["markdown"], case["asset_ids"])
    assert appended == case["appended"]
    assert fn.compute_figure_numbers(appended, case["asset_ids"]) == case["numbers"]


@pytest.mark.parametrize("pair", _STRIP, ids=[p["input"] or "vuoto" for p in _STRIP])
def test_strip_prefix_fixture(pair: dict) -> None:
    assert fn.strip_figure_prefix(pair["input"]) == pair["output"]


def test_fixture_covers_the_required_scenarios() -> None:
    """La fixture deve contenere i casi richiesti dal piano (duplicati, id
    mancante, case diverso, `[fig:x]` ignorato, non citati in coda, nessuna
    figura): guardia contro una fixture svuotata."""
    names = " ".join(c["name"] for c in _CASES)
    for needle in (
        "ripetute",
        "senza asset",
        "case diverso",
        "[fig:x]",
        "non citati",
        "nessuna figura",
    ):
        assert needle in names, needle


def test_fig_ref_re_is_case_sensitive_on_fig_and_stops_at_newline() -> None:
    assert fn.FIG_REF_RE.pattern == r"\[FIG:([^\]\n]+)\]"
    assert fn.FIG_REF_RE.search("[FIG:a1]") is not None
    assert fn.FIG_REF_RE.search("[fig:a1]") is None
    assert fn.FIG_REF_RE.search("[Fig:a1]") is None
    assert fn.FIG_REF_RE.search("[FIG:a\n1]") is None
    assert fn.FIG_REF_RE.search("[FIG:]") is None


def test_numbers_are_bound_to_ids_not_occurrences() -> None:
    md = "[FIG:A] [FIG:B] [FIG:A] [FIG:C] [FIG:B]"
    numbers = fn.compute_figure_numbers(md, ["A", "B", "C"])
    assert numbers == {"a": 1, "b": 2, "c": 3}
    assert list(numbers) == ["a", "b", "c"]  # ordine di prima citazione


def test_missing_ids_never_shift_the_numbers() -> None:
    md = "[FIG:x] [FIG:y] [FIG:A] [FIG:z] [FIG:B]"
    assert fn.compute_figure_numbers(md, ["A", "B"]) == {"a": 1, "b": 2}


def test_append_is_idempotent_and_preserves_declared_case() -> None:
    once = fn.append_uncited_figure_refs("Testo.", ["Fig_A", "fig_b"])
    assert once == "Testo.\n\n[FIG:Fig_A]\n\n[FIG:fig_b]"
    assert fn.append_uncited_figure_refs(once, ["Fig_A", "fig_b"]) == once


def test_uncited_are_numbered_after_cited_when_computed_on_appended_text() -> None:
    md = "Solo [FIG:C]."
    ids = ["A", "B", "C"]
    appended = fn.append_uncited_figure_refs(md, ids)
    assert fn.compute_figure_numbers(appended, ids) == {"c": 1, "a": 2, "b": 3}
    # Sul testo NON appeso gli orfani non hanno numero: la coda deve essere
    # calcolata prima della numerazione.
    assert fn.compute_figure_numbers(md, ids) == {"c": 1}


def test_cited_figure_ids_normalizes_and_deduplicates() -> None:
    assert fn.cited_figure_ids("[FIG: A ] [FIG:a] [FIG:B]") == ["a", "b"]
    assert fn.cited_figure_ids("") == []


@pytest.mark.parametrize(
    ("caption", "expected"),
    [
        ("Figura 7. Schema", "Schema"),
        ("FIGURA 7. Schema", "Schema"),
        ("Figure 10— Long dash", "Long dash"),
        ("Fig.3:Compatto", "Compatto"),
        ("Figura 7. Figura 8. Doppio", "Figura 8. Doppio"),  # una sola rimozione
        ("Figura", "Figura"),
        ("Figura X. Non numerata", "Figura X. Non numerata"),
        ("Figurine 3 pezzi", "Figurine 3 pezzi"),
    ],
)
def test_strip_prefix_edge_cases(caption: str, expected: str) -> None:
    assert fn.strip_figure_prefix(caption) == expected


def test_strip_prefix_handles_none_like_input() -> None:
    assert fn.strip_figure_prefix("") == ""
    assert fn.strip_figure_prefix(None) == ""  # type: ignore[arg-type]
