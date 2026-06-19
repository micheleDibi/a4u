from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError
from app.core.permissions import P, R, require
from app.models.avatar import Avatar
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.role import OrganizationRole
from app.models.user import User
from app.schemas.avatar import AvatarOut
from app.schemas.membership import (
    ChangeRoleRequest,
    MembershipOut,
    PermissionOverridesUpdate,
    TransferCreatorRequest,
)
from app.services import avatar_service, membership_service, permission_service

router = APIRouter(prefix="/orgs/{org_id}", tags=["organizations"])


@router.get("/members", response_model=list[MembershipOut])
async def list_members(
    org_id: uuid.UUID, db: DbSession, granted=require(P.MEMBER_VIEW)
) -> list[MembershipOut]:
    org = await db.get(Organization, org_id)
    if org is None or org.deleted_at is not None:
        raise NotFoundError("Organizzazione non trovata.", code="organization_not_found")

    # Lo stato avatar è incluso solo per chi può vederlo (no leak ai ruoli
    # che hanno `member:view` ma non `member:avatar:view`).
    include_avatar = P.MEMBER_AVATAR_VIEW in granted

    rows = (
        await db.execute(
            select(
                Membership,
                User,
                OrganizationRole,
                Avatar.clips_status,
                Avatar.audio_path,
            )
            .join(User, User.id == Membership.user_id)
            .join(OrganizationRole, OrganizationRole.id == Membership.role_id)
            .outerjoin(Avatar, Avatar.user_id == Membership.user_id)
            .where(Membership.organization_id == org_id)
            .order_by(OrganizationRole.rank.asc(), User.full_name.asc())
        )
    ).all()
    return [
        MembershipOut(
            id=m.id,
            user_id=u.id,
            user_email=u.email,
            user_full_name=u.full_name,
            organization_id=m.organization_id,
            role_id=m.role_id,
            role_code=r.code,
            role_name_it=r.name_it,
            joined_at=m.joined_at,
            avatar_status=clips_status if include_avatar else None,
            avatar_audio=bool(include_avatar and audio_path),
        )
        for m, u, r, clips_status, audio_path in rows
    ]


@router.get("/members/{user_id}/avatar", response_model=AvatarOut | None)
async def get_member_avatar(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    db: DbSession,
    _=require(P.MEMBER_AVATAR_VIEW),
) -> AvatarOut | None:
    """Avatar (clip + audio) di un membro dell'org, in sola lettura.

    Verifica che l'utente target sia membro dell'organizzazione (404
    altrimenti): l'avatar è legato all'utente, non all'org.
    """
    await _get_membership_or_404(db, org_id=org_id, user_id=user_id)
    avatar = await avatar_service.get_my_avatar(db, user_id)
    if avatar is None:
        return None
    return AvatarOut.model_validate(avatar)


