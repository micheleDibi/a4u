from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, DbSession
from app.core.errors import PermissionDeniedError
from app.models.membership import Membership, MembershipPermissionOverride
from app.models.permission import (
    OrganizationRolePermission,
    Permission,
    RolePermission,
)
from app.models.role import OrganizationRole
from app.models.user import User


# === Codici permessi (mirror in frontend/src/lib/permissions.ts) ===
class P:
    MEMBER_VIEW = "member:view"
    MEMBER_INVITE = "member:invite"
    MEMBER_ASSIGN_ROLE = "member:assign_role"
    MEMBER_REMOVE = "member:remove"
    MEMBER_AVATAR_VIEW = "member:avatar:view"
    TEMPLATE_SLIDE_MANAGE = "template:slide:manage"
    TEMPLATE_PDF_MANAGE = "template:pdf:manage"
    PERMISSION_MANAGE = "permission:manage"
    ORG_TRANSFER_CREATOR = "org:transfer_creator"
    ORG_UPDATE = "org:update"
    COURSE_CONFIG_MANAGE = "course_config:manage"
    COURSE_VIEW = "course:view"
    COURSE_VIEW_ALL = "course:view_all"
    COURSE_CREATE = "course:create"
    COURSE_ASSIGN = "course:assign"
    COURSE_EDIT = "course:edit"
    COURSE_DELETE = "course:delete"
    COURSE_GENERATE = "course:generate"
    COURSE_SAVE_DRAFT = "course:save_draft"
    COURSE_DUPLICATE = "course:duplicate"


ALL_PERMISSION_CODES: tuple[str, ...] = (
    P.MEMBER_VIEW,
    P.MEMBER_INVITE,
    P.MEMBER_ASSIGN_ROLE,
    P.MEMBER_REMOVE,
    P.MEMBER_AVATAR_VIEW,
    P.TEMPLATE_SLIDE_MANAGE,
    P.TEMPLATE_PDF_MANAGE,
    P.PERMISSION_MANAGE,
    P.ORG_TRANSFER_CREATOR,
    P.ORG_UPDATE,
    P.COURSE_CONFIG_MANAGE,
    P.COURSE_VIEW,
    P.COURSE_VIEW_ALL,
    P.COURSE_CREATE,
    P.COURSE_ASSIGN,
    P.COURSE_EDIT,
    P.COURSE_DELETE,
    P.COURSE_GENERATE,
    P.COURSE_SAVE_DRAFT,
    P.COURSE_DUPLICATE,
)


# === Codici ruoli ===
class R:
    CREATOR = "creator"
    ORG_ADMIN = "org_admin"
    MANAGER = "manager"
    MEMBER = "member"


ROLE_RANK: dict[str, int] = {R.CREATOR: 10, R.ORG_ADMIN: 20, R.MANAGER: 30, R.MEMBER: 40}
ROLE_NAME_IT: dict[str, str] = {
    R.CREATOR: "Creatore",
    R.ORG_ADMIN: "Amministratore organizzazione",
    R.MANAGER: "Manager",
    R.MEMBER: "Membro",
}

# Permessi default per ruolo (usati dal seed iniziale).
ROLE_DEFAULT_PERMISSIONS: dict[str, set[str]] = {
    R.CREATOR: set(ALL_PERMISSION_CODES),
    R.ORG_ADMIN: {
        P.MEMBER_VIEW, P.MEMBER_INVITE, P.MEMBER_ASSIGN_ROLE, P.MEMBER_REMOVE,
        P.MEMBER_AVATAR_VIEW,
        P.TEMPLATE_SLIDE_MANAGE, P.TEMPLATE_PDF_MANAGE, P.ORG_UPDATE,
        P.COURSE_CONFIG_MANAGE,
        P.COURSE_VIEW, P.COURSE_VIEW_ALL, P.COURSE_CREATE, P.COURSE_ASSIGN,
        P.COURSE_EDIT, P.COURSE_DELETE, P.COURSE_GENERATE, P.COURSE_SAVE_DRAFT,
        P.COURSE_DUPLICATE,
    },
    R.MANAGER: {
        P.MEMBER_VIEW, P.MEMBER_AVATAR_VIEW,
        P.COURSE_VIEW, P.COURSE_VIEW_ALL, P.COURSE_CREATE, P.COURSE_ASSIGN,
        P.COURSE_EDIT, P.COURSE_GENERATE, P.COURSE_SAVE_DRAFT,
        P.COURSE_DUPLICATE,
    },
    R.MEMBER: {P.COURSE_VIEW},
}

