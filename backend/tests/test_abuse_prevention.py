"""Tests for abuse prevention controls (operations.md §6 / #118).

* Local owner password login: loopback-only, exponential delay on consecutive failures.
* Workspace pending invite cap (100).
* Same email re-invite cooldown (10 minutes).
* Uniform non-enumerating invite token rejection.
* Agent credential issuance rate limiting under hosted profile.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from ak5.contracts.errors import ErrorCode
from ak5.server.metrics import quota_rejections_total, rate_limited_total
from ak5.server.models.workspace import User, Workspace
from ak5.server.models.workspace_invite import WorkspaceInvite
from ak5.server.services.workspaces import (
    MAX_PENDING_INVITES_PER_WORKSPACE,
    WorkspaceServiceError,
    accept_invite,
    create_invite,
)
from ak5.server.web_auth import (
    check_rate_limit,
    record_failed_attempt,
    record_successful_attempt,
    reset_rate_limits_for_tests,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(autouse=True)
def _reset_web_auth_rate_limits():
    reset_rate_limits_for_tests()
    yield
    reset_rate_limits_for_tests()


def test_local_owner_password_exponential_delay():
    """Verify consecutive failed attempts escalate delay exponentially beyond MAX_ATTEMPTS."""
    ip = "127.0.0.1"

    # 1 to 4 attempts: not blocked
    for _ in range(4):
        record_failed_attempt(ip)
        allowed, wait = check_rate_limit(ip)
        assert allowed is True
        assert wait is None

    # 5th attempt: triggers initial delay (2s)
    record_failed_attempt(ip)
    allowed, wait = check_rate_limit(ip)
    assert allowed is False
    assert wait is not None and 1 <= wait <= 2

    # 6th attempt: 4s
    record_failed_attempt(ip)
    allowed, wait = check_rate_limit(ip)
    assert allowed is False
    assert wait is not None and 3 <= wait <= 4

    # 7th attempt: 8s
    record_failed_attempt(ip)
    allowed, wait = check_rate_limit(ip)
    assert allowed is False
    assert wait is not None and 7 <= wait <= 8

    # Success resets attempts
    record_successful_attempt(ip)
    allowed, wait = check_rate_limit(ip)
    assert allowed is True
    assert wait is None


@pytest.mark.asyncio
async def test_auth_local_login_exponential_delay(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    """Test that POST /auth/local/login enforces rate limiting and exponential lockout."""
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "correct-secret")
    reset_rate_limits_for_tests()

    prev_lockout = rate_limited_total._values.get((("rule", "local_login_lockout"),), 0.0)

    # 5 failed attempts
    for _ in range(5):
        resp = await client.post("/auth/local/login", json={"password": "wrong"})
        assert resp.status_code == 401

    # 6th attempt is blocked with 429
    blocked = await client.post("/auth/local/login", json={"password": "correct-secret"})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert "Too many failed attempts" in blocked.json()["error"]

    # Metric counter incremented
    now_lockout = rate_limited_total._values.get((("rule", "local_login_lockout"),), 0.0)
    assert now_lockout > prev_lockout


@pytest.mark.asyncio
async def test_workspace_pending_invites_cap(test_db: AsyncSession):
    """Test that creating more than 100 pending invites raises QUOTA_EXCEEDED and increments counter."""
    ws = Workspace(workspace_id="ws_pending_cap", name="Cap WS")
    user = User(user_id="usr_cap_owner", email="cap_owner@example.com", display_name="Cap Owner")
    test_db.add_all([ws, user])
    await test_db.flush()

    prev_quota = quota_rejections_total._values.get((("quota", "pending_invites"),), 0.0)

    now = datetime.now(UTC).replace(tzinfo=None)
    # Pre-populate 100 pending invites
    for i in range(MAX_PENDING_INVITES_PER_WORKSPACE):
        inv = WorkspaceInvite(
            invite_id=f"inv_cap_{i}",
            workspace_id="ws_pending_cap",
            email=f"user_{i}@example.com",
            role="member",
            token_hash=f"hash_{i}",
            invited_by="usr_cap_owner",
            expires_at=now + timedelta(days=7),
            created_at=now - timedelta(minutes=15),  # past cooldown
        )
        test_db.add(inv)
    await test_db.commit()

    # 101st invite must be rejected with QUOTA_EXCEEDED
    with pytest.raises(WorkspaceServiceError) as exc_info:
        await create_invite(
            test_db,
            workspace_id="ws_pending_cap",
            email="user_101@example.com",
            role="member",
            invited_by="usr_cap_owner",
        )
    assert exc_info.value.code == ErrorCode.QUOTA_EXCEEDED
    assert "pending invites limit" in str(exc_info.value)

    # Quota metric counter incremented
    now_quota = quota_rejections_total._values.get((("quota", "pending_invites"),), 0.0)
    assert now_quota > prev_quota


@pytest.mark.asyncio
async def test_workspace_email_reinvite_cooldown(test_db: AsyncSession):
    """Test that reinviting the same email within 10 minutes raises RATE_LIMITED and increments counter,
    while revoking the mistaken invite allows immediate re-invite without cooldown error."""
    ws = Workspace(workspace_id="ws_cooldown", name="Cooldown WS")
    user = User(user_id="usr_cd_owner", email="cd_owner@example.com", display_name="CD Owner")
    test_db.add_all([ws, user])
    await test_db.commit()

    prev_cooldown = rate_limited_total._values.get((("rule", "invite_cooldown"),), 0.0)

    # First invite succeeds
    invite1, token1 = await create_invite(
        test_db,
        workspace_id="ws_cooldown",
        email="target@example.com",
        role="member",
        invited_by="usr_cd_owner",
    )
    assert invite1 is not None

    # Immediate second invite for same email triggers cooldown
    with pytest.raises(WorkspaceServiceError) as exc_info:
        await create_invite(
            test_db,
            workspace_id="ws_cooldown",
            email="target@example.com",
            role="member",
            invited_by="usr_cd_owner",
        )
    assert exc_info.value.code == ErrorCode.RATE_LIMITED
    assert "cooldown in effect" in str(exc_info.value)

    # Counter incremented
    now_cooldown = rate_limited_total._values.get((("rule", "invite_cooldown"),), 0.0)
    assert now_cooldown > prev_cooldown

    # Admin revokes the mistaken invite
    invite1.revoked_at = datetime.now(UTC).replace(tzinfo=None)
    await test_db.commit()

    # Re-inviting immediately after revoke succeeds without hitting cooldown
    invite2, token2 = await create_invite(
        test_db,
        workspace_id="ws_cooldown",
        email="target@example.com",
        role="member",
        invited_by="usr_cd_owner",
    )
    assert invite2 is not None


@pytest.mark.asyncio
async def test_accept_invite_uniform_rejection(test_db: AsyncSession):
    """Test that accept_invite returns identical error message regardless of why token is invalid."""
    from ak5.server.services.workspaces import _hash_token

    ws = Workspace(workspace_id="ws_uniform", name="Uniform WS")
    user = User(user_id="usr_uni_test", email="uni@example.com", display_name="Uni Test")
    test_db.add_all([ws, user])
    await test_db.commit()

    now = datetime.now(UTC).replace(tzinfo=None)

    # 1. Non-existent token
    with pytest.raises(WorkspaceServiceError) as e1:
        await accept_invite(test_db, token="non_existent_token_12345", user=user)
    assert e1.value.code == ErrorCode.PAYLOAD_INVALID
    assert str(e1.value) == "Invalid or expired invite token"

    # 2. Revoked token
    inv_rev = WorkspaceInvite(
        invite_id="inv_revoked",
        workspace_id="ws_uniform",
        email="uni@example.com",
        role="member",
        token_hash=_hash_token("token_revoked"),
        invited_by="usr_uni_owner",
        expires_at=now + timedelta(days=7),
        revoked_at=now,
    )
    test_db.add(inv_rev)
    await test_db.commit()

    with pytest.raises(WorkspaceServiceError) as e2:
        await accept_invite(test_db, token="token_revoked", user=user)
    assert e2.value.code == ErrorCode.PAYLOAD_INVALID
    assert str(e2.value) == "Invalid or expired invite token"

    # 3. Already accepted token
    inv_used = WorkspaceInvite(
        invite_id="inv_used",
        workspace_id="ws_uniform",
        email="uni@example.com",
        role="member",
        token_hash=_hash_token("token_used"),
        invited_by="usr_uni_owner",
        expires_at=now + timedelta(days=7),
        accepted_at=now,
    )
    test_db.add(inv_used)
    await test_db.commit()

    with pytest.raises(WorkspaceServiceError) as e3:
        await accept_invite(test_db, token="token_used", user=user)
    assert e3.value.code == ErrorCode.PAYLOAD_INVALID
    assert str(e3.value) == "Invalid or expired invite token"

    # 4. Expired token
    inv_exp = WorkspaceInvite(
        invite_id="inv_expired",
        workspace_id="ws_uniform",
        email="uni@example.com",
        role="member",
        token_hash=_hash_token("token_expired"),
        invited_by="usr_uni_owner",
        expires_at=now - timedelta(days=1),
    )
    test_db.add(inv_exp)
    await test_db.commit()

    with pytest.raises(WorkspaceServiceError) as e4:
        await accept_invite(test_db, token="token_expired", user=user)
    assert e4.value.code == ErrorCode.PAYLOAD_INVALID
    assert str(e4.value) == "Invalid or expired invite token"


@pytest.mark.asyncio
async def test_agent_issuance_rate_limit_under_hosted(
    test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """Test that agent credential issuance is rate-limited under AK5_PROFILE=hosted."""
    from ak5.server.ratelimit import RateLimited, enforce_authenticated
    from starlette.datastructures import Headers

    monkeypatch.setenv("AK5_PROFILE", "hosted")
    monkeypatch.setenv("AK5_RATE_LIMITS", '{"agent_issuance_principal": 2}')

    class FakeRequest:
        method = "POST"
        url = type("URL", (), {"path": "/api/v1/workspaces/ws_test/agents"})()
        headers = Headers()

    req = FakeRequest()
    principal = "usr_agent_creator"
    ws = "ws_test"

    # 1st and 2nd allowed
    await enforce_authenticated(test_db, req, principal_id=principal, workspace_id=ws)
    await enforce_authenticated(test_db, req, principal_id=principal, workspace_id=ws)

    # 3rd hits rate limit
    with pytest.raises(RateLimited) as exc_info:
        await enforce_authenticated(test_db, req, principal_id=principal, workspace_id=ws)
    assert exc_info.value.rule == "agent_issuance_principal"


def test_web_auth_attempts_prune_and_capacity_bound(monkeypatch: pytest.MonkeyPatch):
    """Test that web_auth._attempts prunes expired entries and caps in-memory capacity (#118)."""
    import time

    from ak5.server.web_auth import _AttemptRecord, _attempts, _prune_expired_attempts

    reset_rate_limits_for_tests()
    now = time.monotonic()

    # Add expired entries (WINDOW_SECONDS is 300s)
    _attempts["10.0.0.1"] = _AttemptRecord(count=5, first_attempt=now - 400, blocked_until=now - 10)
    _attempts["10.0.0.2"] = _AttemptRecord(count=2, first_attempt=now - 400, blocked_until=0.0)
    # Add active entry
    _attempts["10.0.0.3"] = _AttemptRecord(count=5, first_attempt=now - 10, blocked_until=now + 50)

    _prune_expired_attempts(now)
    assert "10.0.0.1" not in _attempts
    assert "10.0.0.2" not in _attempts
    assert "10.0.0.3" in _attempts

    # Test size capacity bound
    monkeypatch.setattr("ak5.server.web_auth.MAX_ATTEMPT_ENTRIES", 10)
    for i in range(20):
        _attempts[f"192.168.1.{i}"] = _AttemptRecord(
            count=1, first_attempt=now + i, blocked_until=now + 100
        )
    _prune_expired_attempts(now)
    assert len(_attempts) <= 10
