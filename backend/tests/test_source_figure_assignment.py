"""Assegnazione globale delle figure di fonte (WP6, doc 18 §23.4): modulo puro.

Tetto di riuso K e unicità nella lezione, figure già nella lezione (anche
oltre K), bassa risoluzione dopo le altre, documento prima della
letteratura, precedenza alla specificità, posto dei must, letteratura
riservata, riparazione a scambio singolo, determinismo, confronto con
l'ottimo esaustivo su istanze piccole.
"""

from __future__ import annotations

import itertools
import random
import uuid

import pytest

from app.services import source_figure_assignment as sa

L1, L2, L3 = (uuid.UUID(int=i) for i in (1, 2, 3))
F = [uuid.UUID(int=100 + i) for i in range(10)]


def _slots(*lessons: uuid.UUID, budget: int = 8, own: dict | None = None) -> list[sa.LessonSlot]:
    own = own or {}
    return [sa.LessonSlot(lid, budget, frozenset(own.get(lid, ()))) for lid in lessons]


def _supplies(**over: dict) -> list[sa.Supply]:
    return [sa.Supply(f, **over.get(str(i), {})) for i, f in enumerate(F)]


def test_reuse_cap_and_uniqueness_in_the_lesson() -> None:
    demands = [sa.Demand(lid, "n1", True) for lid in (L1, L2, L3)]
    demands.append(sa.Demand(L1, "n2", True))
    arcs = [sa.Arc(lid, "n1", F[0], 4) for lid in (L1, L2, L3)]
    arcs.append(sa.Arc(L1, "n2", F[0], 4))
    out = sa.assign(_slots(L1, L2, L3), demands, _supplies(), arcs, cap=2)
    holders = [k for k, a in out.chosen.items() if a.figure_id == F[0]]
    assert len({k[0] for k in holders}) == 2 and len(holders) == 2
    assert set(out.unassigned.values()) <= {"reuse_cap", "duplicate_in_lesson"}
    assert len(out.unassigned) == 2


def test_a_figure_already_in_the_lesson_stays_even_over_the_cap() -> None:
    supplies = _supplies(**{"0": {"fixed_uses": 4}})
    demands = [sa.Demand(L1, "n1", True), sa.Demand(L2, "n1", True)]
    arcs = [sa.Arc(L1, "n1", F[0], 3), sa.Arc(L2, "n1", F[0], 4)]
    out = sa.assign(_slots(L1, L2, own={L1: [F[0]]}), demands, supplies, arcs, cap=2)
    assert out.chosen[(L1, "n1")].figure_id == F[0]
    assert out.unassigned[(L2, "n1")] == "reuse_cap"


def test_an_own_figure_counts_for_the_other_lessons() -> None:
    demands = [sa.Demand(L1, "n1", True), sa.Demand(L2, "n1", True)]
    arcs = [sa.Arc(L1, "n1", F[0], 3), sa.Arc(L2, "n1", F[0], 4)]
    out = sa.assign(_slots(L1, L2, own={L1: [F[0]]}), demands, _supplies(), arcs, cap=1)
    assert out.chosen[(L1, "n1")].figure_id == F[0]
    assert (L2, "n1") not in out.chosen


def test_low_resolution_only_without_a_better_alternative() -> None:
    supplies = _supplies(**{"0": {"resolution": "low"}, "1": {"resolution": "acceptable"}})
    out = sa.assign(
        _slots(L1),
        [sa.Demand(L1, "n1", True)],
        supplies,
        [sa.Arc(L1, "n1", F[0], 4), sa.Arc(L1, "n1", F[1], 2)],
        cap=2,
    )
    assert out.chosen[(L1, "n1")].figure_id == F[1]
    only_low = sa.assign(
        _slots(L1), [sa.Demand(L1, "n1", True)], supplies, [sa.Arc(L1, "n1", F[0], 4)], cap=2
    )
    assert only_low.chosen[(L1, "n1")].figure_id == F[0]


def test_documents_come_before_literature_at_the_same_tier() -> None:
    supplies = _supplies(**{"0": {"literature": True}})
    arcs = [sa.Arc(L1, "n1", F[0], 4, score=9.0), sa.Arc(L1, "n1", F[1], 4, score=1.0)]
    out = sa.assign(_slots(L1), [sa.Demand(L1, "n1", True)], supplies, arcs, cap=2)
    assert out.chosen[(L1, "n1")].figure_id == F[1]


