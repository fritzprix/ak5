"""Issue #65: opaque agent auth must not dirty the request session."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from ak5.server.auth.tokens import (
    _LAST_USED_MIN_INTERVAL,
    _touch_agent_credential_last_used,
    verify_access_token,
)
from ak5.server.models.workspace import Agent, AgentCredential
from ak5.server.seed import LOCAL_USER_ID, LOCAL_WORKSPACE_ID, ensure_local_tenancy
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_opaque_verify_does_not_dirty_request_session(test_db: AsyncSession):
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    agent_id = "agt_lock_probe"
    test_db.add(
        Agent(
            agent_id=agent_id,
            workspace_id=LOCAL_WORKSPACE_ID,
            name="Lock Probe",
            workflow_role="worker",
            capabilities="[]",
            board_scope="[]",
            created_by=LOCAL_USER_ID,
        )
    )
    plaintext = "ak5_agt_lock_probe_token_value"
    token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    test_db.add(
        AgentCredential(
            credential_id="cred_lock_probe",
            agent_id=agent_id,
            token_hash=token_hash,
            board_scope="[]",
            expires_at=None,
            revoked_at=None,
            last_used_at=None,
        )
    )
    await test_db.commit()

    principal = await verify_access_token(plaintext, test_db)
    assert principal.principal_id == agent_id
    # Request session must not hold an uncommitted write (long-poll lock cause).
    assert not test_db.dirty
    assert not test_db.new
    assert not test_db.deleted

    # last_used_at was committed (short txn / immediate commit path).
    cred = await test_db.get(AgentCredential, "cred_lock_probe")
    assert cred is not None
    await test_db.refresh(cred)
    assert cred.last_used_at is not None


@pytest.mark.asyncio
async def test_last_used_touch_throttled(test_db: AsyncSession):
    await ensure_local_tenancy(test_db)
    agent_id = "agt_throttle"
    test_db.add(
        Agent(
            agent_id=agent_id,
            workspace_id=LOCAL_WORKSPACE_ID,
            name="Throttle",
            workflow_role="worker",
            capabilities="[]",
            board_scope="[]",
            created_by=LOCAL_USER_ID,
        )
    )
    recent = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5)
    test_db.add(
        AgentCredential(
            credential_id="cred_throttle",
            agent_id=agent_id,
            token_hash=hashlib.sha256(b"x").hexdigest(),
            board_scope="[]",
            last_used_at=recent,
        )
    )
    await test_db.commit()

    now = datetime.now(UTC).replace(tzinfo=None)
    await _touch_agent_credential_last_used(
        test_db, "cred_throttle", previous=recent, now=now
    )
    cred = await test_db.get(AgentCredential, "cred_throttle")
    assert cred is not None
    await test_db.refresh(cred)
    # Within throttle window — unchanged.
    assert abs((cred.last_used_at - recent).total_seconds()) < 1

    old = now - _LAST_USED_MIN_INTERVAL - timedelta(seconds=1)
    cred.last_used_at = old
    await test_db.commit()

    await _touch_agent_credential_last_used(
        test_db, "cred_throttle", previous=old, now=now
    )
    await test_db.refresh(cred)
    assert cred.last_used_at is not None
    assert cred.last_used_at >= now - timedelta(seconds=2)
