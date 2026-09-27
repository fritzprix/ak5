import json
import secrets
import string
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.authz import RESERVED_ADMIN_IDS, RESERVED_ADMIN_ROLES, is_admin
from ak5.database import get_db
from ak5.models.actor import Actor
from ak5.models.board import Board
from ak5.models.board_member import BoardMember
from ak5.models.device_code import DeviceCode
from ak5.routers.auth import create_access_token, get_current_actor
from ak5.schemas.actor import ActorOut, TokenResponse
from ak5.schemas.device_auth import (
    DeviceApprovalRequest,
    DeviceCodeRequest,
    DeviceCodeResponse,
    DeviceTokenRequest,
    DeviceVerificationView,
)
from ak5.seed import DEFAULT_SYSTEM_ACTORS

router = APIRouter(prefix="/auth/device", tags=["device-auth"])

CODE_EXPIRE_MINUTES = 10
POLL_INTERVAL_SECONDS = 3


def _generate_user_code() -> str:
    """Generate human-friendly user code like AK5-7X2M."""
    chars = string.ascii_uppercase + "23456789"  # omit easily confused 0, 1, O, I
    suffix = "".join(secrets.choice(chars) for _ in range(4))
    return f"AK5-{suffix}"


@router.post("/code", response_model=DeviceCodeResponse)
async def request_device_code(
    req: DeviceCodeRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeviceCodeResponse:
    """Initiate OAuth 2.0 Device Authorization flow for an agent or client."""
    actor_id_lower = req.actor_id.lower()
    if (
        actor_id_lower in RESERVED_ADMIN_IDS
        or actor_id_lower in DEFAULT_SYSTEM_ACTORS
        or req.role.lower() in RESERVED_ADMIN_ROLES
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot request device authorization for reserved administrative or system actor ID/role",
        )

    # Check if actor already exists (prevent identity hijacking/overwrite)
    existing_actor = await db.get(Actor, req.actor_id)
    if existing_actor:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Actor '{req.actor_id}' already exists and cannot be registered via device flow",
        )

    # Generate codes
    device_code = secrets.token_urlsafe(32)
    user_code = _generate_user_code()

    # Retry collision if any (proper select on user_code column)
    for _ in range(5):
        stmt = select(DeviceCode).where(DeviceCode.user_code == user_code)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if not existing:
            break
        user_code = _generate_user_code()

    expires_at = datetime.now(UTC) + timedelta(minutes=CODE_EXPIRE_MINUTES)
    target_boards = [req.board_id] if req.board_id else []

    record = DeviceCode(
        device_code=device_code,
        user_code=user_code,
        actor_id=req.actor_id,
        actor_type=req.actor_type,
        name=req.name or req.actor_id.replace("-", " ").replace("_", " ").title(),
        role=req.role,
        capabilities=json.dumps(req.capabilities),
        status="pending",
        target_board_ids=json.dumps(target_boards),
        expires_at=expires_at,
    )
    db.add(record)
    await db.commit()

    # Build verification URI from request base url
    base = str(request.base_url).rstrip("/")
    verification_uri = f"{base}/auth/device"
    verification_uri_complete = f"{verification_uri}?code={user_code}"

    return DeviceCodeResponse(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=verification_uri_complete,
        expires_in=CODE_EXPIRE_MINUTES * 60,
        interval=POLL_INTERVAL_SECONDS,
    )


@router.get("/request", response_model=DeviceVerificationView)
async def get_device_request(
    code: str = Query(..., description="User code shown to user"),
    db: Annotated[AsyncSession, Depends(get_db)] = None,
) -> DeviceVerificationView:
    """Retrieve details of a pending device authorization request by user code."""
    normalized_code = code.strip().upper()
    stmt = select(DeviceCode).where(DeviceCode.user_code == normalized_code)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Authorization code '{normalized_code}' not found",
        )

    # Check expiration with timezone awareness
    now = datetime.now(UTC)
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if record.status == "pending" and now > expires_at:
        record.status = "expired"
        await db.commit()

    caps = []
    if record.capabilities:
        try:
            caps = json.loads(record.capabilities)
        except Exception:
            pass

    target_boards = []
    if record.target_board_ids:
        try:
            target_boards = json.loads(record.target_board_ids)
        except Exception:
            pass

    return DeviceVerificationView(
        user_code=record.user_code,
        actor_id=record.actor_id,
        actor_type=record.actor_type,
        name=record.name,
        role=record.role,
        capabilities=caps,
        status=record.status,
        target_board_ids=target_boards,
        expires_at=expires_at,
    )


