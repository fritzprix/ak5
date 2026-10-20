"""HTTP routes for local-owner password login (loopback) — DB web sessions."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.server.auth.sessions import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    create_web_session_tokens,
    require_csrf_header,
    resolve_web_session,
    revoke_web_session,
)
from ak5.server.auth.users import ensure_local_owner_user
from ak5.server.database import get_db
from ak5.server.metrics import rate_limited_total, safe_inc
from ak5.server.security import is_loopback_client
from ak5.server.web_auth import (
    COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    check_rate_limit,
    credentials_match,
    get_web_auth_config,
    record_failed_attempt,
    record_successful_attempt,
    resolve_client_ip,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["web-auth"])


class LoginBody(BaseModel):
    username: str = Field(default="")
    password: str = Field(default="")


def _client_ip(request: Request) -> str:
    headers = {k.lower(): v for k, v in request.headers.items()}
    return resolve_client_ip(
        headers,
        request.client.host if request.client else "127.0.0.1",
    )


def _is_secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


@router.post("/login")
async def web_login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Local-owner password login — **loopback only**; mints DB ``ak5_session``."""
    ip = _client_ip(request)
    allowed, wait_seconds = check_rate_limit(ip)
    if not allowed:
        safe_inc(rate_limited_total, rule="local_login_lockout")
        wait = wait_seconds or 60
        logger.warning(
            "Local login locked out due to consecutive failed attempts (retry after %ss)",
            wait,
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(wait)
        return {"error": f"Too many failed attempts. Locked out for {wait} seconds."}

    cfg = get_web_auth_config()
    if not cfg.enabled:
        return {"success": True, "message": "Auth disabled"}

    if not is_loopback_client(request):
        response.status_code = 403
        return {"error": "Password login is loopback-only; use OIDC on exposed binds"}

    if not credentials_match(body.username, body.password):
        record_failed_attempt(ip)
        response.status_code = 401
        return {"error": "Invalid username or password"}

    record_successful_attempt(ip)
    secure = _is_secure(request)
    user = await ensure_local_owner_user(db)
    session_cookie, csrf = await create_web_session_tokens(db, user.user_id)
    await db.commit()

    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_cookie,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=SESSION_MAX_AGE_SECONDS,
    )
    response.set_cookie(
        key=CSRF_COOKIE,
        value=csrf,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=SESSION_MAX_AGE_SECONDS,
    )
    # Clear any legacy in-memory cookie from older builds.
    response.set_cookie(
        key=COOKIE_NAME,
        value="",
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=0,
    )
    return {"success": True, "user_id": user.user_id, "csrf": csrf}


@router.post("/logout", response_model=None)
async def web_logout(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool] | JSONResponse:
    """Revoke web session — CSRF **header only**; cookie fallback removed (#56)."""
    secure = _is_secure(request)
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        resolved = await resolve_web_session(db, cookie)
        if resolved is not None:
            _uid, csrf_hash = resolved
            if not require_csrf_header(request.headers, csrf_hash):
                # Keep session cookies — failed CSRF must not log the user out.
                return JSONResponse(
                    status_code=403,
                    content={"error": "CSRF validation failed", "success": False},
                )
            await revoke_web_session(db, cookie)
            await db.commit()
    for key in (SESSION_COOKIE, CSRF_COOKIE, COOKIE_NAME):
        response.set_cookie(
            key=key,
            value="",
            httponly=key != CSRF_COOKIE,
            secure=secure,
            samesite="lax",
            path="/",
            max_age=0,
        )
    return {"success": True}


@router.get("/status")
async def web_auth_status(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    cfg = get_web_auth_config()
    if not cfg.enabled:
        return {"authEnabled": False, "authenticated": True, "username": None}

    cookie = request.cookies.get(SESSION_COOKIE)
    authenticated = False
    if cookie:
        authenticated = await resolve_web_session(db, cookie) is not None
    return {
        "authEnabled": True,
        "authenticated": authenticated,
        "username": cfg.username if authenticated else None,
    }
