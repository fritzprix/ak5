"""OAuth 2.0 endpoints — device grant (identity.md §3.3 / §4.1 / RFC 8628)."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.contracts.stages import Role
from ak5.server.auth.device_grant import (
    CLI_CLIENT_ID,
    DeviceGrantError,
    approve_or_deny_device,
    check_user_code_rate_limit,
    lookup_device_by_user_code,
    mark_device_consumed,
    mint_agent_from_approved_device,
    poll_device_token,
    record_user_code_failure,
    start_device_authorization,
)
from ak5.server.auth.oidc_config import public_base_url
from ak5.server.auth.principal import Principal
from ak5.server.auth.sessions import (
    CSRF_HEADER,
    SESSION_COOKIE,
    create_cli_session_tokens,
    csrf_matches,
    resolve_web_session,
    revoke_agent_credential_by_token,
    revoke_cli_refresh_token,
    rotate_cli_refresh_token,
)
from ak5.server.auth.tokens import issue_access_token
from ak5.server.database import get_db
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID, Membership
from ak5.server.web_auth import resolve_client_ip

router = APIRouter(tags=["oauth"])

ACCESS_TOKEN_TTL = timedelta(minutes=15)
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


def _oauth_error(error: str, *, status_code: int = 400, interval: int | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": error}
    if interval is not None:
        body["interval"] = interval
    return JSONResponse(status_code=status_code, content=body)


def _client_key(request: Request) -> str:
    headers = {k.lower(): v for k, v in request.headers.items()}
    return resolve_client_ip(
        headers,
        request.client.host if request.client else "127.0.0.1",
    )


class DeviceAuthorizationBody(BaseModel):
    client_id: str = Field(default=CLI_CLIENT_ID)
    principal: Literal["user", "agent"] = "user"
    name: str | None = None
    workflow_role: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    workspace_id: str | None = None


class DeviceApproveBody(BaseModel):
    user_code: str = Field(..., min_length=1)
    approved: bool = True
    workspace_id: str | None = None
    workflow_role: str | None = None
    capabilities: list[str] | None = None
    board_scope: list[str] | None = None


class RevokeBody(BaseModel):
    token: str = Field(..., min_length=1)
    token_type_hint: str | None = None


async def _parse_token_params(request: Request) -> dict[str, str]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            raw = await request.json()
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in raw.items():
            if value is None:
                continue
            out[str(key)] = str(value)
        return out
    form = await request.form()
    return {str(k): str(v) for k, v in form.items()}


async def _require_web_session_csrf(
    request: Request, db: AsyncSession
) -> tuple[str, JSONResponse | None]:
    cookie = request.cookies.get(SESSION_COOKIE)
    resolved = await resolve_web_session(db, cookie)
    if resolved is None:
        return "", JSONResponse(status_code=401, content={"error": "not_authenticated"})
    user_id, csrf_hash = resolved
    presented = request.headers.get(CSRF_HEADER)
    if not csrf_matches(presented, csrf_hash):
        return "", JSONResponse(status_code=403, content={"error": "csrf_failed"})
    return user_id, None


_DEVICE_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>AK5 device approval</title>
  <style>
    :root { color-scheme: light; font-family: "IBM Plex Sans", "Segoe UI", sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center;
           background: linear-gradient(160deg, #e8f0ec 0%, #f7f3ea 55%, #efe8dc 100%); color: #1c2420; }
    main { width: min(28rem, 92vw); }
    h1 { font-size: 1.35rem; letter-spacing: -0.02em; margin: 0 0 0.35rem; }
    p { margin: 0 0 1rem; color: #3d4a44; line-height: 1.45; }
    label { display: block; font-size: 0.85rem; margin-bottom: 0.35rem; }
    input, button { font: inherit; }
    input { width: 100%; box-sizing: border-box; padding: 0.65rem 0.75rem; border: 1px solid #9aada3;
            background: #fff; margin-bottom: 0.75rem; }
    .row { display: flex; gap: 0.5rem; }
    button { flex: 1; padding: 0.7rem 0.9rem; border: 0; cursor: pointer; background: #1f6f4a; color: #fff; }
    button.deny { background: #6b3a32; }
    #msg { min-height: 1.25rem; font-size: 0.9rem; margin-top: 0.75rem; }
  </style>
</head>
<body>
  <main>
    <h1>Approve device</h1>
    <p>Enter the code shown in your CLI. You must be signed in to this AK5 server.</p>
    <label for="code">User code</label>
    <input id="code" name="code" autocomplete="one-time-code" placeholder="XXXX-XXXX"
           value="__CODE__"/>
    <div class="row">
      <button type="button" id="approve">Approve</button>
      <button type="button" id="deny" class="deny">Deny</button>
    </div>
    <p id="msg"></p>
  </main>
  <script>
    const params = new URLSearchParams(location.search);
    const codeInput = document.getElementById("code");
    if (params.get("code") && !codeInput.value) codeInput.value = params.get("code");
    const msg = document.getElementById("msg");
    function csrf() {
      const m = document.cookie.match(/(?:^|; )ak5_csrf=([^;]*)/);
      return m ? decodeURIComponent(m[1]) : "";
    }
    async function submitDecision(approved) {
      msg.textContent = "";
      const user_code = codeInput.value.trim();
      const res = await fetch("/oauth/device/approve", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrf(),
        },
        body: JSON.stringify({ user_code, approved }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        msg.textContent = body.error || ("HTTP " + res.status);
        if (res.status === 401) msg.textContent += " — sign in first (/login or /auth/local/login).";
        return;
      }
      msg.textContent = approved ? ("Approved (" + (body.principal || "") + ").") : "Denied.";
    }
    document.getElementById("approve").onclick = () => submitDecision(true);
    document.getElementById("deny").onclick = () => submitDecision(false);
  </script>
</body>
</html>
"""


