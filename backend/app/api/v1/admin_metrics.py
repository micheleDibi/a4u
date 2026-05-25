from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import DbSession, PlatformAdmin
from app.schemas.admin_metrics import AdminMetricsOut
from app.services import admin_metrics_service

router = APIRouter(prefix="/admin", tags=["admin-metrics"])


@router.get("/metrics", response_model=AdminMetricsOut)
async def read_admin_metrics(
    db: DbSession, _: PlatformAdmin
) -> AdminMetricsOut:
    """Snapshot di metriche platform-wide per la dashboard admin.

    Cache lato service TTL 60s (non realtime). Richiede
    `is_platform_admin=True`.
    """
    return await admin_metrics_service.get_admin_metrics(db)
