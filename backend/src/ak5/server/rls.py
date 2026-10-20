"""PostgreSQL Row-Level Security (RLS) enforcement (deployment.md §1.5, identity.md §7, #106).

Dual-defense tenant isolation:
1. Application queries enforce workspace scoping (auth/scope.py).
2. Database-level RLS policies on PostgreSQL prevent cross-workspace reads/writes
   even in the event of application query bugs.

GUC values are stored on ``session.info`` and re-applied on every transaction begin
(``after_begin``) so mid-request ``commit()`` does not clear workspace context.
"""

from __future__ import annotations

import logging

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SESSION_WS_KEY = "ak5_rls_workspace_id"
_SESSION_BYPASS_KEY = "ak5_rls_bypass"

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
                    f"USING ("
                    f"  current_setting('ak5.bypass_rls', true) = 'on' "
                    f"  OR workspace_id = current_setting('ak5.workspace_id', true)"
                    f") "
                    f"WITH CHECK ("
                    f"  current_setting('ak5.bypass_rls', true) = 'on' "
                    f"  OR workspace_id = current_setting('ak5.workspace_id', true)"
                    f")"
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


def _apply_rls_gucs_on_connection(connection: Connection, session: Session) -> None:
    """Re-apply stored RLS GUC intent for the current transaction (sync path)."""
    if connection.dialect.name != "postgresql":
        return
    if session.info.get(_SESSION_BYPASS_KEY):
        connection.execute(text("SELECT set_config('ak5.bypass_rls', 'on', true)"))
    workspace_id = session.info.get(_SESSION_WS_KEY)
    if workspace_id:
        connection.execute(
            text("SELECT set_config('ak5.workspace_id', :ws, true)"),
            {"ws": workspace_id},
        )


@event.listens_for(Session, "after_begin")
def _reapply_rls_after_begin(
    session: Session, _transaction: object, connection: Connection
) -> None:
    """Restore transaction-local RLS GUCs after each BEGIN (including post-commit)."""
    _apply_rls_gucs_on_connection(connection, session)


async def set_workspace_rls_context(db: AsyncSession, workspace_id: str) -> None:
    """Set transaction-local ak5.workspace_id (and remember it for later transactions)."""
    db.info[_SESSION_WS_KEY] = workspace_id
    try:
        bind = db.get_bind()
    except Exception:
        bind = None

    if bind is not None and bind.dialect.name == "postgresql":
        await db.execute(
            text("SELECT set_config('ak5.workspace_id', :ws, true)"),
            {"ws": workspace_id},
        )


async def set_bypass_rls_context(db: AsyncSession, bypass: bool = True) -> None:
    """Set transaction-local ak5.bypass_rls (and remember it for later transactions)."""
    if bypass:
        db.info[_SESSION_BYPASS_KEY] = True
    else:
        db.info.pop(_SESSION_BYPASS_KEY, None)

    try:
        bind = db.get_bind()
    except Exception:
        bind = None

    if bind is not None and bind.dialect.name == "postgresql":
        await db.execute(
            text("SELECT set_config('ak5.bypass_rls', :val, true)"),
            {"val": "on" if bypass else "off"},
        )
