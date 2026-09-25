"""Piano delle figure: fabbisogni per lezione (WP4, doc 18 §23), parte su DB.

- richiesta di Fase 3: fabbisogni in coda solo se mancano o se l'impronta
  è cambiata; verifiche e lezioni senza scaletta `skipped`; piano spento →
  nessun effetto;
- worker: fabbisogni pronti con impronta e costo; errore recuperabile →
  di nuovo `pending`, poi `failed` oltre il tetto; chiave assente →
  `failed`; struttura cambiata nel frattempo → si calcola l'input attuale;
- attesa della Fase 3: una lezione in coda aspetta i fabbisogni delle
  lezioni in coda dello stesso corso, al più `FIGURE_WAIT_MAX_MINUTES` dalla
  propria richiesta; le lezioni di un altro corso non contano;
- ripiego inline: una chiamata se mancano, nessuna se validi;
- duplicazione: i fabbisogni pronti si copiano, il costo no;
- dashboard admin: fase `figure_needs`;
- migrazione 0040: CHECK e indice uguali nel modello.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import CheckConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_content_service as content_svc
from app.services import course_lesson_content_worker as content_worker
from app.services import course_lesson_figure_needs_worker as needs_worker
from app.services import figure_plan_service as plan
from app.services import openai_figure_needs_service as needs_svc
from app.services.openai_client import OpenAINotConfiguredError
from tests.course_builders import build_course, find_lesson

USAGE = {
    "model": "gpt-4.1-mini",
    "prompt": 900,
    "completion": 300,
    "total": 1200,
    "cost_usd": 0.0008,
}


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    def apply(**updates: Any) -> Any:
        patched = get_settings().model_copy(
            update={"figure_source_enabled": True, "figure_plan_enabled": True, **updates}
        )
        for module in (plan, needs_worker, content_worker):
            monkeypatch.setattr(module, "get_settings", lambda: patched)
        monkeypatch.setattr(needs_svc, "get_settings", lambda: patched)
        return patched

    apply()
    return apply


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"calls": [], "error": None}

    async def generate(item: needs_svc.NeedsInput, *, max_total: int) -> Any:
        state["calls"].append(item)
        if state["error"] is not None:
            raise state["error"]
        raw = [
            needs_svc.RawNeed.model_validate(
                {
                    "section_id": item.section_ids[0],
                    "subject": f"Schema per {item.title}",
                    "representation": "schematic",
                    "focus": "Il principio",
                    "priority": "must",
                    "object_en": "laser Doppler vibrometer",
                    "object_terms": ["LDV"],
                    "variant_en": "",
                    "variant_terms": [],
                    "is_base": True,
                    "sequence_group": "",
                    "sequence_index": 0,
                    "terms_course": ["vibrometro"],
                    "terms_en": ["vibrometer"],
                    "reason": "",
                }
            )
        ]
        return needs_svc.validate_needs(raw, item, max_total=max_total), dict(USAGE)

    monkeypatch.setattr(needs_svc, "generate_needs", generate)
    return state


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch, _engine: Any) -> Any:
    made = async_sessionmaker(_engine, expire_on_commit=False)
    monkeypatch.setattr(needs_worker, "async_session_factory", made)
    return made


async def _course(db: AsyncSession, **kw: Any) -> Any:
    course_id, _org, user = await build_course(db, modules=1, lessons_per_module=3, **kw)
    course = await content_svc.load_course_full(db, course_id=course_id)
    assert course is not None
    return course, user


async def _park_other_queues(db: AsyncSession) -> None:
    """Il DB dei test è condiviso: le lezioni in coda di altri test fuori."""
    from sqlalchemy import update

    await db.execute(
        update(CourseLesson)
        .where(CourseLesson.figure_needs_status.in_(("pending", "processing")))
        .values(figure_needs_status="skipped")
    )
    await db.commit()


# --- richiesta ----------------------------------------------------------------------


async def test_request_queues_needs_once_per_fingerprint(
    seeded_db: AsyncSession, settings: Any
) -> None:
    course, user = await _course(seeded_db)
    lesson = find_lesson(course, "M1.L1")
    await content_svc.request_lesson_generation(
        seeded_db, course=course, lesson=lesson, actor_id=user.id, regeneration_hint=None
    )
    assert lesson.figure_needs_status == "pending"
    assert lesson.figure_needs_requested_at is not None
    # Fabbisogni pronti con la stessa impronta: una nuova richiesta non li
    # rimette in coda (ma aggiorna l'ora della richiesta).
    item = plan.needs_input(course, lesson)
    assert item is not None
    lesson.figure_needs = {
        "fingerprint": plan.fingerprint(item, plan.max_needs(lesson)),
        "needs": [],
    }
    lesson.figure_needs_status = "ready"
    await seeded_db.commit()
    assert plan.request_needs(course, lesson) is False
    assert lesson.figure_needs_status == "ready"
    # Scaletta cambiata → impronta nuova → di nuovo in coda.
    lesson.section_outline = [
        {"section_id": "S1", "title": "Altro"},
        {"section_id": "S2", "title": "Due"},
    ]
    assert plan.request_needs(course, lesson) is True
    assert lesson.figure_needs_status == "pending"


async def test_generate_all_queues_every_lesson_and_skips_the_assessment(
    seeded_db: AsyncSession, settings: Any
) -> None:
    course, user = await _course(seeded_db, with_assessment=True)
    await content_svc.request_all_lessons_generation(
        seeded_db, course=course, actor_id=user.id, regeneration_hint=None
    )
    statuses = {
        lesson.lesson_code: lesson.figure_needs_status
        for module in course.modules
        for lesson in module.lessons
    }
    assert statuses == {"M1.L1": "pending", "M1.L2": "pending", "M1.L3": "skipped"}


async def test_plan_off_leaves_the_lessons_untouched(
    seeded_db: AsyncSession, settings: Any
) -> None:
    settings(figure_plan_enabled=False)
    course, _user = await _course(seeded_db)
    lesson = find_lesson(course, "M1.L1")
    assert plan.request_needs(course, lesson) is False
    assert lesson.figure_needs_status is None and lesson.figure_needs_requested_at is None


# --- worker -------------------------------------------------------------------------


async def _queue(db: AsyncSession, course: Any, codes: tuple[str, ...]) -> list[CourseLesson]:
    lessons = [find_lesson(course, code) for code in codes]
    for lesson in lessons:
        plan.request_needs(course, lesson)
    await db.commit()
    return lessons


async def test_worker_stores_needs_with_fingerprint_and_cost(
    seeded_db: AsyncSession, settings: Any, fake: dict[str, Any], factory: Any
) -> None:
    await _park_other_queues(seeded_db)
    course, _user = await _course(seeded_db)
    (lesson,) = await _queue(seeded_db, course, ("M1.L1",))
    await needs_worker._tick()
    fresh = await seeded_db.get(CourseLesson, lesson.id, populate_existing=True)
    assert fresh is not None and fresh.figure_needs_status == "ready"
    item = plan.needs_input(course, fresh)
    assert item is not None
    assert fresh.figure_needs["fingerprint"] == plan.fingerprint(item, plan.max_needs(fresh))
    (need,) = fresh.figure_needs["needs"]
    assert need["need_id"].startswith("n") and need["section_id"] == "S1"
    assert fresh.figure_needs_usage["cost_usd"] == 0.0008
    assert fresh.figure_needs_usage["calls"] == 1
    assert [c.lesson_code for c in fake["calls"]] == ["M1.L1"]
    assert "M1.L2 Lezione 1.2" in fake["calls"][0].sibling_titles


async def test_worker_retries_then_fails_and_keeps_the_cost(
    seeded_db: AsyncSession, settings: Any, fake: dict[str, Any], factory: Any
) -> None:
    await _park_other_queues(seeded_db)
    settings(figure_needs_auto_retry_max=1)
    course, _user = await _course(seeded_db)
    (lesson,) = await _queue(seeded_db, course, ("M1.L1",))
    fake["error"] = needs_svc.OpenAIFigureNeedsError(
        status=200, message="troncato", usage=dict(USAGE)
    )
    await needs_worker._tick()
    fresh = await seeded_db.get(CourseLesson, lesson.id, populate_existing=True)
    assert fresh is not None and (fresh.figure_needs_status, fresh.figure_needs_attempts) == (
        "pending",
        1,
    )
    assert fresh.figure_needs_usage["cost_usd"] == 0.0008
    fresh.figure_needs_checked_at = datetime.now(UTC) - timedelta(hours=1)  # backoff scaduto
    await seeded_db.commit()
    await needs_worker._tick()
    fresh = await seeded_db.get(CourseLesson, lesson.id, populate_existing=True)
    assert fresh is not None and (fresh.figure_needs_status, fresh.figure_needs_attempts) == (
        "failed",
        2,
    )


async def test_missing_openai_key_fails_at_once(
    seeded_db: AsyncSession, settings: Any, fake: dict[str, Any], factory: Any
) -> None:
    await _park_other_queues(seeded_db)
    course, _user = await _course(seeded_db)
    (lesson,) = await _queue(seeded_db, course, ("M1.L1",))
    fake["error"] = OpenAINotConfiguredError()
    await needs_worker._tick()
    fresh = await seeded_db.get(CourseLesson, lesson.id, populate_existing=True)
    assert fresh is not None and fresh.figure_needs_status == "failed"


async def test_worker_uses_the_current_structure(
    seeded_db: AsyncSession, settings: Any, fake: dict[str, Any], factory: Any
) -> None:
    await _park_other_queues(seeded_db)
    course, _user = await _course(seeded_db)
    (lesson,) = await _queue(seeded_db, course, ("M1.L1",))
    lesson.section_outline = [{"section_id": "S7", "title": "Nuova sezione"}]
    await seeded_db.commit()
    await needs_worker._tick()
    fresh = await seeded_db.get(CourseLesson, lesson.id, populate_existing=True)
    assert fresh is not None and fresh.figure_needs["needs"][0]["section_id"] == "S7"


async def test_interrupted_calculations_return_to_the_queue(
    seeded_db: AsyncSession, settings: Any, factory: Any
) -> None:
    await _park_other_queues(seeded_db)
    course, _user = await _course(seeded_db)
    (lesson,) = await _queue(seeded_db, course, ("M1.L1",))
    lesson.figure_needs_status = "processing"
    await seeded_db.commit()
    await needs_worker.reset_interrupted()
    fresh = await seeded_db.get(CourseLesson, lesson.id, populate_existing=True)
    assert fresh is not None and fresh.figure_needs_status == "pending"


# --- attesa della Fase 3 ------------------------------------------------------------


async def _ready_ids(db: AsyncSession) -> set[uuid.UUID]:
    rows = await db.execute(content_worker._pending_lessons_query())
    return {row[0] for row in rows.all()}


async def test_phase3_waits_for_the_needs_of_the_course_within_the_cap(
    seeded_db: AsyncSession, settings: Any
) -> None:
    settings(figure_literature_enabled=False, figure_extraction_enabled=False)
    course, _user = await _course(seeded_db)
    other, _other_user = await _course(seeded_db)
    first, second = (find_lesson(course, "M1.L1"), find_lesson(course, "M1.L2"))
    foreign = find_lesson(other, "M1.L1")
    for lesson in (first, second, foreign):
        lesson.content_status = "pending"
    plan.request_needs(course, first)
    plan.request_needs(course, second)
    second.figure_needs_status = "ready"  # già calcolati
    foreign.figure_needs_requested_at = datetime.now(UTC)
    foreign.figure_needs_status = "ready"
    await seeded_db.commit()
    ready = await _ready_ids(seeded_db)
    # Entrambe le lezioni del corso aspettano i fabbisogni di M1.L1; l'altro
    # corso no.
    assert first.id not in ready and second.id not in ready
    assert foreign.id in ready
    # Oltre il tetto (dalla propria richiesta) si parte comunque.
    cap = int(get_settings().figure_wait_max_minutes) + 1
    first.figure_needs_requested_at = datetime.now(UTC) - timedelta(minutes=cap)
    await seeded_db.commit()
    ready = await _ready_ids(seeded_db)
    assert first.id in ready and second.id not in ready
    # Piano spento: nessuna attesa.
    settings(
        figure_plan_enabled=False, figure_literature_enabled=False, figure_extraction_enabled=False
    )
    assert {first.id, second.id} <= await _ready_ids(seeded_db)


# --- ripiego inline -------------------------------------------------------------------


async def test_inline_fallback_calls_once_and_reuses_valid_needs(
    seeded_db: AsyncSession, settings: Any, fake: dict[str, Any]
) -> None:
    course, _user = await _course(seeded_db)
    lesson = find_lesson(course, "M1.L1")
    needs = await plan.ensure_lesson_needs(course, lesson)
    assert needs and lesson.figure_needs_status == "ready"
    again = await plan.ensure_lesson_needs(course, lesson)
    assert again == needs and len(fake["calls"]) == 1
    fake["error"] = needs_svc.OpenAIFigureNeedsError(status=500, message="giù", usage=None)
    lesson.section_outline = [{"section_id": "S9", "title": "Cambiata"}]
    assert await plan.ensure_lesson_needs(course, lesson) is None


# --- duplicazione e costi -----------------------------------------------------------


async def test_admin_costs_include_the_figure_needs_phase(
    seeded_db: AsyncSession, settings: Any
) -> None:
    from app.services import admin_metrics_service

    course, _user = await _course(seeded_db)
    lesson = find_lesson(course, "M1.L1")
    lesson.figure_needs_usage = dict(USAGE)
    lesson.figure_needs_checked_at = datetime.now(UTC)
    await seeded_db.commit()
    now = datetime.now(UTC)
    cost = await admin_metrics_service._cost(
        seeded_db, cutoff_7d=now - timedelta(days=7), cutoff_30d=now - timedelta(days=30)
    )
    phases = {p.phase: p.cost_usd for p in cost.by_phase}
    assert phases["figure_needs"] >= 0.0008


async def test_duplication_copies_ready_needs_without_the_cost(
    seeded_db: AsyncSession, settings: Any
) -> None:
    from app.models.course_duplication_job import CourseDuplicationJob
    from app.services import course_duplication_service as dup

    course, _user = await _course(seeded_db)
    ready, queued = find_lesson(course, "M1.L1"), find_lesson(course, "M1.L2")
    ready.figure_needs = {"fingerprint": "abc", "needs": [{"need_id": "n1234abcd"}]}
    ready.figure_needs_status = "ready"
    ready.figure_needs_usage = dict(USAGE)
    queued.figure_needs = {"fingerprint": "old", "needs": []}
    queued.figure_needs_status = "pending"
    await seeded_db.commit()
    source = await dup.load_source_full(seeded_db, course_id=course.id)
    assert source is not None
    job = CourseDuplicationJob(
        source_course_id=source.id, target_language_code="it", requested_by_user_id=None
    )
    seeded_db.add(job)
    await seeded_db.flush()
    target = await dup._clone_course_structure(
        seeded_db, source=source, target_language_code="it", job=job
    )
    await seeded_db.commit()
    clones = {
        row.lesson_code: row
        for row in (
            await seeded_db.execute(select(CourseLesson).where(CourseLesson.course_id == target.id))
        ).scalars()
    }
    assert clones["M1.L1"].figure_needs == ready.figure_needs
    assert clones["M1.L1"].figure_needs_status == "ready"
    assert clones["M1.L1"].figure_needs_usage is None
    assert clones["M1.L2"].figure_needs is None and clones["M1.L2"].figure_needs_status is None


# --- migrazione 0040 ------------------------------------------------------------------

_MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0040_figure_needs.py"


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def test_migration_0040_matches_the_model() -> None:
    text = _MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(text)
    check = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_STATUS_CHECK" for t in node.targets)
    )
    assert isinstance(check, ast.Constant)
    model = {
        _norm(str(c.sqltext))
        for c in CourseLesson.__table__.constraints
        if isinstance(c, CheckConstraint) and str(c.name).endswith("figure_needs_status")
    }
    assert model == {_norm(check.value)}
    (index,) = [
        i
        for i in CourseLesson.__table__.indexes
        if i.name == "ix_course_lesson_figure_needs_pending"
    ]
    assert [c.name for c in index.columns] == ["figure_needs_requested_at"]
    assert "figure_needs_status = 'pending'" in str(index.dialect_options["postgresql"]["where"])
    added: set[str] = set()
    dropped: set[str] = set()
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        for node in ast.walk(fn):
            if (
                fn.name == "upgrade"
                and isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "add_column"
            ):
                column = node.args[1]
                assert isinstance(column, ast.Call) and isinstance(column.args[0], ast.Constant)
                added.add(column.args[0].value)
            if fn.name == "downgrade" and isinstance(node, ast.For):
                assert isinstance(node.iter, ast.Tuple)
                dropped |= {e.value for e in node.iter.elts if isinstance(e, ast.Constant)}
    assert len(added) == 6 and added == dropped
    assert added <= set(CourseLesson.__table__.columns.keys())


async def test_needs_status_domain(seeded_db: AsyncSession) -> None:
    from sqlalchemy.exc import IntegrityError

    course_id, _org, _user = await build_course(seeded_db, modules=1, lessons_per_module=1)
    lesson = (
        (await seeded_db.execute(select(CourseLesson).where(CourseLesson.course_id == course_id)))
        .scalars()
        .one()
    )
    lesson.figure_needs_status = "boh"
    with pytest.raises(IntegrityError):
        await seeded_db.commit()
    await seeded_db.rollback()
