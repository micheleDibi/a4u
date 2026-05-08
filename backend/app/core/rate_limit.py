from __future__ import annotations

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse


limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    from app.core.logging import get_logger, request_id_ctx

    get_logger("app.rate_limit").warning(
        "rate_limited",
        path=request.url.path,
        ip=get_remote_address(request),
        limit=str(exc.detail),
    )
    body = {"code": "rate_limited", "message": "Troppe richieste, riprova più tardi."}
    rid = request_id_ctx.get()
    if rid:
        body["request_id"] = rid
    return JSONResponse(status_code=429, content=body)
