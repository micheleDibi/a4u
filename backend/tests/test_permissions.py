from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.permissions import (
    P,
    R,
    ROLE_DEFAULT_PERMISSIONS,
    resolve_permissions,
)
from app.core.security import hash_password
from app.models.membership import Membership, MembershipPermissionOverride
from app.models.organization import Organization
from app.models.permission import OrganizationRolePermission, Permission
from app.models.role import OrganizationRole
from app.models.user import User

pytestmark = pytest.mark.asyncio


async def _setup_user_membership(db, *, role_code: str) -> tuple[User, Organization, Membership]:
    role = (await db.execute(select(OrganizationRole).where(OrganizationRole.code == role_code))).scalar_one()
    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@x.it",
        password_hash=hash_password("Password123!"),
        full_name="Test",
        is_active=True,
    )
    org = Organization(name="OrgTest", email="org@x.it")
    db.add_all([user, org])
    await db.flush()
    m = Membership(user_id=user.id, organization_id=org.id, role_id=role.id)
    db.add(m)
    await db.commit()
    return user, org, m


async def test_default_permissions_match_seed(seeded_db):
    user, org, _ = await _setup_user_membership(seeded_db, role_code=R.MANAGER)
    perms = await resolve_permissions(seeded_db, user=user, organization_id=org.id)
    assert perms == ROLE_DEFAULT_PERMISSIONS[R.MANAGER]


async def test_org_role_override_grants_permission(seeded_db):
    user, org, _m = await _setup_user_membership(seeded_db, role_code=R.MANAGER)
    role_manager = (await seeded_db.execute(select(OrganizationRole).where(OrganizationRole.code == R.MANAGER))).scalar_one()
    perm = (await seeded_db.execute(select(Permission).where(Permission.code == P.MEMBER_INVITE))).scalar_one()
    seeded_db.add(
        OrganizationRolePermission(
            organization_id=org.id,
            role_id=role_manager.id,
            permission_id=perm.id,
            granted=True,
        )
    )
    await seeded_db.commit()
    perms = await resolve_permissions(seeded_db, user=user, organization_id=org.id)
    assert P.MEMBER_INVITE in perms


async def test_membership_override_revokes_default(seeded_db):
    user, org, m = await _setup_user_membership(seeded_db, role_code=R.ORG_ADMIN)
    perm = (await seeded_db.execute(select(Permission).where(Permission.code == P.TEMPLATE_SLIDE_MANAGE))).scalar_one()
    seeded_db.add(
        MembershipPermissionOverride(membership_id=m.id, permission_id=perm.id, granted=False)
    )
    await seeded_db.commit()
    perms = await resolve_permissions(seeded_db, user=user, organization_id=org.id)
    assert P.TEMPLATE_SLIDE_MANAGE not in perms
    # Altri permessi del ruolo restano
    assert P.MEMBER_INVITE in perms


async def test_platform_admin_has_all(seeded_db):
    user = User(
        email=f"a-{uuid.uuid4().hex[:6]}@x.it",
        password_hash=hash_password("Password123!"),
        full_name="Admin",
        is_active=True,
        is_platform_admin=True,
    )
    seeded_db.add(user)
    await seeded_db.commit()
    perms = await resolve_permissions(
        seeded_db, user=user, organization_id=uuid.uuid4()
    )
    assert P.PERMISSION_MANAGE in perms
    assert P.ORG_TRANSFER_CREATOR in perms
