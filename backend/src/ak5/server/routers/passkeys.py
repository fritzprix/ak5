"""Passkey (WebAuthn) registration + user-presence assertion (identity.md §6, #28).

Session-cookie + CSRF authenticated, like ``/auth/session/token``. A successful
assertion mints a short-lived user JWT carrying a ``pres`` claim; T15 under
``require_user_presence_for_approval`` accepts only such a token.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.contracts.stages import Role
from ak5.server.auth import passkeys as pk
from ak5.server.auth.principal import Principal
from ak5.server.auth.sessions import (
    SESSION_COOKIE,
    require_csrf_header,
    resolve_web_session,
)
from ak5.server.auth.tokens import issue_access_token
from ak5.server.database import get_db
from ak5.server.models.workspace import User
from ak5.server.services.workspaces import get_membership

router = APIRouter(tags=["passkeys"])


class RegisterVerifyRequest(BaseModel):
    challenge_id: str = Field(..., min_length=1, max_length=128)
    credential: dict[str, Any]
    label: str = Field(default="", max_length=128)


class AssertVerifyRequest(BaseModel):
    challenge_id: str = Field(..., min_length=1, max_length=128)
    credential: dict[str, Any]
    workspace_id: str = Field(..., min_length=1, max_length=128)


def _err(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


async def _session_user(
    request: Request, db: AsyncSession, *, mutating: bool
) -> tuple[User, None] | tuple[None, JSONResponse]:
    resolved = await resolve_web_session(db, request.cookies.get(SESSION_COOKIE))
    if resolved is None:
        return None, _err(401, "Not authenticated")
    user_id, csrf_hash = resolved
    if mutating and not require_csrf_header(request.headers, csrf_hash):
        return None, _err(403, "CSRF validation failed")
    user = await db.get(User, user_id)
    if user is None:
        return None, _err(401, "Not authenticated")
    return user, None


@router.get("/auth/passkeys", response_model=None)
async def list_my_passkeys(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> JSONResponse:
    user, error = await _session_user(request, db, mutating=False)
    if error is not None or user is None:
        return error or _err(401, "Not authenticated")
    rows = await pk.list_passkeys(db, user.user_id)
    return JSONResponse({"passkeys": [pk.credential_public(r) for r in rows]})


@router.post("/auth/passkeys/register/options", response_model=None)
async def passkey_register_options(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> JSONResponse:
    user, error = await _session_user(request, db, mutating=True)
    if error is not None or user is None:
        return error or _err(401, "Not authenticated")
    try:
        payload = await pk.registration_options(db, request, user)
    except pk.PasskeyError as exc:
        await db.commit()
        return _err(exc.status_code, exc.message)
    await db.commit()
    return JSONResponse(payload)


@router.post("/auth/passkeys/register/verify", response_model=None)
async def passkey_register_verify(
    body: RegisterVerifyRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    user, error = await _session_user(request, db, mutating=True)
    if error is not None or user is None:
        return error or _err(401, "Not authenticated")
    try:
        row = await pk.verify_registration(
            db,
            request,
            user,
            challenge_id=body.challenge_id,
            credential=body.credential,
            label=body.label,
        )
    except pk.PasskeyError as exc:
        await db.commit()  # the challenge is single use even when verification fails
        return _err(exc.status_code, exc.message)
    await db.commit()
    return JSONResponse(pk.credential_public(row), status_code=201)


@router.delete("/auth/passkeys/{passkey_id}", response_model=None)
async def passkey_delete(
    passkey_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    user, error = await _session_user(request, db, mutating=True)
    if error is not None or user is None:
        return error or _err(401, "Not authenticated")
    if not await pk.delete_passkey(db, user.user_id, passkey_id):
        return _err(404, "Passkey not found")
    await db.commit()
    return JSONResponse({"success": True})


@router.post("/auth/passkeys/assert/options", response_model=None)
async def passkey_assert_options(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> JSONResponse:
    user, error = await _session_user(request, db, mutating=True)
    if error is not None or user is None:
        return error or _err(401, "Not authenticated")
    try:
        payload = await pk.assertion_options(db, request, user.user_id)
    except pk.PasskeyError as exc:
        await db.commit()
        return _err(exc.status_code, exc.message)
    await db.commit()
    return JSONResponse(payload)


@router.post("/auth/passkeys/assert/verify", response_model=None)
async def passkey_assert_verify(
    body: AssertVerifyRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Verify an assertion and mint a presence-bearing user JWT (TTL = presence window)."""
    user, error = await _session_user(request, db, mutating=True)
    if error is not None or user is None:
        return error or _err(401, "Not authenticated")
    membership = await get_membership(db, workspace_id=body.workspace_id, user_id=user.user_id)
    if membership is None:
        return _err(403, "Not a member of this workspace")
    try:
        presence_at = await pk.verify_assertion(
            db,
            request,
            user.user_id,
            challenge_id=body.challenge_id,
            credential=body.credential,
        )
    except pk.PasskeyError as exc:
        await db.commit()
        return _err(exc.status_code, exc.message)
    await db.commit()
    principal = Principal(
        principal_id=user.user_id,
        principal_type="user",
        workspace_id=body.workspace_id,
        workflow_role=Role.HUMAN,
        membership_role=membership.role,
        presence_at=presence_at,
    )
    ttl = pk.PRESENCE_WINDOW
    token = issue_access_token(principal, ttl=ttl, presence_at=presence_at)
    return JSONResponse(
        {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": int(ttl.total_seconds()),
            "workspace_id": body.workspace_id,
            "user_id": user.user_id,
        }
    )
