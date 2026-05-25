"""Aggregazioni org-scoped per `GET /orgs/{org_id}/metrics`.

Niente cache: il traffico è già ridotto (per-org, per-utente). **Niente
costi AI** nel payload (scelta di prodotto — vedi memoria
`feedback_no_api_costs_in_org_views`): il service non calcola nemmeno
`SUM(cost_usd)` per non sprecare query.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.invitation import Invitation
from app.models.membership import Membership
from app.schemas.admin_metrics import LessonsPhaseBreakdown, StatusCount
from app.schemas.org_metrics import (
    CoursesMetrics,
    LessonsMetrics,
    MembersMetrics,
    OrgMetricsOut,
)


async def compute_org_metrics(
    db: AsyncSession, *, org_id: uuid.UUID
) -> OrgMetricsOut:
    now = datetime.now(timezone.utc)

    courses_metrics = await _courses(db, org_id=org_id)
    lessons_metrics = await _lessons(db, org_id=org_id)
    members_metrics = await _members(db, org_id=org_id, now=now)

    return OrgMetricsOut(
        generated_at=now,
        courses=courses_metrics,
        lessons=lessons_metrics,
        members=members_metrics,
    )


# ---------------------------------------------------------------------------
# Sub-aggregations
# ---------------------------------------------------------------------------


async def _courses(db: AsyncSession, *, org_id: uuid.UUID) -> CoursesMetrics:
    total = (
        await db.execute(
            select(func.count(Course.id)).where(
                Course.organization_id == org_id
            )
        )
    ).scalar_one()

    by_status_rows = (
        await db.execute(
            select(Course.status, func.count(Course.id))
            .where(Course.organization_id == org_id)
            .group_by(Course.status)
        )
    ).all()
    by_status = [
        StatusCount(status=s, count=int(c)) for s, c in by_status_rows
    ]

    return CoursesMetrics(total=int(total), by_status=by_status)


async def _by_lesson_status(
    db: AsyncSession, col, *, org_id: uuid.UUID
) -> list[StatusCount]:
    rows = (
        await db.execute(
            select(col, func.count(CourseLesson.id))
            .join(Course, CourseLesson.course_id == Course.id)
            .where(Course.organization_id == org_id)
            .group_by(col)
        )
    ).all()
    return [StatusCount(status=s, count=int(c)) for s, c in rows]


async def _lessons(db: AsyncSession, *, org_id: uuid.UUID) -> LessonsMetrics:
    total = (
        await db.execute(
            select(func.count(CourseLesson.id))
            .join(Course, CourseLesson.course_id == Course.id)
            .where(Course.organization_id == org_id)
        )
    ).scalar_one()

    phases = LessonsPhaseBreakdown(
        content=await _by_lesson_status(
            db, CourseLesson.content_status, org_id=org_id
        ),
        slides=await _by_lesson_status(
            db, CourseLesson.slides_status, org_id=org_id
        ),
        speech=await _by_lesson_status(
            db, CourseLesson.speech_status, org_id=org_id
        ),
        video=await _by_lesson_status(
            db, CourseLesson.video_status, org_id=org_id
        ),
        avatar_video=await _by_lesson_status(
            db, CourseLesson.avatar_video_status, org_id=org_id
        ),
    )
    return LessonsMetrics(total=int(total), phases=phases)


async def _members(
    db: AsyncSession, *, org_id: uuid.UUID, now: datetime
) -> MembersMetrics:
    total = (
        await db.execute(
            select(func.count(Membership.id)).where(
                Membership.organization_id == org_id
            )
        )
    ).scalar_one()

    pending = (
        await db.execute(
            select(func.count(Invitation.id)).where(
                Invitation.organization_id == org_id,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > now,
            )
        )
    ).scalar_one()

    return MembersMetrics(
        total=int(total),
        pending_invitations=int(pending),
    )
