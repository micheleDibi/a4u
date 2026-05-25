"""Aggregazioni platform-wide per `GET /admin/metrics` (dashboard admin).

Cache in-memory TTL 60s con `asyncio.Lock` per evitare thundering herd al
primo hit dopo scadenza. Non c'è persistenza: la dashboard è informativa,
una staleness < 60s è accettabile e ci risparmia decine di query a ogni
refresh del browser.

Costi (`*_tokens.cost_usd`): aggregati dalle 5 fasi AI corsi che usano lo
schema arricchito (`build_usage_dict` in `openai_pricing.py`). Il glossario
(`Course.glossary_tokens`) usa lo schema vecchio senza `cost_usd` ed è
escluso dalla somma per non mostrare sempre "Glossary: $0".
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.avatar_clip import AvatarClip
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.course_module import CourseModule
from app.models.login_attempt import LoginAttempt
from app.models.organization import Organization
from app.models.user import User
from app.schemas.admin_metrics import (
    AdminMetricsOut,
    AuditRecentEntry,
    AvatarClipsMetrics,
    CostByPhase,
    CostMetrics,
    CoursesMetrics,
    LessonsMetrics,
    LessonsPhaseBreakdown,
    LoginActivityMetrics,
    LoginDayMetric,
    OrgsMetrics,
    StatusCount,
    UsersMetrics,
)


_CACHE_TTL_S = 60.0
_cache: tuple[float, AdminMetricsOut] | None = None
_cache_lock = asyncio.Lock()


async def get_admin_metrics(db: AsyncSession) -> AdminMetricsOut:
    """Restituisce le metriche admin. Cache TTL 60s."""
    global _cache
    async with _cache_lock:
        if _cache is not None and (time.monotonic() - _cache[0]) < _CACHE_TTL_S:
            return _cache[1]
    value = await _compute(db)
    async with _cache_lock:
        _cache = (time.monotonic(), value)
    return value


def invalidate_cache() -> None:
    """Forza il refresh al prossimo `get_admin_metrics`. Per test."""
    global _cache
    _cache = None


# ---------------------------------------------------------------------------
# Compute end-to-end
# ---------------------------------------------------------------------------


async def _compute(db: AsyncSession) -> AdminMetricsOut:
    now = datetime.now(timezone.utc)
    cutoff_7d = now - timedelta(days=7)
    cutoff_30d = now - timedelta(days=30)

    users_metrics = await _users(db, cutoff_30d=cutoff_30d)
    orgs_metrics = await _orgs(db)
    courses_metrics = await _courses(db)
    lessons_metrics = await _lessons(db)
    cost_metrics = await _cost(db, cutoff_7d=cutoff_7d, cutoff_30d=cutoff_30d)
    avatar_clips_metrics = await _avatar_clips(db)
    login_metrics = await _login_activity(db, cutoff_7d=cutoff_7d)
    audit = await _audit_recent(db, limit=20)

    return AdminMetricsOut(
        generated_at=now,
        users=users_metrics,
        orgs=orgs_metrics,
        courses=courses_metrics,
        lessons=lessons_metrics,
        cost=cost_metrics,
        avatar_clips=avatar_clips_metrics,
        login_activity=login_metrics,
        audit_recent=audit,
    )


# ---------------------------------------------------------------------------
# Sub-aggregations
# ---------------------------------------------------------------------------


async def _users(db: AsyncSession, *, cutoff_30d: datetime) -> UsersMetrics:
    total = (await db.execute(select(func.count(User.id)))).scalar_one()
    active = (
        await db.execute(
            select(func.count(User.id)).where(User.is_active.is_(True))
        )
    ).scalar_one()
    active_30d = (
        await db.execute(
            select(func.count(User.id)).where(
                User.last_login_at.is_not(None),
                User.last_login_at >= cutoff_30d,
            )
        )
    ).scalar_one()
    return UsersMetrics(
        total=int(total), active=int(active), active_last_30d=int(active_30d)
    )


async def _orgs(db: AsyncSession) -> OrgsMetrics:
    total = (
        await db.execute(
            select(func.count(Organization.id)).where(
                Organization.deleted_at.is_(None)
            )
        )
    ).scalar_one()
    return OrgsMetrics(total=int(total))


async def _courses(db: AsyncSession) -> CoursesMetrics:
    total = (await db.execute(select(func.count(Course.id)))).scalar_one()
    rows = (
        await db.execute(
            select(Course.status, func.count(Course.id)).group_by(Course.status)
        )
    ).all()
    return CoursesMetrics(
        total=int(total),
        by_status=[StatusCount(status=s, count=int(c)) for s, c in rows],
    )


async def _by_lesson_status(db: AsyncSession, col) -> list[StatusCount]:
    rows = (
        await db.execute(
            select(col, func.count(CourseLesson.id)).group_by(col)
        )
    ).all()
    return [StatusCount(status=s, count=int(c)) for s, c in rows]


async def _lessons(db: AsyncSession) -> LessonsMetrics:
    total = (await db.execute(select(func.count(CourseLesson.id)))).scalar_one()
    phases = LessonsPhaseBreakdown(
        content=await _by_lesson_status(db, CourseLesson.content_status),
        slides=await _by_lesson_status(db, CourseLesson.slides_status),
        speech=await _by_lesson_status(db, CourseLesson.speech_status),
        video=await _by_lesson_status(db, CourseLesson.video_status),
        avatar_video=await _by_lesson_status(
            db, CourseLesson.avatar_video_status
        ),
    )
    return LessonsMetrics(total=int(total), phases=phases)


async def _sum_cost(
    db: AsyncSession,
    *,
    jsonb_col,
    generated_at_col,
    cutoff_7d: datetime,
    cutoff_30d: datetime,
) -> tuple[float, float, float]:
    """Restituisce `(total, last_7d, last_30d)` di
    `(jsonb_col->>'cost_usd')::float` sulla tabella implicita di
    `jsonb_col`. Le finestre sono filtrate via `generated_at_col`.
    Record con `cost_usd` NULL/mancante non contribuiscono.
    """
    cost_expr = cast(jsonb_col["cost_usd"].astext, Float)
    total_expr = func.coalesce(func.sum(cost_expr), 0.0)
    last_7d_expr = func.coalesce(
        func.sum(case((generated_at_col >= cutoff_7d, cost_expr), else_=None)),
        0.0,
    )
    last_30d_expr = func.coalesce(
        func.sum(case((generated_at_col >= cutoff_30d, cost_expr), else_=None)),
        0.0,
    )
    row = (
        await db.execute(select(total_expr, last_7d_expr, last_30d_expr))
    ).one()
    return float(row[0]), float(row[1]), float(row[2])


async def _cost(
    db: AsyncSession, *, cutoff_7d: datetime, cutoff_30d: datetime
) -> CostMetrics:
    # 5 sorgenti di costo (Glossary usa schema vecchio senza cost_usd).
    sources: list[tuple[str, object, object]] = [
        ("architecture", Course.architecture_tokens, Course.architecture_generated_at),
        (
            "structure",
            CourseModule.lessons_structure_tokens,
            CourseModule.lessons_structure_generated_at,
        ),
        ("content", CourseLesson.content_tokens, CourseLesson.content_generated_at),
        ("slides", CourseLesson.slides_tokens, CourseLesson.slides_generated_at),
        ("speech", CourseLesson.speech_tokens, CourseLesson.speech_generated_at),
    ]
    by_phase: list[CostByPhase] = []
    total_usd = 0.0
    last_7d_usd = 0.0
    last_30d_usd = 0.0
    for phase, jsonb_col, gen_at_col in sources:
        t, d7, d30 = await _sum_cost(
            db,
            jsonb_col=jsonb_col,
            generated_at_col=gen_at_col,
            cutoff_7d=cutoff_7d,
            cutoff_30d=cutoff_30d,
        )
        by_phase.append(CostByPhase(phase=phase, cost_usd=t))
        total_usd += t
        last_7d_usd += d7
        last_30d_usd += d30
    return CostMetrics(
        total_usd=total_usd,
        last_7d_usd=last_7d_usd,
        last_30d_usd=last_30d_usd,
        by_phase=by_phase,
    )


async def _avatar_clips(db: AsyncSession) -> AvatarClipsMetrics:
    rows = (
        await db.execute(
            select(AvatarClip.status, func.count(AvatarClip.id)).group_by(
                AvatarClip.status
            )
        )
    ).all()
    return AvatarClipsMetrics(
        by_status=[StatusCount(status=s, count=int(c)) for s, c in rows]
    )


async def _login_activity(
    db: AsyncSession, *, cutoff_7d: datetime
) -> LoginActivityMetrics:
    """Bucket per giorno UTC, sempre 7 entries (zero-fill)."""
    day = func.date_trunc("day", LoginAttempt.created_at).label("day")
    rows = (
        await db.execute(
            select(day, LoginAttempt.success, func.count(LoginAttempt.id))
            .where(LoginAttempt.created_at >= cutoff_7d)
            .group_by(day, LoginAttempt.success)
            .order_by(day)
        )
    ).all()

    today_utc = datetime.now(timezone.utc).date()
    grid: dict[str, dict[str, int]] = {}
    for i in range(7):
        d = (today_utc - timedelta(days=6 - i)).isoformat()
        grid[d] = {"success": 0, "failure": 0}

    for day_dt, success_flag, count in rows:
        # day_dt è un datetime tz-aware (date_trunc preserva tz).
        key = day_dt.date().isoformat() if hasattr(day_dt, "date") else str(day_dt)[:10]
        if key not in grid:
            continue
        slot = grid[key]
        if success_flag:
            slot["success"] += int(count)
        else:
            slot["failure"] += int(count)

    last_7d = [
        LoginDayMetric(date=k, success=v["success"], failure=v["failure"])
        for k, v in grid.items()
    ]
    return LoginActivityMetrics(
        last_7d=last_7d,
        success_total_7d=sum(d.success for d in last_7d),
        failure_total_7d=sum(d.failure for d in last_7d),
    )


async def _audit_recent(
    db: AsyncSession, *, limit: int
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
                Organization.name.label("org_name"),
            )
            .outerjoin(User, AuditLog.actor_user_id == User.id)
            .outerjoin(Organization, AuditLog.organization_id == Organization.id)
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
            organization_name=r.org_name,
        )
        for r in rows
    ]
