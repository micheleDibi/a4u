from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger

log = get_logger("app.access")


def _content_length(response: Response) -> int | None:
    """Byte del corpo da `Content-Length`; `None` se assente o non valido (es. streaming)."""
    raw = response.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        status_code = 500
        response_bytes: int | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            response_bytes = _content_length(response)
            return response
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            log.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=status_code,
                duration_ms=duration_ms,
                response_bytes=response_bytes,
                ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