def test_specificity_wins_across_lessons() -> None:
    """Regola del committente: il fabbisogno coperto in modo più specifico
    prende la figura contesa, anche se è uno should."""
    demands = [sa.Demand(L1, "n1", False), sa.Demand(L2, "n1", True)]
    arcs = [sa.Arc(L1, "n1", F[0], 4), sa.Arc(L2, "n1", F[0], 2)]
    out = sa.assign(_slots(L1, L2), demands, _supplies(), arcs, cap=1)
    assert out.chosen[(L1, "n1")].figure_id == F[0]
    assert out.unassigned[(L2, "n1")] == "reuse_cap"


def test_shoulds_do_not_take_the_place_of_a_must() -> None:
    demands = [sa.Demand(L1, "must", True), sa.Demand(L1, "should", False)]
    arcs = [sa.Arc(L1, "should", F[0], 4), sa.Arc(L1, "must", F[1], 2)]
    out = sa.assign(_slots(L1, budget=1), demands, _supplies(), arcs, cap=2)
    assert out.chosen[(L1, "must")].figure_id == F[1]
    assert out.unassigned[(L1, "should")] == "budget"


def test_a_must_without_figures_does_not_block_the_shoulds() -> None:
    demands = [sa.Demand(L1, "must", True), sa.Demand(L1, "should", False)]
    supplies = _supplies(**{"0": {"fixed_uses": 2}})
    arcs = [sa.Arc(L1, "must", F[0], 4), sa.Arc(L1, "should", F[1], 4)]
    out = sa.assign(_slots(L1, budget=1), demands, supplies, arcs, cap=2)
    assert out.unassigned[(L1, "must")] == "reuse_cap"
    assert out.chosen[(L1, "should")].figure_id == F[1]


def test_literature_found_for_a_need_is_reserved_unless_a_document_covers_it() -> None:
    supplies = _supplies(**{"0": {"literature": True, "found_for": (L1, "n1")}})
    demands = [sa.Demand(L1, "n1", True), sa.Demand(L2, "n1", True)]
    arcs = [
        sa.Arc(L1, "n1", F[0], 3),
        sa.Arc(L1, "n1", F[1], 2),
        sa.Arc(L2, "n1", F[0], 4),
    ]
    out = sa.assign(_slots(L1, L2), demands, supplies, arcs, cap=1)
    assert out.chosen[(L1, "n1")].figure_id == F[0] and (L1, "n1") in out.reserved
    covered = [*arcs[:1], sa.Arc(L1, "n1", F[1], 3), arcs[2]]
    out = sa.assign(_slots(L1, L2), demands, supplies, covered, cap=1)
    assert out.chosen[(L1, "n1")].figure_id == F[1] and not out.reserved


def test_repair_swaps_to_an_equivalent_alternative() -> None:
    demands = [sa.Demand(L1, "a", True), sa.Demand(L2, "b", True)]
    arcs = [
        sa.Arc(L2, "b", F[0], 4),
        sa.Arc(L2, "b", F[1], 4),
        sa.Arc(L1, "a", F[0], 3),
    ]
    out = sa.assign(_slots(L1, L2), demands, _supplies(), arcs, cap=1)
    assert out.chosen[(L1, "a")].figure_id == F[0]
    assert out.chosen[(L2, "b")].figure_id == F[1]


def test_repair_never_lowers_the_tier() -> None:
    demands = [sa.Demand(L1, "a", True), sa.Demand(L2, "b", True)]
    arcs = [
        sa.Arc(L2, "b", F[0], 4),
        sa.Arc(L2, "b", F[1], 2),
        sa.Arc(L1, "a", F[0], 3),
    ]
    out = sa.assign(_slots(L1, L2), demands, _supplies(), arcs, cap=1)
    assert out.chosen[(L2, "b")].figure_id == F[0]
    assert out.unassigned[(L1, "a")] == "reuse_cap"


