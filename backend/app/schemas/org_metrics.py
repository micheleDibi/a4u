"""Schemi Pydantic per la dashboard dell'organizzazione
(`GET /orgs/{org_id}/metrics`).

**NIENTE costi AI** nel payload (`cost_usd`, totali token, breakdown per
fase): è una scelta esplicita di prodotto — i costi sono visibili solo
nel pannello admin platform-wide. Vedi memoria
`feedback_no_api_costs_in_org_views`.

`StatusCount` e `LessonsPhaseBreakdown` sono importati dallo schema
admin per non duplicare la stessa shape.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.admin_metrics import LessonsPhaseBreakdown, StatusCount


class AssigneeWorkload(BaseModel):
    """Carico di lavoro di un docente: numero di corsi a lui assegnati."""

    user_id: uuid.UUID
    name: str | None = None
    course_count: int


class RoleCount(BaseModel):
    role_code: str  # creator | org_admin | manager | member
    role_name_it: str | None = None
    count: int


class AvatarReadiness(BaseModel):
    """Stato avatar dei docenti assegnati a qualche corso dell'org.

    - `ready`: ha `audio_path` valorizzato AND almeno una clip MiniMax in
      stato `ready` → la generazione video lezione (Fase 6/6b) può
      partire.
    - `partial`: ha solo uno dei due (manca audio O mancano clip).
    - `not_ready`: non ha avatar oppure non ha né audio né clip pronte.
    """

    total_assignees: int
    ready: int
    partial: int
    not_ready: int


class CoursesMetrics(BaseModel):
    total: int
    by_status: list[StatusCount]
    by_assignee: list[AssigneeWorkload]  # top 10 per workload


class LessonsMetrics(BaseModel):
    total: int
    phases: LessonsPhaseBreakdown


class MembersMetrics(BaseModel):
    total: int
    by_role: list[RoleCount]
    pending_invitations: int  # accepted_at IS NULL AND not revoked AND not expired


class AuditRecentEntry(BaseModel):
    id: uuid.UUID
    created_at: datetime
    action: str
    actor_user_name: str | None = None
    target_type: str | None = None
    target_id: str | None = None


class OrgMetricsOut(BaseModel):
    """Snapshot org-scoped della dashboard organizzazione.

    Niente cache lato service: il traffico è già scoped per-org/utente.
    Niente costi AI: scelta di prodotto.
    """

    generated_at: datetime
    courses: CoursesMetrics
    lessons: LessonsMetrics
    modules_total: int
    members: MembersMetrics
    avatar_readiness: AvatarReadiness
    audit_recent: list[AuditRecentEntry] = Field(default_factory=list)
