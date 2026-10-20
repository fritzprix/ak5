"""Daemon IPC FastAPI app over UDS (client.md §5)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ak5.client.errors import ClientError
from ak5.daemon import __version__
from ak5.daemon.credentials import (
    ProfileCredential,
    delete_credential,
    load_credential,
    mark_revoked,
    save_credential,
)
from ak5.daemon.oauth_flow import (
    poll_device_token,
    refresh_access_token,
    revoke_token,
    start_device_login,
)
from ak5.daemon.profiles import Profile, get_profile, list_profiles, resolve_profile_name, upsert_profile
from ak5.daemon.state import DaemonState

# Access tokens refresh this many seconds before stated expiry.
_TOKEN_REFRESH_SKEW_SECONDS = 60


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
    """Start OAuth device grant (client.md §5 / #26)."""

    profile: str
    server: str
    principal: Literal["user", "agent"] = "agent"
    name: str | None = None
    role: str = "worker"
    capabilities: list[str] = Field(default_factory=list)
    workspace_id: str | None = None


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
    # login_id -> pending device grant metadata
    app.state.pending_logins: dict[str, dict[str, Any]] = {}

    def _state() -> DaemonState:
        return app.state.daemon_state

    def _wake(profile: str) -> None:
        for ev in list(app.state.waiters.get(profile, [])):
            ev.set()

    def _token_needs_refresh(expires_at: str) -> bool:
        if not expires_at.strip():
            return False
        try:
            raw = expires_at.replace("Z", "+00:00")
            when = datetime.fromisoformat(raw)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            remaining = (when - datetime.now(UTC)).total_seconds()
            return remaining <= _TOKEN_REFRESH_SKEW_SECONDS
        except ValueError:
            return False

    def _persist_login_success(pending: dict[str, Any], bundle: Any) -> ProfileCredential:
        profile_name = str(pending["profile"])
        actor_type = "user" if pending["principal"] == "user" else "agent"
        actor_id = str(getattr(bundle, "agent_id", "") or pending.get("name") or "")
        cred = ProfileCredential(
            access_token=bundle.access_token,
            refresh_token=bundle.refresh_token,
            expires_at=bundle.expires_at,
            actor_id=actor_id,
            actor_type=actor_type,
            status="active",
        )
        save_credential(profile_name, cred)
        upsert_profile(
            Profile(
                name=profile_name,
                server=str(pending["server"]),
                workspace=str(pending.get("workspace_id") or ""),
                principal=str(pending["principal"]),
                role=str(pending.get("role") or ""),
                actor_id=actor_id,
            )
        )
        return cred

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

        profile = get_profile(name)
        server = profile.server if profile is not None else ""
        if (
            cred.refresh_token
            and server
            and _token_needs_refresh(cred.expires_at)
        ):
            try:
                bundle = await asyncio.to_thread(
                    refresh_access_token, server, cred.refresh_token
                )
            except ClientError as exc:
                mark_revoked(name)
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            cred = ProfileCredential(
                access_token=bundle.access_token,
                refresh_token=bundle.refresh_token or cred.refresh_token,
                expires_at=bundle.expires_at,
                actor_id=cred.actor_id,
                actor_type=cred.actor_type,
                status="active",
            )
            save_credential(name, cred)

        return TokenResponse(
            access_token=cred.access_token,
            expires_at=cred.expires_at,
            profile=name,
            actor_id=cred.actor_id,
        )

    @app.post("/v1/login")
    async def login(body: LoginRequest) -> dict[str, Any]:
        if not body.profile.strip() or not body.server.strip():
            raise HTTPException(status_code=400, detail="profile and server required")
        principal = body.principal
        name = (body.name or body.profile).strip()
        try:
            started = await asyncio.to_thread(
                start_device_login,
                body.server,
                principal=principal,
                name=name if principal == "agent" else None,
                workflow_role=body.role if principal == "agent" else None,
                capabilities=body.capabilities if principal == "agent" else None,
                workspace_id=body.workspace_id,
            )
        except ClientError as exc:
            raise HTTPException(status_code=exc.http_status or 400, detail=str(exc)) from exc

        login_id = f"login_{uuid.uuid4().hex[:16]}"
        app.state.pending_logins[login_id] = {
            "profile": body.profile.strip(),
            "server": body.server.strip(),
            "principal": principal,
            "name": name,
            "role": body.role,
            "workspace_id": body.workspace_id or "",
            "device_code": started.device_code,
            "user_code": started.user_code,
            "interval": started.interval,
            "expires_at": time.time() + started.expires_in,
        }
        return {
            "login_id": login_id,
            "user_code": started.user_code,
            "verification_uri": started.verification_uri,
            "verification_uri_complete": started.verification_uri_complete,
            "expires_in": started.expires_in,
            "interval": started.interval,
        }

    @app.get("/v1/login/{login_id}")
    async def login_poll(login_id: str) -> dict[str, Any]:
        pending = app.state.pending_logins.get(login_id)
        if pending is None:
            raise HTTPException(status_code=404, detail="login_id not found")
        if time.time() > float(pending["expires_at"]):
            app.state.pending_logins.pop(login_id, None)
            raise HTTPException(status_code=410, detail="device grant expired")
        try:
            bundle = await asyncio.to_thread(
                poll_device_token,
                str(pending["server"]),
                device_code=str(pending["device_code"]),
            )
        except ClientError as exc:
            app.state.pending_logins.pop(login_id, None)
            raise HTTPException(
                status_code=exc.http_status or 401,
                detail=str(exc),
            ) from exc
        if bundle is None:
            return {
                "login_id": login_id,
                "status": "pending",
                "user_code": pending["user_code"],
                "interval": pending["interval"],
            }
        cred = _persist_login_success(pending, bundle)
        app.state.pending_logins.pop(login_id, None)
        return {
            "login_id": login_id,
            "status": "approved",
            "profile": pending["profile"],
            "actor_id": cred.actor_id,
            "actor_type": cred.actor_type,
            "expires_at": cred.expires_at,
        }

    @app.post("/v1/logout")
    async def logout(body: LogoutRequest) -> dict[str, str]:
        name = body.profile.strip()
        if not name:
            raise HTTPException(status_code=400, detail="profile required")
        cred = load_credential(name)
        profile = get_profile(name)
        server = profile.server if profile is not None else ""
        if cred is not None and server:
            # Prefer refresh (user) or opaque access (agent) for revoke.
            token = cred.refresh_token or cred.access_token
            await asyncio.to_thread(revoke_token, server, token)
        delete_credential(name)
        return {"status": "ok", "profile": name}

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
        from ak5.daemon.poller import schedule_dispatch

        _wake(profile)
        # Default wait=true keeps unit tests deterministic; wait=false mirrors poller.
        wait_hooks = body.get("wait", True)
        if wait_hooks is False or wait_hooks == "0":
            schedule_dispatch(_state(), profile)
        else:
            await dispatch_new_events(_state(), profile)
        return {"seq": seq}

    setattr(app.state, "wake_profile", _wake)
    return app
