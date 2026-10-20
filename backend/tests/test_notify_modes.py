"""AK5_NOTIFY modes (deployment.md §3, #108)."""

from __future__ import annotations

import asyncio
import os

import pytest
from ak5.server.config import (
    get_notify_mode,
    notify_poll_interval_seconds,
    settings,
    validate_notify_config,
)
from ak5.server.services import notify as notify_mod
from ak5.server.services.event_bus import EventBus


def test_get_notify_mode_default_inproc(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "NOTIFY", "inproc")
    monkeypatch.setattr(settings, "LONG_POLL_USE_INPROCESS_NOTIFY", True)
    assert get_notify_mode() == "inproc"


def test_legacy_inprocess_flag_maps_to_poll(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "NOTIFY", "inproc")
    monkeypatch.setattr(settings, "LONG_POLL_USE_INPROCESS_NOTIFY", False)
    assert get_notify_mode() == "poll"


def test_validate_rejects_redis(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "NOTIFY", "redis")
    monkeypatch.setattr(settings, "LONG_POLL_USE_INPROCESS_NOTIFY", True)
    with pytest.raises(ValueError, match="not implemented"):
        validate_notify_config()


def test_validate_rejects_pg_on_sqlite(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "NOTIFY", "pg")
    monkeypatch.setattr(settings, "LONG_POLL_USE_INPROCESS_NOTIFY", True)
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    with pytest.raises(ValueError, match="PostgreSQL"):
        validate_notify_config()


def test_validate_pg_requires_asyncpg(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "NOTIFY", "pg")
    monkeypatch.setattr(settings, "LONG_POLL_USE_INPROCESS_NOTIFY", True)
    monkeypatch.setattr(
        settings, "DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/ak5"
    )
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, globals=None, locals=None, fromlist=(), level=0):
        if name == "asyncpg" or name.startswith("asyncpg."):
            raise ImportError("asyncpg missing")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    with pytest.raises(ValueError, match="asyncpg"):
        validate_notify_config()


def test_asyncpg_connect_kwargs_honors_sslmode():
    kwargs = notify_mod._asyncpg_connect_kwargs(
        "postgresql+asyncpg://u:p@db.example:5432/ak5?sslmode=require"
    )
    assert kwargs["host"] == "db.example"
    assert kwargs["ssl"] is True


def test_validate_rejects_inproc_multi_instance(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "NOTIFY", "inproc")
    monkeypatch.setattr(settings, "LONG_POLL_USE_INPROCESS_NOTIFY", True)
    monkeypatch.setattr(settings, "INSTANCES", 2)
    with pytest.raises(ValueError, match="INSTANCES"):
        validate_notify_config()


def test_notify_poll_interval_from_ms(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "NOTIFY_POLL_MS", 500)
    assert notify_poll_interval_seconds() == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_poll_mode_wait_returns_without_bus_wake(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "NOTIFY", "poll")
    monkeypatch.setattr(settings, "LONG_POLL_USE_INPROCESS_NOTIFY", True)
    monkeypatch.setattr(settings, "NOTIFY_POLL_MS", 50)
    assert get_notify_mode() == "poll"
    t0 = asyncio.get_running_loop().time()
    woken = await notify_mod.wait_for_wake(0.2)
    elapsed = asyncio.get_running_loop().time() - t0
    assert woken is False
    assert elapsed >= 0.04


@pytest.mark.asyncio
async def test_inproc_publish_wakes_waiter(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "NOTIFY", "inproc")
    monkeypatch.setattr(settings, "LONG_POLL_USE_INPROCESS_NOTIFY", True)
    bus = EventBus()
    monkeypatch.setattr(notify_mod, "event_bus", bus)

    async def _wake_soon() -> None:
        await asyncio.sleep(0.05)
        await notify_mod.publish_wake(event_type="TEST", data={})

    task = asyncio.create_task(_wake_soon())
    woken = await notify_mod.wait_for_wake(1.0)
    await task
    assert woken is True


@pytest.mark.asyncio
async def test_missed_signal_still_recovers_via_poll(monkeypatch: pytest.MonkeyPatch):
    """Signal loss is delay only — poll mode always re-reads (deployment.md §3)."""
    monkeypatch.setattr(settings, "NOTIFY", "poll")
    monkeypatch.setattr(settings, "NOTIFY_POLL_MS", 20)
    # No publish; waiter returns False after slice — caller re-queries DB.
    assert await notify_mod.wait_for_wake(0.05) is False


@pytest.mark.asyncio
async def test_live_postgres_pg_notify_cross_bus(monkeypatch: pytest.MonkeyPatch):
    """LISTEN on bus B wakes when A signals via pooled pg_notify."""
    pg_url = os.environ.get("AK5_TEST_POSTGRES_URL") or os.environ.get("AK5_POSTGRES_URL")
    if not pg_url:
        pytest.skip("AK5_TEST_POSTGRES_URL is not set")

    from sqlalchemy.ext.asyncio import create_async_engine

    monkeypatch.setattr(settings, "NOTIFY", "pg")
    monkeypatch.setattr(settings, "LONG_POLL_USE_INPROCESS_NOTIFY", True)
    monkeypatch.setattr(settings, "DATABASE_URL", pg_url)
    monkeypatch.setattr(settings, "INSTANCES", 1)
    validate_notify_config()

    pg_engine = create_async_engine(pg_url, future=True)
    monkeypatch.setattr(notify_mod, "_notify_engine", lambda: pg_engine)

    bus_b = EventBus()
    monkeypatch.setattr(notify_mod, "event_bus", bus_b)

    stop = asyncio.Event()
    listener = asyncio.create_task(notify_mod._pg_listen_loop(stop))
    await asyncio.sleep(0.3)

    async def _wait() -> bool:
        return await bus_b.wait_for_notify(3.0)

    waiter = asyncio.create_task(_wait())
    await asyncio.sleep(0.1)
    await notify_mod._pg_notify()
    assert await waiter is True

    stop.set()
    listener.cancel()
    with pytest.raises(asyncio.CancelledError):
        await listener
    await pg_engine.dispose()