@router.get("/device", response_class=HTMLResponse)
async def device_approval_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    code: str | None = None,
) -> HTMLResponse:
    """Minimal server-rendered approval page (identity.md §3.3 / #25)."""
    from ak5.server.routers.oidc_auth import (
        _is_secure,
        _set_session_cookies,
        auto_create_local_web_session,
        local_auto_login_enabled,
    )

    html = _DEVICE_PAGE.replace("__CODE__", (code or "").replace('"', ""))
    response = HTMLResponse(html)
    cookie = request.cookies.get(SESSION_COOKIE)
    if await resolve_web_session(db, cookie) is None and local_auto_login_enabled(request):
        _user, session_cookie, csrf = await auto_create_local_web_session(db)
        _set_session_cookies(
            response,
            session_cookie=session_cookie,
            csrf_token=csrf,
            secure=_is_secure(request),
        )
    return response


@router.post("/oauth/device_authorization")
async def device_authorization(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: DeviceAuthorizationBody | None = None,
) -> JSONResponse:
    """Start RFC 8628 device authorization for ``user`` or ``agent``."""
    payload = body or DeviceAuthorizationBody()
    try:
        started = await start_device_authorization(
            db,
            client_id=payload.client_id,
            principal=payload.principal,
            name=payload.name,
            workflow_role=payload.workflow_role,
            capabilities=payload.capabilities,
            workspace_id=payload.workspace_id,
        )
        await db.commit()
    except DeviceGrantError as exc:
        return _oauth_error(exc.error)

    base = public_base_url(
        bind_host=request.url.hostname or "127.0.0.1",
        bind_port=request.url.port or 8000,
    )
    verification_uri = f"{base}/device"
    return JSONResponse(
        {
            "device_code": started.device_code,
            "user_code": started.user_code,
            "verification_uri": verification_uri,
            "verification_uri_complete": f"{verification_uri}?code={started.user_code}",
            "expires_in": started.expires_in,
            "interval": started.interval,
        }
    )


