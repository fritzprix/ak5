"""blob_gc: deferred blob cleanup table (deployment.md §2, #139).

Revision ID: 0005_blob_gc
Revises: 0004_workspace_usage
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_blob_gc"
down_revision = "0004_workspace_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blob_gc",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=512), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_blob_gc_key", "blob_gc", ["key"], unique=False)
    op.create_index("ix_blob_gc_workspace_id", "blob_gc", ["workspace_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_blob_gc_workspace_id", table_name="blob_gc")
    op.drop_index("ix_blob_gc_key", table_name="blob_gc")
    op.drop_table("blob_gc")
