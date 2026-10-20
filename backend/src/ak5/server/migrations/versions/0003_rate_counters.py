"""rate_counters: fixed-window request counters (operations.md §2.2, #114).

Revision ID: 0003_rate_counters
Revises: 0002_auth_state
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_rate_counters"
down_revision = "0002_auth_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_counters",
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", "window_start"),
    )


def downgrade() -> None:
    op.drop_table("rate_counters")
