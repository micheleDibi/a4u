from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.core.deps import DbSession
from app.core.permissions import P, require
from app.schemas.org_metrics import OrgMetricsOut
from app.services import org_metrics_service

router = APIRouter(prefix="/orgs", tags=["org-metrics"])


@router.get("/{org_id}/metrics", response_model=OrgMetricsOut)
async def read_org_metrics(
    org_id: uuid.UUID,
    db: DbSession,
    _=require(P.COURSE_VIEW),
) -> OrgMetricsOut:
    """Snapshot di metriche org-scoped per la dashboard organizzazione.

    Gate: `course:view` (qualsiasi membership che possa vedere i corsi).
    Niente cache server-side (org-scoped, traffico già ridotto). Il
    payload **non** contiene costi AI per scelta di prodotto.
    """
    return await org_metrics_service.compute_org_metrics(db, org_id=org_id)
