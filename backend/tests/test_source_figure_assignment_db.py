"""Assegnazione delle figure ai fabbisogni sul DB (WP6, doc 18 §23.4).

- offerta della lezione (`reserve`): fabbisogni coperti con la figura, i
  motivi degli scoperti, le alternative; fotografia finale (`settle`);
- partecipanti: una lezione sorella in coda con i fabbisogni pronti compete
  per la stessa figura; una sorella in generazione con un'offerta valida la
  tiene occupata, con un'offerta scaduta no;
- lock di corso: un'altra transazione non lo prende finché il primo commit
  non lo rilascia; mai un'eccezione;
- piano spento: nessuna offerta e nessun lock.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.services import course_lesson_content_service as content_svc
from app.services import figure_plan_service as plan
from app.services import openai_figure_needs_service as needs_svc
from app.services import source_figure_assignment_service as svc
from app.services import source_figure_catalog as catalog
from tests.course_builders import build_course, build_course_document, find_lesson
from tests.source_figure_builders import build_document_figure


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    def apply(**updates: Any) -> Any:
        patched = get_settings().model_copy(
            update={"figure_source_enabled": True, "figure_plan_enabled": True, **updates}
        )
        for module in (plan, svc, catalog):
            monkeypatch.setattr(module, "get_settings", lambda: patched)
        return patched

    apply()
    return apply


def _need(section: str, subject: str, variant: str, *, must: bool = True) -> dict[str, Any]:
    return {
        "need_id": needs_svc.need_id(section, subject),
        "section_id": section,
        "subject": subject,
        "representation": "schematic",
        "focus": "",
        "priority": "must" if must else "should",
        "object_en": "laser Doppler vibrometer",
        "object_terms": ["LDV"],
        "variant_en": variant,
        "variant_terms": [variant] if variant else [],
        "is_base": not variant,
        "sequence_group": "",
        "sequence_index": 0,
        "terms_course": ["vibrometro"],
        "terms_en": ["Bragg cell"],
    }


def _store(course: Any, lesson: Any, needs: list[dict[str, Any]]) -> None:
    item = plan.needs_input(course, lesson)
    assert item is not None
    plan.store_needs(
        lesson,
        result=needs_svc.NeedsResult(needs, {}),
        fp=plan.fingerprint(item, plan.max_needs(lesson)),
        model="test",
        usage=None,
    )


async def _course(db: AsyncSession) -> dict[str, Any]:
    course_id, _org, _user = await build_course(
        db, modules=1, lessons_per_module=2, content_status="pending"
    )
    course = await content_svc.load_course_full(db, course_id=course_id)
    assert course is not None
    doc = build_course_document(course_id, filename="ldv.pdf", policy="citable")
    db.add(doc)
    await db.flush()

    def fig(variant: str) -> Any:
        return build_document_figure(
            course_id,
            doc.id,
            license="cc_by",
            kind="schematic",
            description=f"Schema del vibrometro {variant}",
            keywords={"course": ["vibrometro"], "en": ["laser Doppler vibrometer", "Bragg cell"]},
            depicts={
                "v": 1,
                "items": [{"object_en": "laser Doppler vibrometer", "variant_en": variant}],
                "focus": "optical layout",
            },
        )

    figures = {"scan": fig("scanning"), "diff": fig("differential")}
    db.add_all(figures.values())
    await db.commit()
    return {
        "course": course,
        "first": find_lesson(course, "M1.L1"),
        "second": find_lesson(course, "M1.L2"),
        "figures": figures,
    }


async def test_reserve_writes_the_offer_and_settle_the_binding(
    seeded_db: AsyncSession, settings: Any
) -> None:
    env = await _course(seeded_db)
    course, lesson, figs = env["course"], env["first"], env["figures"]
    scan, diff, rot = (
        _need("S1", "Schema a scansione", "scanning"),
        _need("S1", "Schema differenziale", "differential"),
        _need("S1", "Schema rotazionale", "rotational"),
    )
    _store(course, lesson, [scan, diff, rot])
    await seeded_db.commit()
    data = await svc.reserve(seeded_db, course, lesson)
    await seeded_db.commit()
    assert data is not None and data["state"] == "offered" and data["locked"] is True
    assert data["offers"][scan["need_id"]]["figure_id"] == str(figs["scan"].id)
    assert data["offers"][diff["need_id"]]["figure_id"] == str(figs["diff"].id)
    assert data["unassigned"] == {rot["need_id"]: "no_candidate"}
    assert data["budget"] >= 3 and data["run_token"]
    settled = svc.settle(lesson, {figs["scan"].id})
    assert settled is not None and settled["state"] == "settled"
    assert settled["bound"] == {scan["need_id"]: str(figs["scan"].id)}
    assert settled["missed"] == [diff["need_id"]]


async def test_a_pending_sister_competes_for_the_same_figure(
    seeded_db: AsyncSession, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(catalog, "reuse_cap", lambda: 1)
    env = await _course(seeded_db)
    course, first, second = env["course"], env["first"], env["second"]
    should = _need("S1", "Schema a scansione", "scanning", must=False)
    must = _need("S1", "Schema a scansione", "scanning", must=True)
    _store(course, first, [should])
    _store(course, second, [must])
    await seeded_db.commit()
    data = await svc.reserve(seeded_db, course, first)
    await seeded_db.commit()
    assert data is not None and data["stats"]["participants"] == 2
    assert data["unassigned"] == {should["need_id"]: "reuse_cap"}


@pytest.mark.parametrize(
    ("age", "blocked"), [(timedelta(minutes=5), True), (timedelta(hours=3), False)]
)
async def test_a_processing_sister_holds_its_offered_figure_while_the_offer_is_valid(
    seeded_db: AsyncSession,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    age: timedelta,
    blocked: bool,
) -> None:
    monkeypatch.setattr(catalog, "reuse_cap", lambda: 1)
    env = await _course(seeded_db)
    course, first, second, figs = env["course"], env["first"], env["second"], env["figures"]
    need = _need("S1", "Schema a scansione", "scanning")
    _store(course, first, [need])
    second.content_status = "processing"
    second.figure_assignment = {
        "state": "offered",
        "at": (datetime.now(UTC) - age).isoformat(),
        "offers": {"nX": {"figure_id": str(figs["scan"].id)}},
    }
    await seeded_db.commit()
    data = await svc.reserve(seeded_db, course, first)
    await seeded_db.commit()
    assert data is not None
    if blocked:
        assert data["unassigned"] == {need["need_id"]: "reuse_cap"}
    else:
        assert data["offers"][need["need_id"]]["figure_id"] == str(figs["scan"].id)


async def test_the_course_lock_is_exclusive_until_commit(
    seeded_db: AsyncSession, _engine: Any
) -> None:
    course_id = uuid.uuid4()
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with factory() as one, factory() as two:
        assert await svc.course_lock(one, course_id, timeout=0.1) is True
        assert await svc.course_lock(two, course_id, timeout=0.3) is False
        await one.commit()
        assert await svc.course_lock(two, course_id, timeout=0.3) is True
        await two.commit()


async def test_plan_off_means_no_offer(seeded_db: AsyncSession, settings: Any) -> None:
    settings(figure_plan_enabled=False)
    env = await _course(seeded_db)
    assert await svc.reserve(seeded_db, env["course"], env["first"]) is None
    assert env["first"].figure_assignment is None


def test_plan_budget() -> None:
    course = type("C", (), {"lesson_duration_minutes": 15})()
    lesson = type("L", (), {"is_assessment": False, "is_introductory": False})()
    assert svc.plan_budget(course, lesson, 2) == 4
    assert svc.plan_budget(course, lesson, 6) == 6
    assert svc.plan_budget(course, lesson, 12) == 8
    course.lesson_duration_minutes = 30
    assert svc.plan_budget(course, lesson, 0) == 7
    lesson.is_assessment = True
    assert svc.plan_budget(course, lesson, 3) == 0


def test_migration_0042_matches_the_models() -> None:
    import ast
    from pathlib import Path

    from app.models.course_document_figure import CourseDocumentFigure
    from app.models.course_lesson import CourseLesson

    path = (
        Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0042_figure_assignment.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    added: set[tuple[str, str]] = set()
    dropped: set[tuple[str, str]] = set()
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        for node in ast.walk(fn):
            if (
                fn.name == "upgrade"
                and isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "add_column"
            ):
                table, column = node.args[:2]
                assert isinstance(table, ast.Constant) and isinstance(column, ast.Call)
                assert isinstance(column.args[0], ast.Constant)
                added.add((table.value, column.args[0].value))
            if fn.name == "downgrade" and isinstance(node, ast.For):
                (stmt,) = node.body
                assert isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                table_node = stmt.value.args[0]
                assert isinstance(node.iter, ast.Tuple) and isinstance(table_node, ast.Constant)
                dropped |= {
                    (table_node.value, e.value)
                    for e in node.iter.elts
                    if isinstance(e, ast.Constant)
                }
    assert (
        added
        == dropped
        == {
            ("course_lesson", "figure_assignment"),
            ("course_document_figure", "found_for_lesson_id"),
            ("course_document_figure", "found_for_need_id"),
        }
    )
    assert "figure_assignment" in CourseLesson.__table__.columns
    (fk,) = CourseDocumentFigure.__table__.columns["found_for_lesson_id"].foreign_keys
    assert fk.column.table.name == "course_lesson" and fk.ondelete == "SET NULL"
    assert fk.constraint is not None
    assert fk.constraint.name == "fk_course_document_figure_found_for_lesson_id_course_lesson"
