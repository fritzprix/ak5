import json
from typing import Any


def extract_event_context(event_type: str, data: dict[str, Any]) -> dict[str, str]:
    """Build env-safe context fields from an event payload for hook subprocesses."""
    ticket = data.get("ticket") or data.get("subtask")
    if not isinstance(ticket, dict):
        ticket = {}

    ticket_id = ticket.get("ticket_id") or data.get("ticket_id") or data.get("parent_ticket_id") or ""
    board_id = data.get("board_id") or ticket.get("board_id") or ""
    title = ticket.get("title", "")
    comment = data.get("comment") if isinstance(data.get("comment"), dict) else {}
    actor_id = data.get("actor_id") or comment.get("actor_id") or ""
    status = ticket.get("status", "")
    column_id = ticket.get("column_id", "")
    from_column = data.get("from_column", "")
    to_column = data.get("to_column", "")

    if event_type == "TICKET_CREATED":
        summary = f"Ticket {ticket_id} ('{title}') created by @{actor_id}"
    elif event_type == "TICKET_MOVED":
        summary = f"Ticket {ticket_id} moved to {to_column or column_id} by @{actor_id}"
    elif event_type == "TICKET_DELEGATED":
        summary = f"Subtask {ticket_id} ('{title}') delegated by @{actor_id}"
    elif event_type == "TICKET_UPDATED":
        summary = f"Ticket {ticket_id} updated by @{actor_id}"
    elif event_type == "COMMENT_ADDED":
        summary = f"Comment added on {ticket_id} by @{actor_id}"
    elif event_type == "ATTACHMENT_ADDED":
        att = data.get("attachment")
        filename = att.get("filename", "") if isinstance(att, dict) else ""
        summary = (
            f"Attachment '{filename}' uploaded to {ticket_id}" if filename else f"Attachment uploaded to {ticket_id}"
        )
    elif event_type == "ATTACHMENT_DELETED":
        att_id = data.get("attachment_id", "")
        summary = f"Attachment {att_id} deleted from {ticket_id}" if att_id else f"Attachment deleted from {ticket_id}"
    else:
        summary = f"Event {event_type} on {ticket_id or board_id}"

    data_json = json.dumps(data, ensure_ascii=False)

    return {
        "event": event_type,
        "event_type": event_type,
        "board_id": board_id,
        "ticket_id": ticket_id,
        "title": title,
        "actor_id": actor_id,
        "status": status,
        "column_id": column_id,
        "from_column": from_column,
        "to_column": to_column,
        "summary": summary,
        "data_json": data_json,
    }
