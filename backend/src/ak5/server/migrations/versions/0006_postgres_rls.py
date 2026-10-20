"""postgres_rls: apply PostgreSQL Row-Level Security on tenant tables (deployment.md §1.5, #106).

Revision ID: 0006_postgres_rls
Revises: 0005_blob_gc
Create Date: 2026-10-20
"""

from __future__ import annotations

from alembic import op

revision = "0006_postgres_rls"
down_revision = "0005_blob_gc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from ak5.server.rls import apply_postgres_rls

        apply_postgres_rls(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from ak5.server.rls import drop_postgres_rls

        drop_postgres_rls(bind)
