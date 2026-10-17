"""Daemon IPC FastAPI app over UDS (client.md §5)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ak5.daemon import __version__
from ak5.daemon.credentials import ProfileCredential, load_credential, mark_revoked, save_credential
from ak5.daemon.profiles import list_profiles, resolve_profile_name
from ak5.daemon.state import DaemonState


class TokenRequest(BaseModel):
    profile: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    expires_at: str = ""
    profile: str
    actor_id: str = ""


class SubscriberCreate(BaseModel):
    profile: str | None = None
    board_id: str | None = None
    events: list[str] = Field(default_factory=list)
    exec_command: str
    id: str | None = None


class LoginRequest(BaseModel):
    """Device-grant stub (client.md §5) — interface only until #26."""

    profile: str
    principal: str = "agent"
    role: str = "worker"
    capabilities: list[str] = Field(default_factory=list)


class LogoutRequest(BaseModel):
    profile: str


def create_daemon_app(state: DaemonState | None = None) -> FastAPI:
    app = FastAPI(title="AK5 Client Daemon", version=__version__)
    app.state.daemon_state = state or DaemonState()
    app.state.started_at = time.time()
    # Waiters: profile -> list of asyncio.Event
    app.state.waiters: dict[str, list[asyncio.Event]] = {}
    app.state.poller_stop: asyncio.Event | None = None
    app.state.poller_task: asyncio.Task | None = None

    def _state() -> DaemonState:
        return app.state.daemon_state

    def _wake(profile: str) -> None:
        for ev in list(app.state.waiters.get(profile, [])):
            ev.set()

    @app.on_event("startup")
    async def _start_poller() -> None:
        from ak5.daemon.paths import chmod_socket, socket_path
        from ak5.daemon.poller import poll_loop

        chmod_socket(socket_path())
        stop = asyncio.Event()
        app.state.poller_stop = stop
        app.state.poller_task = asyncio.create_task(
            poll_loop(_state(), wake=_wake, stop=stop)
        )

    @app.on_event("shutdown")
    async def _stop_poller() -> None:
        stop = app.state.poller_stop
        task = app.state.poller_task
        if stop is not None:
            stop.set()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    @app.get("/v1/status")
    async def status() -> dict[str, Any]:
        profiles = [
            {
                "name": p.name,
                "server": p.server,
                "principal": p.principal,
                "role": p.role,
                "has_credential": load_credential(p.name) is not None,
            }
            for p in list_profiles()
        ]
        return {
            "version": __version__,
            "pid": os.getpid(),
            "uptime_seconds": round(time.time() - app.state.started_at, 3),
            "profiles": profiles,
            "subscribers": len(_state().list_subscribers()),
        }

    @app.post("/v1/token", response_model=TokenResponse)
    async def token(body: TokenRequest) -> TokenResponse:
        name = resolve_profile_name(body.profile)
        if not name:
            raise HTTPException(status_code=400, detail="profile required")
        cred = load_credential(name)
        if cred is None or not cred.access_token:
            raise HTTPException(status_code=401, detail=f"profile '{name}' not logged in")
        if cred.status == "revoked":
            raise HTTPException(status_code=401, detail=f"profile '{name}' revoked")
        return TokenResponse(
            access_token=cred.access_token,
            expires_at=cred.expires_at,
            profile=name,
            actor_id=cred.actor_id,
        )

    @app.post("/v1/login")
    async def login(body: LoginRequest) -> dict[str, Any]:
        # Interface stub — real device grant is #26.
        return {
            "login_id": f"login-{body.profile}",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://example.invalid/device",
            "message": "device grant not implemented yet (#26); use credential import",
        }

    @app.get("/v1/login/{login_id}")
    async def login_poll(login_id: str) -> dict[str, Any]:
        return {"login_id": login_id, "status": "pending", "message": "device grant deferred to #26"}

    @app.post("/v1/logout")
    async def logout(body: LogoutRequest) -> dict[str, str]:
        mark_revoked(body.profile)
        return {"status": "ok", "profile": body.profile}

    @app.post("/v1/credentials")
    async def put_credentials(body: dict[str, Any]) -> dict[str, str]:
        """Adapter: store access token for a profile (pre-#26 login bridge)."""
        profile = str(body.get("profile") or "").strip()
        token = str(body.get("access_token") or "").strip()
        if not profile or not token:
            raise HTTPException(status_code=400, detail="profile and access_token required")
        save_credential(
            profile,
            ProfileCredential(
                access_token=token,
                refresh_token=str(body.get("refresh_token") or ""),
                expires_at=str(body.get("expires_at") or ""),
                actor_id=str(body.get("actor_id") or ""),
                actor_type=str(body.get("actor_type") or "agent"),
                status="active",
            ),
        )
        return {"status": "ok", "profile": profile}

    @app.get("/v1/subscribers")
    async def list_subscribers(profile: str | None = None) -> list[dict[str, Any]]:
        rows = _state().list_subscribers(profile)
        return [
            {
                "id": s.id,
                "profile": s.profile,
                "board_id": s.board_id,
                "events": s.events,
                "exec_command": s.exec_command,
                "local_cursor": s.local_cursor,
                "dead_letter": s.dead_letter,
            }
            for s in rows
        ]

    @app.post("/v1/subscribers")
    async def create_subscriber(body: SubscriberCreate) -> dict[str, Any]:
        name = resolve_profile_name(body.profile)
        if not name:
            raise HTTPException(status_code=400, detail="profile required")
        if not body.exec_command.strip():
            raise HTTPException(status_code=400, detail="exec_command required")
        sub = _state().add_subscriber(
            profile=name,
            exec_command=body.exec_command,
            board_id=body.board_id,
            events=body.events,
            subscriber_id=body.id,
        )
        return {
            "id": sub.id,
            "profile": sub.profile,
            "board_id": sub.board_id,
            "events": sub.events,
            "exec_command": sub.exec_command,
            "local_cursor": sub.local_cursor,
        }

    @app.delete("/v1/subscribers/{subscriber_id}")
    async def delete_subscriber(subscriber_id: str) -> dict[str, str]:
        if not _state().remove_subscriber(subscriber_id):
            raise HTTPException(status_code=404, detail="subscriber not found")
        return {"status": "ok", "id": subscriber_id}

    @app.get("/v1/events")
    async def events(
        request: Request,
        profile: str | None = None,
        cursor: int = 0,
        wait: float = 0,
        events: str | None = Query(default=None, description="Comma-separated event types"),
        limit: int = 50,
    ) -> dict[str, Any]:
        name = resolve_profile_name(profile)
        if not name:
            raise HTTPException(status_code=400, detail="profile required")
        types = [e.strip() for e in (events or "").split(",") if e.strip()] or None
        batch = _state().events_since(name, cursor, limit=limit, event_types=types)
        if batch or wait <= 0:
            next_cursor = batch[-1]["seq"] if batch else cursor
            return {"events": batch, "next_cursor": next_cursor}

        ev = asyncio.Event()
        app.state.waiters.setdefault(name, []).append(ev)
        try:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(ev.wait(), timeout=wait)
        finally:
            waiters = app.state.waiters.get(name, [])
            if ev in waiters:
                waiters.remove(ev)
        batch = _state().events_since(name, cursor, limit=limit, event_types=types)
        next_cursor = batch[-1]["seq"] if batch else cursor
        return {"events": batch, "next_cursor": next_cursor}

    @app.post("/v1/spool")
    async def inject_spool(body: dict[str, Any]) -> dict[str, Any]:
        """Test/helper: append an event to the profile spool and wake waiters."""
        profile = resolve_profile_name(str(body.get("profile") or "") or None)
        if not profile:
            raise HTTPException(status_code=400, detail="profile required")
        event_type = str(body.get("type") or body.get("event_type") or "WORK_AVAILABLE")
        payload = body.get("data") if isinstance(body.get("data"), dict) else body
        seq = _state().append_spool(
            profile=profile,
            event_type=event_type,
            payload=dict(payload),
            ticket_id=body.get("ticket_id"),
            board_id=body.get("board_id"),
        )
        from ak5.daemon.hooks import dispatch_new_events

        dispatch_new_events(_state(), profile)
        _wake(profile)
        return {"seq": seq}

    setattr(app.state, "wake_profile", _wake)
    return app
