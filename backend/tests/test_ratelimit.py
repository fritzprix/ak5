"""#114 / #122: rate limits (operations.md §2) and trusted-proxy client IPs."""

from __future__ import annotations

import asyncio
import io
import json

import pytest
from ak5.client.errors import ClientError
from ak5.client.http import _parse_retry_after
from ak5.client.output import print_error
from ak5.contracts.errors import HTTP_STATUS, ErrorCode
from ak5.daemon import poller
from ak5.server import ratelimit as rl
from ak5.server.main import app
from ak5.server.models.actor import Actor
from ak5.server.models.rate_counter import RateCounter
from ak5.server.web_auth import client_ip_from_headers
from conftest import TestAsyncSessionLocal
from helpers_workflow import board_ticket
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# --- configuration -------------------------------------------------------------------------


def test_limits_are_off_by_default_and_hosted_turns_the_defaults_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AK5_PROFILE", raising=False)
    monkeypatch.delenv("AK5_RATE_LIMITS", raising=False)
    assert rl.active_limits() == {}

    monkeypatch.setenv("AK5_PROFILE", "hosted")
    assert rl.active_limits() == rl.HOSTED_DEFAULTS
    assert rl.active_limits()["read_principal"] == 300

    monkeypatch.setenv("AK5_RATE_LIMITS", json.dumps({"read_principal": 7, "login_ip": 0}))
    limits = rl.active_limits()
    assert limits["read_principal"] == 7 and "login_ip" not in limits  # 0 switches a rule off
    assert limits["write_principal"] == 120  # untouched rules keep the hosted default


def test_overrides_work_without_the_hosted_profile_and_bad_input_is_ignored(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AK5_PROFILE", raising=False)
    monkeypatch.setenv("AK5_RATE_LIMITS", json.dumps({"write_principal": 5, "bogus": 3, "read_principal": -1}))
    assert rl.active_limits() == {"write_principal": 5}
    monkeypatch.setenv("AK5_RATE_LIMITS", "not json")
    assert rl.active_limits() == {}
    monkeypatch.setenv("AK5_RATE_LIMITS", "[1, 2]")
    assert rl.active_limits() == {}


def test_error_contract():
    assert HTTP_STATUS[ErrorCode.RATE_LIMITED] == 429
    from ak5.contracts.errors import EXIT_CODE

    assert EXIT_CODE[ErrorCode.RATE_LIMITED] == 3  # retryable, cli.md §3.1


# --- counting --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hit_counts_within_a_window_and_resets_in_the_next(test_db: AsyncSession):
    base = 1_700_000_040.0  # a window boundary (multiple of 60)
    counts = [(await rl.hit(test_db, "k", now=base + i)).count for i in range(3)]
    assert counts == [1, 2, 3]
    assert (await rl.hit(test_db, "k", now=base + 59.5)).count == 4
    assert (await rl.hit(test_db, "k", now=base + 60)).count == 1  # next window starts fresh
    assert (await rl.hit(test_db, "other", now=base)).count == 1  # keys are independent
    retry = (await rl.hit(test_db, "k", now=base + 70)).retry_after
    assert 1 <= retry <= 60


@pytest.mark.asyncio
async def test_concurrent_hits_never_lose_an_increment(test_db: AsyncSession):
    async def one() -> int:
        async with TestAsyncSessionLocal() as session:
            return (await rl.hit(session, "race", now=1_700_000_040.0)).count

    counts = await asyncio.gather(*[one() for _ in range(12)])
    assert sorted(counts) == list(range(1, 13))  # the shared counter is atomic across instances


# --- authenticated routes -----------------------------------------------------------------------

@pytest.fixture
async def second_user(test_db: AsyncSession, auth_headers) -> dict[str, str]:
    """Another human principal in the same workspace (own per-principal budget)."""
    test_db.add(
        Actor(
            actor_id="user_two",
            actor_type="human",
            name="Two",
            role="human",
            capabilities="[]",
            status="idle",
        )
    )
    await test_db.commit()
    return auth_headers("user_two", "human")


async def _rows(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(RateCounter))).scalar_one()


@pytest.mark.asyncio
async def test_nothing_is_counted_when_limits_are_off(client: AsyncClient, test_db: AsyncSession, monkeypatch):
    monkeypatch.delenv("AK5_PROFILE", raising=False)
    monkeypatch.delenv("AK5_RATE_LIMITS", raising=False)
    for _ in range(30):
        assert (await client.get("/api/v1/boards")).status_code == 200
    assert await _rows(test_db) == 0


