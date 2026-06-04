from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response
from sqlalchemy import func, select

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.deps import CurrentUser, DbSession
from app.core.errors import AuthenticationError, ConflictError, ValidationAppError
from app.core.permissions import resolve_permissions
from app.core.rate_limit import limiter
from app.core.security import hash_password, verify_password
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.role import OrganizationRole
from app.models.user import User
from app.schemas.auth import (
    ChangeEmailRequest,
    ChangePasswordRequest,
    LoginRequest,
    ProfileUpdate,
)
from app.schemas.user import MeOrganizationOut, MeOut, UserOut
from app.services.auth_service import login, revoke_refresh_token, rotate_refresh

router = APIRouter(prefix="/auth", tags=["auth"])


async def _build_me(db: DbSession, user: User) -> MeOut:
    """Costruisce il payload `MeOut` (utente + organizzazioni + permessi).
    Condiviso da GET /auth/me e dalle rotte self-service."""
    rows = (
        await db.execute(
            select(Membership, Organization, OrganizationRole)
            .join(Organization, Organization.id == Membership.organization_id)
            .join(OrganizationRole, OrganizationRole.id == Membership.role_id)
            .where(Membership.user_id == user.id, Organization.deleted_at.is_(None))
        )
    ).all()

    orgs: list[MeOrganizationOut] = []
    for membership, organization, role in rows:
        permissions = await resolve_permissions(
            db, user=user, organization_id=organization.id
        )
        orgs.append(
            MeOrganizationOut(
                organization_id=organization.id,
                organization_name=organization.name,
                role_code=role.code,
                role_name_it=role.name_it,
                permissions=sorted(permissions),
            )
        )

    return MeOut(
        user=UserOut.model_validate(user),
        organizations=orgs,
        is_platform_admin=user.is_platform_admin,
    )


def _set_auth_cookies(response: Response, *, access: str, refresh: str) -> None:
    settings = get_settings()
    common = {
        "httponly": True,
        "samesite": "lax",
        "secure": settings.cookie_secure,
        "domain": settings.cookie_domain,
    }
    response.set_cookie(
        "access_token",
        access,
        max_age=settings.access_token_ttl_seconds,
        path="/",
        **common,
    )
    response.set_cookie(
        "refresh_token",
        refresh,
        max_age=settings.refresh_token_ttl_seconds,
        path="/api/v1/auth/refresh",
        **common,
    )


def _clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie("access_token", path="/", domain=settings.cookie_domain)
    response.delete_cookie(
        "refresh_token", path="/api/v1/auth/refresh", domain=settings.cookie_domain
    )


@router.post("/login")
@limiter.limit("5/minute")
async def login_endpoint(
    request: Request,
    payload: LoginRequest,
    response: Response,
    db: DbSession,
) -> dict[str, str]:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    user, access, refresh = await login(
        db, email=payload.email, password=payload.password, ip=ip, user_agent=ua
    )
    _set_auth_cookies(response, access=access, refresh=refresh)
    return {"status": "ok", "user_id": str(user.id)}


@router.post("/refresh")
@limiter.limit("30/minute")
async def refresh_endpoint(
    request: Request,
    response: Response,
    db: DbSession,
    refresh_token: Annotated[str | None, Cookie(alias="refresh_token")] = None,
) -> dict[str, str]:
    if not refresh_token:
        # rispondi 401 con corpo coerente
        response.status_code = 401
        return {"status": "missing_refresh"}
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    user, access, new_refresh = await rotate_refresh(
        db, refresh_token=refresh_token, ip=ip, user_agent=ua
    )
    _set_auth_cookies(response, access=access, refresh=new_refresh)
    return {"status": "ok", "user_id": str(user.id)}


@router.post("/logout")
async def logout_endpoint(
    request: Request,
    response: Response,
    db: DbSession,
    refresh_token: Annotated[str | None, Cookie(alias="refresh_token")] = None,
) -> dict[str, str]:
    ip = request.client.host if request.client else None
    await revoke_refresh_token(db, refresh_token=refresh_token, ip=ip)
    _clear_auth_cookies(response)
    return {"status": "ok"}


@router.get("/me", response_model=MeOut)
async def me_endpoint(user: CurrentUser, db: DbSession) -> MeOut:
    return await _build_me(db, user)


@router.patch("/me", response_model=MeOut)
async def update_profile(
    payload: ProfileUpdate, request: Request, user: CurrentUser, db: DbSession
) -> MeOut:
    """Modifica del proprio nome. Nessuna re-auth (azione a basso rischio)."""
    user.full_name = payload.full_name.strip()
    await db.flush()
    await write_audit(
        db,
        action="user.profile.update",
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return await _build_me(db, user)


@router.post("/me/change-email", response_model=MeOut)
async def change_email(
    payload: ChangeEmailRequest, request: Request, user: CurrentUser, db: DbSession
) -> MeOut:
    """Cambio email: richiede la password attuale. Le sessioni restano
    valide (l'email è un identificatore, non una credenziale)."""
    if not verify_password(payload.current_password, user.password_hash):
        raise AuthenticationError(
            "Password attuale non corretta.", code="invalid_current_password"
        )
    normalized = payload.new_email.lower().strip()
    if normalized == user.email.lower():
        raise ValidationAppError(
            "La nuova email coincide con quella attuale.", code="email_unchanged"
        )
    clash = (
        await db.execute(
            select(User).where(func.lower(User.email) == normalized, User.id != user.id)
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise ConflictError("Email già in uso.", code="email_in_use")
    old_email = user.email
    user.email = normalized
    await db.flush()
    await write_audit(
        db,
        action="user.email.change",
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        metadata={"old": old_email, "new": normalized},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return await _build_me(db, user)


@router.post("/me/change-password", response_model=MeOut)
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    user: CurrentUser,
    db: DbSession,
) -> MeOut:
    """Cambio password self-service: richiede la password attuale. NON
    revoca le proprie sessioni (UX amichevole, l'utente resta loggato sul
    device corrente) — asimmetria voluta rispetto al reset lato admin, che
    invece forza il re-login perché implica un possibile compromesso."""
    if not verify_password(payload.current_password, user.password_hash):
        raise AuthenticationError(
            "Password attuale non corretta.", code="invalid_current_password"
        )
    if payload.new_password == payload.current_password:
        raise ValidationAppError(
            "La nuova password coincide con quella attuale.",
            code="password_unchanged",
        )
    user.password_hash = hash_password(payload.new_password)
    await db.flush()
    await write_audit(
        db,
        action="user.password.change",
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return await _build_me(db, user)
