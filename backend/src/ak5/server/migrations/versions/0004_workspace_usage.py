"""workspace_usage: workspace resource usage counters (operations.md §3, #116).

Revision ID: 0004_workspace_usage
Revises: 0003_rate_counters
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_workspace_usage"
down_revision = "0003_rate_counters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_usage",
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("workspace_id", "key"),
    )


def downgrade() -> None:
    op.drop_table("workspace_usage")
