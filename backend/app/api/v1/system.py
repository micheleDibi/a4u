from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from sqlalchemy import text

from app.core.deps import DbSession
from app.core.logging import get_logger, request_id_ctx
from app.core.rate_limit import limiter

router = APIRouter(prefix="/system", tags=["system"])
log = get_logger("app.client")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(db: DbSession) -> dict[str, str]:
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}


@router.post("/log-client")
@limiter.limit("60/minute")
async def log_client(
    request: Request,
    payload: Any = Body(...),
) -> dict[str, str]:
    """Inoltro errori dal frontend per centralizzare i log."""
    rid = request_id_ctx.get()
    log.warning("client_event", payload=payload, request_id=rid)
    return {"status": "received"}
