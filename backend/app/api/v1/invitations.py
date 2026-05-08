from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request

from app.core.config import get_settings
from app.core.deps import CurrentUser, DbSession
from app.core.permissions import P, require
from app.schemas.auth import InvitationAcceptRequest
from app.schemas.membership import (
    InvitationCreateRequest,
    InvitationCreateResponse,
    InvitationOut,
)
from app.services import invitation_service
from app.services.membership_service import get_role_by_code

router = APIRouter(tags=["invitations"])


@router.post(
    "/orgs/{org_id}/invitations",
    response_model=InvitationCreateResponse,
    status_code=201,
)
async def create_invitation(
    org_id: uuid.UUID,
    payload: InvitationCreateRequest,
    db: DbSession,
    current: CurrentUser,
    _=require(P.MEMBER_INVITE),
) -> InvitationCreateResponse:
    invitation, token = await invitation_service.create_invitation(
        db,
        organization_id=org_id,
        email=payload.email,
        role_code=payload.role_code,
        actor_id=current.id,
    )
    role = await get_role_by_code(db, payload.role_code)
    settings = get_settings()
    accept_url = f"{settings.frontend_origin.rstrip('/')}/invitations/{token}"
    return InvitationCreateResponse(
        invitation=InvitationOut(
            id=invitation.id,
            organization_id=invitation.organization_id,
            email=invitation.email,
            role_code=role.code,
            expires_at=invitation.expires_at,
            accepted_at=invitation.accepted_at,
        ),
        token=token,
        accept_url=accept_url,
    )


@router.get("/invitations/{token}/preview")
async def preview_invitation(token: str, db: DbSession) -> dict[str, Any]:
    """Rende disponibili al frontend i dati base dell'invito (org name, email, role) prima dell'accept."""
    from app.core.security import hash_secret
    from app.models.invitation import Invitation
    from app.models.organization import Organization
    from app.models.role import OrganizationRole
    from app.models.user import User
    from sqlalchemy import select

    inv = (
        await db.execute(
            select(Invitation, Organization, OrganizationRole)
            .join(Organization, Organization.id == Invitation.organization_id)
            .join(OrganizationRole, OrganizationRole.id == Invitation.role_id)
            .where(Invitation.token_hash == hash_secret(token))
        )
    ).first()
    if inv is None:
        return {"valid": False}
    invitation, org, role = inv
    user_exists = (
        await db.execute(select(User).where(User.email == invitation.email))
    ).scalar_one_or_none()
    return {
        "valid": invitation.accepted_at is None and invitation.revoked_at is None,
        "organization_name": org.name,
        "email": invitation.email,
        "role_name_it": role.name_it,
        "user_exists": bool(user_exists),
        "expires_at": invitation.expires_at.isoformat(),
    }


@router.post("/invitations/{token}/accept")
async def accept_invitation_endpoint(
    token: str,
    payload: InvitationAcceptRequest,
    db: DbSession,
    request: Request,
) -> dict[str, str]:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    user, membership = await invitation_service.accept_invitation(
        db,
        token=token,
        full_name=payload.full_name,
        password=payload.password,
        ip=ip,
        user_agent=ua,
    )
    return {
        "status": "ok",
        "user_id": str(user.id),
        "membership_id": str(membership.id),
        "organization_id": str(membership.organization_id),
    }
