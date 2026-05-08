from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.permissions import (
    ALL_PERMISSION_CODES,
    CREATOR_REQUIRED_PERMISSIONS,
    R,
)
from app.models.membership import Membership, MembershipPermissionOverride
from app.models.permission import (
    OrganizationRolePermission,
    Permission,
    RolePermission,
)
from app.models.role import OrganizationRole
from app.schemas.membership import (
    PermissionOverrideEntry,
    RolePermissionDefaultUpdate,
)


async def _ensure_codes_exist(db: AsyncSession, codes: list[str]) -> dict[str, uuid.UUID]:
    unknown = [c for c in codes if c not in ALL_PERMISSION_CODES]
    if unknown:
        raise ValidationAppError(
            f"Codici permesso sconosciuti: {', '.join(unknown)}",
            code="unknown_permissions",
            meta={"unknown": unknown},
        )
    rows = (
        await db.execute(select(Permission.code, Permission.id).where(Permission.code.in_(codes)))
    ).all()
    return {code: pid for code, pid in rows}


async def update_role_default_permissions(
    db: AsyncSession,
    *,
    payload: RolePermissionDefaultUpdate,
    actor_id: uuid.UUID,
) -> None:
    role = (
        await db.execute(select(OrganizationRole).where(OrganizationRole.code == payload.role_code))
    ).scalar_one_or_none()
    if role is None:
        raise NotFoundError("Ruolo non trovato.", code="role_not_found")

    if role.code == R.CREATOR:
        missing = CREATOR_REQUIRED_PERMISSIONS - set(payload.permissions)
        if missing:
            raise ConflictError(
                f"Il ruolo creator non può perdere: {', '.join(sorted(missing))}",
                code="creator_required_permissions",
            )

    code_to_id = await _ensure_codes_exist(db, payload.permissions)

    await db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    for pid in code_to_id.values():
        db.add(RolePermission(role_id=role.id, permission_id=pid))
    await db.flush()

    await write_audit(
        db,
        action="permission.role_defaults.update",
        actor_user_id=actor_id,
        target_type="role",
        target_id=role.code,
        metadata={"permissions": payload.permissions},
    )


async def upsert_org_role_permissions(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    role_code: str,
    overrides: list[PermissionOverrideEntry],
    actor_id: uuid.UUID,
) -> None:
    role = (
        await db.execute(select(OrganizationRole).where(OrganizationRole.code == role_code))
    ).scalar_one_or_none()
    if role is None:
        raise NotFoundError("Ruolo non trovato.", code="role_not_found")

    if role.code == R.CREATOR:
        revoked_required = {
            o.code for o in overrides if not o.granted and o.code in CREATOR_REQUIRED_PERMISSIONS
        }
        if revoked_required:
            raise ConflictError(
                f"Il creator non può perdere: {', '.join(sorted(revoked_required))}",
                code="creator_required_permissions",
            )

    code_to_id = await _ensure_codes_exist(db, [o.code for o in overrides])
    await db.execute(
        delete(OrganizationRolePermission).where(
            OrganizationRolePermission.organization_id == organization_id,
            OrganizationRolePermission.role_id == role.id,
        )
    )
    for o in overrides:
        db.add(
            OrganizationRolePermission(
                organization_id=organization_id,
                role_id=role.id,
                permission_id=code_to_id[o.code],
                granted=o.granted,
            )
        )
    await db.flush()

    await write_audit(
        db,
        action="permission.org_role.update",
        actor_user_id=actor_id,
        organization_id=organization_id,
        target_type="role",
        target_id=role.code,
        metadata={"overrides": [o.model_dump() for o in overrides]},
    )


async def upsert_membership_permissions(
    db: AsyncSession,
    *,
    membership: Membership,
    overrides: list[PermissionOverrideEntry],
    actor_id: uuid.UUID,
) -> None:
    role = await db.get(OrganizationRole, membership.role_id)
    if role and role.code == R.CREATOR:
        revoked_required = {
            o.code for o in overrides if not o.granted and o.code in CREATOR_REQUIRED_PERMISSIONS
        }
        if revoked_required:
            raise ConflictError(
                f"Il creator non può perdere: {', '.join(sorted(revoked_required))}",
                code="creator_required_permissions",
            )

    code_to_id = await _ensure_codes_exist(db, [o.code for o in overrides])
    await db.execute(
        delete(MembershipPermissionOverride).where(
            MembershipPermissionOverride.membership_id == membership.id
        )
    )
    for o in overrides:
        db.add(
            MembershipPermissionOverride(
                membership_id=membership.id,
                permission_id=code_to_id[o.code],
                granted=o.granted,
            )
        )
    await db.flush()
    await write_audit(
        db,
        action="permission.membership.update",
        actor_user_id=actor_id,
        organization_id=membership.organization_id,
        target_type="membership",
        target_id=str(membership.id),
        metadata={"overrides": [o.model_dump() for o in overrides]},
    )


async def get_role_default_permissions(
    db: AsyncSession, *, role_code: str
) -> list[str]:
    role = (
        await db.execute(select(OrganizationRole).where(OrganizationRole.code == role_code))
    ).scalar_one_or_none()
    if role is None:
        raise NotFoundError("Ruolo non trovato.", code="role_not_found")
    rows = await db.execute(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role.id)
    )
    return list(rows.scalars().all())


async def list_organization_role_overrides(
    db: AsyncSession, *, organization_id: uuid.UUID, role_code: str
) -> list[PermissionOverrideEntry]:
    role = (
        await db.execute(select(OrganizationRole).where(OrganizationRole.code == role_code))
    ).scalar_one_or_none()
    if role is None:
        raise NotFoundError("Ruolo non trovato.", code="role_not_found")
    rows = await db.execute(
        select(Permission.code, OrganizationRolePermission.granted)
        .join(OrganizationRolePermission, OrganizationRolePermission.permission_id == Permission.id)
        .where(
            OrganizationRolePermission.organization_id == organization_id,
            OrganizationRolePermission.role_id == role.id,
        )
    )
    return [PermissionOverrideEntry(code=c, granted=g) for c, g in rows.all()]


async def list_membership_overrides(
    db: AsyncSession, *, membership_id: uuid.UUID
) -> list[PermissionOverrideEntry]:
    rows = await db.execute(
        select(Permission.code, MembershipPermissionOverride.granted)
        .join(MembershipPermissionOverride, MembershipPermissionOverride.permission_id == Permission.id)
        .where(MembershipPermissionOverride.membership_id == membership_id)
    )
    return [PermissionOverrideEntry(code=c, granted=g) for c, g in rows.all()]