@pytest.mark.asyncio
async def test_reads_are_limited_per_principal_with_retry_after(
    client: AsyncClient, auth_headers, second_user, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AK5_RATE_LIMITS", json.dumps({"read_principal": 3}))
    for _ in range(3):
        assert (await client.get("/api/v1/boards")).status_code == 200
    blocked = await client.get("/api/v1/boards")
    assert blocked.status_code == 429
    body = blocked.json()["error"]
    assert body["code"] == "RATE_LIMITED"
    assert body["details"] == {"limit": 3, "window_seconds": 60, "scope": "principal"}
    assert 1 <= int(blocked.headers["retry-after"]) <= 60
    assert "usr_" not in blocked.text and "user_pm" not in blocked.text  # no one's identity in the body

    other = await client.get("/api/v1/boards", headers=second_user)
    assert other.status_code == 200  # another principal has its own budget


@pytest.mark.asyncio
async def test_writes_have_principal_and_workspace_budgets(
    client: AsyncClient, auth_headers, second_user, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AK5_RATE_LIMITS", json.dumps({"write_principal": 2, "write_workspace": 3}))
    pm = auth_headers("user_pm", "human")
    for i in range(2):
        r = await client.post("/api/v1/tickets", json=board_ticket(f"T{i}"), headers=pm)
        assert r.status_code == 201, r.text
    third = await client.post("/api/v1/tickets", json=board_ticket("T2"), headers=pm)
    assert third.status_code == 429 and third.json()["error"]["details"]["scope"] == "principal"

    # a second principal is fine until the *workspace* budget (3) is used up
    triage = second_user
    assert (await client.post("/api/v1/tickets", json=board_ticket("T3"), headers=triage)).status_code == 201
    over = await client.post("/api/v1/tickets", json=board_ticket("T4"), headers=triage)
    assert over.status_code == 429 and over.json()["error"]["details"]["scope"] == "workspace"


@pytest.mark.asyncio
async def test_uploads_have_their_own_budget(client: AsyncClient, tmp_path, monkeypatch: pytest.MonkeyPatch):
    from ak5.config import settings

    monkeypatch.setattr(settings, "ATTACHMENTS_DIR", str(tmp_path))
    tid = (await client.post("/api/v1/tickets", json=board_ticket("Upload"))).json()["ticket_id"]
    monkeypatch.setenv("AK5_RATE_LIMITS", json.dumps({"upload_principal": 1}))

    def send():
        return client.post(
            f"/api/v1/tickets/{tid}/attachments", files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")}
        )

    assert (await send()).status_code == 201
    assert (await send()).status_code == 429


def test_streams_are_exempt_from_request_counting():
    for path in ("/api/v1/events/stream", "/api/v1/subscriptions/sub_1/events"):
        assert rl._EXEMPT.search(path), path
    for path in ("/api/v1/tickets", "/api/v1/subscriptions/sub_1", "/api/v1/boards/b/events-log"):
        assert not rl._EXEMPT.search(path), path


# --- unauthenticated entry points (per IP) ----------------------------------------------------------


def _client_from(ip: str) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 5000)), base_url="http://test")


async def _login(c: AsyncClient, **headers: str):
    return await c.post("/auth/local/login", json={"password": "nope"}, headers=headers)


