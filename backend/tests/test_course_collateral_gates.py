from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.course_phase_order import normalize_course_status_from_data
from app.core.errors import ConflictError
from app.models.course import Course
from app.schemas.course import CourseUpdateInput
from app.schemas.course_architecture import ModuleCreateInput
from app.services import (
    course_architecture_crud,
    course_glossary_service,
    course_service,
)
from app.services import (
    course_lesson_content_service as content_svc,
)
from app.services import (
    course_lesson_structure_service as structure_svc,
)
from tests.course_builders import build_course, find_lesson

pytestmark = pytest.mark.asyncio


async def _load(db, course_id):
    course = await content_svc.load_course_full(db, course_id=course_id)
    assert course is not None
    return course


# ---------------------------------------------------------------------------
# Glossario: allow-set rank-based (sana i buchi slides_approved,
# speech_approved, video_*, avatar_*)
# ---------------------------------------------------------------------------


async def test_glossary_regen_gate_open_on_late_statuses(seeded_db):
    """A `slides_approved`/`avatar_video_ready` il gate su course.status
    passa (prima: 409 per i buchi dell'allow-set). Si usa il secondo
    gate (glossario `processing`) come sentinella per non invocare
    OpenAI nel test."""
    for status in ("slides_approved", "avatar_video_ready"):
        course_id, _org, user = await build_course(seeded_db, status=status)
        course = await _load(seeded_db, course_id)
        course.glossary_status = "processing"
        await seeded_db.flush()
        with pytest.raises(ConflictError) as exc:
            await course_glossary_service.regenerate_glossary(
                seeded_db, course=course, actor_id=user.id
            )
        assert exc.value.code == "glossary_already_processing"


async def test_glossary_regen_blocked_on_draft_and_archived(seeded_db):
    for status in ("draft", "archived"):
        course_id, _org, user = await build_course(seeded_db, status=status)
        course = await _load(seeded_db, course_id)
        with pytest.raises(ConflictError) as exc:
            await course_glossary_service.regenerate_glossary(
                seeded_db, course=course, actor_id=user.id
            )
        assert exc.value.code == "invalid_course_status"


# ---------------------------------------------------------------------------
# CRUD architettura: editabilità data-based (in-flight, non più allow-set)
# ---------------------------------------------------------------------------


async def test_architecture_crud_editable_at_content_pending(seeded_db):
    """`content_pending` è ora uno stato di soggiorno: senza generazioni
    in volo l'editing dell'architettura è permesso (prima: 409)."""
    course_id, _org, user = await build_course(
        seeded_db, status="content_pending", content_status="approved"
    )
    course = await _load(seeded_db, course_id)
    await course_architecture_crud.create_module(
        seeded_db,
        course=course,
        actor_id=user.id,
        payload=ModuleCreateInput(title="Modulo aggiunto tardi"),
    )
    # Verifica su query diretta: la sessione di test ha
    # expire_on_commit=False e non ri-popola la relazione già caricata.
    from app.models.course_module import CourseModule

    titles = (
        await seeded_db.execute(
            select(CourseModule.title).where(
                CourseModule.course_id == course_id
            )
        )
    ).scalars().all()
    assert "Modulo aggiunto tardi" in titles


async def test_architecture_crud_blocked_with_generation_in_flight(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="content_pending", content_status="approved"
    )
    course = await _load(seeded_db, course_id)
    find_lesson(course, "M1.L1").content_status = "processing"
    await seeded_db.flush()
    with pytest.raises(ConflictError) as exc:
        await course_architecture_crud.create_module(
            seeded_db,
            course=course,
            actor_id=user.id,
            payload=ModuleCreateInput(title="Nope"),
        )
    assert exc.value.code == "architecture_not_editable"


async def test_architecture_crud_blocked_on_published(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="published", content_status="approved"
    )
    course = await _load(seeded_db, course_id)
    with pytest.raises(ConflictError) as exc:
        await course_architecture_crud.create_module(
            seeded_db,
            course=course,
            actor_id=user.id,
            payload=ModuleCreateInput(title="Nope"),
        )
    assert exc.value.code == "architecture_not_editable"


# ---------------------------------------------------------------------------
# Approve-all tolleranti (dispense + moduli)
# ---------------------------------------------------------------------------


async def test_approve_all_content_tolerates_empty(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="content_pending", content_status="empty"
    )
    course = await _load(seeded_db, course_id)
    find_lesson(course, "M1.L1").content_status = "ready"
    await seeded_db.flush()
    refreshed = await content_svc.approve_all_lessons_content(
        seeded_db, course=course, actor_id=user.id
    )
    assert find_lesson(refreshed, "M1.L1").content_status == "approved"
    assert find_lesson(refreshed, "M1.L2").content_status == "empty"


