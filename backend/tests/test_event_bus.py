"""event_bus is a wake-up signal only; events are read from the log (#35)."""

from __future__ import annotations

import asyncio

import pytest
from ak5.server.services.event_bus import EventBus


@pytest.mark.asyncio
async def test_publish_wakes_waiters_and_keeps_no_history():
    bus = EventBus()
    waiter = asyncio.create_task(bus.wait_for_notify(2.0))
    await asyncio.sleep(0)
    await bus.publish("WORK_AVAILABLE", {"ticket_id": "TK-1"})
    assert await waiter is True
    assert not hasattr(bus, "subscribe") and not hasattr(bus, "_history")


@pytest.mark.asyncio
async def test_wait_times_out_without_publish():
    assert await EventBus().wait_for_notify(0.01) is False
