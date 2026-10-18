import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.server.auth.bridge import actor_id_for_principal, principal_id_for_actor
from ak5.server.auth.principal import Principal
from ak5.server.auth.tokens import verify_access_token
from ak5.server.authz import RESERVED_ADMIN_IDS, RESERVED_ADMIN_ROLES, attach_principal
from ak5.server.config import settings
from ak5.server.database import get_db
from ak5.server.models.actor import Actor
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID
from ak5.server.schemas.actor import ActorIdentifyRequest, ActorOut, TokenResponse
from ak5.server.security import (
    IDENTIFY_SECRET_HEADER,
    extract_identify_secret,
    get_jwt_secret,
    is_dev_insecure_identify_allowed,
    is_dev_insecure_identify_enabled,
    is_identify_authorized,
)

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    """Resolved Bearer auth for one request (single JWT verify)."""

    actor: Actor
    workspace_id: str
    principal: Principal | None = None


def create_access_token(actor_id: str, actor_type: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "sub": actor_id,
        "actor_type": actor_type,
        "exp": expire,
    }
    return jwt.encode(to_encode, get_jwt_secret(), algorithm=settings.JWT_ALGORITHM)


def _overlay_actor_from_principal(actor: Actor, principal: Principal) -> None:
    """Request-scoped role overlay so authz follows Principal, not PATCH /actors."""
    if principal.principal_type == "user":
        set_committed_value(actor, "actor_type", "human")
        set_committed_value(actor, "role", "human")
    else:
        set_committed_value(actor, "actor_type", "agent")
        set_committed_value(actor, "role", principal.workflow_role.value)
    attach_principal(actor, principal)


async def _load_actor_for_subject(
    db: AsyncSession,
    *,
    subject_id: str,
    prefer_principal_id: str | None = None,
) -> Actor | None:
    """Load Actor preferring principal-id alias rows (identity.md §9)."""
    candidates: list[str] = []
    if prefer_principal_id:
        candidates.append(prefer_principal_id)
    candidates.append(subject_id)
    bridged = principal_id_for_actor(subject_id)
    if bridged not in candidates:
        candidates.append(bridged)
    legacy = (
        actor_id_for_principal(prefer_principal_id)
        if prefer_principal_id is not None
        else actor_id_for_principal(subject_id)
    )
    if legacy not in candidates:
        candidates.append(legacy)

    for actor_id in candidates:
        actor = await db.get(Actor, actor_id)
        if actor is not None:
            return actor
    return None


async def resolve_auth_context(token: str, db: AsyncSession) -> AuthContext:
    """Resolve Bearer token → Actor + workspace (+ Principal when present).

    Opaque ``ak5_agt_…`` credentials are verified **before** JWT decode (#25).
    """
    principal: Principal | None = None
    if token.startswith("ak5_agt_"):
        principal = await verify_access_token(token, db)
        workspace_id = principal.workspace_id
        actor = await _load_actor_for_subject(
            db,
            subject_id=actor_id_for_principal(principal.principal_id),
            prefer_principal_id=principal.principal_id,
        )
        lookup_label = principal.principal_id
    else:
        try:
            payload = jwt.decode(
                token, get_jwt_secret(), algorithms=[settings.JWT_ALGORITHM]
            )
            subject: str | None = payload.get("sub")
            if not subject:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token payload",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is invalid or expired",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        if payload.get("principal_type") in ("user", "agent"):
            principal = await verify_access_token(token, db)
            workspace_id = principal.workspace_id
            actor = await _load_actor_for_subject(
                db,
                subject_id=actor_id_for_principal(principal.principal_id),
                prefer_principal_id=principal.principal_id,
            )
            lookup_label = principal.principal_id
        else:
            workspace_id = LOCAL_WORKSPACE_ID
            actor = await _load_actor_for_subject(db, subject_id=subject)
            lookup_label = subject

    if not actor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Actor '{lookup_label}' not found",
        )
    if principal is not None:
        _overlay_actor_from_principal(actor, principal)
    return AuthContext(actor=actor, workspace_id=workspace_id, principal=principal)


