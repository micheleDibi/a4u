"""Direzione delle catene lineari Mermaid (D15): la funzione pura.

Tre livelli:
1. la fixture condivisa `chain_layout_cases.json`, che vale anche per il
   mirror TypeScript (`test_frontend_figure_layout` la esegue con Node);
2. l'invariante di forma: la variante è byte per byte il sorgente con il
   SOLO token di direzione cambiato;
3. gli oracoli reali del docente, con i pt PRIMA e DOPO misurati sul box
   della dispensa (`test_lesson_pdf_chain_direction` li rende davvero).

Test puro: nessun DB, nessuna rete, nessun Chromium.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.figure_compute.chain_layout import (
    MIN_CHAIN_NODES,
    VERTICAL_OF,
    is_linear_chain,
    vertical_chain_variant,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "chain_layout_cases.json"


def _cases() -> list[dict[str, Any]]:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert data["min_chain_nodes"] == MIN_CHAIN_NODES
    assert data["vertical_of"] == VERTICAL_OF
    return list(data["cases"])


_CASES = _cases()


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_fixture_cases(case: dict[str, Any]) -> None:
    assert vertical_chain_variant(case["source"]) == case["variant"], case["name"]


@pytest.mark.parametrize(
    "case", [c for c in _CASES if c["variant"]], ids=[c["name"] for c in _CASES if c["variant"]]
)
def test_only_the_direction_token_changes(case: dict[str, Any]) -> None:
    """Sorgente e variante differiscono in UN solo tratto, lungo due
    caratteri, e quel tratto è la direzione: nessun altro byte si muove."""
    source: str = case["source"]
    variant: str = case["variant"]
    assert len(source) == len(variant)
    diff = [i for i, (a, b) in enumerate(zip(source, variant, strict=True)) if a != b]
    assert diff, case["name"]
    start, end = diff[0], diff[-1] + 1
    assert end - start == 2, (case["name"], diff)
    assert VERTICAL_OF[source[start:end]] == variant[start:end]
    assert source[:start] == variant[:start] and source[end:] == variant[end:]


def test_the_variant_is_idempotent_and_never_flips_back() -> None:
    """La variante non è a sua volta una catena orizzontale: applicarla
    due volte non cambia nulla."""
    source = "flowchart LR\n  A --> B\n  B --> C\n"
    variant = vertical_chain_variant(source)
    assert variant == "flowchart TB\n  A --> B\n  B --> C\n"
    assert vertical_chain_variant(variant) is None


def test_is_linear_chain_on_the_degrees() -> None:
    """Il predicato è esposto perché è l'invariante del riconoscimento:
    n-1 archi distinti, gradi ≤ 1, un solo componente, almeno tre nodi."""
    assert is_linear_chain(["a", "b", "c"], [("a", "b"), ("b", "c")])
    assert not is_linear_chain(["a", "b"], [("a", "b")])  # meno di tre nodi
    assert not is_linear_chain(["a", "b", "c"], [("a", "b"), ("a", "c")])  # diramazione
    assert not is_linear_chain(["a", "b", "c"], [("a", "b"), ("a", "b")])  # arco doppio
    assert is_linear_chain(["a", "b", "c", "d"], [("a", "b"), ("b", "c"), ("c", "d")])
    # Quattro nodi, tre archi, gradi a posto, ma due componenti: `d → a`
    # chiude un triangolo e `c` resta da solo.
    assert not is_linear_chain(["a", "b", "c", "d"], [("a", "b"), ("b", "d"), ("d", "a")])


def test_the_real_chains_of_the_teacher_are_recognised() -> None:
    """Gli oracoli del docente: 8 e 12 nodi, entrambi riconosciuti."""
    named = {c["name"]: c for c in _CASES}
    for name in ("catena reale di 8 nodi (docente)", "catena reale di 12 nodi (docente)"):
        case = named[name]
        assert case["variant"] is not None
        assert vertical_chain_variant(case["source"]) == case["variant"]
    branched = named["fig_market_structure (7 nodi, 9 archi, diramazioni)"]
    assert vertical_chain_variant(branched["source"]) is None
