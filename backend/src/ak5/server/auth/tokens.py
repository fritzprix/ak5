"""Access token issue / verify / FastAPI dependency (identity.md §2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.contracts.stages import Role
from ak5.server.auth.principal import Principal
from ak5.server.config import settings
from ak5.server.database import get_db
from ak5.server.models.workspace import Agent, Membership, User
from ak5.server.security import get_jwt_secret

_security = HTTPBearer(auto_error=False)

_AGENT_ROLES = frozenset({Role.TRIAGE.value, Role.WORKER.value, Role.GATEKEEPER.value})


def issue_access_token(
    principal: Principal,
    *,
    ttl: timedelta | None = None,
    scope: str = "",
) -> str:
    """Sign a Principal access JWT (identity.md §2 claim shape)."""
    expire = datetime.now(UTC) + (
        ttl if ttl is not None else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    claims: dict[str, Any] = {
        "sub": principal.principal_id,
        "principal_type": principal.principal_type,
        "ws": principal.workspace_id,
        "scope": scope,
        "exp": expire,
        "iat": datetime.now(UTC),
        "jti": uuid.uuid4().hex,
    }
    if principal.principal_type == "agent":
        claims["role"] = principal.workflow_role.value
    return jwt.encode(claims, get_jwt_secret(), algorithm=settings.JWT_ALGORITHM)


def _decode_claims(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


async def verify_access_token(token: str, db: AsyncSession) -> Principal:
    """Verify signature/exp and re-load Principal from DB (disabled agents rejected)."""
    payload = _decode_claims(token)
    principal_id = str(payload.get("sub") or "")
    principal_type = payload.get("principal_type")
    workspace_id = str(payload.get("ws") or "")
    if not principal_id or principal_type not in ("user", "agent") or not workspace_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if principal_type == "user":
        user = await db.get(User, principal_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
        membership = (
            await db.execute(
                select(Membership).where(
                    Membership.workspace_id == workspace_id,
                    Membership.user_id == principal_id,
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            # Raise via HTTPException; main maps unknown codes — use envelope in detail
            # for clients that read detail, and status from WORKSPACE_FORBIDDEN.
            raise HTTPException(
                status_code=HTTP_STATUS[ErrorCode.WORKSPACE_FORBIDDEN],
                detail=error_body(
                    ErrorCode.WORKSPACE_FORBIDDEN,
                    "Not a member of this workspace",
                    details={"workspace_id": workspace_id, "principal_id": principal_id},
                ),
            )
        return Principal(
            principal_id=principal_id,
            principal_type="user",
            workspace_id=workspace_id,
            workflow_role=Role.HUMAN,
            membership_role=membership.role,
            board_scope=(),
        )

    agent = await db.get(Agent, principal_id)
    if agent is None or agent.disabled_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent not found or disabled",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if agent.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent workspace mismatch",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claim_role = str(payload.get("role") or "")
    if claim_role != agent.workflow_role or claim_role not in _AGENT_ROLES:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent role claim does not match registry",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Principal(
        principal_id=principal_id,
        principal_type="agent",
        workspace_id=workspace_id,
        workflow_role=Role(agent.workflow_role),
        membership_role=None,
        board_scope=tuple(agent.board_scope_list),
    )


async def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Principal:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await verify_access_token(credentials.credentials, db)
