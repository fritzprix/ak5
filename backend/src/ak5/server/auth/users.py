"""User / identity upsert helpers for OIDC + local owner (identity.md §3)."""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.server.models.workspace import (
    LOCAL_WORKSPACE_ID,
    Membership,
    User,
    UserIdentity,
)
from ak5.server.seed import LOCAL_USER_ID

LOCAL_OWNER_ISSUER = "https://ak5.local/owner"


def local_owner_password() -> str:
    """Password for built-in local owner (env / settings)."""
    from ak5.server.config import settings

    return (
        os.environ.get("AK5_AUTH_PASSWORD")
        or os.environ.get("AK5_WEB_PASSWORD")
        or settings.AUTH_PASSWORD
        or settings.WEB_PASSWORD
        or ""
    ).strip()


def local_owner_configured() -> bool:
    return bool(local_owner_password())


def verify_local_owner_password(password: str) -> bool:
    expected = local_owner_password()
    if not expected:
        return False
    return secrets.compare_digest(
        hashlib.sha256(password.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


async def ensure_local_owner_user(db: AsyncSession) -> User:
    """Ensure ``usr_local_pm`` exists with a local-owner identity row."""
    user = await db.get(User, LOCAL_USER_ID)
    if user is None:
        user = User(
            user_id=LOCAL_USER_ID,
            email="local-owner@localhost",
            display_name="Local Owner",
        )
        db.add(user)
    identity_id = f"idn_local_owner_{LOCAL_USER_ID}"
    if await db.get(UserIdentity, identity_id) is None:
        db.add(
            UserIdentity(
                identity_id=identity_id,
                user_id=LOCAL_USER_ID,
                issuer=LOCAL_OWNER_ISSUER,
                subject="local-owner",
                email_verified=True,
            )
        )
    mem_id = f"mem_{LOCAL_WORKSPACE_ID}_{LOCAL_USER_ID}"
    if await db.get(Membership, mem_id) is None:
        db.add(
            Membership(
                membership_id=mem_id,
                workspace_id=LOCAL_WORKSPACE_ID,
                user_id=LOCAL_USER_ID,
                role="owner",
            )
        )
    await db.flush()
    return user


async def upsert_user_from_oidc(
    db: AsyncSession,
    *,
    issuer: str,
    subject: str,
    email: str | None,
    email_verified: bool,
    display_name: str,
) -> User:
    """Find or create User by ``(issuer, subject)``."""
    existing = (
        await db.execute(
            select(UserIdentity).where(
                UserIdentity.issuer == issuer,
                UserIdentity.subject == subject,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        user = await db.get(User, existing.user_id)
        assert user is not None
        existing.email_verified = email_verified
        if email:
            user.email = email
        if display_name:
            user.display_name = display_name
        await db.flush()
        return user

    user_id = f"usr_{uuid.uuid4().hex[:16]}"
    user = User(
        user_id=user_id,
        email=email,
        display_name=display_name or subject,
    )
    db.add(user)
    db.add(
        UserIdentity(
            identity_id=f"idn_{uuid.uuid4().hex}",
            user_id=user_id,
            issuer=issuer,
            subject=subject,
            email_verified=email_verified,
        )
    )
    # First OIDC user on empty memberships becomes local workspace member.
    mem_id = f"mem_{LOCAL_WORKSPACE_ID}_{user_id}"
    if await db.get(Membership, mem_id) is None:
        # Prefer owner if no other owners exist.
        owners = (
            await db.execute(
                select(Membership).where(
                    Membership.workspace_id == LOCAL_WORKSPACE_ID,
                    Membership.role == "owner",
                )
            )
        ).scalars().all()
        role = "member" if owners else "owner"
        db.add(
            Membership(
                membership_id=mem_id,
                workspace_id=LOCAL_WORKSPACE_ID,
                user_id=user_id,
                role=role,
            )
        )
    await db.flush()
    return user
