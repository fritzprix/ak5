"""Fixtures for regression gates (#29)."""

from __future__ import annotations

import json

import pytest
from ak5.models.actor import Actor
from live_server_fixture import free_port, identify, live_server  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["free_port", "identify", "live_server", "roles"]


async def _seed_roles(db: AsyncSession) -> None:
    actors = [
        Actor(
            actor_id="agent_triage",
            actor_type="agent",
            name="Triage Bot",
            role="triage",
            capabilities=json.dumps([]),
            status="idle",
        ),
        Actor(
            actor_id="agent_worker",
            actor_type="agent",
            name="Worker Bot",
            role="worker",
            capabilities=json.dumps([]),
            status="idle",
        ),
        Actor(
            actor_id="agent_gk",
            actor_type="agent",
            name="Gatekeeper Bot",
            role="gatekeeper",
            capabilities=json.dumps([]),
            status="idle",
        ),
    ]
    for a in actors:
        existing = await db.get(Actor, a.actor_id)
        if existing is None:
            db.add(a)
    await db.commit()


@pytest.fixture
async def roles(test_db: AsyncSession, auth_headers):
    await _seed_roles(test_db)
    return auth_headers
