from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class CsrfOriginMiddleware(BaseHTTPMiddleware):
    """Difesa CSRF leggera per richieste cookie-based:
    su metodi mutating verifica che `Origin` (o `Referer`) coincida con il frontend autorizzato."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method.upper() in UNSAFE_METHODS:
            settings = get_settings()
            allowed = settings.frontend_origin.rstrip("/")
            origin = (request.headers.get("origin") or "").rstrip("/")
            referer = (request.headers.get("referer") or "")
            ok = False
            if origin and origin == allowed:
                ok = True
            elif referer.startswith(allowed):
                ok = True
            elif not origin and not referer:
                # client non-browser (CLI / test): consentito solo se autenticato via Bearer
                if request.headers.get("authorization", "").startswith("Bearer "):
                    ok = True
            if not ok:
                return JSONResponse(
                    status_code=403,
                    content={"code": "csrf_origin_invalid", "message": "Origine richiesta non autorizzata."},
                )
        return await call_next(request)
