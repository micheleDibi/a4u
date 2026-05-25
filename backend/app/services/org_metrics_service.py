"""Aggregazioni org-scoped per `GET /orgs/{org_id}/metrics`.

Niente cache: il traffico è già ridotto (per-org, per-utente). **Niente
costi AI** nel payload (scelta di prodotto — vedi memoria
`feedback_no_api_costs_in_org_views`): il service non calcola nemmeno
`SUM(cost_usd)` per non sprecare query.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.avatar import Avatar
from app.models.avatar_clip import AvatarClip
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.models.invitation import Invitation
from app.models.membership import Membership
from app.models.role import OrganizationRole
from app.models.user import User
from app.schemas.admin_metrics import LessonsPhaseBreakdown, StatusCount
from app.schemas.org_metrics import (
    AssigneeWorkload,
    AuditRecentEntry,
    AvatarReadiness,
    CoursesMetrics,
    LessonsMetrics,
    MembersMetrics,
    OrgMetricsOut,
    RoleCount,
)


async def compute_org_metrics(
    db: AsyncSession, *, org_id: uuid.UUID
) -> OrgMetricsOut:
    now = datetime.now(timezone.utc)

    courses_metrics = await _courses(db, org_id=org_id)
    lessons_metrics = await _lessons(db, org_id=org_id)
    modules_total = await _modules_total(db, org_id=org_id)
    members_metrics = await _members(db, org_id=org_id, now=now)
    avatar_readiness = await _avatar_readiness(db, org_id=org_id)
    audit = await _audit_recent(db, org_id=org_id, limit=20)

    return OrgMetricsOut(
        generated_at=now,
        courses=courses_metrics,
        lessons=lessons_metrics,
        modules_total=modules_total,
        members=members_metrics,
        avatar_readiness=avatar_readiness,
        audit_recent=audit,
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

    # Top 10 docenti per numero di corsi assegnati.
    by_assignee_rows = (
        await db.execute(
            select(
                Course.assignee_user_id,
                User.full_name,
                func.count(Course.id).label("course_count"),
            )
            .join(User, Course.assignee_user_id == User.id)
            .where(
                Course.organization_id == org_id,
                Course.assignee_user_id.is_not(None),
            )
            .group_by(Course.assignee_user_id, User.full_name)
            .order_by(func.count(Course.id).desc())
            .limit(10)
        )
    ).all()
    by_assignee = [
        AssigneeWorkload(user_id=uid, name=name, course_count=int(c))
        for uid, name, c in by_assignee_rows
    ]

    return CoursesMetrics(
        total=int(total), by_status=by_status, by_assignee=by_assignee
    )


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


async def _modules_total(db: AsyncSession, *, org_id: uuid.UUID) -> int:
    total = (
        await db.execute(
            select(func.count(CourseModule.id))
            .join(Course, CourseModule.course_id == Course.id)
            .where(Course.organization_id == org_id)
        )
    ).scalar_one()
    return int(total)


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

    by_role_rows = (
        await db.execute(
            select(
                OrganizationRole.code,
                OrganizationRole.name_it,
                func.count(Membership.id),
            )
            .join(Membership, Membership.role_id == OrganizationRole.id)
            .where(Membership.organization_id == org_id)
            .group_by(OrganizationRole.code, OrganizationRole.name_it)
        )
    ).all()
    by_role = [
        RoleCount(role_code=code, role_name_it=name, count=int(c))
        for code, name, c in by_role_rows
    ]

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
        by_role=by_role,
        pending_invitations=int(pending),
    )


async def _avatar_readiness(
    db: AsyncSession, *, org_id: uuid.UUID
) -> AvatarReadiness:
    """Per ogni assignee distinto, conta quanti hanno audio + ≥1 clip ready."""
    assignee_ids = (
        await db.execute(
            select(Course.assignee_user_id)
            .where(
                Course.organization_id == org_id,
                Course.assignee_user_id.is_not(None),
            )
            .distinct()
        )
    ).scalars().all()
    assignee_ids = list(assignee_ids)

    if not assignee_ids:
        return AvatarReadiness(
            total_assignees=0, ready=0, partial=0, not_ready=0
        )

    # has_audio: True se Avatar.audio_path IS NOT NULL.
    audio_rows = (
        await db.execute(
            select(Avatar.user_id, Avatar.audio_path.is_not(None))
            .where(Avatar.user_id.in_(assignee_ids))
        )
    ).all()
    audio_map: dict[uuid.UUID, bool] = {uid: bool(ok) for uid, ok in audio_rows}

    # Conteggio clip ready per Avatar. Outerjoin per restituire 0 anche
    # per avatar senza clip ready.
    clips_rows = (
        await db.execute(
            select(Avatar.user_id, func.count(AvatarClip.id))
            .select_from(Avatar)
            .outerjoin(
                AvatarClip,
                and_(
                    AvatarClip.avatar_id == Avatar.id,
                    AvatarClip.status == "ready",
                ),
            )
            .where(Avatar.user_id.in_(assignee_ids))
            .group_by(Avatar.user_id)
        )
    ).all()
    clips_map: dict[uuid.UUID, int] = {uid: int(c) for uid, c in clips_rows}

    ready = 0
    partial = 0
    not_ready = 0
    for uid in assignee_ids:
        has_audio = audio_map.get(uid, False)
        has_clips = clips_map.get(uid, 0) > 0
        if has_audio and has_clips:
            ready += 1
        elif has_audio or has_clips:
            partial += 1
        else:
            not_ready += 1

    return AvatarReadiness(
        total_assignees=len(assignee_ids),
        ready=ready,
        partial=partial,
        not_ready=not_ready,
    )


async def _audit_recent(
    db: AsyncSession, *, org_id: uuid.UUID, limit: int
) -> list[AuditRecentEntry]:
    rows = (
        await db.execute(
            select(
                AuditLog.id,
                AuditLog.created_at,
                AuditLog.action,
                AuditLog.target_type,
                AuditLog.target_id,
                User.full_name.label("actor_name"),
            )
            .outerjoin(User, AuditLog.actor_user_id == User.id)
            .where(AuditLog.organization_id == org_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        AuditRecentEntry(
            id=r.id,
            created_at=r.created_at,
            action=r.action,
            target_type=r.target_type,
            target_id=r.target_id,
            actor_user_name=r.actor_name,
        )
        for r in rows
    ]
