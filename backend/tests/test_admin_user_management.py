from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import create_access_token, hash_password, verify_password
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services import user_admin_service

pytestmark = pytest.mark.asyncio


def _bearer(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(subject=str(user_id))
    return {"Authorization": f"Bearer {token}"}


async def _make_user(
    engine,
    *,
    email: str,
    is_platform_admin: bool = False,
    is_active: bool = True,
    password: str = "Password123!",
) -> uuid.UUID:
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        user = User(
            email=email,
            password_hash=hash_password(password),
            full_name="Test User",
            is_platform_admin=is_platform_admin,
            is_active=is_active,
        )
        session.add(user)
        await session.commit()
        return user.id


async def test_admin_set_password_revokes_refresh_and_changes_hash(
    client, _engine, random_email
):
    admin_id = await _make_user(
        _engine, email=f"admin-{uuid.uuid4().hex[:8]}@a4u.local", is_platform_admin=True
    )
    target_id = await _make_user(_engine, email=random_email)

    # Refresh token vivo per il target.
    SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)
    async with SessionLocal() as session:
        session.add(
            RefreshToken(
                user_id=target_id,
                token_hash=uuid.uuid4().hex,
                expires_at=datetime.now(tz=UTC) + timedelta(days=7),
                revoked_at=None,
            )
        )
        await session.commit()

    res = await client.post(
        f"/api/v1/admin/users/{target_id}/password",
        json={"password": "BrandNew123"},
        headers=_bearer(admin_id),
    )
    assert res.status_code == 200, res.text

    async with SessionLocal() as session:
        user = await session.get(User, target_id)
        assert user is not None
        assert verify_password("BrandNew123", user.password_hash)
        tokens = (
            await session.execute(
                select(RefreshToken).where(RefreshToken.user_id == target_id)
            )
        ).scalars().all()
        assert tokens and all(t.revoked_at is not None for t in tokens)


async def test_admin_set_password_weak_rejected(client, _engine, random_email):
    admin_id = await _make_user(
        _engine, email=f"admin-{uuid.uuid4().hex[:8]}@a4u.local", is_platform_admin=True
    )
    target_id = await _make_user(_engine, email=random_email)
    res = await client.post(
        f"/api/v1/admin/users/{target_id}/password",
        json={"password": "weak"},
        headers=_bearer(admin_id),
    )
    assert res.status_code == 422, res.text


async def test_update_user_email_uniqueness(client, _engine):
    admin_id = await _make_user(
        _engine, email=f"admin-{uuid.uuid4().hex[:8]}@a4u.local", is_platform_admin=True
    )
    a_id = await _make_user(_engine, email=f"a-{uuid.uuid4().hex[:8]}@a4u.local")
    b_email = f"b-{uuid.uuid4().hex[:8]}@a4u.local"
    await _make_user(_engine, email=b_email)

    res = await client.put(
        f"/api/v1/admin/users/{a_id}",
        json={"email": b_email},
        headers=_bearer(admin_id),
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "email_in_use"


async def test_update_user_email_change_succeeds(client, _engine):
    admin_id = await _make_user(
        _engine, email=f"admin-{uuid.uuid4().hex[:8]}@a4u.local", is_platform_admin=True
    )
    a_id = await _make_user(_engine, email=f"a-{uuid.uuid4().hex[:8]}@a4u.local")
    new_email = f"new-{uuid.uuid4().hex[:8]}@a4u.local"

    res = await client.put(
        f"/api/v1/admin/users/{a_id}",
        json={"email": new_email},
        headers=_bearer(admin_id),
    )
    assert res.status_code == 200, res.text
    assert res.json()["email"].lower() == new_email.lower()


async def test_cannot_deactivate_self(client, _engine):
    admin_id = await _make_user(
        _engine, email=f"admin-{uuid.uuid4().hex[:8]}@a4u.local", is_platform_admin=True
    )
    res = await client.put(
        f"/api/v1/admin/users/{admin_id}",
        json={"is_active": False},
        headers=_bearer(admin_id),
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "cannot_deactivate_self"


async def test_cannot_demote_self(client, _engine):
    admin_id = await _make_user(
        _engine, email=f"admin-{uuid.uuid4().hex[:8]}@a4u.local", is_platform_admin=True
    )
    res = await client.put(
        f"/api/v1/admin/users/{admin_id}",
        json={"is_platform_admin": False},
        headers=_bearer(admin_id),
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "cannot_demote_self"


async def test_count_other_active_platform_admins(db):
    a = User(
        email=f"a-{uuid.uuid4().hex[:8]}@a4u.local",
        password_hash=hash_password("Password123!"),
        full_name="A",
        is_platform_admin=True,
        is_active=True,
    )
    b = User(
        email=f"b-{uuid.uuid4().hex[:8]}@a4u.local",
        password_hash=hash_password("Password123!"),
        full_name="B",
        is_platform_admin=True,
        is_active=True,
    )
    db.add_all([a, b])
    await db.flush()

    before = await user_admin_service._count_other_active_platform_admins(
        db, exclude_user_id=a.id
    )
    # B (admin attivo, != A) deve essere contato.
    assert before >= 1

    b.is_active = False
    await db.flush()
    after = await user_admin_service._count_other_active_platform_admins(
        db, exclude_user_id=a.id
    )
    assert after == before - 1
