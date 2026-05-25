"""Schemi Pydantic per la dashboard del pannello admin (`GET /admin/metrics`).

I conteggi per status (`by_status`) sono restituiti **raw** — il bucketing
in macro-fasi (es. accorpare `*_pending`/`*_ready`/`*_approved` di Fase 3
sotto un'unica voce "Contenuti") avviene lato frontend, così le label
restano localizzate e il bucketing è cambiabile senza migrazione backend.

Snapshot cached server-side TTL 60s in `admin_metrics_service`.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class StatusCount(BaseModel):
    """Conteggio per uno status (course.status, course_lesson.*_status,
    avatar_clips.status, ecc.)."""

    status: str
    count: int


class UsersMetrics(BaseModel):
    total: int
    active: int  # users.is_active = true
    active_last_30d: int  # users.last_login_at >= now() - 30d


class OrgsMetrics(BaseModel):
    total: int  # organizations WHERE deleted_at IS NULL


class CoursesMetrics(BaseModel):
    total: int
    by_status: list[StatusCount]  # 17 valori possibili (vedi 01-data-model)


class LessonsPhaseBreakdown(BaseModel):
    """Conteggi per ciascuna delle 5 fasi pipeline per-lezione."""

    content: list[StatusCount]
    slides: list[StatusCount]
    speech: list[StatusCount]
    video: list[StatusCount]
    avatar_video: list[StatusCount]


class LessonsMetrics(BaseModel):
    total: int
    phases: LessonsPhaseBreakdown


class CostByPhase(BaseModel):
    """Costo OpenAI cumulato per una fase della pipeline AI corsi.

    Nota: `glossary_tokens` usa lo schema vecchio (`{model, prompt,
    completion, total}` senza `cost_usd`), quindi non compare in
    `by_phase`.
    """

    phase: str  # architecture | structure | content | slides | speech
    cost_usd: float


class CostMetrics(BaseModel):
    total_usd: float
    last_7d_usd: float
    last_30d_usd: float
    by_phase: list[CostByPhase]


class AvatarClipsMetrics(BaseModel):
    by_status: list[StatusCount]  # pending|processing|ready|failed


class LoginDayMetric(BaseModel):
    date: str  # YYYY-MM-DD (UTC)
    success: int
    failure: int


class LoginActivityMetrics(BaseModel):
    last_7d: list[LoginDayMetric]  # esattamente 7 entries, ordine cronologico
    success_total_7d: int
    failure_total_7d: int


class AuditRecentEntry(BaseModel):
    id: uuid.UUID
    created_at: datetime
    action: str
    actor_user_name: str | None = None
    organization_name: str | None = None
    target_type: str | None = None
    target_id: str | None = None


class AdminMetricsOut(BaseModel):
    """Snapshot di metriche platform-wide per la dashboard admin.

    Cache TTL 60s lato service: non realtime, ma sufficiente per una
    dashboard informativa.
    """

    generated_at: datetime
    users: UsersMetrics
    orgs: OrgsMetrics
    courses: CoursesMetrics
    lessons: LessonsMetrics
    cost: CostMetrics
    avatar_clips: AvatarClipsMetrics
    login_activity: LoginActivityMetrics
    audit_recent: list[AuditRecentEntry] = Field(default_factory=list)
