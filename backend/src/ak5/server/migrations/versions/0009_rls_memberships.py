"""rls_memberships: RLS on memberships (identity.md tenant table).

Revision ID: 0009_rls_memberships
Revises: 0008_widen_session_token_hash
Create Date: 2026-11-15
"""

from __future__ import annotations

from alembic import op

revision = "0009_rls_memberships"
down_revision = "0008_widen_session_token_hash"
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