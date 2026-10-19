"""WebAuthn passkeys + one-shot challenges (identity.md §6, #28)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ak5.server.models.base import Base


class PasskeyCredential(Base):
    __tablename__ = "passkey_credentials"

    passkey_id: Mapped[str] = mapped_column(String(128), primary_key=True)  # pk_…
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WebauthnChallenge(Base):
    """DB-backed so multi-instance deployments work (see #20); single use."""

    __tablename__ = "webauthn_challenges"

    challenge_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)  # register|assert
    challenge: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
