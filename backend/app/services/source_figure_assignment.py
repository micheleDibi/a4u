"""Assegnazione globale delle figure di fonte ai fabbisogni del corso (WP6, doc 18 §23.4).

Modulo puro: riceve lezioni, fabbisogni, figure e archi dell'abbinamento
(`figure_need_matching`) e restituisce una figura per fabbisogno, o il
motivo per cui il fabbisogno resta scoperto. Nessun accesso a DB o Settings.

Vincoli:
- una figura al più una volta per lezione, un fabbisogno al più una figura;
- tetto di riuso K (`FIGURE_SOURCE_MAX_LESSONS_PER_FIGURE`): una figura sta
  in al più K lezioni, contando gli usi FISSI (lezioni che non si
  rigenerano) e le assegnazioni di questo giro. Una figura già collocata
  nella lezione (`own`) resta sua anche oltre K (U1), ma conta per le altre;
- budget (b) del piano per lezione; uno should non prende il posto che
  serve a un must ancora da coprire della stessa lezione.

Greedy deterministico sugli archi ordinati per chiave:
`(bassa risoluzione, −livello, letteratura, −must, −in sequenza,
−classe di risoluzione, −già nella lezione, n. candidate del fabbisogno,
−punteggio, lezione, fabbisogno, figura)`: tutti gli archi a bassa
risoluzione dopo gli altri (una `low` solo senza alternativa migliore);
fra gli altri prima la specificità, poi il documento del corso prima della
letteratura aperta, poi i must.

Prima del greedy la letteratura riservata: una figura trovata PER un
fabbisogno (`found_for`) gli va se lo copre e nessuna figura di documento
lo copre con livello uguale o maggiore. Dopo il greedy una riparazione a
scambio singolo per i must rimasti scoperti per il tetto di riuso: chi
tiene la figura passa a un'alternativa libera di livello e classe non
inferiori. Infine una seconda passata, con di nuovo liberi il posto dei
must senza figura assegnabile e le figure già nella lezione che la lezione
non ha tenuto.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

Key = tuple[uuid.UUID, str]

_RES_RANK = {"good": 2, "acceptable": 1, None: 1, "low": 0}


@dataclass(frozen=True)
class LessonSlot:
    lesson_id: uuid.UUID
    budget: int
    own: frozenset[uuid.UUID] = frozenset()


@dataclass(frozen=True)
class Demand:
    lesson_id: uuid.UUID
    need_id: str
    must: bool
    in_sequence: bool = False

    @property
    def key(self) -> Key:
        return (self.lesson_id, self.need_id)


@dataclass(frozen=True)
class Supply:
    figure_id: uuid.UUID
    literature: bool = False
    resolution: str | None = None
    fixed_uses: int = 0
    found_for: Key | None = None

    @property
    def low(self) -> bool:
        return self.resolution == "low"

    @property
    def res_rank(self) -> int:
        return _RES_RANK.get(self.resolution, 1)


@dataclass(frozen=True)
class Arc:
    lesson_id: uuid.UUID
    need_id: str
    figure_id: uuid.UUID
    tier: int
    score: float = 0.0
    relation: str = "exact"

    @property
    def key(self) -> Key:
        return (self.lesson_id, self.need_id)


@dataclass
class Assignment:
    chosen: dict[Key, Arc] = field(default_factory=dict)
    unassigned: dict[Key, str] = field(default_factory=dict)
    reserved: set[Key] = field(default_factory=set)
    repaired: set[Key] = field(default_factory=set)

    def figures_of(self, lesson_id: uuid.UUID) -> dict[str, uuid.UUID]:
        return {k[1]: a.figure_id for k, a in self.chosen.items() if k[0] == lesson_id}


class _State:
    def __init__(
        self,
        lessons: dict[uuid.UUID, LessonSlot],
        demands: dict[Key, Demand],
        supplies: dict[uuid.UUID, Supply],
        arcs_by_need: dict[Key, list[Arc]],
        cap: int,
    ) -> None:
        self.lessons = lessons
        self.demands = demands
        self.supplies = supplies
        self.arcs_by_need = arcs_by_need
        self.cap = max(1, cap)
        self.out = Assignment()
        self.holders: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        self.in_lesson: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        self.count: dict[uuid.UUID, int] = defaultdict(int)
        self.musts_open: dict[uuid.UUID, int] = defaultdict(int)
        # Posto tenuto per i must ancora coperibili (spento nella seconda
        # passata) ed eccezione U1 limitata alle figure già tenute.
        self.reserve_musts = True
        self.own_frozen = False
        for key, demand in demands.items():
            if demand.must and arcs_by_need.get(key):
                self.musts_open[demand.lesson_id] += 1
        # Lezioni che possono tenere una figura già loro: il posto resta
        # prenotato finché non la prendono (o fino alla seconda passata).
        self.keepers: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        for key, group in arcs_by_need.items():
            for arc in group:
                if arc.figure_id in lessons[key[0]].own:
                    self.keepers[arc.figure_id].add(key[0])

    def own(self, lesson_id: uuid.UUID, figure_id: uuid.UUID) -> bool:
        slot = self.lessons.get(lesson_id)
        if slot is None or figure_id not in slot.own:
            return False
        # Dopo la passata delle figure già nella lezione, l'eccezione al tetto
        # vale solo per quelle che la lezione ha tenuto: una figura lasciata
        # conta come le altre (mai K superato da chi non la aveva già).
        return not self.own_frozen or figure_id in self.in_lesson[lesson_id]

    def capacity_ok(self, arc: Arc) -> bool:
        if self.own(arc.lesson_id, arc.figure_id):
            return True
        figure = arc.figure_id
        supply = self.supplies[figure]
        waiting = self.keepers[figure] - self.holders[figure]
        return supply.fixed_uses + len(self.holders[figure]) + len(waiting) < self.cap

    def budget_ok(self, arc: Arc) -> bool:
        slot = self.lessons[arc.lesson_id]
        used = self.count[arc.lesson_id]
        if self.demands[arc.key].must:
            return used < slot.budget
        reserved = self.musts_open[arc.lesson_id] if self.reserve_musts else 0
        return used + max(0, reserved) < slot.budget

    def blocked_by(self, arc: Arc) -> str | None:
        if arc.key in self.out.chosen:
            return "taken"
        if arc.figure_id in self.in_lesson[arc.lesson_id]:
            return "duplicate_in_lesson"
        if not self.capacity_ok(arc):
            return "reuse_cap"
        if not self.budget_ok(arc):
            return "budget"
        return None

    def take(self, arc: Arc) -> None:
        self.out.chosen[arc.key] = arc
        self.holders[arc.figure_id].add(arc.lesson_id)
        self.in_lesson[arc.lesson_id].add(arc.figure_id)
        self.count[arc.lesson_id] += 1
        if self.demands[arc.key].must:
            self.musts_open[arc.lesson_id] -= 1

    def release(self, arc: Arc) -> None:
        del self.out.chosen[arc.key]
        self.holders[arc.figure_id].discard(arc.lesson_id)
        self.in_lesson[arc.lesson_id].discard(arc.figure_id)
        self.count[arc.lesson_id] -= 1
        if self.demands[arc.key].must:
            self.musts_open[arc.lesson_id] += 1


def _sort_key(state: _State, arc: Arc) -> tuple[object, ...]:
    demand = state.demands[arc.key]
    supply = state.supplies[arc.figure_id]
    return (
        supply.low,
        -arc.tier,
        1 if supply.literature else 0,
        -int(demand.must),
        -int(demand.in_sequence),
        -supply.res_rank,
        -int(state.own(arc.lesson_id, arc.figure_id)),
        len(state.arcs_by_need.get(arc.key, ())),
        -arc.score,
        str(arc.lesson_id),
        arc.need_id,
        str(arc.figure_id),
    )


def assign(
    lessons: Iterable[LessonSlot],
    demands: Iterable[Demand],
    supplies: Iterable[Supply],
    arcs: Iterable[Arc],
    *,
    cap: int,
    release: frozenset[uuid.UUID] | None = None,
) -> Assignment:
    """Assegnazione deterministica (greedy + riparazione). `release`: lezioni
    il cui contenuto sta per essere sostituito; nella seconda passata solo le
    loro figure non tenute tornano libere (None = tutte le lezioni)."""
    lesson_map = {s.lesson_id: s for s in lessons}
    demand_map = {d.key: d for d in demands if d.lesson_id in lesson_map}
    supply_map = {s.figure_id: s for s in supplies}
    arcs_by_need: dict[Key, list[Arc]] = defaultdict(list)
    for arc in arcs:
        if arc.key in demand_map and arc.figure_id in supply_map and arc.tier > 0:
            arcs_by_need[arc.key].append(arc)
    state = _State(lesson_map, demand_map, supply_map, arcs_by_need, cap)
    ordered = sorted(
        (a for group in arcs_by_need.values() for a in group), key=lambda a: _sort_key(state, a)
    )

    _reserved_literature(state)
    for arc in ordered:
        if state.blocked_by(arc) is None:
            state.take(arc)
    _repair(state)
    # Seconda passata: i must rimasti scoperti non hanno più una figura
    # assegnabile e il posto che tenevano torna agli should. Prima le figure
    # già nella lezione (col loro posto ancora tenuto); poi quelle che la
    # lezione non ha tenuto tornano libere e senza eccezione al tetto.
    state.reserve_musts = False
    for arc in ordered:
        if state.own(arc.lesson_id, arc.figure_id) and state.blocked_by(arc) is None:
            state.take(arc)
    state.own_frozen = True
    if release is None:
        state.keepers.clear()
    else:
        for figure, keepers in state.keepers.items():
            state.keepers[figure] = keepers - release
    for arc in ordered:
        if state.blocked_by(arc) is None:
            state.take(arc)

    for key in demand_map:
        if key in state.out.chosen:
            continue
        options = arcs_by_need.get(key, [])
        if not options:
            state.out.unassigned[key] = "no_candidate"
            continue
        reasons = {state.blocked_by(a) for a in options}
        for reason in ("reuse_cap", "budget", "duplicate_in_lesson"):
            if reason in reasons:
                state.out.unassigned[key] = reason
                break
        else:
            state.out.unassigned[key] = "no_candidate"
    return state.out


def _reserved_literature(state: _State) -> None:
    """Per ogni fabbisogno con figure della letteratura trovate PER lui, la
    migliore (stessa chiave del greedy) se lo copre e nessuna figura di
    documento lo copre con livello uguale o maggiore."""
    wanted = {
        s.found_for
        for s in state.supplies.values()
        if s.literature and s.found_for in state.demands
    }
    for key in sorted(wanted, key=lambda k: (str(k[0]), k[1])):
        options = state.arcs_by_need.get(key, [])
        found = sorted(
            (a for a in options if state.supplies[a.figure_id].found_for == key),
            key=lambda a: _sort_key(state, a),
        )
        if not found:
            continue  # le figure trovate non coprono il fabbisogno: nessuna riserva
        best_document = max(
            (a.tier for a in options if not state.supplies[a.figure_id].literature), default=0
        )
        for arc in found:
            if best_document >= arc.tier:
                break
            if state.blocked_by(arc) is None:
                state.take(arc)
                state.out.reserved.add(key)
                break


def _repair(state: _State) -> None:
    """Scambio singolo per i must scoperti per il tetto di riuso."""
    open_musts = sorted(
        (k for k, d in state.demands.items() if d.must and k not in state.out.chosen),
        key=lambda k: (str(k[0]), k[1]),
    )
    for key in open_musts:
        for arc in sorted(state.arcs_by_need.get(key, []), key=lambda a: _sort_key(state, a)):
            if state.blocked_by(arc) != "reuse_cap":
                continue
            if _swap_for(state, arc):
                state.out.repaired.add(key)
                break


def _swap_for(state: _State, arc: Arc) -> bool:
    figure = arc.figure_id
    holders = [
        held
        for held in state.out.chosen.values()
        if held.figure_id == figure
        and not state.own(held.lesson_id, figure)
        and held.key not in state.out.reserved
    ]
    for held in sorted(holders, key=lambda a: (str(a.lesson_id), a.need_id)):
        current = state.supplies[figure]
        for alt in sorted(state.arcs_by_need.get(held.key, []), key=lambda a: _sort_key(state, a)):
            if alt.figure_id == figure or alt.tier < held.tier:
                continue
            candidate = state.supplies[alt.figure_id]
            if candidate.res_rank < current.res_rank or (candidate.low and not current.low):
                continue
            state.release(held)
            if state.blocked_by(alt) is None:
                state.take(alt)
                if state.blocked_by(arc) is None:
                    state.take(arc)
                    return True
                state.release(alt)
            state.take(held)
    return False