@router.post("/approve")
async def approve_device_code(
    req: DeviceApprovalRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Approve or deny an agent device authorization request (authenticated approver required)."""
    normalized_code = req.user_code.strip().upper()
    stmt = select(DeviceCode).where(DeviceCode.user_code == normalized_code)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Authorization code '{normalized_code}' not found",
        )

    now = datetime.now(UTC)
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if now > expires_at or record.status == "expired":
        record.status = "expired"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authorization request has expired",
        )

    if record.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Authorization request already marked as '{record.status}'",
        )

    if not req.approved:
        record.status = "denied"
        await db.commit()
        return {"success": True, "status": "denied", "actor_id": record.actor_id}

    # Verify again that the actor doesn't collide with existing system actors
    if record.actor_id.lower() in DEFAULT_SYSTEM_ACTORS or record.actor_id.lower() in RESERVED_ADMIN_IDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot approve device authorization for reserved system actor ID",
        )

    # Approved: Register or update Actor
    stmt = select(Actor).where(Actor.actor_id == record.actor_id)
    actor_result = await db.execute(stmt)
    actor = actor_result.scalar_one_or_none()

    if not actor:
        actor = Actor(
            actor_id=record.actor_id,
            actor_type=record.actor_type,
            name=record.name,
            role=record.role,
            capabilities=record.capabilities,
            status="idle",
        )
        db.add(actor)
    else:
        actor.actor_type = record.actor_type
        actor.name = record.name
        actor.role = record.role
        actor.capabilities = record.capabilities

    # Enroll in target boards: ONLY use boards explicitly selected by the approver (req.board_ids)
    all_target_boards = [bid for bid in req.board_ids if bid]

    for board_id in all_target_boards:
        board = await db.get(Board, board_id)
        if not board:
            continue
        mem_stmt = select(BoardMember).where(
            BoardMember.board_id == board_id,
            BoardMember.actor_id == record.actor_id,
        )
        mem_res = await db.execute(mem_stmt)
        if not mem_res.scalar_one_or_none():
            member = BoardMember(
                board_id=board_id,
                actor_id=record.actor_id,
                role="agent" if record.actor_type == "agent" else "member",
            )
            db.add(member)

    # Issue token and update record
    token = create_access_token(actor.actor_id, actor.actor_type)
    record.access_token = token
    record.status = "approved"
    await db.commit()

    return {
        "success": True,
        "status": "approved",
        "actor_id": record.actor_id,
        "enrolled_boards": all_target_boards,
    }


@router.post("/token", response_model=TokenResponse)
async def poll_device_token(
    req: DeviceTokenRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """Agent polling endpoint to retrieve token once user has approved the request."""
    stmt = select(DeviceCode).where(DeviceCode.device_code == req.device_code)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_grant",
        )

    now = datetime.now(UTC)
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if record.status == "pending" and now > expires_at:
        record.status = "expired"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="expired_token",
        )

    if record.status == "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="authorization_pending",
        )

    if record.status == "denied":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="access_denied",
        )

    if record.status == "expired":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="expired_token",
        )

    if record.status == "consumed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_grant",
        )

    if record.status == "approved" and record.access_token:
        actor = await db.get(Actor, record.actor_id)
        if not actor:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Actor record not found after approval",
            )
        issued_token = record.access_token
        # Single-use: transition to consumed and clear plaintext token from database
        record.status = "consumed"
        record.access_token = None
        await db.commit()

        return TokenResponse(
            access_token=issued_token,
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

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="invalid_request",
    )