@pytest.mark.asyncio
async def test_login_is_limited_per_client_ip(test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_RATE_LIMITS", json.dumps({"login_ip": 2}))
    async with _client_from("198.51.100.7") as a, _client_from("198.51.100.8") as b:
        assert (await _login(a)).status_code != 429
        assert (await _login(a)).status_code != 429
        blocked = await _login(a)
        assert blocked.status_code == 429
        assert blocked.json()["error"]["details"]["scope"] == "ip"
        assert int(blocked.headers["retry-after"]) >= 1
        assert (await _login(b)).status_code != 429  # a different address has its own budget


@pytest.mark.asyncio
async def test_spoofed_forwarded_for_cannot_pick_a_fresh_bucket(test_db: AsyncSession, monkeypatch):
    """#122: without AK5_TRUSTED_PROXIES the header is ignored, so rotating it buys nothing."""
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    monkeypatch.setenv("AK5_RATE_LIMITS", json.dumps({"login_ip": 2}))
    async with _client_from("198.51.100.7") as attacker:
        statuses = [(await _login(attacker, **{"X-Forwarded-For": f"203.0.113.{i}"})).status_code for i in range(4)]
    assert statuses[:2] != [429, 429] and statuses[2:] == [429, 429]


@pytest.mark.asyncio
async def test_forwarded_for_is_honoured_from_a_trusted_proxy(test_db: AsyncSession, monkeypatch):
    monkeypatch.setenv("AK5_TRUSTED_PROXIES", "10.0.0.0/8")
    monkeypatch.setenv("AK5_RATE_LIMITS", json.dumps({"login_ip": 2}))
    async with _client_from("10.1.2.3") as proxy:  # the reverse proxy in front of us
        for _ in range(2):
            assert (await _login(proxy, **{"X-Forwarded-For": "203.0.113.5"})).status_code != 429
        assert (await _login(proxy, **{"X-Forwarded-For": "203.0.113.5"})).status_code == 429
        assert (await _login(proxy, **{"X-Forwarded-For": "203.0.113.6"})).status_code != 429  # another end user


@pytest.mark.asyncio
async def test_device_authorization_and_invite_accept_are_limited(test_db: AsyncSession, monkeypatch):
    monkeypatch.setenv("AK5_RATE_LIMITS", json.dumps({"device_ip": 2, "invite_ip": 1}))
    async with _client_from("198.51.100.20") as c:
        codes = [
            (await c.post("/oauth/device_authorization", data={"client_id": "ak5-cli"})).status_code
            for _ in range(3)
        ]
        assert codes[2] == 429 and 429 not in codes[:2]
        first = await c.post("/api/v1/workspaces/invites/accept", json={"token": "x"})
        assert first.status_code != 429
        assert (await c.post("/api/v1/workspaces/invites/accept", json={"token": "x"})).status_code == 429


# --- client_ip_from_headers (#122) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("trusted", "peer", "xff", "real", "expected"),
    [
        ("", "198.51.100.7", "1.2.3.4", None, "198.51.100.7"),  # nothing trusted: headers ignored
        ("10.0.0.0/8", "198.51.100.7", "1.2.3.4", None, "198.51.100.7"),  # peer is not a proxy
        ("10.0.0.0/8", "10.0.0.1", "203.0.113.9", None, "203.0.113.9"),
        ("10.0.0.0/8", "10.0.0.1", "6.6.6.6, 203.0.113.9", None, "203.0.113.9"),  # client-supplied prefix ignored
        ("10.0.0.0/8", "10.0.0.1", "203.0.113.9, 10.0.0.2", None, "203.0.113.9"),  # inner proxy hop skipped
        ("10.0.0.0/8", "10.0.0.1", "10.0.0.5, 10.0.0.2", None, "10.0.0.5"),  # all hops are proxies
        ("10.0.0.0/8", "10.0.0.1", None, "203.0.113.4", "203.0.113.4"),  # X-Real-IP from a trusted peer
        ("10.0.0.0/8", "10.0.0.1", "not-an-ip", None, "10.0.0.1"),  # garbage: do not guess
        ("10.0.0.0/8", "10.0.0.1", None, None, "10.0.0.1"),
        ("bogus, 10.0.0.1", "10.0.0.1", "203.0.113.9", None, "203.0.113.9"),  # bad entries are skipped
    ],
)
def test_client_ip_from_headers(monkeypatch, trusted, peer, xff, real, expected):
    monkeypatch.setenv("AK5_TRUSTED_PROXIES", trusted)
    assert client_ip_from_headers(xff, real, peer) == expected


# --- hosted profile gate ----------------------------------------------------------------------------------


def test_hosted_profile_refuses_an_exposed_bind_without_trusted_proxies(monkeypatch: pytest.MonkeyPatch):
    from ak5.cli.commands.serve import assert_safe_bind

    monkeypatch.setenv("AK5_PROFILE", "hosted")
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    with pytest.raises(SystemExit):
        assert_safe_bind("0.0.0.0", insecure=True)  # even --insecure does not waive it
    assert_safe_bind("127.0.0.1", insecure=False)  # loopback is not exposed

    monkeypatch.setenv("AK5_TRUSTED_PROXIES", "10.0.0.0/8")
    assert_safe_bind("0.0.0.0", insecure=True)  # configured: falls through to the usual auth rules

    monkeypatch.setenv("AK5_PROFILE", "local")
    monkeypatch.delenv("AK5_TRUSTED_PROXIES", raising=False)
    assert_safe_bind("0.0.0.0", insecure=True)


# --- clients ------------------------------------------------------------------------------------------------


def test_retry_after_parsing_for_cli_and_daemon():
    assert _parse_retry_after("12") == 12.0
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert poller.retry_after_seconds("30") == 30.0
    assert poller.retry_after_seconds("0") == 1.0  # never a busy loop
    assert poller.retry_after_seconds("99999") == 300.0  # and never an unbounded stall
    assert poller.retry_after_seconds(None) == 5.0


def test_daemon_backs_off_a_rate_limited_profile():
    poller._rate_limited_until.pop("p1", None)
    assert poller.rate_limit_remaining("p1") == 0
    poller.note_rate_limited("p1", "20")
    assert 15 < poller.rate_limit_remaining("p1") <= 20
    assert poller.rate_limit_remaining("other-profile") == 0
    poller._rate_limited_until.pop("p1", None)


def test_cli_shows_retry_after(capsys: pytest.CaptureFixture[str]):
    err = ClientError("Too many requests", code=ErrorCode.RATE_LIMITED, exit_code=3, retry_after=17)
    with pytest.raises(SystemExit) as exc:
        print_error(err, json_mode=False)
    assert exc.value.code == 3
    assert "retry after 17s" in capsys.readouterr().err