async def _get_membership_or_404(
    db, *, org_id: uuid.UUID, user_id: uuid.UUID
) -> Membership:
    membership = (
        await db.execute(
            select(Membership).where(
                Membership.organization_id == org_id, Membership.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFoundError("Membership non trovata.", code="membership_not_found")
    return membership


@router.put("/members/{user_id}/role", response_model=MembershipOut)
async def change_member_role(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: ChangeRoleRequest,
    db: DbSession,
    current: CurrentUser,
    _=require(P.MEMBER_ASSIGN_ROLE),
) -> MembershipOut:
    membership = await _get_membership_or_404(db, org_id=org_id, user_id=user_id)
    actor_membership = (
        await db.execute(
            select(Membership).where(
                Membership.organization_id == org_id, Membership.user_id == current.id
            )
        )
    ).scalar_one_or_none()
    membership = await membership_service.change_role(
        db,
        membership=membership,
        new_role_code=payload.role_code,
        actor_user=current,
        actor_membership=actor_membership,
    )
    user = await db.get(User, membership.user_id)
    role = await db.get(OrganizationRole, membership.role_id)
    return MembershipOut(
        id=membership.id,
        user_id=membership.user_id,
        user_email=user.email,  # type: ignore[union-attr]
        user_full_name=user.full_name,  # type: ignore[union-attr]
        organization_id=membership.organization_id,
        role_id=membership.role_id,
        role_code=role.code,  # type: ignore[union-attr]
        role_name_it=role.name_it,  # type: ignore[union-attr]
        joined_at=membership.joined_at,
    )


@router.delete("/members/{user_id}", status_code=204)
async def remove_member(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    db: DbSession,
    current: CurrentUser,
    _=require(P.MEMBER_REMOVE),
) -> None:
    membership = await _get_membership_or_404(db, org_id=org_id, user_id=user_id)
    await membership_service.remove_membership(db, membership=membership, actor_user=current)


@router.get("/members/{user_id}/permissions")
async def get_member_permissions(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    db: DbSession,
    _=require(P.PERMISSION_MANAGE),
) -> dict[str, object]:
    membership = await _get_membership_or_404(db, org_id=org_id, user_id=user_id)
    overrides = await permission_service.list_membership_overrides(
        db, membership_id=membership.id
    )
    return {
        "membership_id": str(membership.id),
        "overrides": [o.model_dump() for o in overrides],
    }


@router.put("/members/{user_id}/permissions")
async def update_member_permissions(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: PermissionOverridesUpdate,
    db: DbSession,
    current: CurrentUser,
    _=require(P.PERMISSION_MANAGE),
) -> dict[str, str]:
    membership = await _get_membership_or_404(db, org_id=org_id, user_id=user_id)
    await permission_service.upsert_membership_permissions(
        db,
        membership=membership,
        overrides=payload.overrides,
        actor_id=current.id,
    )
    return {"status": "ok"}


@router.get("/permissions/role/{role_code}")
async def get_org_role_permissions(
    org_id: uuid.UUID,
    role_code: str,
    db: DbSession,
    _=require(P.PERMISSION_MANAGE),
) -> dict[str, object]:
    overrides = await permission_service.list_organization_role_overrides(
        db, organization_id=org_id, role_code=role_code
    )
    defaults = await permission_service.get_role_default_permissions(db, role_code=role_code)
    return {
        "role_code": role_code,
        "defaults": defaults,
        "overrides": [o.model_dump() for o in overrides],
    }


@router.put("/permissions/role/{role_code}")
async def update_org_role_permissions(
    org_id: uuid.UUID,
    role_code: str,
    payload: PermissionOverridesUpdate,
    db: DbSession,
    current: CurrentUser,
    _=require(P.PERMISSION_MANAGE),
) -> dict[str, str]:
    await permission_service.upsert_org_role_permissions(
        db,
        organization_id=org_id,
        role_code=role_code,
        overrides=payload.overrides,
        actor_id=current.id,
    )
    return {"status": "ok"}


@router.post("/transfer-creator")
async def transfer_creator_endpoint(
    org_id: uuid.UUID,
    payload: TransferCreatorRequest,
    db: DbSession,
    current: CurrentUser,
    _=require(P.ORG_TRANSFER_CREATOR),
) -> dict[str, str]:
    actor_membership = (
        await db.execute(
            select(Membership).where(
                Membership.organization_id == org_id, Membership.user_id == current.id
            )
        )
    ).scalar_one_or_none()
    if actor_membership is None and not current.is_platform_admin:
        raise NotFoundError("Membership non trovata.", code="membership_not_found")
    # platform admin trasferisce: dobbiamo trovare il creator corrente come actor
    if current.is_platform_admin and actor_membership is None:
        # cerca il creator dell'org
        actor_membership = (
            await db.execute(
                select(Membership)
                .join(OrganizationRole, OrganizationRole.id == Membership.role_id)
                .where(
                    Membership.organization_id == org_id,
                    OrganizationRole.code == R.CREATOR,
                )
            )
        ).scalar_one_or_none()
        if actor_membership is None:
            raise NotFoundError(
                "L'organizzazione non ha un creator.", code="creator_missing"
            )
    await membership_service.transfer_creator(
        db,
        organization_id=org_id,
        actor_user=current,
        actor_membership=actor_membership,  # type: ignore[arg-type]
        target_user_id=payload.target_user_id,
    )
    return {"status": "ok"}
