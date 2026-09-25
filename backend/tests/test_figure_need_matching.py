"""Abbinamento fabbisogno ↔ figura (WP6, doc 18 §23.3): modulo puro.

- tabella delle relazioni sui casi di `fixtures/figure_need_matching_cases.json`
  (vibrometri e un secondo dominio: prove modali);
- H1: una figura generica non copre mai un fabbisogno con variante;
  H3: una figura con un'altra variante non lo copre mai;
- stem leggero con uguaglianza esatta («different» ≠ «differential»);
- l'ordine degli oggetti in `depicts` e delle figure non cambia l'esito.
"""

from __future__ import annotations

import itertools
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import figure_need_matching as m

_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "figure_need_matching_cases.json").read_text()
)


def _figure(name: str) -> SimpleNamespace:
    data = _CASES["figures"][name]
    return SimpleNamespace(
        id=uuid.uuid5(uuid.NAMESPACE_URL, name),
        kind=data["kind"],
        depicts=data["depicts"],
        source_caption=data.get("caption"),
        description=data.get("description"),
        keywords=data.get("keywords") or {"en": [], "course": []},
    )


@pytest.mark.parametrize(("need", "figure", "relation"), _CASES["cases"])
def test_relation_table(need: str, figure: str, relation: str) -> None:
    got = m.match(_CASES["needs"][need], _figure(figure))
    assert got.relation == relation
    assert got.covers == (relation in m.COVERING)
    if got.covers:
        assert got.tier >= 1


def test_a_generic_or_conflicting_figure_never_covers_a_variant_need() -> None:
    variant_needs = {k: v for k, v in _CASES["needs"].items() if not v["is_base"]}
    for need, figure in itertools.product(variant_needs.values(), _CASES["figures"]):
        got = m.match(need, _figure(figure))
        if got.relation in ("generic", "conflict"):
            assert not got.covers and got.tier == 0


def test_tiers_prefer_the_exact_figure() -> None:
    need = _CASES["needs"]["scan"]
    exact = m.match(need, _figure("scan"))
    finer = m.match(need, _figure("cont_scan"))
    legacy = m.match(need, _figure("legacy_scan"))
    assert exact.tier > finer.tier > legacy.tier
    photo = m.match(_CASES["needs"]["rot"], _figure("rot_photo"))
    assert photo.relation == "exact" and photo.tier == m.TIERS["exact"] - 1


@pytest.mark.parametrize(
    ("a", "b", "equal"),
    [
        ("scanning", "scan", True),
        ("rotational", "rotation", True),
        ("vibrometers", "vibrometer", True),
        ("gauges", "gauge", True),
        ("differential", "different", False),
        ("tracking", "track", True),
        # «laser Doppler velocimeter» è anche l'anemometro per i flussi (M-A3).
        ("velocimeter", "vibrometer", False),
    ],
)
def test_light_stem_uses_exact_equality(a: str, b: str, equal: bool) -> None:
    assert (m.stem(a) == m.stem(b)) is equal


def test_item_order_does_not_matter() -> None:
    figure = _figure("two_variants")
    items = list(figure.depicts["items"])
    for need in ("scan", "diff", "rot"):
        results = set()
        for perm in itertools.permutations(items):
            figure.depicts = {**figure.depicts, "items": list(perm)}
            results.add(m.match(_CASES["needs"][need], figure).relation)
        assert len(results) == 1


def test_best_matches_is_ordered_and_deterministic() -> None:
    figures = [_figure(name) for name in _CASES["figures"]]
    need = _CASES["needs"]["scan"]
    forward = [(f.id, mt.relation) for f, mt in m.best_matches(need, figures)]
    backward = [(f.id, mt.relation) for f, mt in m.best_matches(need, list(reversed(figures)))]
    assert forward == backward
    assert forward[0][1] == "exact"
    assert all(rel in m.COVERING for _fid, rel in forward)


def test_a_base_need_without_variant_fields_is_base() -> None:
    need: dict[str, Any] = {**_CASES["needs"]["scan"], "variant_en": "", "variant_terms": []}
    need["is_base"] = False
    assert m.NeedKey.from_need(need).is_base is True


def test_the_index_finds_the_same_figures_as_the_full_scan() -> None:
    figures = [_figure(name) for name in _CASES["figures"]]
    index = m.FigureIndex(figures)
    for need in _CASES["needs"].values():
        full = [(f.id, mt) for f, mt in m.best_matches(need, figures)]
        assert index.covering(need) == full


def test_legacy_evidence_can_be_switched_off() -> None:
    need = _CASES["needs"]["scan"]
    legacy = _figure("legacy_scan")
    assert m.match(need, legacy).relation == "legacy"
    off = m.match(need, legacy, legacy=False)
    assert off.relation == "no_depicts" and not off.covers
    index = m.FigureIndex([legacy, _figure("scan")], legacy=False)
    assert [mt.relation for _fid, mt in index.covering(need)] == ["exact"]
