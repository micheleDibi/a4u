"""Endpoint REST di Nova — assistente AI floating widget.

Due endpoint stateless:
- POST /nova/chat — risposta a un messaggio utente, con history + contesto
- POST /nova/welcome — saluto contestuale al primo open del widget

Auth: `CurrentUser` (qualsiasi utente autenticato). Nessun permesso RBAC
specifico — Nova è cross-org e accessibile a tutti i loggati.

Rate limit: settings.nova_rate_limit_per_minute (default 30/min).
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.config import get_settings
from app.core.deps import CurrentUser, DbSession
from app.core.rate_limit import limiter
from app.schemas.nova import (
    NovaChatRequest,
    NovaChatResponse,
    NovaWelcomeRequest,
    NovaWelcomeResponse,
)
from app.services import nova_service

router = APIRouter(prefix="/nova", tags=["nova"])

_settings = get_settings()


@router.post("/chat", response_model=NovaChatResponse)
@limiter.limit(f"{_settings.nova_rate_limit_per_minute}/minute")
async def nova_chat(
    request: Request,  # required dal limiter
    payload: NovaChatRequest,
    db: DbSession,
    current: CurrentUser,
) -> NovaChatResponse:
    message = await nova_service.nova_chat(
        db,
        user_message=payload.message,
        context=payload.context,
        history=payload.history,
        language_code=payload.language_code,
        actor_user_id=current.id,
    )
    return NovaChatResponse(message=message)


@router.post("/welcome", response_model=NovaWelcomeResponse)
@limiter.limit(f"{_settings.nova_rate_limit_per_minute}/minute")
async def nova_welcome(
    request: Request,  # required dal limiter
    payload: NovaWelcomeRequest,
    db: DbSession,
    current: CurrentUser,
) -> NovaWelcomeResponse:
    message = await nova_service.nova_welcome(
        db,
        context=payload.context,
        language_code=payload.language_code,
        actor_user_id=current.id,
    )
    return NovaWelcomeResponse(message=message)
