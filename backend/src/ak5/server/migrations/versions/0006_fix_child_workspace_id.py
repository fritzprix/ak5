"""Backfill mismatched child workspace_id from parent board/ticket (#158).

Revision ID: 0006_fix_child_workspace_id
Revises: 0005_blob_gc
Create Date: 2026-10-20
"""

from __future__ import annotations

from alembic import op

revision = "0006_fix_child_workspace_id"
down_revision = "0005_blob_gc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Board-scoped children
    op.execute(
        """
        UPDATE columns
        SET workspace_id = (
            SELECT boards.workspace_id FROM boards WHERE boards.board_id = columns.board_id
        )
        WHERE EXISTS (
            SELECT 1 FROM boards
            WHERE boards.board_id = columns.board_id
              AND boards.workspace_id <> columns.workspace_id
        )
        """
    )
    op.execute(
        """
        UPDATE board_members
        SET workspace_id = (
            SELECT boards.workspace_id FROM boards
            WHERE boards.board_id = board_members.board_id
        )
        WHERE EXISTS (
            SELECT 1 FROM boards
            WHERE boards.board_id = board_members.board_id
              AND boards.workspace_id <> board_members.workspace_id
        )
        """
    )
    # Ticket-scoped children
    for table in (
        "ticket_revisions",
        "ticket_reviews",
        "ticket_postmortems",
        "ticket_comments",
        "ticket_attachments",
    ):
        op.execute(
            f"""
            UPDATE {table}
            SET workspace_id = (
                SELECT tickets.workspace_id FROM tickets
                WHERE tickets.ticket_id = {table}.ticket_id
            )
            WHERE EXISTS (
                SELECT 1 FROM tickets
                WHERE tickets.ticket_id = {table}.ticket_id
                  AND tickets.workspace_id <> {table}.workspace_id
            )
            """
        )
    # Audit rows targeting tickets
    op.execute(
        """
        UPDATE audit_logs
        SET workspace_id = (
            SELECT tickets.workspace_id FROM tickets
            WHERE tickets.ticket_id = audit_logs.target_id
        )
        WHERE audit_logs.target_type = 'ticket'
          AND EXISTS (
            SELECT 1 FROM tickets
            WHERE tickets.ticket_id = audit_logs.target_id
              AND tickets.workspace_id <> audit_logs.workspace_id
          )
        """
    )


def downgrade() -> None:
    # Data repair is not reversible.
    pass
