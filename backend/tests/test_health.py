from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_health(client) -> None:
    res = await client.get("/api/v1/system/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


async def test_ready(client) -> None:
    res = await client.get("/api/v1/system/ready")
    assert res.status_code == 200
    assert res.json()["db"] == "ok"
