"""Access token issue / verify / FastAPI dependency (identity.md §2)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.contracts.stages import Role
from ak5.server.auth.principal import Principal
from ak5.server.config import settings
from ak5.server.database import get_db
from ak5.server.models.workspace import Agent, AgentCredential, Membership, User
from ak5.server.security import get_jwt_secret

_security = HTTPBearer(auto_error=False)

_AGENT_ROLES = frozenset({Role.TRIAGE.value, Role.WORKER.value, Role.GATEKEEPER.value})
_AGENT_TOKEN_PREFIX = "ak5_agt_"
# Throttle last_used_at writes so auth does not touch SQLite on every poll (#65).
_LAST_USED_MIN_INTERVAL = timedelta(seconds=60)
logger = logging.getLogger(__name__)


def session_token_ttl() -> timedelta:
    """TTL for ``/auth/session/token`` JWTs (``AK5_SESSION_TOKEN_TTL_MINUTES``, #72)."""
    raw = os.environ.get("AK5_SESSION_TOKEN_TTL_MINUTES", "").strip()
    minutes = settings.SESSION_TOKEN_TTL_MINUTES
    if raw:
        try:
            minutes = int(raw)
        except ValueError:
            logger.warning("Ignoring invalid AK5_SESSION_TOKEN_TTL_MINUTES=%r", raw)
    return timedelta(minutes=max(1, minutes))


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


async def _touch_agent_credential_last_used(
    db: AsyncSession,
    credential_id: str,
    *,
    previous: datetime | None,
    now: datetime,
) -> None:
    """Update ``last_used_at`` in a short-lived session on the same bind (#65).

    Must not leave an uncommitted write on the request-scoped session — a
    long-poll holds that session open and would otherwise keep SQLite's write
    lock for the whole wait.
    """
    if previous is not None:
        prev = previous.replace(tzinfo=None) if previous.tzinfo else previous
        if now - prev < _LAST_USED_MIN_INTERVAL:
            return
    try:
        # ``db.bind`` is the AsyncEngine; ``get_bind()`` returns a sync Engine
        # and cannot be passed to ``async_sessionmaker``.
        engine = db.bind
        if engine is None:
            from ak5.server.database import engine as app_engine

            engine = app_engine

        # In-memory tests use StaticPool (one connection). Opening a parallel
        # session would deadlock, so commit a targeted UPDATE on this session
        # immediately — still releases the write lock before long-poll waits.
        if isinstance(engine.sync_engine.pool, StaticPool):
            await db.execute(
                update(AgentCredential)
                .where(AgentCredential.credential_id == credential_id)
                .values(last_used_at=now)
            )
            await db.commit()
            return

        factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        async with factory() as session, session.begin():
            row = await session.get(AgentCredential, credential_id)
            if row is None or row.revoked_at is not None:
                return
            row.last_used_at = now
    except Exception:  # noqa: BLE001 — auth must not fail on telemetry write
        logger.debug("last_used_at touch failed for %s", credential_id, exc_info=True)


async def _verify_opaque_agent_token(token: str, db: AsyncSession) -> Principal:
    """Resolve ``ak5_agt_…`` credential → Principal (identity.md §4.3)."""
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cred = (
        await db.execute(
            select(AgentCredential).where(AgentCredential.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if cred is None or cred.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent credential invalid or revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    now = datetime.now(UTC).replace(tzinfo=None)
    if cred.expires_at is not None and cred.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent credential expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    agent = await db.get(Agent, cred.agent_id)
    if agent is None or agent.disabled_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent not found or disabled",
            headers={"WWW-Authenticate": "Bearer"},
        )
    credential_id = cred.credential_id
    previous_used = cred.last_used_at
    try:
        cred_scope = json.loads(cred.board_scope) if cred.board_scope else []
        if isinstance(cred_scope, list) and cred_scope:
            scope = tuple(str(item) for item in cred_scope)
        else:
            scope = tuple(agent.board_scope_list)
    except (json.JSONDecodeError, TypeError):
        scope = tuple(agent.board_scope_list)
    principal = Principal(
        principal_id=agent.agent_id,
        principal_type="agent",
        workspace_id=agent.workspace_id,
        workflow_role=Role(agent.workflow_role),
        membership_role=None,
        board_scope=scope,
    )
    # Separate short commit — never leave a write on the request session (#65).
    await _touch_agent_credential_last_used(
        db, credential_id, previous=previous_used, now=now
    )
    return principal


async def verify_access_token(token: str, db: AsyncSession) -> Principal:
    """Verify JWT or opaque agent credential; re-load Principal from DB."""
    if token.startswith(_AGENT_TOKEN_PREFIX):
        return await _verify_opaque_agent_token(token, db)
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
