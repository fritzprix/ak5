"""HTTP routes for the optional Kanban web-dashboard login gate."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from ak5.web_auth import (
    COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    check_rate_limit,
    client_ip_from_headers,
    compute_session_hash,
    get_web_auth_config,
    record_failed_attempt,
    record_successful_attempt,
    verify_session_cookie,
)

router = APIRouter(prefix="/api/auth", tags=["web-auth"])


class LoginBody(BaseModel):
    username: str = Field(default="")
    password: str = Field(default="")


def _client_ip(request: Request) -> str:
    return client_ip_from_headers(
        request.headers.get("x-forwarded-for"),
        request.headers.get("x-real-ip"),
        request.client.host if request.client else "127.0.0.1",
    )


@router.post("/login")
async def web_login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
    ip = _client_ip(request)
    allowed, wait_seconds = check_rate_limit(ip)
    if not allowed:
        wait = wait_seconds or 60
        response.status_code = 429
        response.headers["Retry-After"] = str(wait)
        return {"error": f"Too many failed attempts. Locked out for {wait} seconds."}

    cfg = get_web_auth_config()
    if not cfg.enabled:
        return {"success": True, "message": "Auth disabled"}

    if body.username != cfg.username or body.password != cfg.password:
        record_failed_attempt(ip)
        response.status_code = 401
        return {"error": "Invalid username or password"}

    record_successful_attempt(ip)
    token = compute_session_hash(cfg.username, cfg.password)
    secure = request.url.scheme == "https"
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=SESSION_MAX_AGE_SECONDS,
    )
    return {"success": True}


@router.post("/logout")
async def web_logout(request: Request, response: Response) -> dict[str, bool]:
    secure = request.url.scheme == "https"
    response.set_cookie(
        key=COOKIE_NAME,
        value="",
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=0,
    )
    return {"success": True}


@router.get("/status")
async def web_auth_status(request: Request) -> dict[str, Any]:
    cfg = get_web_auth_config()
    if not cfg.enabled:
        return {"authEnabled": False, "authenticated": True, "username": None}

    cookie = request.cookies.get(COOKIE_NAME)
    authenticated = verify_session_cookie(cookie)
    return {
        "authEnabled": True,
        "authenticated": authenticated,
        "username": cfg.username if authenticated else None,
    }
