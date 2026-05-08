from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, request_id_ctx
from app.models.audit_log import AuditLog

log = get_logger("app.audit")


async def write_audit(
    session: AsyncSession,
    *,
    action: str,
    actor_user_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Inserisce una riga immutabile in audit_logs.
    Non rilancia eccezioni: l'audit non deve mai bloccare l'azione di business."""
    try:
        entry = AuditLog(
            action=action,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            target_type=target_type,
            target_id=target_id,
            payload=metadata or {},
            request_id=request_id_ctx.get(),
            ip=ip,
            user_agent=user_agent,
        )
        session.add(entry)
        await session.flush()
        log.info("audit", action=action, actor=str(actor_user_id) if actor_user_id else None, org=str(organization_id) if organization_id else None)
    except Exception as exc:  # pragma: no cover - defensive
        log.error("audit_write_failed", error=str(exc), action=action)
