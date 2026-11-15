"""Concurrency regression: reclaim_expired_leases vs claim_ticket (#T8)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from ak5.server.models.actor import Actor
from ak5.server.models.board_member import BoardMember
from ak5.server.models.ticket import Ticket
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID
from ak5.server.services import workflow as wf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _seed_claimable_ticket(session: AsyncSession, *, expired: bool) -> Ticket:
    ticket = Ticket(
        ticket_id="TK-CONCUR",
        workspace_id=LOCAL_WORKSPACE_ID,
        board_id="proj-core-engine",
        column_id="col_todo",
        title="Concurrency",
        description="",
        rank="0|hzzzzz:",
        created_by="user_pm",
        claim_token="clm_expired",
        claimed_by="agent_worker",
        assigned_to="agent_worker",
        lease_expires_at=(datetime.now(UTC) - timedelta(seconds=30)).replace(tzinfo=None)
        if expired
        else (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None),
    )
    session.add(ticket)
    await session.commit()
    return ticket


@pytest.mark.asyncio
async def test_reclaim_and_claim_do_not_clobber_active_claim(
    test_db: AsyncSession, test_engine_fixture
):
    """Parallel reclaim must not clear a lease that claim_ticket just renewed."""
    worker = Actor(
        actor_id="agent_worker",
        actor_type="agent",
        name="Worker",
        role="worker",
        description="",
        capabilities=json.dumps(["python"]),
        status="idle",
    )
    if await test_db.get(Actor, worker.actor_id) is None:
        test_db.add(worker)
    exists = (
        await test_db.execute(
            select(BoardMember.id).where(
                BoardMember.board_id == "proj-core-engine",
                BoardMember.actor_id == "agent_worker",
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        test_db.add(
            BoardMember(
                board_id="proj-core-engine",
                workspace_id=LOCAL_WORKSPACE_ID,
                actor_id="agent_worker",
                role="agent",
            )
        )
    await test_db.commit()

    await _seed_claimable_ticket(test_db, expired=True)
    maker = async_sessionmaker(
        bind=test_engine_fixture, class_=AsyncSession, expire_on_commit=False
    )

    async def reclaim() -> list[str]:
        async with maker() as db:
            affected = await wf.reclaim_expired_leases(db)
            await db.commit()
            return affected

    async def claim() -> dict:
        async with maker() as db:
            actor = await db.get(Actor, "agent_worker")
            assert actor is not None
            payload = await wf.claim_ticket(db, actor, "TK-CONCUR")
            await db.commit()
            return payload

    await asyncio.gather(reclaim(), claim())

    async with maker() as db:
        ticket = await db.get(Ticket, "TK-CONCUR")
        assert ticket is not None
        # Invalid end state: active token with already-expired lease (reclaim cleared a fresh claim).
        if ticket.claim_token is not None:
            assert ticket.lease_expires_at is not None
            assert ticket.lease_expires_at > datetime.now(UTC).replace(tzinfo=None)