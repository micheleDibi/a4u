"""Assegnazione delle figure di fonte ai fabbisogni, sul DB (WP6, doc 18 §23.4).

- `course_lock`: advisory lock di TRANSAZIONE per corso, preso con tentativi
  (`pg_try_advisory_xact_lock`) fino a `LOCK_TIMEOUT_SECONDS`; mai
  un'eccezione né un'attesa bloccante: scaduto il tempo si procede senza
  (log) e il ricontrollo del riuso resta.
- `snapshot`: partecipanti (la lezione corrente e le lezioni del corso in
  coda per la Fase 3 con i fabbisogni pronti), figure proponibili del corso
  (predicato unico in modo `select`, senza il filtro del riuso per
  lezione), archi dell'abbinamento e assegnazione globale
  (`source_figure_assignment.assign`). Contano come usi FISSI le
  collocazioni delle lezioni che non partecipano; per una lezione in
  generazione con un'offerta valida (meno di `OFFER_TTL`) contano le figure
  offerte al posto del contenuto che sta per sostituire.
- `reserve`: all'avvio della Fase 3, sotto il lock, scrive in
  `course_lesson.figure_assignment` l'offerta della lezione (`state:
  offered`, `run_token`, `at`, `offers`, `alternatives`, `unassigned`,
  `budget`). Il commit lo fa il chiamante (e rilascia il lock).
- `settle`: alla materializzazione, nella stessa transazione e sotto il
  lock, scrive la fotografia finale (`state: settled`, figure collocate,
  fabbisogni legati all'offerta).

Lo stato di copertura (coperto, scoperto, collocato) non si salva mai
altrove: si calcola alla lettura da fabbisogni, offerta e contenuto.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import figure_plan_service as plan
from app.services import source_figure_assignment as sa
from app.services import source_figure_catalog as catalog
from app.services.document_figures_service import source_figure_ids
from app.services.figure_need_matching import FigureIndex

log = get_logger("app.source_figure_assignment")

ASSIGNMENT_VERSION = 1
OFFER_TTL = timedelta(hours=2)
LOCK_TIMEOUT_SECONDS = 20.0
LOCK_RETRY_SECONDS = 0.2
MAX_ALTERNATIVES = 2


def _now() -> datetime:
    return datetime.now(UTC)


def lock_key(course_id: uuid.UUID) -> int:
    digest = hashlib.sha1(b"figure_assignment:" + course_id.bytes, usedforsecurity=False)
    return int.from_bytes(digest.digest()[:8], "big", signed=True)


async def course_lock(
    db: AsyncSession, course_id: uuid.UUID, *, timeout: float = LOCK_TIMEOUT_SECONDS
) -> bool:
    """Lock di transazione del corso; False se non preso entro `timeout`."""
    key = lock_key(course_id)
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if await db.scalar(select(func.pg_try_advisory_xact_lock(key))):
            return True
        if time.monotonic() >= deadline:
            log.warning("figure_assignment_lock_timeout", course_id=str(course_id))
            return False
        await asyncio.sleep(LOCK_RETRY_SECONDS)


def plan_budget(course: Course, lesson: CourseLesson, musts: int) -> int:
    """Budget (b) con il piano: min(tetto, max(morbido, must pronti)); il
    morbido sta fra il budget senza piano e il tetto."""
    settings = get_settings()
    floor = catalog.budget_for(lesson)
    if floor <= 0:
        return 0
    ceiling = max(floor, int(settings.figure_plan_max_per_lesson))
    per = max(1, int(settings.figure_source_minutes_per_figure))
    soft = min(ceiling, max(floor, int(course.lesson_duration_minutes or 0) // per))
    return min(ceiling, max(soft, musts))


def valid_offer(lesson: CourseLesson, now: datetime | None = None) -> dict[str, Any] | None:
    data = lesson.figure_assignment if isinstance(lesson.figure_assignment, dict) else None
    if not data or data.get("state") != "offered":
        return None
    try:
        at = datetime.fromisoformat(str(data.get("at")))
    except ValueError:
        return None
    return data if (now or _now()) - at <= OFFER_TTL else None


def _offered_ids(data: dict[str, Any]) -> set[uuid.UUID]:
    out: set[uuid.UUID] = set()
    for offer in (data.get("offers") or {}).values():
        if isinstance(offer, dict) and offer.get("figure_id"):
            try:
                out.add(uuid.UUID(str(offer["figure_id"])))
            except ValueError:
                continue
    return out


@dataclass
class Snapshot:
    assignment: sa.Assignment
    needs: dict[uuid.UUID, list[dict[str, Any]]]
    arcs: dict[sa.Key, list[sa.Arc]]
    budgets: dict[uuid.UUID, int]
    participants: list[uuid.UUID]
    stats: dict[str, Any] = field(default_factory=dict)


async def _course_lessons(db: AsyncSession, course_id: uuid.UUID) -> list[CourseLesson]:
    rows = await db.execute(
        select(CourseLesson)
        .where(CourseLesson.course_id == course_id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


def _ready_needs(course: Course, lesson: CourseLesson) -> list[dict[str, Any]]:
    item = plan.needs_input(course, lesson)
    if item is None:
        return []
    return plan.current_needs(lesson, plan.fingerprint(item, plan.max_needs(lesson))) or []


async def snapshot(db: AsyncSession, course: Course, current: CourseLesson) -> Snapshot | None:
    """Assegnazione globale vista dalla lezione `current`; None senza piano
    o senza fabbisogni pronti per la lezione."""
    if not plan.plan_active() or current.is_assessment:
        return None
    started = time.monotonic()
    lessons = await _course_lessons(db, course.id)
    now = _now()
    needs: dict[uuid.UUID, list[dict[str, Any]]] = {}
    participants: list[CourseLesson] = []
    for lesson in lessons:
        if lesson.is_assessment:
            continue
        if lesson.id == current.id or lesson.content_status == "pending":
            ready = _ready_needs(course, lesson if lesson.id != current.id else current)
            if ready:
                needs[lesson.id] = ready
                participants.append(lesson)
    if current.id not in needs:
        return None
    joined = {lesson.id for lesson in participants}
    fixed: dict[uuid.UUID, int] = {}
    for lesson in lessons:
        if lesson.id in joined:
            continue
        offer = valid_offer(lesson, now) if lesson.content_status == "processing" else None
        used = _offered_ids(offer) if offer else source_figure_ids(lesson.content_raw)
        for fid in used:
            fixed[fid] = fixed.get(fid, 0) + 1
    policy = await catalog.license_policy_for(db, course)
    figures = await catalog.selectable_figures(db, course, license_policy=policy)
    index = FigureIndex(figures, legacy=bool(get_settings().figure_plan_legacy_match_enabled))
    supplies = []
    for fig in figures:
        found_for = (
            (fig.found_for_lesson_id, str(fig.found_for_need_id))
            if fig.found_for_lesson_id is not None and fig.found_for_need_id
            else None
        )
        supplies.append(
            sa.Supply(
                figure_id=fig.id,
                literature=fig.source_kind != "uploaded",
                resolution=catalog.resolution_class(fig),
                fixed_uses=fixed.get(fig.id, 0),
                found_for=found_for,
            )
        )
    slots: list[sa.LessonSlot] = []
    demands: list[sa.Demand] = []
    arcs: dict[sa.Key, list[sa.Arc]] = {}
    budgets: dict[uuid.UUID, int] = {}
    for lesson in participants:
        ready = needs[lesson.id]
        musts = sum(1 for n in ready if n.get("priority") == "must")
        budgets[lesson.id] = plan_budget(course, lesson, musts)
        source = current if lesson.id == current.id else lesson
        slots.append(
            sa.LessonSlot(
                lesson.id, budgets[lesson.id], frozenset(source_figure_ids(source.content_raw))
            )
        )
        for need in ready:
            need_id = str(need.get("need_id") or "")
            if not need_id:
                continue
            demands.append(
                sa.Demand(
                    lesson.id,
                    need_id,
                    need.get("priority") == "must",
                    bool(need.get("sequence_group")),
                )
            )
            arcs[(lesson.id, need_id)] = [
                sa.Arc(lesson.id, need_id, fid, found.tier, found.score, found.relation)
                for fid, found in index.covering(need)
            ]
    # Le figure già nel contenuto di una lezione partecipante che nessun suo
    # fabbisogno usa restano lì finché la lezione non si rigenera: contano
    # come usi fissi (lo stesso conto del ricontrollo sotto il lock).
    with_arcs: dict[uuid.UUID, set[uuid.UUID]] = {}
    for (lesson_id, _need), group in arcs.items():
        with_arcs.setdefault(lesson_id, set()).update(a.figure_id for a in group)
    extra_fixed: dict[uuid.UUID, int] = {}
    for slot in slots:
        for fid in slot.own - with_arcs.get(slot.lesson_id, set()):
            extra_fixed[fid] = extra_fixed.get(fid, 0) + 1
    if extra_fixed:
        supplies = [
            sa.Supply(
                figure_id=s.figure_id,
                literature=s.literature,
                resolution=s.resolution,
                fixed_uses=s.fixed_uses + extra_fixed.get(s.figure_id, 0),
                found_for=s.found_for,
            )
            for s in supplies
        ]
    assignment = sa.assign(
        slots,
        demands,
        supplies,
        [a for group in arcs.values() for a in group],
        cap=catalog.reuse_cap(),
    )
    return Snapshot(
        assignment=assignment,
        needs=needs,
        arcs=arcs,
        budgets=budgets,
        participants=[lesson.id for lesson in participants],
        stats={
            "participants": len(participants),
            "figures": len(figures),
            "arcs": sum(len(g) for g in arcs.values()),
            "ms": int((time.monotonic() - started) * 1000),
        },
    )


def offer_payload(snap: Snapshot, lesson: CourseLesson, *, locked: bool) -> dict[str, Any]:
    chosen = snap.assignment.chosen
    taken_here = {a.figure_id for k, a in chosen.items() if k[0] == lesson.id}
    offers: dict[str, Any] = {}
    alternatives: dict[str, list[dict[str, Any]]] = {}
    unassigned: dict[str, str] = {}
    for need in snap.needs.get(lesson.id, []):
        need_id = str(need.get("need_id") or "")
        key = (lesson.id, need_id)
        arc = chosen.get(key)
        if arc is not None:
            offers[need_id] = {
                "figure_id": str(arc.figure_id),
                "tier": arc.tier,
                "relation": arc.relation,
                "reserved": key in snap.assignment.reserved,
            }
        else:
            unassigned[need_id] = snap.assignment.unassigned.get(key, "no_candidate")
        others = [
            a
            for a in snap.arcs.get(key, [])
            if a.figure_id not in taken_here and (arc is None or a.figure_id != arc.figure_id)
        ]
        if others:
            alternatives[need_id] = [
                {"figure_id": str(a.figure_id), "tier": a.tier, "relation": a.relation}
                for a in others[:MAX_ALTERNATIVES]
            ]
    return {
        "v": ASSIGNMENT_VERSION,
        "state": "offered",
        "run_token": uuid.uuid4().hex,
        "at": _now().isoformat(),
        "locked": locked,
        "budget": snap.budgets.get(lesson.id, 0),
        "offers": offers,
        "alternatives": alternatives,
        "unassigned": unassigned,
        "stats": snap.stats,
    }


def plan_in_prompt() -> bool:
    return plan.plan_active() and bool(get_settings().figure_plan_in_prompt_enabled)


async def reserve(db: AsyncSession, course: Course, lesson: CourseLesson) -> dict[str, Any] | None:
    """Offerta della lezione all'avvio della Fase 3 (senza commit: il commit
    del chiamante salva l'offerta e rilascia il lock). Con il piano fuori dal
    prompt (`FIGURE_PLAN_IN_PROMPT_ENABLED=false`) nessuna offerta."""
    if not plan_in_prompt() or lesson.is_assessment:
        return None
    locked = await course_lock(db, course.id)
    snap = await snapshot(db, course, lesson)
    if snap is None:
        lesson.figure_assignment = None
        return None
    lesson.figure_assignment = offer_payload(snap, lesson, locked=locked)
    log.info(
        "figure_assignment_offered",
        lesson_id=str(lesson.id),
        offers=len(lesson.figure_assignment["offers"]),
        unassigned=len(lesson.figure_assignment["unassigned"]),
        **snap.stats,
    )
    return lesson.figure_assignment


async def plan_catalog(
    db: AsyncSession,
    course: Course,
    lesson: CourseLesson,
    offer: dict[str, Any] | None,
    lexical: catalog.CatalogResult | None,
) -> catalog.CatalogResult | None:
    """Catalogo del piano per il PROMPT 3 dall'offerta (figure assegnate e
    alternative, rilette ora), più il residuo del catalogo lessicale."""
    from app.models.course_document_figure import CourseDocumentFigure
    from app.services.source_figure_plan import build_plan_catalog

    if not offer or not offer.get("offers"):
        return None
    ids: set[uuid.UUID] = set()
    for value in (offer.get("offers") or {}).values():
        if isinstance(value, dict) and (fid := _as_uuid(value.get("figure_id"))):
            ids.add(fid)
    for values in (offer.get("alternatives") or {}).values():
        for value in values or []:
            if isinstance(value, dict) and (fid := _as_uuid(value.get("figure_id"))):
                ids.add(fid)
    rows = (
        (await db.execute(select(CourseDocumentFigure).where(CourseDocumentFigure.id.in_(ids))))
        .scalars()
        .all()
    )
    figures = {row.id: row for row in rows if row.course_id == course.id}
    residual: list[Any] = []
    if lexical is not None:
        by_id = {c.figure_id for c in lexical.catalog.candidates}
        residual = list(
            (
                await db.execute(
                    select(CourseDocumentFigure).where(CourseDocumentFigure.id.in_(by_id))
                )
            )
            .scalars()
            .all()
        )
        order = {c.figure_id: i for i, c in enumerate(lexical.catalog.candidates)}
        residual.sort(key=lambda row: order.get(row.id, 0))
    needs = _ready_needs(course, lesson)
    built = build_plan_catalog(needs, offer, figures, residual, list(lesson.section_outline or []))
    if built is None:
        return None
    policy = lexical.license_policy if lexical else await catalog.license_policy_for(db, course)
    return catalog.CatalogResult(
        catalog=built, budget=int(offer.get("budget") or 0), license_policy=policy
    )


def settle(
    lesson: CourseLesson,
    placed: set[uuid.UUID],
    placement: dict[str, Any] | None = None,
    *,
    run_token: str | None = None,
) -> dict[str, Any] | None:
    """Fotografia finale nella transazione della materializzazione, solo per
    l'offerta del giro in corso (`run_token`)."""
    data = dict(lesson.figure_assignment) if isinstance(lesson.figure_assignment, dict) else {}
    if not data or data.get("state") != "offered":
        return None
    if run_token is not None and data.get("run_token") != run_token:
        return None
    offers = data.get("offers") or {}
    bound = {
        need_id: offer["figure_id"]
        for need_id, offer in offers.items()
        if isinstance(offer, dict) and _as_uuid(offer.get("figure_id")) in placed
    }
    data.update(
        state="settled",
        settled_at=_now().isoformat(),
        placed=sorted(str(f) for f in placed),
        bound=bound,
        missed=sorted(n for n in offers if n not in bound),
    )
    if placement is not None:
        data["placement"] = placement
    lesson.figure_assignment = data
    return data


def _as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
