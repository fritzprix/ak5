"""OIDC login/callback + local-owner password login (identity.md §3, §8)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.contracts.stages import Role
from ak5.server.auth.oidc import (
    build_authorize_url,
    discover_endpoints,
    exchange_code,
    fetch_jwks,
    fetch_oauth_userinfo,
    generate_pkce_pair,
    generate_state_nonce,
    verify_id_token,
)
from ak5.server.auth.oidc_config import (
    get_oidc_provider,
    load_oidc_providers,
    oidc_providers_configured,
    public_base_url,
)
from ak5.server.auth.oidc_device import (
    UpstreamDevicePending,
    poll_upstream_device_flow,
    start_upstream_device_flow,
    upstream_device_endpoints,
)
from ak5.server.auth.principal import Principal
from ak5.server.auth.sessions import (
    CSRF_COOKIE,
    OIDC_STATE_COOKIE,
    OIDC_STATE_COOKIE_MAX_AGE,
    SESSION_COOKIE,
    create_web_session_tokens,
    oidc_state_matches,
    pop_oidc_pending,
    require_csrf_header,
    resolve_web_session,
    revoke_web_session,
    store_oidc_pending,
)
from ak5.server.auth.tokens import issue_access_token, session_token_ttl
from ak5.server.auth.users import (
    ensure_local_owner_user,
    local_owner_configured,
    upsert_user_from_oidc,
    verify_local_owner_password,
)
from ak5.server.database import get_db
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID, User
from ak5.server.schemas.workspace import SessionTokenOut, SessionTokenRequest
from ak5.server.security import is_loopback_client
from ak5.server.services.workspaces import (
    get_membership,
    list_workspaces_for_user,
)

router = APIRouter(tags=["oidc-auth"])


def _is_secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _set_session_cookies(
    response: Response,
    *,
    session_cookie: str,
    csrf_token: str,
    secure: bool,
) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_cookie,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=60 * 60 * 24 * 14,
    )
    response.set_cookie(
        key=CSRF_COOKIE,
        value=csrf_token,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=60 * 60 * 24 * 14,
    )


def _clear_session_cookies(response: Response, *, secure: bool) -> None:
    for key in (SESSION_COOKIE, CSRF_COOKIE):
        response.set_cookie(
            key=key,
            value="",
            httponly=key == SESSION_COOKIE,
            secure=secure,
            samesite="lax",
            path="/",
            max_age=0,
        )


def _set_oidc_state_cookie(response: Response, *, state: str, secure: bool) -> None:
    response.set_cookie(
        key=OIDC_STATE_COOKIE,
        value=state,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=OIDC_STATE_COOKIE_MAX_AGE,
    )


def _clear_oidc_state_cookie(response: Response, *, secure: bool) -> None:
    response.set_cookie(
        key=OIDC_STATE_COOKIE,
        value="",
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=0,
    )


class LocalLoginBody(BaseModel):
    password: str = Field(default="")


class UpstreamDevicePollBody(BaseModel):
    provider: str = Field(..., min_length=1)
    device_code: str = Field(..., min_length=1)


@router.get("/auth/login")
async def oidc_login(provider: str, request: Request) -> RedirectResponse:
    """Start OIDC Authorization Code + PKCE flow."""
    cfg = get_oidc_provider(provider)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Unknown OIDC provider '{provider}'")
    endpoints = await discover_endpoints(cfg)
    state, nonce = generate_state_nonce()
    verifier, challenge = generate_pkce_pair()
    store_oidc_pending(
        state=state,
        provider_id=cfg.id,
        nonce=nonce,
        code_verifier=verifier,
    )
    base = public_base_url(
        bind_host=request.url.hostname or "127.0.0.1",
        bind_port=request.url.port or 8000,
    )
    redirect_uri = f"{base}/auth/callback"
    url = build_authorize_url(
        cfg,
        endpoints=endpoints,
        redirect_uri=redirect_uri,
        state=state,
        nonce=nonce,
        code_challenge=challenge,
    )
    response = RedirectResponse(url=url, status_code=302)
    _set_oidc_state_cookie(response, state=state, secure=_is_secure(request))
    return response


@router.get("/auth/callback")
async def oidc_callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Complete OIDC login: exchange code, verify ID token, set session cookies."""
    secure = _is_secure(request)
    if error:
        fail = JSONResponse(
            status_code=400,
            content={"detail": f"IdP error: {error}"},
        )
        _clear_oidc_state_cookie(fail, secure=secure)
        return fail
    if not code or not state:
        fail = JSONResponse(
            status_code=400,
            content={"detail": "Missing code or state"},
        )
        _clear_oidc_state_cookie(fail, secure=secure)
        return fail
    cookie_state = request.cookies.get(OIDC_STATE_COOKIE)
    if not oidc_state_matches(cookie_state, state):
        # Do not consume pending state — attacker may be probing with a stolen URL.
        fail = JSONResponse(
            status_code=400,
            content={"detail": "OIDC state cookie mismatch"},
        )
        _clear_oidc_state_cookie(fail, secure=secure)
        return fail
    pending = pop_oidc_pending(state)
    if pending is None:
        fail = JSONResponse(
            status_code=400,
            content={"detail": "Invalid or expired state"},
        )
        _clear_oidc_state_cookie(fail, secure=secure)
        return fail
    cfg = get_oidc_provider(pending["provider_id"])
    if cfg is None:
        fail = JSONResponse(
            status_code=400,
            content={"detail": "Provider no longer configured"},
        )
        _clear_oidc_state_cookie(fail, secure=secure)
        return fail

    # Reject authorization responses that omit PKCE (verifier must be present).
    if not pending.get("code_verifier"):
        fail = JSONResponse(
            status_code=400,
            content={"detail": "PKCE verifier missing"},
        )
        _clear_oidc_state_cookie(fail, secure=secure)
        return fail

    endpoints = await discover_endpoints(cfg)
    base = public_base_url(
        bind_host=request.url.hostname or "127.0.0.1",
        bind_port=request.url.port or 8000,
    )
    redirect_uri = f"{base}/auth/callback"
    try:
        token_payload = await exchange_code(
            cfg,
            endpoints=endpoints,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=pending["code_verifier"],
        )

        if cfg.oauth_userinfo:
            access_token = token_payload.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise HTTPException(
                    status_code=400,
                    detail="Token response missing access_token",
                )
            claims = await fetch_oauth_userinfo(
                cfg,
                endpoints=endpoints,
                access_token=access_token,
            )
        else:
            id_token = token_payload.get("id_token")
            if not isinstance(id_token, str) or not id_token:
                raise HTTPException(
                    status_code=400,
                    detail="Token response missing id_token",
                )
            jwks = None
            if not cfg.id_token_hs_secret:
                jwks_uri = endpoints.get("jwks_uri") or ""
                if not jwks_uri:
                    raise HTTPException(
                        status_code=500,
                        detail="Provider JWKS URI missing",
                    )
                jwks = await fetch_jwks(jwks_uri)
            claims = verify_id_token(
                cfg,
                id_token,
                nonce=pending["nonce"],
                jwks=jwks,
            )
    except HTTPException as exc:
        fail = JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
        _clear_oidc_state_cookie(fail, secure=secure)
        return fail
    except Exception as exc:
        fail = JSONResponse(
            status_code=401,
            content={"detail": f"Identity verification rejected: {exc}"},
        )
        _clear_oidc_state_cookie(fail, secure=secure)
        return fail

    user = await upsert_user_from_oidc(
        db,
        issuer=claims.issuer,
        subject=claims.subject,
        email=claims.email,
        email_verified=claims.email_verified,
        display_name=claims.display_name,
    )
    session_cookie, csrf = await create_web_session_tokens(db, user.user_id)
    await db.commit()

    response = RedirectResponse(url="/", status_code=302)
    _set_session_cookies(
        response,
        session_cookie=session_cookie,
        csrf_token=csrf,
        secure=secure,
    )
    _clear_oidc_state_cookie(response, secure=secure)
    return response


