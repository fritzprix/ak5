"""Short-lived auth state that used to live in process memory (deployment.md §6, #107).

All three tables are shared by every server instance and expire on their own:
rows past ``expires_at`` (or older than the failure window) are deleted
opportunistically on write — nothing is ever evicted to make room for someone else.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ak5.server.models.base import Base


class OidcPending(Base):
    """OIDC Authorization-Code login in flight (state → nonce + PKCE verifier). One use."""

    __tablename__ = "oidc_pending"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce: Mapped[str] = mapped_column(String(256), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(256), nullable=False)
    client_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class DevicePending(Base):
    """Upstream-IdP device flow being polled. Keyed by the *hash* of the IdP device_code."""

    __tablename__ = "device_pending"

    device_code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    interval: Mapped[int] = mapped_column(Integer, nullable=False)
    last_poll: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    client_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class DeviceGrantFailure(Base):
    """One failed ``user_code`` lookup/approval; counted per client key over a window."""

    __tablename__ = "device_grant_failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        # (client_key, created_at) is not unique: two failures can share a timestamp.
        Index("ix_device_grant_failures_key_time", "client_key", "created_at"),
    )
