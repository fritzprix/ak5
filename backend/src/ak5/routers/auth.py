from datetime import datetime, timedelta, timezone
import json
from typing import Annotated
import jwt
from fastapi import APIRouter, Depends, HTTPException, Header, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.config import settings
from ak5.database import get_db
from ak5.models.actor import Actor
from ak5.schemas.actor import ActorIdentifyRequest, ActorOut, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)


def create_access_token(actor_id: str, actor_type: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "sub": actor_id,
        "actor_type": actor_type,
        "exp": expire,
    }
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


async def get_current_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Actor:
    """Extract and validate Actor from Bearer token."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        actor_id: str = payload.get("sub")
        if not actor_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    stmt = select(Actor).where(Actor.actor_id == actor_id)
    result = await db.execute(stmt)
    actor = result.scalar_one_or_none()
    if not actor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Actor '{actor_id}' not found",
        )
    return actor


# Optional actor dependency for endpoints that can be accessed anonymously or with token
async def get_current_actor_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Actor | None:
    if not credentials:
        return None
    try:
        return await get_current_actor(credentials, db)
    except HTTPException:
        return None


@router.post("/identify", response_model=TokenResponse)
async def identify_actor(
    req: ActorIdentifyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """Identify or register an Actor (Human PM/Dev or AI Agent) and issue JWT token."""
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
