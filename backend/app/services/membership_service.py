from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.permissions import R, ROLE_RANK
from app.models.membership import Membership
from app.models.role import OrganizationRole
from app.models.user import User


async def get_role_by_code(db: AsyncSession, code: str) -> OrganizationRole:
    role = (
        await db.execute(select(OrganizationRole).where(OrganizationRole.code == code))
    ).scalar_one_or_none()
    if role is None:
        raise NotFoundError(f"Ruolo '{code}' non trovato.", code="role_not_found")
    return role


async def enroll_user(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    role_code: str,
    actor_id: uuid.UUID,
) -> Membership:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("Utente non trovato.", code="user_not_found")
    role = await get_role_by_code(db, role_code)

    existing = (
        await db.execute(
            select(Membership).where(
                Membership.user_id == user_id, Membership.organization_id == organization_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("L'utente è già membro di questa organizzazione.", code="already_member")

    if role.code == R.CREATOR:
        # Solo via transfer-creator (non si può creare un secondo creator)
        existing_creator = (
            await db.execute(
                select(Membership)
                .join(OrganizationRole, OrganizationRole.id == Membership.role_id)
                .where(
                    Membership.organization_id == organization_id,
                    OrganizationRole.code == R.CREATOR,
                )
            )
        ).scalar_one_or_none()
        if existing_creator is not None:
            raise ConflictError(
                "Esiste già un creatore per questa organizzazione. Usa transfer-creator.",
                code="creator_exists",
            )

    membership = Membership(
        user_id=user_id,
        organization_id=organization_id,
        role_id=role.id,
        joined_by_user_id=actor_id,
    )
    db.add(membership)
    await db.flush()
    await db.refresh(membership)
    await write_audit(
        db,
        action="membership.create",
        actor_user_id=actor_id,
        organization_id=organization_id,
        target_type="user",
        target_id=str(user_id),
        metadata={"role": role.code},
    )
    return membership


async def change_role(
    db: AsyncSession,
    *,
    membership: Membership,
    new_role_code: str,
    actor_user: User,
    actor_membership: Membership | None,
) -> Membership:
    new_role = await get_role_by_code(db, new_role_code)
    if new_role.code == R.CREATOR:
        raise PermissionDeniedError(
            "Il ruolo creator si assegna solo via transfer-creator.",
            code="creator_via_transfer",
        )

    # Vincolo di rank: solo platform admin può promuovere a un rank superiore al proprio.
    if not actor_user.is_platform_admin:
        if actor_membership is None:
            raise PermissionDeniedError("Membership richiesta.", code="not_a_member")
        actor_role = await db.get(OrganizationRole, actor_membership.role_id)
        target_role_current = await db.get(OrganizationRole, membership.role_id)
        if actor_role is None or target_role_current is None:
            raise PermissionDeniedError("Ruolo non trovato.", code="role_missing")
        # un creator può cambiare ruolo a chiunque tranne se stesso (verificato a monte)
        if actor_role.code != R.CREATOR:
            if ROLE_RANK[new_role.code] < ROLE_RANK[actor_role.code]:
                raise PermissionDeniedError(
                    "Non puoi promuovere a un ruolo superiore al tuo.",
                    code="rank_violation",
                )
            if ROLE_RANK[target_role_current.code] < ROLE_RANK[actor_role.code]:
                raise PermissionDeniedError(
                    "Non puoi modificare un membro con ruolo superiore al tuo.",
                    code="rank_violation",
                )

    membership.role_id = new_role.id
    await db.flush()
    await db.refresh(membership)
    await write_audit(
        db,
        action="membership.role_change",
        actor_user_id=actor_user.id,
        organization_id=membership.organization_id,
        target_type="user",
        target_id=str(membership.user_id),
        metadata={"new_role": new_role.code},
    )
    return membership


async def remove_membership(
    db: AsyncSession, *, membership: Membership, actor_user: User
) -> None:
    role = await db.get(OrganizationRole, membership.role_id)
    if role and role.code == R.CREATOR:
        raise ConflictError(
            "Non puoi rimuovere il creatore. Usa transfer-creator e poi rimuovi.",
            code="cannot_remove_creator",
        )
    await db.delete(membership)
    await db.flush()
    await write_audit(
        db,
        action="membership.remove",
        actor_user_id=actor_user.id,
        organization_id=membership.organization_id,
        target_type="user",
        target_id=str(membership.user_id),
    )


async def transfer_creator(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_user: User,
    actor_membership: Membership,
    target_user_id: uuid.UUID,
) -> tuple[Membership, Membership]:
    creator_role = await get_role_by_code(db, R.CREATOR)
    org_admin_role = await get_role_by_code(db, R.ORG_ADMIN)

    if actor_membership.role_id != creator_role.id:
        raise PermissionDeniedError("Solo il creatore può trasferire il ruolo.", code="not_creator")
    if target_user_id == actor_user.id:
        raise ConflictError("Non puoi trasferire a te stesso.", code="self_transfer")

    target_membership = (
        await db.execute(
            select(Membership).where(
                Membership.user_id == target_user_id,
                Membership.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    if target_membership is None:
        raise NotFoundError("L'utente non è membro dell'organizzazione.", code="not_a_member")

    actor_membership.role_id = org_admin_role.id
    target_membership.role_id = creator_role.id
    await db.flush()
    await db.refresh(actor_membership)
    await db.refresh(target_membership)

    await write_audit(
        db,
        action="organization.transfer_creator",
        actor_user_id=actor_user.id,
        organization_id=organization_id,
        target_type="user",
        target_id=str(target_user_id),
    )
    return actor_membership, target_membership
