"""WebAuthn passkey registration + assertion (identity.md §6, #28).

Challenges are persisted (single use, short TTL) rather than kept in process
memory, so a callback landing on another instance still verifies.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ak5.server.auth.oidc_config import public_base_url
from ak5.server.models.passkey import PasskeyCredential, WebauthnChallenge
from ak5.server.models.workspace import User

CHALLENGE_TTL = timedelta(minutes=5)
# Window in which a WebAuthn assertion counts as "user present" for T15 (identity.md §6).
PRESENCE_WINDOW = timedelta(seconds=60)
MAX_PASSKEYS_PER_USER = 10


class PasskeyError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class RelyingParty:
    rp_id: str
    origin: str


def relying_party(request: Request) -> RelyingParty:
    """RP ID / origin: ``AK5_WEBAUTHN_*`` > ``AK5_PUBLIC_URL`` > the request's own URL."""
    rp_id = (os.environ.get("AK5_WEBAUTHN_RP_ID") or "").strip()
    origin = (os.environ.get("AK5_WEBAUTHN_ORIGIN") or "").strip().rstrip("/")
    if not origin:
        if (os.environ.get("AK5_PUBLIC_URL") or "").strip():
            origin = public_base_url()
        else:
            secure = (
                request.url.scheme == "https"
                or request.headers.get("x-forwarded-proto") == "https"
            )
            scheme = "https" if secure else "http"
            origin = f"{scheme}://{request.headers.get('host') or request.url.netloc}"
    if not rp_id:
        rp_id = urlparse(origin).hostname or "localhost"
    return RelyingParty(rp_id=rp_id, origin=origin)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _store_challenge(db: AsyncSession, user_id: str, purpose: str, challenge: bytes) -> str:
    now = _now()
    await db.execute(delete(WebauthnChallenge).where(WebauthnChallenge.expires_at <= now))
    challenge_id = f"wch_{secrets.token_urlsafe(18)}"
    db.add(
        WebauthnChallenge(
            challenge_id=challenge_id,
            user_id=user_id,
            purpose=purpose,
            challenge=challenge,
            expires_at=now + CHALLENGE_TTL,
        )
    )
    await db.flush()
    return challenge_id


async def _consume_challenge(
    db: AsyncSession, *, challenge_id: str, user_id: str, purpose: str
) -> bytes:
    row = await db.get(WebauthnChallenge, challenge_id, with_for_update=True)
    if row is None or row.user_id != user_id or row.purpose != purpose:
        raise PasskeyError("Unknown or already used challenge")
    expired = row.expires_at <= _now()
    challenge = row.challenge
    await db.delete(row)  # single use, even on failure
    await db.flush()
    if expired:
        raise PasskeyError("Challenge expired")
    return challenge


async def list_passkeys(db: AsyncSession, user_id: str) -> list[PasskeyCredential]:
    rows = await db.execute(
        select(PasskeyCredential)
        .where(PasskeyCredential.user_id == user_id)
        .order_by(PasskeyCredential.created_at)
    )
    return list(rows.scalars().all())


async def registration_options(db: AsyncSession, request: Request, user: User) -> dict[str, Any]:
    existing = await list_passkeys(db, user.user_id)
    if len(existing) >= MAX_PASSKEYS_PER_USER:
        raise PasskeyError("Too many passkeys registered", status_code=409)
    rp = relying_party(request)
    options = generate_registration_options(
        rp_id=rp.rp_id,
        rp_name="AK5",
        user_id=user.user_id.encode("utf-8"),
        user_name=user.email or user.user_id,
        user_display_name=user.display_name,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=p.credential_id) for p in existing],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    challenge_id = await _store_challenge(db, user.user_id, "register", options.challenge)
    return {"challenge_id": challenge_id, "options": json.loads(options_to_json(options))}


async def verify_registration(
    db: AsyncSession,
    request: Request,
    user: User,
    *,
    challenge_id: str,
    credential: dict[str, Any],
    label: str,
) -> PasskeyCredential:
    challenge = await _consume_challenge(
        db, challenge_id=challenge_id, user_id=user.user_id, purpose="register"
    )
    rp = relying_party(request)
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp.rp_id,
            expected_origin=rp.origin,
            require_user_verification=True,
        )
    except (InvalidRegistrationResponse, ValueError, KeyError) as exc:
        raise PasskeyError(f"Passkey registration failed: {exc}") from exc
    taken = await db.execute(
        select(PasskeyCredential.passkey_id).where(
            PasskeyCredential.credential_id == verified.credential_id
        )
    )
    if taken.first() is not None:
        raise PasskeyError("Credential already registered", status_code=409)
    row = PasskeyCredential(
        passkey_id=f"pk_{uuid.uuid4().hex}",
        user_id=user.user_id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        label=(label or "").strip()[:128] or "Passkey",
    )
    db.add(row)
    await db.flush()
    return row


async def assertion_options(db: AsyncSession, request: Request, user_id: str) -> dict[str, Any]:
    keys = await list_passkeys(db, user_id)
    if not keys:
        raise PasskeyError("No passkey registered", status_code=409)
    rp = relying_party(request)
    options = generate_authentication_options(
        rp_id=rp.rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=k.credential_id) for k in keys],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge_id = await _store_challenge(db, user_id, "assert", options.challenge)
    return {"challenge_id": challenge_id, "options": json.loads(options_to_json(options))}


async def verify_assertion(
    db: AsyncSession,
    request: Request,
    user_id: str,
    *,
    challenge_id: str,
    credential: dict[str, Any],
) -> datetime:
    """Verify an assertion; return the UTC time presence was established."""
    challenge = await _consume_challenge(
        db, challenge_id=challenge_id, user_id=user_id, purpose="assert"
    )
    try:
        raw_id = base64url_to_bytes(str(credential.get("rawId") or credential.get("id") or ""))
    except Exception as exc:
        raise PasskeyError("Malformed credential id") from exc
    row = (
        await db.execute(
            select(PasskeyCredential).where(
                PasskeyCredential.credential_id == raw_id,
                PasskeyCredential.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise PasskeyError("Unknown passkey", status_code=401)
    rp = relying_party(request)
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp.rp_id,
            expected_origin=rp.origin,
            credential_public_key=row.public_key,
            credential_current_sign_count=row.sign_count,
            require_user_verification=True,
        )
    except (InvalidAuthenticationResponse, ValueError, KeyError) as exc:
        raise PasskeyError(f"Passkey assertion failed: {exc}", status_code=401) from exc
    row.sign_count = verified.new_sign_count
    row.last_used_at = _now()
    await db.flush()
    return datetime.now(UTC)


async def delete_passkey(db: AsyncSession, user_id: str, passkey_id: str) -> bool:
    row = await db.get(PasskeyCredential, passkey_id)
    if row is None or row.user_id != user_id:
        return False
    await db.delete(row)
    await db.flush()
    return True


def credential_public(row: PasskeyCredential) -> dict[str, Any]:
    return {
        "passkey_id": row.passkey_id,
        "label": row.label,
        "credential_id": bytes_to_base64url(row.credential_id),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }
