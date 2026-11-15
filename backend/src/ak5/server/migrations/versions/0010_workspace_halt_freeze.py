"""Add workspace halt and freeze flags (#199).

Revision ID: 0010_workspace_halt_freeze
Revises: 0009_rls_memberships
Create Date: 2026-11-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_workspace_halt_freeze"
down_revision = "0009_rls_memberships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("halted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "workspaces",
        sa.Column("frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("workspaces", sa.Column("halt_reason", sa.Text(), nullable=True))
    op.add_column("workspaces", sa.Column("freeze_reason", sa.Text(), nullable=True))
    op.add_column("workspaces", sa.Column("halted_by", sa.String(length=128), nullable=True))
    op.add_column("workspaces", sa.Column("frozen_by", sa.String(length=128), nullable=True))
    op.add_column("workspaces", sa.Column("halted_at", sa.DateTime(), nullable=True))
    op.add_column("workspaces", sa.Column("frozen_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspaces", "frozen_at")
    op.drop_column("workspaces", "halted_at")
    op.drop_column("workspaces", "frozen_by")
    op.drop_column("workspaces", "halted_by")
    op.drop_column("workspaces", "freeze_reason")
    op.drop_column("workspaces", "halt_reason")
    op.drop_column("workspaces", "frozen")
    op.drop_column("workspaces", "halted")
