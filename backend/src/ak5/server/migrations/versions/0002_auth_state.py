"""DB-backed auth state: oidc_pending, device_pending, device_grant_failures (#107).

Revision ID: 0002_auth_state
Revises: 0001_baseline
Create Date: 2026-10-19

Replaces the process-memory dicts in ``auth.sessions`` (OIDC login), ``auth.oidc_device``
(upstream device flow) and ``auth.device_grant`` (failure rate limit) so every instance
sees the same state (deployment.md §6).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_auth_state"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oidc_pending",
        sa.Column("state", sa.String(length=128), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("nonce", sa.String(length=256), nullable=False),
        sa.Column("code_verifier", sa.String(length=256), nullable=False),
        sa.Column("client_key", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("state"),
    )
    op.create_index(op.f("ix_oidc_pending_client_key"), "oidc_pending", ["client_key"], unique=False)
    op.create_index(op.f("ix_oidc_pending_expires_at"), "oidc_pending", ["expires_at"], unique=False)

    op.create_table(
        "device_pending",
        sa.Column("device_code_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("interval", sa.Integer(), nullable=False),
        sa.Column("last_poll", sa.Float(), nullable=False),
        sa.Column("client_key", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("device_code_hash"),
    )
    op.create_index(op.f("ix_device_pending_client_key"), "device_pending", ["client_key"], unique=False)
    op.create_index(op.f("ix_device_pending_expires_at"), "device_pending", ["expires_at"], unique=False)

    op.create_table(
        "device_grant_failures",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("client_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_device_grant_failures_key_time",
        "device_grant_failures",
        ["client_key", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_device_grant_failures_key_time", table_name="device_grant_failures")
    op.drop_table("device_grant_failures")
    op.drop_index(op.f("ix_device_pending_expires_at"), table_name="device_pending")
    op.drop_index(op.f("ix_device_pending_client_key"), table_name="device_pending")
    op.drop_table("device_pending")
    op.drop_index(op.f("ix_oidc_pending_expires_at"), table_name="oidc_pending")
    op.drop_index(op.f("ix_oidc_pending_client_key"), table_name="oidc_pending")
    op.drop_table("oidc_pending")
