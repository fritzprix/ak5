"""Server long-poll → local spool (client.md §4.1–§4.2)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import httpx

from ak5.daemon.credentials import load_credential
from ak5.daemon.hooks import dispatch_new_events
from ak5.daemon.profiles import Profile, list_profiles
from ak5.daemon.state import DaemonState

logger = logging.getLogger(__name__)


def _api_base(server: str) -> str:
    s = server.rstrip("/")
    if s.endswith("/api/v1"):
        return s
    return f"{s}/api/v1"


def _tip_from_expired(body: dict[str, Any]) -> int:
    """CURSOR_EXPIRED tip from error envelope (events.md)."""
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    details = err.get("details") if isinstance(err.get("details"), dict) else {}
    for key in ("cursor", "oldest_seq", "tip"):
        raw = details.get(key)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    for key in ("cursor", "next_cursor"):
        raw = body.get(key)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    return 0


async def _ack_server(
    client: httpx.AsyncClient, sub_id: str, cursor: int
) -> None:
    with contextlib.suppress(httpx.HTTPError):
        await client.post(f"/subscriptions/{sub_id}/ack", json={"cursor": cursor})


async def _ensure_subscription(
    client: httpx.AsyncClient, profile: Profile, state: DaemonState
) -> str:
    sub_id, _cursor = state.get_server_cursor(profile.name)
    if sub_id:
        return sub_id
    # Broad filter — local subscribers refine (client.md §4.2)
    resp = await client.post(
        "/subscriptions",
        json={
            "filter": {
                "events": ["WORK_AVAILABLE", "HUMAN_ACTION_REQUIRED", "TICKET_TRANSITIONED"]
            },
            "start": "now",
        },
    )
    if resp.status_code not in (200, 201):
        resp.raise_for_status()
    body = resp.json()
    sid = str(body.get("subscription_id") or body.get("id") or "")
    if not sid:
        raise RuntimeError("subscription create returned no id")
    ack = int(body.get("ack_cursor") or 0)
    state.set_server_cursor(profile.name, subscription_id=sid, ack_cursor=ack)
    return sid


async def poll_profile_once(
    profile: Profile,
    state: DaemonState,
    *,
    wake: Any | None = None,
    wait: float = 25.0,
) -> int:
    """Pull one batch from the server into the spool. Returns events written."""
    cred = load_credential(profile.name)
    if cred is None or not cred.access_token or cred.status == "revoked":
        return 0
    base = _api_base(profile.server)
    headers = {
        "Authorization": f"Bearer {cred.access_token}",
        "X-AK5-Consumer": f"daemon-{profile.name}",
    }
    written = 0
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=wait + 10) as client:
        try:
            sub_id = await _ensure_subscription(client, profile, state)
        except Exception:
            logger.debug("subscription ensure failed for %s", profile.name, exc_info=True)
            return 0
        _sid, cursor = state.get_server_cursor(profile.name)
        try:
            resp = await client.get(
                f"/subscriptions/{sub_id}/events",
                params={"wait": wait, "limit": 50, "cursor": cursor},
            )
        except httpx.HTTPError:
            logger.debug("long-poll failed for %s", profile.name, exc_info=True)
            return 0
        if resp.status_code == 401:
            from ak5.daemon.credentials import mark_revoked

            mark_revoked(profile.name)
            return 0
        if resp.status_code == 410:
            body: dict[str, Any] = {}
            if resp.headers.get("content-type", "").startswith("application/json"):
                with contextlib.suppress(Exception):
                    parsed = resp.json()
                    if isinstance(parsed, dict):
                        body = parsed
            tip = _tip_from_expired(body)
            state.append_spool(
                profile=profile.name,
                event_type="RESYNC",
                payload={"reason": "CURSOR_EXPIRED", "tip": tip},
            )
            state.set_server_cursor(profile.name, subscription_id=sub_id, ack_cursor=tip)
            await _ack_server(client, sub_id, tip)
            await dispatch_new_events(state, profile.name)
            if wake:
                wake(profile.name)
            return 1
        if resp.status_code != 200:
            return 0
        body = resp.json()
        events = body.get("events") or []
        next_cursor = int(body.get("next_cursor") if body.get("next_cursor") is not None else cursor)
        for ev in events:
            et = str(ev.get("type") or "")
            state.append_spool(
                profile=profile.name,
                event_type=et,
                payload=ev if isinstance(ev, dict) else {"raw": ev},
                ticket_id=ev.get("ticket_id") if isinstance(ev, dict) else None,
                board_id=ev.get("board_id") if isinstance(ev, dict) else None,
            )
            written += 1
        # Always persist + ack next_cursor (even when the batch is empty)
        if next_cursor != cursor or events:
            state.set_server_cursor(
                profile.name, subscription_id=sub_id, ack_cursor=next_cursor
            )
            await _ack_server(client, sub_id, next_cursor)
        if events:
            await dispatch_new_events(state, profile.name)
            if wake:
                wake(profile.name)
    return written


async def poll_loop(state: DaemonState, *, wake: Any | None = None, stop: asyncio.Event) -> None:
    """Background loop: one long-poll per profile, forever until stop."""
    while not stop.is_set():
        profiles = list_profiles()
        if not profiles:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=2.0)
            continue
        tasks = [
            asyncio.create_task(poll_profile_once(p, state, wake=wake, wait=20.0))
            for p in profiles
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=0.5)