async def test_approve_all_content_blocked_on_processing(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="content_pending", content_status="ready"
    )
    course = await _load(seeded_db, course_id)
    find_lesson(course, "M2.L1").content_status = "processing"
    await seeded_db.flush()
    with pytest.raises(ConflictError) as exc:
        await content_svc.approve_all_lessons_content(
            seeded_db, course=course, actor_id=user.id
        )
    assert exc.value.code == "not_all_lessons_ready"


async def test_approve_all_content_noop_when_all_approved(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="content_approved", content_status="approved"
    )
    course = await _load(seeded_db, course_id)
    refreshed = await content_svc.approve_all_lessons_content(
        seeded_db, course=course, actor_id=user.id
    )
    assert all(
        lesson.content_status == "approved"
        for m in refreshed.modules
        for lesson in m.lessons
    )


async def test_approve_all_content_requires_some_content(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="lessons_structure_approved", content_status="empty"
    )
    course = await _load(seeded_db, course_id)
    with pytest.raises(ConflictError) as exc:
        await content_svc.approve_all_lessons_content(
            seeded_db, course=course, actor_id=user.id
        )
    assert exc.value.code == "no_content_to_approve"


async def test_approve_all_modules_tolerates_empty(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="lessons_structure_ready", module_status="ready"
    )
    course = await _load(seeded_db, course_id)
    module2 = next(m for m in course.modules if m.module_code == "M2")
    module2.lessons_structure_status = "empty"
    await seeded_db.flush()
    refreshed = await structure_svc.approve_all_modules_structure(
        seeded_db, course=course, actor_id=user.id
    )
    m1 = next(m for m in refreshed.modules if m.module_code == "M1")
    m2 = next(m for m in refreshed.modules if m.module_code == "M2")
    assert m1.lessons_structure_status == "approved"
    assert m2.lessons_structure_status == "empty"


# ---------------------------------------------------------------------------
# PATCH status ristretto + riattivazione con ricalcolo
# ---------------------------------------------------------------------------


async def test_update_course_status_allows_publish_and_archive(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="content_ready", content_status="ready"
    )
    course = await _load(seeded_db, course_id)
    refreshed = await course_service.update_course(
        seeded_db,
        course=course,
        payload=CourseUpdateInput(status="published"),
        actor_id=user.id,
    )
    assert refreshed.status == "published"


async def test_update_course_status_rejects_arbitrary_values(seeded_db):
    course_id, _org, user = await build_course(
        seeded_db, status="speech_approved",
        content_status="approved", slides_status="approved",
    )
    course = await _load(seeded_db, course_id)
    with pytest.raises(ConflictError) as exc:
        await course_service.update_course(
            seeded_db,
            course=course,
            payload=CourseUpdateInput(status="draft"),
            actor_id=user.id,
        )
    assert exc.value.code == "invalid_status_transition"


async def test_update_course_status_reactivation_recomputes(seeded_db):
    """Da published, chiedere uno stato non terminale = riattivazione:
    lo status torna alla milestone derivata dai dati (non al valore
    richiesto)."""
    course_id, _org, user = await build_course(
        seeded_db, status="published", content_status="approved"
    )
    course = await _load(seeded_db, course_id)
    refreshed = await course_service.update_course(
        seeded_db,
        course=course,
        payload=CourseUpdateInput(status="draft"),
        actor_id=user.id,
    )
    assert refreshed.status == "content_approved"
    persisted = (
        await seeded_db.execute(
            select(Course.status).where(Course.id == course_id)
        )
    ).scalar_one()
    assert persisted == "content_approved"


# ---------------------------------------------------------------------------
# normalize_course_status_from_data (unit puro sul grafo caricato)
# ---------------------------------------------------------------------------


async def test_normalize_status_milestones(seeded_db):
    cases = [
        ({"content_status": "ready"}, "content_ready"),
        ({"content_status": "approved"}, "content_approved"),
        (
            {"content_status": "approved", "slides_status": "approved"},
            "slides_approved",
        ),
        (
            {
                "content_status": "approved",
                "slides_status": "approved",
                "speech_status": "approved",
            },
            "speech_approved",
        ),
        ({}, "lessons_structure_approved"),
    ]
    for overrides, expected in cases:
        course_id, _org, _user = await build_course(
            seeded_db, status="lessons_structure_approved", **overrides
        )
        course = await _load(seeded_db, course_id)
        assert normalize_course_status_from_data(course) == expected, (
            overrides,
            expected,
        )


async def test_normalize_status_assessment_only_course_not_vacuous(seeded_db):
    """Un corso con sole lezioni-verifica non deve saltare a
    speech_approved per condizioni vacuamente vere sugli insiemi vuoti."""
    course_id, _org, _user = await build_course(
        seeded_db,
        status="lessons_structure_approved",
        modules=1,
        lessons_per_module=1,
        with_assessment=True,
        content_status="approved",
    )
    course = await _load(seeded_db, course_id)
    assert normalize_course_status_from_data(course) == "content_approved"