async def get_auth_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AuthContext:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await resolve_auth_context(credentials.credentials, db)


async def get_auth_workspace_id(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> str:
    """Workspace for the Bearer token (Principal ``ws`` claim, else ``ws_local``)."""
    return ctx.workspace_id


async def get_current_actor(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> Actor:
    """Actor for the Bearer token (Principal JWT overlays role from Principal)."""
    return ctx.actor


async def get_current_actor_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Actor | None:
    if not credentials:
        return None
    try:
        ctx = await resolve_auth_context(credentials.credentials, db)
        return ctx.actor
    except HTTPException:
        return None


@router.post("/identify", response_model=None)
async def identify_actor(
    req: ActorIdentifyRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse | JSONResponse:
    """Legacy self-registration — local only (identity.md §2).

    Requires ``AK5_DEV_INSECURE_IDENTIFY=1`` and a loopback client. Optionally
    also ``AK5_IDENTIFY_SECRET`` / web-auth cookie when configured.
    """
    # identity.md §2 — identify is removed outside the local insecure path.
    if not is_dev_insecure_identify_enabled():
        return JSONResponse(
            status_code=404,
            content=error_body(
                ErrorCode.UNAUTHENTICATED,
                "POST /auth/identify has been removed; use OIDC or device grant (identity.md §2)",
            ),
        )
    if not is_dev_insecure_identify_allowed(request):
        return JSONResponse(
            status_code=HTTP_STATUS[ErrorCode.UNAUTHENTICATED],
            content=error_body(
                ErrorCode.UNAUTHENTICATED,
                "POST /auth/identify requires AK5_DEV_INSECURE_IDENTIFY=1 on a loopback client",
            ),
        )

    provided = extract_identify_secret(request, req.identify_secret)
    authorized = is_identify_authorized(request, provided)
    if not authorized:
        # Allow a valid DB web session in place of the identify secret.
        from ak5.server.auth.sessions import SESSION_COOKIE, resolve_web_session

        session_cookie = request.cookies.get(SESSION_COOKIE)
        if session_cookie:
            resolved = await resolve_web_session(db, session_cookie)
            authorized = resolved is not None
    if not authorized:
        return JSONResponse(
            status_code=HTTP_STATUS[ErrorCode.UNAUTHENTICATED],
            content=error_body(
                ErrorCode.UNAUTHENTICATED,
                (
                    "Identify secret required. Set AK5_IDENTIFY_SECRET on the client "
                    f"(header {IDENTIFY_SECRET_HEADER}) or sign in via the web auth gate."
                ),
            ),
        )

    if req.actor_id.lower() in RESERVED_ADMIN_IDS or req.role.lower() in RESERVED_ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot self-assign reserved administrative actor ID or role via public identify",
        )

    stmt = select(Actor).where(Actor.actor_id == req.actor_id)
    result = await db.execute(stmt)
    actor = result.scalar_one_or_none()

    caps_json = json.dumps(req.capabilities)

    if not actor:
        actor = Actor(
            actor_id=req.actor_id,
            actor_type=req.actor_type,
            name=req.name,
            role=req.role,
            description=req.description,
            capabilities=caps_json,
            status="idle",
            avatar_url=req.avatar_url,
        )
        db.add(actor)
    else:
        # Update existing actor information
        actor.actor_type = req.actor_type
        actor.name = req.name
        actor.role = req.role
        actor.description = req.description
        actor.capabilities = caps_json
        if req.avatar_url is not None:
            actor.avatar_url = req.avatar_url

    await db.commit()
    await db.refresh(actor)

    token = create_access_token(actor.actor_id, actor.actor_type)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        actor=ActorOut(
            actor_id=actor.actor_id,
            actor_type=actor.actor_type,
            name=actor.name,
            role=actor.role,
            description=actor.description,
            capabilities=actor.capability_list,
            status=actor.status,
            avatar_url=actor.avatar_url,
            created_at=actor.created_at,
            updated_at=actor.updated_at,
        ),
    )