@router.post("/auth/local/login")
async def local_owner_login(
    body: LocalLoginBody,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Local-owner password login — **loopback only** (identity.md §8)."""
    if not is_loopback_client(request):
        return JSONResponse(
            status_code=403,
            content={"error": "Local owner password login is loopback-only"},
        )
    if not local_owner_configured():
        return JSONResponse(
            status_code=400,
            content={"error": "Local owner password is not configured"},
        )
    if not verify_local_owner_password(body.password):
        return JSONResponse(status_code=401, content={"error": "Invalid password"})

    user = await ensure_local_owner_user(db)
    session_cookie, csrf = await create_web_session_tokens(db, user.user_id)
    await db.commit()

    response = JSONResponse({"success": True, "user_id": user.user_id, "csrf": csrf})
    _set_session_cookies(
        response,
        session_cookie=session_cookie,
        csrf_token=csrf,
        secure=_is_secure(request),
    )
    return response


@router.post("/auth/logout")
async def oidc_logout(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Revoke web session. Mutating — requires CSRF when a session exists."""
    cookie = request.cookies.get(SESSION_COOKIE)
    resolved = await resolve_web_session(db, cookie)
    if resolved is not None:
        _user_id, csrf_hash = resolved
        if not require_csrf_header(request.headers, csrf_hash):
            return JSONResponse(status_code=403, content={"error": "CSRF validation failed"})
        await revoke_web_session(db, cookie)
        await db.commit()

    response = JSONResponse({"success": True})
    _clear_session_cookies(response, secure=_is_secure(request))
    return response


@router.get("/auth/me")
async def auth_me(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    cookie = request.cookies.get(SESSION_COOKIE)
    resolved = await resolve_web_session(db, cookie)
    providers = [{"id": p.id, "issuer": p.issuer} for p in load_oidc_providers()]
    if resolved is None:
        return {
            "authenticated": False,
            "user_id": None,
            "display_name": None,
            "oidc_configured": oidc_providers_configured(),
            "local_owner_configured": local_owner_configured(),
            "providers": providers,
        }
    user_id, _csrf_hash = resolved
    user = await db.get(User, user_id)
    return {
        "authenticated": True,
        "user_id": user_id,
        "display_name": user.display_name if user else None,
        "oidc_configured": oidc_providers_configured(),
        "local_owner_configured": local_owner_configured(),
        "providers": providers,
    }


@router.post("/auth/session/token", response_model=None)
async def mint_session_access_token(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: SessionTokenRequest | None = None,
) -> SessionTokenOut | JSONResponse:
    """Exchange a valid web session cookie for a user Principal JWT (#27).

    Optional ``workspace_id`` selects the ``ws`` claim (must be a membership).
    """
    cookie = request.cookies.get(SESSION_COOKIE)
    resolved = await resolve_web_session(db, cookie)
    if resolved is None:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})
    user_id, csrf_hash = resolved
    if not require_csrf_header(request.headers, csrf_hash):
        return JSONResponse(status_code=403, content={"error": "CSRF validation failed"})

    requested = (body.workspace_id if body else None) or None
    membership = None
    workspace_id = requested
    if workspace_id:
        membership = await get_membership(
            db, workspace_id=workspace_id, user_id=user_id
        )
        if membership is None:
            return JSONResponse(
                status_code=403,
                content={"error": "Not a member of this workspace"},
            )
    else:
        workspaces = await list_workspaces_for_user(db, user_id)
        if workspaces:
            workspace_id = workspaces[0].workspace_id
            membership = await get_membership(
                db, workspace_id=workspace_id, user_id=user_id
            )
        else:
            workspace_id = LOCAL_WORKSPACE_ID
            membership = await get_membership(
                db, workspace_id=workspace_id, user_id=user_id
            )
            if membership is None:
                return JSONResponse(
                    status_code=403,
                    content={"error": "No workspace membership"},
                )

    assert workspace_id is not None
    principal = Principal(
        principal_id=user_id,
        principal_type="user",
        workspace_id=workspace_id,
        workflow_role=Role.HUMAN,
        membership_role=membership.role if membership else None,
    )
    ttl = session_token_ttl()
    token = issue_access_token(principal, ttl=ttl)
    return SessionTokenOut(
        access_token=token,
        expires_in=int(ttl.total_seconds()),
        workspace_id=workspace_id,
        user_id=user_id,
        membership_role=membership.role if membership else None,
    )


