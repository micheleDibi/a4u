"""Schemi Pydantic per la dashboard dell'organizzazione
(`GET /orgs/{org_id}/metrics`).

**NIENTE costi AI** nel payload (`cost_usd`, totali token, breakdown per
fase): scelta esplicita di prodotto — i costi sono visibili solo nel
pannello admin platform-wide. Vedi memoria
`feedback_no_api_costs_in_org_views`.

`StatusCount` e `LessonsPhaseBreakdown` sono importati dallo schema
admin per non duplicare la stessa shape.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.admin_metrics import LessonsPhaseBreakdown, StatusCount


class CoursesMetrics(BaseModel):
    total: int
    by_status: list[StatusCount]


class LessonsMetrics(BaseModel):
    total: int
    phases: LessonsPhaseBreakdown


class MembersMetrics(BaseModel):
    total: int
    pending_invitations: int  # accepted_at IS NULL AND not revoked AND not expired


class OrgMetricsOut(BaseModel):
    """Snapshot org-scoped della dashboard organizzazione.

    Niente cache lato service: il traffico è già scoped per-org/utente.
    Niente costi AI: scelta di prodotto.
    """

    generated_at: datetime
    courses: CoursesMetrics
    lessons: LessonsMetrics
    members: MembersMetrics
