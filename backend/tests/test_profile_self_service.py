from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _bearer(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(subject=str(user_id))
    return {"Authorization": f"Bearer {token}"}


async def _make_user(engine, *, email: str, password: str = "Password123!") -> uuid.UUID:
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        user = User(
            email=email,
            password_hash=hash_password(password),
            full_name="Test User",
            is_platform_admin=False,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        return user.id


async def test_profile_name_update(client, _engine, random_email):
    uid = await _make_user(_engine, email=random_email)
    res = await client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Nome Aggiornato"},
        headers=_bearer(uid),
    )
    assert res.status_code == 200, res.text
    assert res.json()["user"]["full_name"] == "Nome Aggiornato"

    me = await client.get("/api/v1/auth/me", headers=_bearer(uid))
    assert me.json()["user"]["full_name"] == "Nome Aggiornato"


async def test_change_password_flow(client, _engine, random_email):
    """Tutte le asserzioni in un'unica funzione per restare entro il
    rate-limit (5/min) di /auth/me/change-password."""
    uid = await _make_user(_engine, email=random_email, password="Password123!")
    hdr = _bearer(uid)

    # 1) password attuale errata → 401
    r1 = await client.post(
        "/api/v1/auth/me/change-password",
        json={"current_password": "WrongPass123", "new_password": "NewPass1234"},
        headers=hdr,
    )
    assert r1.status_code == 401, r1.text
    assert r1.json()["code"] == "invalid_current_password"

    # 2) nuova password debole → 422
    r2 = await client.post(
        "/api/v1/auth/me/change-password",
        json={"current_password": "Password123!", "new_password": "weak"},
        headers=hdr,
    )
    assert r2.status_code == 422, r2.text

    # 3) nuova uguale all'attuale → 422
    r3 = await client.post(
        "/api/v1/auth/me/change-password",
        json={"current_password": "Password123!", "new_password": "Password123!"},
        headers=hdr,
    )
    assert r3.status_code == 422, r3.text
    assert r3.json()["code"] == "password_unchanged"

    # 4) successo
    r4 = await client.post(
        "/api/v1/auth/me/change-password",
        json={"current_password": "Password123!", "new_password": "NewPass1234"},
        headers=hdr,
    )
    assert r4.status_code == 200, r4.text

    # La sessione corrente resta valida (nessuna revoca self).
    me = await client.get("/api/v1/auth/me", headers=hdr)
    assert me.status_code == 200

    # Hash aggiornato a livello DB.
    SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)
    async with SessionLocal() as session:
        user = await session.get(User, uid)
        assert user is not None
        assert verify_password("NewPass1234", user.password_hash)
        assert not verify_password("Password123!", user.password_hash)


async def test_change_email_wrong_current_rejected(client, _engine, random_email):
    uid = await _make_user(_engine, email=random_email)
    res = await client.post(
        "/api/v1/auth/me/change-email",
        json={
            "current_password": "WrongPass123",
            "new_email": f"new-{uuid.uuid4().hex[:8]}@a4u.local",
        },
        headers=_bearer(uid),
    )
    assert res.status_code == 401, res.text
    assert res.json()["code"] == "invalid_current_password"


async def test_change_email_uniqueness(client, _engine, random_email):
    uid = await _make_user(_engine, email=random_email)
    other = f"other-{uuid.uuid4().hex[:8]}@a4u.local"
    await _make_user(_engine, email=other)
    res = await client.post(
        "/api/v1/auth/me/change-email",
        json={"current_password": "Password123!", "new_email": other},
        headers=_bearer(uid),
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "email_in_use"


async def test_change_email_success(client, _engine, random_email):
    uid = await _make_user(_engine, email=random_email)
    new_email = f"new-{uuid.uuid4().hex[:8]}@a4u.local"
    res = await client.post(
        "/api/v1/auth/me/change-email",
        json={"current_password": "Password123!", "new_email": new_email},
        headers=_bearer(uid),
    )
    assert res.status_code == 200, res.text
    assert res.json()["user"]["email"].lower() == new_email.lower()