def _random_instance(seed: int) -> tuple[list, list, list, list]:
    rng = random.Random(seed)
    lessons = [uuid.UUID(int=1 + i) for i in range(rng.randint(1, 3))]
    demands = [
        sa.Demand(rng.choice(lessons), f"n{i}", rng.random() < 0.7)
        for i in range(rng.randint(2, 6))
    ]
    demands = list({d.key: d for d in demands}.values())
    figures = F[: rng.randint(1, 4)]
    supplies = [
        sa.Supply(f, fixed_uses=rng.choice((0, 0, 1)), resolution=rng.choice((None, "good", "low")))
        for f in figures
    ]
    arcs = [
        sa.Arc(d.lesson_id, d.need_id, f, rng.randint(1, 4))
        for d in demands
        for f in figures
        if rng.random() < 0.5
    ]
    slots = [sa.LessonSlot(lid, rng.randint(1, 3)) for lid in lessons]
    return slots, demands, supplies, arcs


def _feasible(slots, demands, supplies, pick, cap) -> bool:
    budget = {s.lesson_id: s.budget for s in slots}
    fixed = {s.figure_id: s.fixed_uses for s in supplies}
    per_lesson: dict = {}
    holders: dict = {}
    for demand, arc in zip(demands, pick, strict=True):
        if arc is None:
            continue
        per_lesson.setdefault(demand.lesson_id, []).append(arc.figure_id)
        holders.setdefault(arc.figure_id, set()).add(demand.lesson_id)
    for lid, figs in per_lesson.items():
        if len(figs) != len(set(figs)) or len(figs) > budget[lid]:
            return False
    return all(fixed[f] + len(ls) <= cap for f, ls in holders.items())


@pytest.mark.parametrize("seed", range(60))
@pytest.mark.parametrize("cap", [1, 2, 3])
def test_greedy_is_close_to_the_exhaustive_optimum(seed: int, cap: int) -> None:
    slots, demands, supplies, arcs = _random_instance(seed)
    options = [[None, *[a for a in arcs if a.key == d.key]] for d in demands]
    best = 0
    for pick in itertools.product(*options):
        if _feasible(slots, demands, supplies, pick, cap):
            musts = sum(1 for d, a in zip(demands, pick, strict=True) if a and d.must)
            best = max(best, musts)
    out = sa.assign(slots, demands, supplies, arcs, cap=cap)
    got = sum(1 for d in demands if d.must and d.key in out.chosen)
    assert got >= best - 1
    picks = [out.chosen.get(d.key) for d in demands]
    assert _feasible(slots, demands, supplies, picks, cap)


def test_the_result_does_not_depend_on_the_input_order() -> None:
    slots, demands, supplies, arcs = _random_instance(7)
    first = sa.assign(slots, demands, supplies, arcs, cap=1)
    rng = random.Random(3)
    for _ in range(5):
        rng.shuffle(arcs)
        rng.shuffle(demands)
        again = sa.assign(slots, demands, supplies, arcs, cap=1)
        assert again.chosen == first.chosen and again.unassigned == first.unassigned


def _instance_with_own(seed: int) -> tuple[list, list, list, list, int]:
    rng = random.Random(seed)
    lessons = [uuid.UUID(int=1 + i) for i in range(rng.randint(1, 3))]
    figures = F[: rng.randint(1, 4)]
    slots = [
        sa.LessonSlot(lid, rng.randint(1, 3), frozenset(f for f in figures if rng.random() < 0.3))
        for lid in lessons
    ]
    demands = [
        sa.Demand(rng.choice(lessons), f"n{i}", rng.random() < 0.6)
        for i in range(rng.randint(2, 7))
    ]
    demands = list({d.key: d for d in demands}.values())
    supplies = [
        sa.Supply(
            f,
            fixed_uses=rng.choice((0, 0, 1, 2)),
            resolution=rng.choice((None, "low")),
            literature=rng.random() < 0.3,
            found_for=(rng.choice(demands).key if rng.random() < 0.2 else None),
        )
        for f in figures
    ]
    arcs = [
        sa.Arc(d.lesson_id, d.need_id, f, rng.randint(1, 4))
        for d in demands
        for f in figures
        if rng.random() < 0.6
    ]
    return slots, demands, supplies, arcs, rng.randint(1, 3)