# Permessi che il `creator` non può perdere (server-side guard).
CREATOR_REQUIRED_PERMISSIONS: set[str] = {P.PERMISSION_MANAGE, P.ORG_TRANSFER_CREATOR}


async def _resolve_for_membership(
    db: AsyncSession, *, membership: Membership
) -> tuple[set[str], OrganizationRole]:
    role = await db.get(OrganizationRole, membership.role_id)
    if role is None:
        raise PermissionDeniedError("Ruolo non trovato.", code="role_missing")

    # Permessi default del ruolo
    base_q = (
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role.id)
    )
    base = set((await db.execute(base_q)).scalars().all())

    # Override a livello organizzazione (per il ruolo)
    org_q = (
        select(Permission.code, OrganizationRolePermission.granted)
        .join(OrganizationRolePermission, OrganizationRolePermission.permission_id == Permission.id)
        .where(
            OrganizationRolePermission.organization_id == membership.organization_id,
            OrganizationRolePermission.role_id == role.id,
        )
    )
    for code, granted in (await db.execute(org_q)).all():
        if granted:
            base.add(code)
        else:
            base.discard(code)

    # Override per singolo membership
    member_q = (
        select(Permission.code, MembershipPermissionOverride.granted)
        .join(MembershipPermissionOverride, MembershipPermissionOverride.permission_id == Permission.id)
        .where(MembershipPermissionOverride.membership_id == membership.id)
    )
    for code, granted in (await db.execute(member_q)).all():
        if granted:
            base.add(code)
        else:
            base.discard(code)

    return base, role


async def get_membership(
    db: AsyncSession, *, user_id: uuid.UUID, organization_id: uuid.UUID
) -> Membership | None:
    res = await db.execute(
        select(Membership).where(
            Membership.user_id == user_id, Membership.organization_id == organization_id
        )
    )
    return res.scalar_one_or_none()


async def resolve_permissions(
    db: AsyncSession, *, user: User, organization_id: uuid.UUID
) -> set[str]:
    if user.is_platform_admin:
        return set(ALL_PERMISSION_CODES)
    membership = await get_membership(db, user_id=user.id, organization_id=organization_id)
    if membership is None:
        raise PermissionDeniedError(
            "Non sei membro di questa organizzazione.", code="not_a_member"
        )
    permissions, _ = await _resolve_for_membership(db, membership=membership)
    return permissions


def require(*codes: str):
    """Factory di dependency che richiede tutti i permessi indicati nell'organizzazione `org_id`."""

    async def dependency(
        org_id: Annotated[uuid.UUID, Path(...)],
        user: CurrentUser,
        db: DbSession,
    ) -> set[str]:
        granted = await resolve_permissions(db, user=user, organization_id=org_id)
        missing = [c for c in codes if c not in granted]
        if missing:
            raise PermissionDeniedError(
                f"Permessi mancanti: {', '.join(missing)}",
                code="permission_denied",
                meta={"missing": missing},
            )
        return granted

    return Depends(dependency)


def require_membership():
    """Dipendenza che richiede solo l'appartenenza all'organizzazione (qualsiasi ruolo)."""

    async def dependency(
        org_id: Annotated[uuid.UUID, Path(...)],
        user: CurrentUser,
        db: DbSession,
    ) -> Membership | None:
        if user.is_platform_admin:
            return None
        membership = await get_membership(db, user_id=user.id, organization_id=org_id)
        if membership is None:
            raise PermissionDeniedError(
                "Non sei membro di questa organizzazione.", code="not_a_member"
            )
        return membership

    return Depends(dependency)
