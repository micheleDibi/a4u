from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import request_id_ctx

HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get(HEADER) or uuid.uuid4().hex
        token = request_id_ctx.set(rid)
        try:
            request.state.request_id = rid
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers[HEADER] = rid
        return response
