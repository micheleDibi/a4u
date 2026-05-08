from __future__ import annotations

import pytest

from app.core.security import hash_password
from app.models.user import User

pytestmark = pytest.mark.asyncio


async def test_login_logout_me(client, _engine, random_email):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)
    async with SessionLocal() as session:
        session.add(
            User(
                email=random_email,
                password_hash=hash_password("Password123!"),
                full_name="Mario Rossi",
                is_active=True,
                is_platform_admin=True,
            )
        )
        await session.commit()

    # Login
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": random_email, "password": "Password123!"},
    )
    assert res.status_code == 200, res.text
    assert "access_token" in res.cookies

    # /me con cookie
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"].lower() == random_email.lower()
    assert body["is_platform_admin"] is True

    # Logout
    out = await client.post("/api/v1/auth/logout")
    assert out.status_code == 200
    me_after = await client.get("/api/v1/auth/me")
    assert me_after.status_code == 401


async def test_login_invalid_credentials(client, random_email):
    res = await client.post(
        "/api/v1/auth/login", json={"email": random_email, "password": "Wrong-Password1"}
    )
    assert res.status_code == 401
