"""Per-workspace inbox board ids (identity.md §7, workflow.md §2.1).

Every workspace has one system inbox. ``ws_local`` keeps the historical id
``inbox``; other workspaces use ``<workspace_id>__inbox``. The API alias
``inbox`` always means the caller's workspace inbox.
"""

from __future__ import annotations

from ak5.server.models.workspace import LOCAL_WORKSPACE_ID

INBOX_BOARD_ID = "inbox"
INBOX_ALIAS = "inbox"
_INBOX_SUFFIX = "__inbox"


def inbox_board_id_for(workspace_id: str | None) -> str:
    """Inbox board id for ``workspace_id`` (``None`` → local workspace)."""
    if workspace_id is None or workspace_id == LOCAL_WORKSPACE_ID:
        return INBOX_BOARD_ID
    return f"{workspace_id}{_INBOX_SUFFIX}"


def is_inbox_board_id(board_id: str | None) -> bool:
    """True for any workspace's inbox id. Board creation rejects these ids."""
    if not board_id:
        return False
    return board_id == INBOX_BOARD_ID or board_id.endswith(_INBOX_SUFFIX)


def resolve_board_alias(board_id: str, workspace_id: str | None) -> str:
    """Map the ``inbox`` alias to the caller's workspace inbox; other ids pass through."""
    if board_id == INBOX_ALIAS:
        return inbox_board_id_for(workspace_id)
    return board_id
