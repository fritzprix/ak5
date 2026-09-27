import json
from typing import Any

DEFAULT_AGENT_EVENTS = frozenset({
    "TICKET_CREATED",
    "TICKET_DELEGATED",
    "TICKET_UPDATED",
    "TICKET_MOVED",
    "COMMENT_ADDED",
})

EVENT_ALIASES = {
    "TICKET_COMMENTED": "COMMENT_ADDED",
    "COMMENT_CREATED": "COMMENT_ADDED",
    "TICKET_ADD": "TICKET_CREATED",
    "TICKET_MOVE": "TICKET_MOVED",
    "TICKET_UPDATE": "TICKET_UPDATED",
    "TICKET_DELEGATE": "TICKET_DELEGATED",
}


def normalize_actor_id(actor_id: str | None) -> str:
    """Normalize actor ID by stripping leading @ and surrounding whitespace."""
    if not actor_id:
        return ""
    return actor_id.strip().lstrip("@").lower()


def matches_event_filter(events_filter: str | list[str] | set[str] | None, event_type: str) -> bool:
    """Check if an event_type matches the subscription event filter.

    Supports:
    - None / empty: Matches all events
    - 'DEFAULT' or 'LIFECYCLE': Matches DEFAULT_AGENT_EVENTS
    - 'TICKET' or 'TICKET_*': Matches any TICKET_* event plus COMMENT_ADDED
    - TICKET_CREATED: Also matches TICKET_DELEGATED (delegating is subtask creation)
    - Aliases: e.g. TICKET_COMMENTED -> COMMENT_ADDED
    """
    if not events_filter:
        return True

    if isinstance(events_filter, str):
        tokens = [e.strip().upper() for e in events_filter.split(",") if e.strip()]
    else:
        tokens = [e.strip().upper() for e in events_filter if isinstance(e, str) and e.strip()]

    if not tokens:
        return True

    normalized_allowed: set[str] = set()
    wildcard_ticket = False

    for token in tokens:
        if token in ("*", "ALL"):
            return True
        if token in ("DEFAULT", "LIFECYCLE"):
            normalized_allowed.update(DEFAULT_AGENT_EVENTS)
        elif token in ("TICKET", "TICKET_*"):
            wildcard_ticket = True
        else:
            resolved = EVENT_ALIASES.get(token, token)
            normalized_allowed.add(resolved)
            if resolved == "TICKET_CREATED":
                normalized_allowed.add("TICKET_DELEGATED")

    curr_evt = event_type.strip().upper()
    if wildcard_ticket and (curr_evt.startswith("TICKET_") or curr_evt == "COMMENT_ADDED"):
        return True

    return curr_evt in normalized_allowed


def matches_agent_filter(
    for_agent: str | None,
    ticket_data: dict[str, Any] | None,
    raw_data: dict[str, Any] | None = None,
) -> bool:
    """Check if an event matches for_agent, normalizing '@' prefix.

    Matches if:
    1. The ticket's assigned_to matches for_agent.
    2. OR target_actor_id matches for_agent (delegation).
    3. OR for_agent is mentioned in comment content (e.g. '@agent-name').
    """
    if not for_agent:
        return True
    clean_for = normalize_actor_id(for_agent)
    if not clean_for:
        return True

    # 1. Check ticket assignment
    if ticket_data and isinstance(ticket_data, dict):
        clean_assigned = normalize_actor_id(ticket_data.get("assigned_to"))
        if clean_assigned and clean_assigned == clean_for:
            return True

    # 2. Check delegation target_actor_id or comment mentions in raw event data
    if raw_data and isinstance(raw_data, dict):
        target_actor = raw_data.get("target_actor_id")
        if target_actor and normalize_actor_id(target_actor) == clean_for:
            return True

        comment = raw_data.get("comment")
        if isinstance(comment, dict):
            content = str(comment.get("content", "")).lower()
            if f"@{clean_for}" in content or f"@{for_agent.lower()}" in content:
                return True

    return False


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
