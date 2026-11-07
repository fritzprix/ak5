"""Widen user_sessions.refresh_token_hash for web cookie:csrf digests.

Web sessions store ``sha256:sha256`` (129 chars) which exceeds String(128).
SQLite does not enforce the length; Postgres rejects the insert.

Downgrade deletes sessions whose hash no longer fits (every web session), so
those users sign in again; narrowing the column would otherwise fail.

Revision ID: 0008_widen_session_token_hash
Revises: 0007_fix_child_workspace_id
Create Date: 2026-10-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_widen_session_token_hash"
down_revision = "0007_fix_child_workspace_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("user_sessions") as batch:
            batch.alter_column(
                "refresh_token_hash",
                existing_type=sa.String(length=128),
                type_=sa.String(length=256),
                existing_nullable=False,
            )
    else:
        op.alter_column(
            "user_sessions",
            "refresh_token_hash",
            existing_type=sa.String(length=128),
            type_=sa.String(length=256),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute(sa.text("DELETE FROM user_sessions WHERE length(refresh_token_hash) > 128"))
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("user_sessions") as batch:
            batch.alter_column(
                "refresh_token_hash",
                existing_type=sa.String(length=256),
                type_=sa.String(length=128),
                existing_nullable=False,
            )
    else:
        op.alter_column(
            "user_sessions",
            "refresh_token_hash",
            existing_type=sa.String(length=256),
            type_=sa.String(length=128),
            existing_nullable=False,
        )
