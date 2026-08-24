from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.core.course_phase_order import normalize_course_status_from_data
from app.models.course import Course
from app.models.course_duplication_job import CourseDuplicationJob
from app.services import course_lesson_content_service as content_svc
from tests.course_builders import build_course, find_lesson

pytestmark = pytest.mark.asyncio

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0034_normalize_course_status_indicator.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0034", _MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def _run_statements(db) -> None:
    """Esegue gli stessi UPDATE della 0034 sul DB di test (la suite usa
    create_all, non alembic: qui si testa l'EQUIVALENZA LOGICA degli
    statement rispetto a `normalize_course_status_from_data`)."""
    mig = _load_migration()
    for target, milestone in mig._statements():
        await db.execute(
            text(
                f"UPDATE course c SET status = '{target}' "
                f"WHERE c.status IN ({mig._from_set(target)}) "
                f"AND {mig._NO_ACTIVE_DUPLICATION} "
                f"AND {milestone}"
            )
        )
    await db.commit()


async def _status_of(db, course_id) -> str:
    return (
        await db.execute(select(Course.status).where(Course.id == course_id))
    ).scalar_one()


async def test_regressed_course_advances_to_derived_milestone(seeded_db):
    """Corso regredito a `content_pending` da un vecchio bulk ma con
    tutte le fasi 3-5 approvate → sale a `speech_approved` (identico a
    normalize_course_status_from_data)."""
    course_id, _org, _user = await build_course(
        seeded_db,
        status="content_pending",
        content_status="approved",
        slides_status="approved",
        speech_status="approved",
    )
    await _run_statements(seeded_db)
    assert await _status_of(seeded_db, course_id) == "speech_approved"
    course = await content_svc.load_course_full(seeded_db, course_id=course_id)
    assert normalize_course_status_from_data(course) == "speech_approved"


async def test_assessment_only_course_not_vacuously_promoted(seeded_db):
    """Corso di sole lezioni-verifica (escluse da slides/speech): non
    deve saltare a `speech_approved` per NOT EXISTS vacuamente veri."""
    course_id, _org, _user = await build_course(
        seeded_db,
        status="lessons_structure_approved",
        modules=1,
        lessons_per_module=1,
        with_assessment=True,
        content_status="approved",
    )
    await _run_statements(seeded_db)
    assert await _status_of(seeded_db, course_id) == "content_approved"


async def test_partial_course_stays_put(seeded_db):
    """Avanzamento per-unità parziale (una sola lezione avanti): nessuna
    milestone completa → status invariato."""
    course_id, _org, _user = await build_course(
        seeded_db, status="content_pending", content_status="empty"
    )
    course = await content_svc.load_course_full(seeded_db, course_id=course_id)
    lesson = find_lesson(course, "M1.L1")
    lesson.content_status = "approved"
    lesson.slides_status = "approved"
    await seeded_db.commit()
    await _run_statements(seeded_db)
    assert await _status_of(seeded_db, course_id) == "content_pending"


async def test_duplication_targets_are_skipped(seeded_db):
    """Target di duplicazione con job non `ready` (in volo O fallito a
    clone parziale): mai normalizzati."""
    for job_status in ("processing", "failed"):
        source_id, _org, _user = await build_course(
            seeded_db, status="slides_approved",
            content_status="approved", slides_status="approved",
        )
        target_id, _torg, _tuser = await build_course(
            seeded_db, status="draft",
            content_status="approved", slides_status="approved",
        )
        seeded_db.add(
            CourseDuplicationJob(
                source_course_id=source_id,
                target_course_id=target_id,
                target_language_code="en",
                status=job_status,
            )
        )
        await seeded_db.commit()
        await _run_statements(seeded_db)
        assert await _status_of(seeded_db, target_id) == "draft", job_status


async def test_published_and_archived_untouched(seeded_db):
    for status in ("published", "archived"):
        course_id, _org, _user = await build_course(
            seeded_db,
            status=status,
            content_status="approved",
            slides_status="approved",
            speech_status="approved",
        )
        await _run_statements(seeded_db)
        assert await _status_of(seeded_db, course_id) == status


async def test_statements_are_idempotent(seeded_db):
    course_id, _org, _user = await build_course(
        seeded_db,
        status="lessons_structure_pending",
        content_status="approved",
        slides_status="approved",
    )
    await _run_statements(seeded_db)
    first = await _status_of(seeded_db, course_id)
    await _run_statements(seeded_db)
    assert await _status_of(seeded_db, course_id) == first == "slides_approved"
