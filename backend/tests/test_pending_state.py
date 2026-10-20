"""#107: pending auth state lives in the DB (deployment.md §6), not process memory.

Two ``AsyncSession``s on the same database stand in for two server instances.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from ak5.server.auth import device_grant as dg
from ak5.server.auth import pending_state as ps
from ak5.server.models.auth_state import DeviceGrantFailure, DevicePending
from conftest import TestAsyncSessionLocal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _store(db: AsyncSession, state: str, client_key: str = "ip-a") -> None:
    await ps.store_oidc_pending(
        db,
        state=state,
        provider_id="mock",
        nonce="n",
        code_verifier="v",
        client_key=client_key,
    )


@pytest.mark.asyncio
async def test_oidc_pending_is_visible_to_another_instance_and_single_use(test_db: AsyncSession):
    await _store(test_db, "state-1")
    async with TestAsyncSessionLocal() as instance_b:  # the callback lands on another instance
        popped = await ps.pop_oidc_pending(instance_b, "state-1")
    assert popped == {"provider_id": "mock", "nonce": "n", "code_verifier": "v"}
    assert await ps.pop_oidc_pending(test_db, "state-1") is None  # consumed exactly once


@pytest.mark.asyncio
async def test_concurrent_callbacks_consume_a_state_only_once(test_db: AsyncSession):
    await _store(test_db, "race")

    async def pop() -> dict | None:
        async with TestAsyncSessionLocal() as session:
            return await ps.pop_oidc_pending(session, "race")

    results = await asyncio.gather(*[pop() for _ in range(5)])
    assert len([r for r in results if r is not None]) == 1


@pytest.mark.asyncio
async def test_expired_oidc_pending_is_rejected_and_purged_on_next_store(test_db: AsyncSession):
    await ps.inject_expired_oidc_pending_for_tests(test_db, state="old")
    assert await ps.pop_oidc_pending(test_db, "old") is None

    await ps.inject_expired_oidc_pending_for_tests(test_db, state="old2")
    await _store(test_db, "fresh")
    assert await ps.count_oidc_pending(test_db) == 1  # "old2" was purged by the write


@pytest.mark.asyncio
async def test_flooding_one_client_never_evicts_other_clients(test_db: AsyncSession):
    """The old table dropped the oldest of 256 entries; now each caller only fills its own budget."""
    await _store(test_db, "real-user", client_key="ip-real")
    for i in range(ps.PENDING_PER_CLIENT_MAX):
        await _store(test_db, f"flood-{i}", client_key="ip-attacker")
    with pytest.raises(ps.PendingLimitError) as exc:
        await _store(test_db, "flood-extra", client_key="ip-attacker")
    assert exc.value.retry_after > 0

    assert await ps.count_oidc_pending(test_db) == ps.PENDING_PER_CLIENT_MAX + 1
    assert await ps.pop_oidc_pending(test_db, "real-user") is not None  # still there
    await _store(test_db, "other-ip-still-ok", client_key="ip-other")


@pytest.mark.asyncio
async def test_login_start_returns_429_for_the_flooding_client_only(
    client, test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    import json

    monkeypatch.setenv(
        "AK5_OIDC_PROVIDERS",
        json.dumps(
            [
                {
                    "id": "mock",
                    "issuer": "https://mock.ak5.test",
                    "client_id": "ak5-test",
                    "client_secret": "secret",
                    "authorization_endpoint": "https://mock.ak5.test/authorize",
                    "token_endpoint": "https://mock.ak5.test/token",
                    "id_token_hs_secret": "mock-id-token-hs-secret-32bytes!!",
                }
            ]
        ),
    )
    monkeypatch.setenv("AK5_PUBLIC_URL", "http://test")
    for _ in range(ps.PENDING_PER_CLIENT_MAX):
        resp = await client.get("/auth/login", params={"provider": "mock"}, follow_redirects=False)
        assert resp.status_code == 302, resp.text
    blocked = await client.get("/auth/login", params={"provider": "mock"}, follow_redirects=False)
    assert blocked.status_code == 429
    assert blocked.json()["error"] == "too_many_pending"
    assert int(blocked.headers["retry-after"]) > 0


@pytest.mark.asyncio
async def test_device_pending_stores_only_a_hash_of_the_code(test_db: AsyncSession):
    await ps.store_device_pending(
        test_db,
        device_code="secret-idp-device-code",
        provider_id="github",
        interval=5,
        expires_in=600,
        client_key="ip-a",
    )
    rows = (await test_db.execute(select(DevicePending))).scalars().all()
    assert [r.device_code_hash for r in rows] == [ps.hash_device_code("secret-idp-device-code")]
    assert "secret-idp-device-code" not in rows[0].device_code_hash
    async with TestAsyncSessionLocal() as other:
        assert await ps.load_device_pending(other, "secret-idp-device-code") is not None
        assert await ps.load_device_pending(other, "another-code") is None


@pytest.mark.asyncio
async def test_device_poll_slot_is_claimed_by_exactly_one_instance(test_db: AsyncSession):
    code = await ps.mint_local_device_code_for_tests(test_db, "github", interval=5)

    async def claim() -> bool:
        async with TestAsyncSessionLocal() as session:
            return await ps.claim_device_poll(session, code, interval=5)

    results = await asyncio.gather(*[claim() for _ in range(5)])
    assert results.count(True) == 1  # the others get slow_down
    assert await ps.claim_device_poll(test_db, code, interval=0) is True  # interval elapsed

    await ps.set_device_interval(test_db, code, 10)
    row = await ps.load_device_pending(test_db, code)
    await test_db.refresh(row)
    assert row.interval == 10
    await ps.delete_device_pending(test_db, code)
    assert await ps.load_device_pending(test_db, code) is None


@pytest.mark.asyncio
async def test_device_pending_is_capped_per_client(test_db: AsyncSession):
    for i in range(ps.PENDING_PER_CLIENT_MAX):
        await ps.mint_local_device_code_for_tests(test_db, "github", client_key="ip-a")
    with pytest.raises(ps.PendingLimitError):
        await ps.mint_local_device_code_for_tests(test_db, "github", client_key="ip-a")
    await ps.mint_local_device_code_for_tests(test_db, "github", client_key="ip-b")


@pytest.mark.asyncio
async def test_user_code_failures_are_shared_across_instances(test_db: AsyncSession):
    """N instances must not multiply the brute-force budget (#55 review note)."""
    await dg.check_user_code_rate_limit(test_db, "ip-a")  # nothing recorded yet
    for _ in range(dg._RATE_MAX_FAILURES):
        async with TestAsyncSessionLocal() as instance:  # failures land on different instances
            await dg.record_user_code_failure(instance, "ip-a")
    with pytest.raises(dg.DeviceGrantError) as exc:
        await dg.check_user_code_rate_limit(test_db, "ip-a")
    assert exc.value.error == "slow_down"
    await dg.check_user_code_rate_limit(test_db, "ip-b")  # other clients unaffected


@pytest.mark.asyncio
async def test_user_code_failures_age_out_of_the_window(test_db: AsyncSession):
    from datetime import UTC, datetime

    long_ago = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=dg._RATE_WINDOW_SECONDS + 5)
    test_db.add_all(
        [DeviceGrantFailure(client_key="ip-a", created_at=long_ago) for _ in range(dg._RATE_MAX_FAILURES)]
    )
    await test_db.commit()
    await dg.check_user_code_rate_limit(test_db, "ip-a")  # stale rows do not count

    await dg.record_user_code_failure(test_db, "ip-a")  # and are purged by the next write
    remaining = (await test_db.execute(select(DeviceGrantFailure))).scalars().all()
    assert len(remaining) == 1
