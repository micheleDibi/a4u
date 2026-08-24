from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.course_phase_order import COURSE_STATUS_RANK
from app.models.course import COURSE_STATUSES, Course
from tests.course_builders import build_course

pytestmark = pytest.mark.asyncio


async def test_course_statuses_tuple_matches_rank_map():
    """La tuple del modello e COURSE_STATUS_RANK devono restare 1:1
    (drift storico: il modello ometteva i 4 stati video/avatar)."""
    assert set(COURSE_STATUSES) == set(COURSE_STATUS_RANK)
    assert len(COURSE_STATUSES) == 22


async def test_check_constraint_accepts_all_22_statuses(seeded_db):
    """Il CHECK generato da metadata (ambienti create_all come i test)
    deve accettare tutti gli stati della pipeline, inclusi video/avatar
    (in DB reale arrivano dalla migrazione 0030)."""
    for status in COURSE_STATUSES:
        course_id, _org, _user = await build_course(
            seeded_db, status=status, modules=1, lessons_per_module=1
        )
        persisted = (
            await seeded_db.execute(
                select(Course.status).where(Course.id == course_id)
            )
        ).scalar_one()
        assert persisted == status