@router.get("/oauth/device/request")
async def device_request_info(
    code: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Lookup pending grant by ``user_code`` — requires web session; rate-limited."""
    cookie = request.cookies.get(SESSION_COOKIE)
    resolved = await resolve_web_session(db, cookie)
    if resolved is None:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})

    client_key = _client_key(request)
    try:
        await check_user_code_rate_limit(db, client_key)
    except DeviceGrantError as exc:
        return _oauth_error(exc.error, interval=exc.retry_after)

    row = await lookup_device_by_user_code(db, code)
    await db.commit()
    if row is None:
        await record_user_code_failure(db, client_key)
        # Uniform response — do not leak existence beyond generic not_found.
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return JSONResponse(
        {
            "user_code": row.user_code,
            "principal": row.principal,
            "status": row.status,
            "requested_name": row.requested_name,
            "requested_role": row.requested_role,
        }
    )


@router.post("/oauth/device/approve")
async def device_approve(
    body: DeviceApproveBody,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Approve/deny a device grant — web session + CSRF header only."""
    user_id, err = await _require_web_session_csrf(request, db)
    if err is not None:
        return err

    client_key = _client_key(request)
    try:
        row = await approve_or_deny_device(
            db,
            user_code=body.user_code,
            approver_user_id=user_id,
            approved=body.approved,
            workspace_id=body.workspace_id,
            workflow_role=body.workflow_role,
            capabilities=body.capabilities,
            board_scope=body.board_scope,
            client_key=client_key,
        )
        await db.commit()
    except DeviceGrantError as exc:
        await db.commit()
        return _oauth_error(exc.error, interval=exc.retry_after)

    return JSONResponse(
        {
            "status": row.status,
            "user_code": row.user_code,
            "workspace_id": row.workspace_id,
            "principal": row.principal,
        }
    )


async def _issue_user_tokens(
    db: AsyncSession, *, user_id: str, workspace_id: str
) -> JSONResponse:
    membership = (
        await db.execute(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    membership_role = membership.role if membership else "member"
    principal = Principal(
        principal_id=user_id,
        principal_type="user",
        workspace_id=workspace_id,
        workflow_role=Role.HUMAN,
        membership_role=membership_role,
    )
    access_token = issue_access_token(principal, ttl=ACCESS_TOKEN_TTL)
    refresh, _session_id = await create_cli_session_tokens(db, user_id)
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
            "refresh_token": refresh,
        }
    )


@router.post("/oauth/token")
async def oauth_token(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Token endpoint — device_code + refresh_token grants."""
    params = await _parse_token_params(request)
    grant_type = params.get("grant_type")
    if not grant_type:
        return _oauth_error("invalid_request")

    if grant_type == DEVICE_GRANT_TYPE:
        device_code = params.get("device_code")
        client_id = params.get("client_id")
        if not device_code or not client_id:
            return _oauth_error("invalid_request")
        try:
            row = await poll_device_token(db, device_code=device_code, client_id=client_id)
        except DeviceGrantError as exc:
            await db.commit()
            return _oauth_error(exc.error, interval=exc.retry_after)

        if row.principal == "user":
            user_id = row.approved_by_user_id
            workspace_id = row.workspace_id or LOCAL_WORKSPACE_ID
            if not user_id:
                return _oauth_error("invalid_grant")
            response = await _issue_user_tokens(
                db, user_id=user_id, workspace_id=workspace_id
            )
            await mark_device_consumed(db, row)
            await db.commit()
            return response

        if row.principal == "agent":
            try:
                agent, opaque = await mint_agent_from_approved_device(db, row)
            except DeviceGrantError as exc:
                await db.commit()
                return _oauth_error(exc.error)
            await mark_device_consumed(db, row)
            await db.commit()
            return JSONResponse(
                {
                    "access_token": opaque,
                    "token_type": "Bearer",
                    "expires_in": 0,
                    "agent_id": agent.agent_id,
                    "workspace_id": agent.workspace_id,
                    "workflow_role": agent.workflow_role,
                }
            )

        return _oauth_error("invalid_grant")

    if grant_type == "refresh_token":
        refresh = params.get("refresh_token")
        if not refresh:
            return _oauth_error("invalid_request")
        rotated = await rotate_cli_refresh_token(db, refresh)
        if rotated is None:
            await db.commit()
            return _oauth_error("invalid_grant")
        new_refresh, user_id = rotated
        # Default workspace for refreshed CLI sessions.
        workspace_id = LOCAL_WORKSPACE_ID
        membership = (
            await db.execute(
                select(Membership).where(
                    Membership.workspace_id == workspace_id,
                    Membership.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        principal = Principal(
            principal_id=user_id,
            principal_type="user",
            workspace_id=workspace_id,
            workflow_role=Role.HUMAN,
            membership_role=membership.role if membership else "member",
        )
        access_token = issue_access_token(principal, ttl=ACCESS_TOKEN_TTL)
        await db.commit()
        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
                "refresh_token": new_refresh,
            }
        )

    return _oauth_error("unsupported_grant_type")


@router.post("/oauth/revoke")
async def oauth_revoke(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: RevokeBody | None = None,
) -> JSONResponse:
    """RFC 7009 token revocation (CLI refresh or agent opaque credential)."""
    token: str | None = None
    if body is not None:
        token = body.token
    else:
        params = await _parse_token_params(request)
        token = params.get("token")
    if not token:
        return _oauth_error("invalid_request")

    if token.startswith("ak5_agt_"):
        await revoke_agent_credential_by_token(db, token)
    else:
        await revoke_cli_refresh_token(db, token)
    await db.commit()
    # RFC 7009: always 200 even if token was unknown.
    return JSONResponse({"revoked": True})
