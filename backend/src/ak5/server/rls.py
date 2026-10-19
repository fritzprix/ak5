"""PostgreSQL Row-Level Security (RLS) enforcement (deployment.md §1.5, identity.md §7, #106).

Dual-defense tenant isolation:
1. Application queries enforce workspace scoping (auth/scope.py).
2. Database-level RLS policies on PostgreSQL prevent cross-workspace reads/writes
   even in the event of application query bugs.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Tenant data tables containing a workspace_id column (identity.md §1 line 38)
TENANT_RLS_TABLES: tuple[str, ...] = (
    "boards",
    "columns",
    "tickets",
    "ticket_revisions",
    "ticket_reviews",
    "ticket_comments",
    "ticket_attachments",
    "ticket_postmortems",
    "board_members",
    "events",
    "subscriptions",
    "idempotency_keys",
    "audit_logs",
    "workspace_invites",
    "workspace_usage",
    "blob_gc",
    "agents",
)

RLS_POLICY_NAME = "ak5_workspace_isolation"


def apply_postgres_rls(connection: Connection) -> None:
    """Enable RLS and create isolation policies on all tenant tables (PostgreSQL only)."""
    if connection.dialect.name != "postgresql":
        return

    for table in TENANT_RLS_TABLES:
        try:
            connection.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            connection.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            connection.execute(text(f"DROP POLICY IF EXISTS {RLS_POLICY_NAME} ON {table}"))
            connection.execute(
                text(
                    f"CREATE POLICY {RLS_POLICY_NAME} ON {table} "
                    f"USING (workspace_id = current_setting('ak5.workspace_id', true))"
                )
            )
        except Exception as exc:
            logger.warning("Failed to configure RLS on table %s: %s", table, exc)
            raise


def drop_postgres_rls(connection: Connection) -> None:
    """Drop RLS policies and disable RLS on tenant tables (PostgreSQL only)."""
    if connection.dialect.name != "postgresql":
        return

    for table in TENANT_RLS_TABLES:
        try:
            connection.execute(text(f"DROP POLICY IF EXISTS {RLS_POLICY_NAME} ON {table}"))
            connection.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
            connection.execute(text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
        except Exception as exc:
            logger.warning("Failed to drop RLS on table %s: %s", table, exc)


async def set_workspace_rls_context(db: AsyncSession, workspace_id: str) -> None:
    """Set transaction-local ak5.workspace_id setting on PostgreSQL sessions."""
    try:
        bind = db.get_bind()
    except Exception:
        bind = None

    if bind is not None and bind.dialect.name == "postgresql":
        await db.execute(text("SET LOCAL ak5.workspace_id = :ws"), {"ws": workspace_id})
