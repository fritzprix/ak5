"""Add non-blocking attention flag columns to tickets (#200).

Migrates existing ``blocked_kind = 'needs_human'`` rows to
``attention_flag = true`` and clears their blocking overlay so
non-blocking attention never stops work from being pulled or reviewed.

Revision ID: 0009_attention_flag
Revises: 0008_widen_session_token_hash
Create Date: 2026-11-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_attention_flag"
down_revision = "0008_widen_session_token_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("attention_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("tickets", sa.Column("attention_reason", sa.Text(), nullable=True))
    op.add_column("tickets", sa.Column("attention_since", sa.DateTime(), nullable=True))
    op.add_column("tickets", sa.Column("attention_actor", sa.String(length=128), nullable=True))

    op.execute(
        sa.text(
            "UPDATE tickets "
            "SET attention_flag = TRUE, "
            "    attention_reason = blocked_by, "
            "    attention_since = blocked_since, "
            "    attention_actor = blocked_actor, "
            "    blocked_kind = NULL, "
            "    blocked_by = NULL, "
            "    blocked_since = NULL, "
            "    blocked_actor = NULL "
            "WHERE blocked_kind = 'needs_human'"
        )
    )


def downgrade() -> None:
    op.drop_column("tickets", "attention_actor")
    op.drop_column("tickets", "attention_since")
    op.drop_column("tickets", "attention_reason")
    op.drop_column("tickets", "attention_flag")