@router.post("/auth/csrf-check")
async def csrf_check(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Test helper / example mutating endpoint that requires CSRF."""
    cookie = request.cookies.get(SESSION_COOKIE)
    resolved = await resolve_web_session(db, cookie)
    if resolved is None:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})
    _user_id, csrf_hash = resolved
    if not require_csrf_header(request.headers, csrf_hash):
        return JSONResponse(status_code=403, content={"error": "CSRF validation failed"})
    return JSONResponse({"ok": True})


@router.post("/auth/oidc/device/start")
async def upstream_device_start(provider: str) -> JSONResponse:
    """Start upstream IdP device flow when redirect URIs are unavailable (§8.1)."""
    cfg = get_oidc_provider(provider)
    if cfg is None:
        return JSONResponse(status_code=404, content={"error": f"Unknown provider '{provider}'"})
    if upstream_device_endpoints(cfg) is None:
        return JSONResponse(
            status_code=400,
            content={"error": f"Provider '{provider}' does not support device flow"},
        )
    try:
        started = await start_upstream_device_flow(cfg)
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    return JSONResponse(
        {
            "device_code": started.device_code,
            "user_code": started.user_code,
            "verification_uri": started.verification_uri,
            "verification_uri_complete": started.verification_uri_complete,
            "expires_in": started.expires_in,
            "interval": started.interval,
        }
    )


@router.post("/auth/oidc/device/poll")
async def upstream_device_poll(
    body: UpstreamDevicePollBody,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Poll upstream IdP device token; on success mint a web session."""
    cfg = get_oidc_provider(body.provider)
    if cfg is None:
        return JSONResponse(status_code=404, content={"error": f"Unknown provider '{body.provider}'"})
    try:
        claims = await poll_upstream_device_flow(cfg, device_code=body.device_code)
    except UpstreamDevicePending as pending:
        payload: dict[str, Any] = {"error": pending.error}
        if pending.retry_after is not None:
            payload["interval"] = pending.retry_after
        status_code = 400
        return JSONResponse(status_code=status_code, content=payload)
    except ValueError as exc:
        code = str(exc)
        status_code = 400 if code in {"expired_token", "access_denied"} else 401
        return JSONResponse(status_code=status_code, content={"error": code})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})

    user = await upsert_user_from_oidc(
        db,
        issuer=claims.issuer,
        subject=claims.subject,
        email=claims.email,
        email_verified=claims.email_verified,
        display_name=claims.display_name,
    )
    session_cookie, csrf = await create_web_session_tokens(db, user.user_id)
    await db.commit()
    response = JSONResponse({"success": True, "user_id": user.user_id, "csrf": csrf})
    _set_session_cookies(
        response,
        session_cookie=session_cookie,
        csrf_token=csrf,
        secure=_is_secure(request),
    )
    return response
