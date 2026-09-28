"""Read endpoints added JWT gates — unauthenticated clients must get 401."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_read_endpoints_require_bearer(anon_client: AsyncClient):
    paths = [
        "/api/v1/boards",
        "/api/v1/boards/proj-core-engine",
        "/api/v1/tickets",
        "/api/v1/tickets/does-not-exist",
        "/api/v1/actors",
        "/api/v1/actors/discovery",
        "/api/v1/actors/user_pm",
        "/api/v1/boards/proj-core-engine/members",
    ]
    for path in paths:
        resp = await anon_client.get(path)
        assert resp.status_code == 401, f"{path} → {resp.status_code}"
