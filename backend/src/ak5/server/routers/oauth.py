"""OAuth 2.0 endpoints — device grant (identity.md §3.3 / RFC 8628)."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.contracts.stages import Role
from ak5.server.auth.device_grant import (
    CLI_CLIENT_ID,
    DeviceGrantError,
    approve_or_deny_user_device,
    lookup_device_by_user_code,
    mark_device_consumed,
    poll_user_device_token,
    start_user_device_authorization,
)
from ak5.server.auth.oidc_config import public_base_url
from ak5.server.auth.principal import Principal
from ak5.server.auth.sessions import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    create_cli_session_tokens,
    csrf_matches,
    resolve_web_session,
)
from ak5.server.auth.tokens import issue_access_token
from ak5.server.database import get_db
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID, Membership

router = APIRouter(tags=["oauth"])

ACCESS_TOKEN_TTL = timedelta(minutes=15)
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


def _oauth_error(error: str, *, status_code: int = 400, interval: int | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": error}
    if interval is not None:
        body["interval"] = interval
    return JSONResponse(status_code=status_code, content=body)


class DeviceAuthorizationBody(BaseModel):
    client_id: str = Field(default=CLI_CLIENT_ID)
    principal: Literal["user", "agent"] = "user"


class DeviceApproveBody(BaseModel):
    user_code: str = Field(..., min_length=1)
    approved: bool = True
    workspace_id: str | None = None


async def _parse_token_params(request: Request) -> dict[str, str]:
    """Accept application/x-www-form-urlencoded or JSON token requests."""
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            raw = await request.json()
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items() if v is not None}
    form = await request.form()
    return {str(k): str(v) for k, v in form.items()}


@router.post("/oauth/device_authorization")
async def device_authorization(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: DeviceAuthorizationBody | None = None,
) -> JSONResponse:
    """Start RFC 8628 device authorization (slice 1: ``principal=user`` only)."""
    payload = body or DeviceAuthorizationBody()
    if payload.principal == "agent":
        return _oauth_error("unsupported_principal", status_code=501)
    try:
        started = await start_user_device_authorization(db, client_id=payload.client_id)
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
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Public lookup of a pending user device grant by ``user_code``."""
    row = await lookup_device_by_user_code(db, code)
    await db.commit()
    if row is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return JSONResponse(
        {
            "user_code": row.user_code,
            "principal": row.principal,
            "client_id": row.client_id,
            "status": row.status,
            "expires_at": row.expires_at.isoformat() + "Z",
        }
    )


@router.post("/oauth/device/approve")
async def device_approve(
    body: DeviceApproveBody,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Approve/deny a user device grant — requires web session + CSRF."""
    cookie = request.cookies.get(SESSION_COOKIE)
    resolved = await resolve_web_session(db, cookie)
    if resolved is None:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    user_id, csrf_hash = resolved
    presented = request.headers.get(CSRF_HEADER) or request.cookies.get(CSRF_COOKIE)
    if not csrf_matches(presented, csrf_hash):
        return JSONResponse(status_code=403, content={"error": "csrf_failed"})

    try:
        row = await approve_or_deny_user_device(
            db,
            user_code=body.user_code,
            approver_user_id=user_id,
            approved=body.approved,
            workspace_id=body.workspace_id,
        )
        await db.commit()
    except DeviceGrantError as exc:
        await db.commit()
        return _oauth_error(exc.error)

    return JSONResponse(
        {
            "status": row.status,
            "user_code": row.user_code,
            "workspace_id": row.workspace_id,
        }
    )


@router.post("/oauth/token")
async def oauth_token(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Token endpoint — device_code grant (slice 1); refresh deferred to later slice."""
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
            row = await poll_user_device_token(
                db, device_code=device_code, client_id=client_id
            )
        except DeviceGrantError as exc:
            await db.commit()
            return _oauth_error(exc.error, interval=exc.retry_after)

        user_id = row.approved_by_user_id
        workspace_id = row.workspace_id or LOCAL_WORKSPACE_ID
        if not user_id:
            return _oauth_error("invalid_grant")

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
        await mark_device_consumed(db, row)
        await db.commit()
        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
                "refresh_token": refresh,
            }
        )

    if grant_type == "refresh_token":
        return _oauth_error("unsupported_grant_type", status_code=501)

    return _oauth_error("unsupported_grant_type")