@pytest.mark.parametrize("seed", range(3000))
def test_invariants_hold_with_figures_already_in_the_lessons(seed: int) -> None:
    """Budget per lezione, una figura per lezione e il tetto K: oltre K solo
    se TUTTI i detentori oltre gli usi fissi avevano già la figura (U1)."""
    slots, demands, supplies, arcs, cap = _instance_with_own(seed)
    out = sa.assign(slots, demands, supplies, arcs, cap=cap)
    by_lesson: dict[uuid.UUID, list[uuid.UUID]] = {}
    holders: dict[uuid.UUID, set[uuid.UUID]] = {}
    for key, arc in out.chosen.items():
        by_lesson.setdefault(key[0], []).append(arc.figure_id)
        holders.setdefault(arc.figure_id, set()).add(key[0])
    budget = {s.lesson_id: s.budget for s in slots}
    own = {s.lesson_id: s.own for s in slots}
    fixed = {s.figure_id: s.fixed_uses for s in supplies}
    for lesson, figs in by_lesson.items():
        assert len(figs) == len(set(figs))
        assert len(figs) <= budget[lesson]
    for fig, lessons in holders.items():
        non_own = {lesson for lesson in lessons if fig not in own[lesson]}
        # Chi non aveva la figura non la prende mai oltre K.
        assert fixed[fig] + len(lessons) <= cap or not non_own


def test_the_best_found_figure_is_reserved() -> None:
    supplies = _supplies(
        **{
            "0": {"literature": True, "found_for": (L1, "n1")},
            "1": {"literature": True, "found_for": (L1, "n1")},
        }
    )
    arcs = [sa.Arc(L1, "n1", F[0], 2), sa.Arc(L1, "n1", F[1], 4)]
    out = sa.assign(_slots(L1), [sa.Demand(L1, "n1", True)], supplies, arcs, cap=1)
    assert out.chosen[(L1, "n1")].figure_id == F[1] and (L1, "n1") in out.reserved


def test_the_lesson_keeps_its_own_figures_first() -> None:
    """Prima passata sulle figure già nella lezione (regressione dalla
    verifica WP7-WP8: istanza reale del test di proprietà in cui, senza la
    passata, la lezione perdeva la figura 103 che aveva nel contenuto)."""
    f = {i: uuid.UUID(int=i) for i in (100, 101, 102, 103)}
    supplies = [
        sa.Supply(f[100], literature=True, resolution="low", fixed_uses=1),
        sa.Supply(f[101], fixed_uses=2),
        sa.Supply(f[102], resolution="low", fixed_uses=2),
        sa.Supply(f[103], fixed_uses=2, found_for=(L1, "n4")),
    ]
    demands = [
        sa.Demand(L1, need, must)
        for need, must in (("n0", True), ("n1", False), ("n2", True), ("n3", True), ("n4", False))
    ]
    arcs = [
        sa.Arc(L1, need, f[fid], tier)
        for need, fid, tier in (
            ("n0", 100, 1),
            ("n0", 102, 4),
            ("n1", 100, 3),
            ("n1", 102, 1),
            ("n1", 103, 2),
            ("n2", 100, 2),
            ("n2", 101, 4),
            ("n3", 100, 2),
            ("n4", 101, 2),
        )
    ]
    slots = [sa.LessonSlot(L1, 3, frozenset({f[101], f[103]}))]
    out = sa.assign(slots, demands, supplies, arcs, cap=1)
    assert {f[101], f[103]} <= {a.figure_id for a in out.chosen.values()}


def test_repair_never_moves_a_reserved_literature_figure() -> None:
    """La riparazione non scambia una figura della letteratura riservata al
    fabbisogno per cui è stata trovata, anche se lo scambio coprirebbe un
    altro fabbisogno."""
    supplies = [
        sa.Supply(F[0], literature=True, found_for=(L1, "n1")),
        sa.Supply(F[1], literature=True),
    ]
    demands = [sa.Demand(L1, "n1", True), sa.Demand(L2, "n1", True)]
    arcs = [sa.Arc(L1, "n1", F[0], 3), sa.Arc(L1, "n1", F[1], 3), sa.Arc(L2, "n1", F[0], 3)]
    out = sa.assign(_slots(L1, L2), demands, supplies, arcs, cap=1)
    assert out.chosen[(L1, "n1")].figure_id == F[0]
    assert (L1, "n1") in out.reserved
    assert out.unassigned[(L2, "n1")] == "reuse_cap"
