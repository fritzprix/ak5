"""MCP tools — 1:1 adapters for cli.md §4.3 / §4.4 verbs.

Payload parameters use ``ak5.contracts`` Pydantic models so MCP ``input_schema``
matches the workflow payload JSON schemas. Errors surface as ``ErrorCode``
envelopes (``{"error": {"code", "message", "details"}}``).
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import TextContent

from ak5.contracts.payloads import Deliverable, Postmortem, RoutePlan, Verdict
from ak5.contracts.stages import BlockedKind
from ak5.mcp.client import AK5APIError, AK5Client

server = MCPServer("ak5-orchestrator")
_client = AK5Client()

# cli.md §4.3 + §4.4 verbs → MCP tool names (exact 1:1 mapping for issue #14).
V2_CLI_TOOL_MAP: dict[str, str] = {
    "next": "ak5_next",
    "ticket claim": "ak5_ticket_claim",
    "ticket heartbeat": "ak5_ticket_heartbeat",
    "ticket release": "ak5_ticket_release",
    "ticket submit": "ak5_ticket_submit",
    "ticket bounce": "ak5_ticket_bounce",
    "ticket route": "ak5_ticket_route",
    "ticket verdict": "ak5_ticket_verdict",
    "ticket postmortem": "ak5_ticket_postmortem",
    "ticket create": "ak5_ticket_create",
    "ticket block": "ak5_ticket_block",
    "ticket unblock": "ak5_ticket_unblock",
    "ticket reopen": "ak5_ticket_reopen",
    "ticket reroute": "ak5_ticket_reroute",
    "ticket history": "ak5_ticket_history",
}

V2_TOOL_NAMES: frozenset[str] = frozenset(V2_CLI_TOOL_MAP.values())


def get_client() -> AK5Client:
    return _client


def _json_content(data: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, default=str))]


def _tool_result(data: Any) -> list[TextContent]:
    return _json_content(data)


def _tool_error(exc: AK5APIError) -> list[TextContent]:
    return _json_content(exc.envelope())


# ---------------------------------------------------------------------------
# §4.3 work-loop verbs
# ---------------------------------------------------------------------------


@server.tool()
async def ak5_next(
    board_id: str = "inbox",
    capabilities: list[str] | None = None,
) -> list[TextContent]:
    """Return the oldest unclaimed unblocked ticket on a board (cli.md §4.3 ``next``).

    Does not claim. Empty result is ``{"ticket": null}`` (CLI exit 6).
    """
    client = get_client()
    try:
        ticket = await client.next_ticket(board_id, capabilities=capabilities)
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result({"ticket": ticket})


@server.tool()
async def ak5_ticket_claim(
    ticket_id: str,
    capabilities: list[str] | None = None,
) -> list[TextContent]:
    """Claim a ticket and receive ``claim_token`` + lease (cli.md ``ticket claim``)."""
    client = get_client()
    try:
        result = await client.claim(ticket_id, capabilities=capabilities)
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_heartbeat(ticket_id: str, claim_token: str) -> list[TextContent]:
    """Extend the claim lease (cli.md ``ticket heartbeat``)."""
    client = get_client()
    try:
        result = await client.heartbeat(ticket_id, claim_token=claim_token)
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_release(ticket_id: str, claim_token: str, reason: str) -> list[TextContent]:
    """Release an in-progress claim back to backlog (cli.md ``ticket release``)."""
    client = get_client()
    try:
        result = await client.release(ticket_id, claim_token=claim_token, reason=reason)
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_submit(
    ticket_id: str,
    claim_token: str,
    deliverable: Deliverable,
) -> list[TextContent]:
    """Submit a worker deliverable → agent_review (cli.md ``ticket submit``).

    ``deliverable`` follows workflow.md §5.2 (contracts ``Deliverable`` schema).
    """
    client = get_client()
    try:
        result = await client.submit(ticket_id, claim_token=claim_token, deliverable=deliverable)
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_bounce(ticket_id: str, claim_token: str, reason: str) -> list[TextContent]:
    """Bounce an in-progress ticket back to inbox (cli.md ``ticket bounce``)."""
    client = get_client()
    try:
        result = await client.bounce(ticket_id, claim_token=claim_token, reason=reason)
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_route(
    ticket_id: str,
    claim_token: str,
    plan: RoutePlan,
) -> list[TextContent]:
    """Route an unclassified inbox ticket to a board (cli.md ``ticket route``).

    ``plan`` follows workflow.md §5.1 (contracts ``RoutePlan`` schema).
    """
    client = get_client()
    try:
        result = await client.route(ticket_id, claim_token=claim_token, plan=plan)
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_verdict(
    ticket_id: str,
    verdict: Verdict,
    claim_token: str | None = None,
) -> list[TextContent]:
    """Approve or reject at agent_review / human_review (cli.md ``ticket verdict``).

    ``verdict`` follows workflow.md §5.3 (contracts ``Verdict`` schema).
    Gatekeeper paths require ``claim_token``; human_review may omit it.
    """
    client = get_client()
    try:
        result = await client.verdict(ticket_id, verdict=verdict, claim_token=claim_token)
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_postmortem(
    ticket_id: str,
    claim_token: str,
    postmortem: Postmortem,
) -> list[TextContent]:
    """Submit a postmortem after human reject / reopen (cli.md ``ticket postmortem``).

    ``postmortem`` follows workflow.md §5.4 (contracts ``Postmortem`` schema).
    """
    client = get_client()
    try:
        result = await client.postmortem(
            ticket_id, claim_token=claim_token, postmortem=postmortem
        )
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


# ---------------------------------------------------------------------------
# §4.4 human / common verbs
# ---------------------------------------------------------------------------


@server.tool()
async def ak5_ticket_create(
    title: str,
    board_id: str | None = None,
    original_request: str | None = None,
    spec: str | None = None,
    acceptance_criteria: list[str] | None = None,
    description: str | None = None,
    priority: str = "medium",
    labels: list[str] | None = None,
    max_attempts: int | None = None,
    parent_ticket_id: str | None = None,
) -> list[TextContent]:
    """Create a ticket (T1 inbox or T2 create_direct) — cli.md ``ticket create``."""
    client = get_client()
    try:
        result = await client.create(
            title,
            board_id=board_id,
            original_request=original_request,
            spec=spec,
            acceptance_criteria=acceptance_criteria,
            description=description,
            priority=priority,
            labels=labels,
            max_attempts=max_attempts,
            parent_ticket_id=parent_ticket_id,
        )
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_block(
    ticket_id: str,
    kind: BlockedKind,
    reason: str,
    claim_token: str | None = None,
) -> list[TextContent]:
    """Block a ticket (B1) — cli.md ``ticket block``."""
    client = get_client()
    try:
        result = await client.block(
            ticket_id, kind=kind, reason=reason, claim_token=claim_token
        )
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_unblock(
    ticket_id: str,
    resolution: str | None = None,
) -> list[TextContent]:
    """Unblock a ticket (B2/B3) — cli.md ``ticket unblock``."""
    client = get_client()
    try:
        result = await client.unblock(ticket_id, resolution=resolution)
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_reopen(ticket_id: str, reason: str) -> list[TextContent]:
    """Reopen a done ticket to postmortem_pending (cli.md ``ticket reopen``)."""
    client = get_client()
    try:
        result = await client.reopen(ticket_id, reason=reason)
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_reroute(
    ticket_id: str,
    reason: str,
    to_inbox: bool = False,
    target_board_id: str | None = None,
) -> list[TextContent]:
    """Reroute a ticket to inbox or another board backlog (cli.md ``ticket reroute``)."""
    client = get_client()
    try:
        result = await client.reroute(
            ticket_id,
            reason=reason,
            to_inbox=to_inbox,
            target_board_id=target_board_id,
        )
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_history(ticket_id: str) -> list[TextContent]:
    """Revision / review / postmortem / audit history (cli.md ``ticket history``)."""
    client = get_client()
    try:
        result = await client.history(ticket_id)
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


# ---------------------------------------------------------------------------
# Non-conflicting discovery / utility tools
# ---------------------------------------------------------------------------


@server.tool()
async def ak5_list_boards() -> list[TextContent]:
    """현재 시스템에 등록된 전체 칸반 보드(Board) 목록을 조회합니다.

    각 보드의 ID, 이름, 설명, 생성자 정보를 반환합니다.
    """
    client = get_client()
    try:
        boards = await client.list_boards()
    except AK5APIError as exc:
        return _tool_error(exc)
    if not boards:
        return [TextContent(type="text", text="No boards found.")]

    lines = [f"Found {len(boards)} board(s):"]
    for b in boards:
        desc = b.get("description") or "N/A"
        lines.append(
            f"- Board ID: {b['board_id']} | Name: {b['name']}\n"
            f"  Description: {desc}\n"
            f"  Created by: @{b.get('created_by')}"
        )
    return [TextContent(type="text", text="\n".join(lines))]


@server.tool()
async def ak5_list_available_agents(
    capability: str | None = None,
    search_query: str | None = None,
) -> list[TextContent]:
    """현재 시스템에 등록된 에이전트 목록을 조회하여 적절한 작업 위임 대상을 탐색합니다.

    - capability: 특정 역량 태그 (예: 'image-resize', 'code-review', 'db-migration')
    - search_query: 자연어 검색어 (예: '이미지 최적화 모듈을 작성할 에이전트')
    """
    client = get_client()
    try:
        agents = await client.list_available_agents(
            capability=capability, search_query=search_query
        )
    except AK5APIError as exc:
        return _tool_error(exc)

    if not agents:
        msg = f"No available agents found for capability='{capability}', query='{search_query}'."
        return [TextContent(type="text", text=msg)]

    lines = [f"Found {len(agents)} matching agent(s):"]
    for a in agents:
        caps = ", ".join(a.get("capabilities", []))
        lines.append(
            f"- ID: @{a['actor_id']} | Name: {a['name']} | Role: {a['role']} | Status: {a['status']}\n"
            f"  Capabilities: [{caps}]\n"
            f"  Description: {a.get('description') or 'N/A'}"
        )
    return [TextContent(type="text", text="\n".join(lines))]


@server.tool()
async def ak5_delegate_subtask(
    parent_ticket_id: str,
    target_agent_id: str,
    title: str,
    description: str,
    priority: str = "medium",
) -> list[TextContent]:
    """[DEPRECATED] Prefer ``ak5_ticket_create`` with ``parent_ticket_id`` (pull model).

    Creates a child ticket via create_direct; ``target_agent_id`` is ignored by the server.
    """
    client = get_client()
    try:
        subtask = await client.delegate_subtask(
            parent_ticket_id=parent_ticket_id,
            target_agent_id=target_agent_id,
            title=title,
            description=description,
            priority=priority,
        )
    except AK5APIError as exc:
        return _tool_error(exc)
    result_text = (
        f"✓ Subtask '{subtask['ticket_id']}' successfully created and delegated to @{target_agent_id}.\n"
        f"  Title: {subtask['title']}\n"
        f"  Parent Ticket: {parent_ticket_id}\n"
        f"  Column: {subtask['column_id']} | Status: {subtask['status']}"
    )
    return [TextContent(type="text", text=result_text)]


@server.tool()
async def ak5_get_ticket_context(ticket_id: str) -> list[TextContent]:
    """티켓의 전체 세부사항, 부모 및 자식 서브태스크들의 상태, 최근 코멘트 내역을 압축된 요약 형태로 조회합니다."""
    client = get_client()
    try:
        ticket = await client.get_ticket_context(ticket_id)
    except AK5APIError as exc:
        return _tool_error(exc)

    lines = [
        f"=== Ticket Context: {ticket['ticket_id']} ===",
        f"Title: {ticket['title']}",
        f"Status: {ticket['status'].upper()} | Priority: {ticket['priority']} | Column: {ticket['column_id']}",
        f"Assignee: @{ticket['assigned_to'] or 'Unassigned'} | Created by: @{ticket['created_by']}",
        f"Description:\n{ticket.get('description') or '(No description)'}",
    ]

    subtasks = ticket.get("subtasks", [])
    if subtasks:
        lines.append(f"\nSubtasks ({len(subtasks)} total):")
        for s in subtasks:
            lines.append(
                f"  - [{s['status'].upper()}] {s['ticket_id']}: {s['title']} (@{s.get('assigned_to')})"
            )

    comments = ticket.get("comments", [])
    if comments:
        lines.append(f"\nRecent Comments ({len(comments)} total):")
        for c in comments[-5:]:
            int_flag = " [INTERNAL]" if c.get("is_internal") else ""
            lines.append(f"  - @{c['actor_id']}{int_flag}: {c['content']}")

    attachments = ticket.get("attachments", [])
    if attachments:
        lines.append(f"\nAttachments ({len(attachments)} total):")
        for a in attachments:
            lines.append(
                f"  - [{a.get('attachment_id')}] {a.get('filename')} "
                f"({a.get('file_size')} bytes, uploaded by @{a.get('actor_id')})"
            )

    exec_ctx = ticket.get("execution_context")
    if exec_ctx:
        lines.append(f"\nExecution Context:\n{json.dumps(exec_ctx, indent=2)}")

    return [TextContent(type="text", text="\n".join(lines))]


@server.tool()
async def ak5_update_ticket_status(
    ticket_id: str,
    column_name: str,
    status_note: str,
    execution_context: dict[str, Any] | None = None,
) -> list[TextContent]:
    """[DEPRECATED] Free column moves are not workflow progress (workflow.md §8).

    Same-stage column moves only. Prefer ``ak5_ticket_claim`` / ``submit`` /
    ``verdict`` / ``route`` transition tools for stage changes.
    """
    client = get_client()
    try:
        ticket = await client.update_ticket_status(
            ticket_id=ticket_id,
            column_name=column_name,
            status_note=status_note,
            execution_context=execution_context,
        )
    except AK5APIError as exc:
        return _tool_error(exc)
    except ValueError as exc:
        return _json_content(
            {
                "error": {
                    "code": "PAYLOAD_INVALID",
                    "message": str(exc),
                    "details": {},
                }
            }
        )
    result_text = (
        f"✓ Ticket '{ticket_id}' updated.\n"
        f"  New Column: {ticket['column_id']} | Status: {ticket['status']}\n"
        f"  Note: {status_note}"
    )
    return [TextContent(type="text", text=result_text)]


@server.tool()
async def ak5_report_block(
    ticket_id: str,
    blocking_reason: str,
    required_actor_id: str | None = None,
    kind: BlockedKind = BlockedKind.EXTERNAL,
    claim_token: str | None = None,
) -> list[TextContent]:
    """[DEPRECATED] Prefer ``ak5_ticket_block`` (cli.md ``ticket block``).

    Defaults to ``kind=external`` and posts a mention comment.
    """
    client = get_client()
    try:
        await client.report_block(
            ticket_id=ticket_id,
            blocking_reason=blocking_reason,
            required_actor_id=required_actor_id,
            kind=kind,
            claim_token=claim_token,
        )
    except AK5APIError as exc:
        return _tool_error(exc)
    mention_str = f" @{required_actor_id}" if required_actor_id else ""
    result_text = f"⚠️ Ticket '{ticket_id}' is now BLOCKED.{mention_str}\n  Reason: {blocking_reason}"
    return [TextContent(type="text", text=result_text)]


@server.tool()
async def ak5_search_tickets(
    query: str | None = None,
    board_id: str | None = None,
    is_archived: bool | None = False,
    include_all: bool = False,
    status: str | None = None,
    stage: str | None = None,
    assigned_to: str | None = None,
    labels: str | None = None,
    limit: int = 5,
) -> list[TextContent]:
    """티켓 목록을 다양한 조건(검색어, 아카이브 여부, 상태, 담당자, 라벨 등)으로 필터링/검색합니다.

    과거 완료되어 아카이브된 티켓(is_archived=True)을 검색하여 유사 작업 컨텍스트를 회수하거나,
    현재 진행 중인 활성 티켓(is_archived=False) 또는 전체 티켓(include_all=True)을 선별 조회할 때 사용합니다.
    """
    client = get_client()
    try:
        tickets = await client.search_tickets(
            board_id=board_id,
            query=query,
            is_archived=is_archived,
            include_all=include_all,
            status=status,
            stage=stage,
            assigned_to=assigned_to,
            labels=labels,
            limit=limit,
        )
    except AK5APIError as exc:
        return _tool_error(exc)
    if not tickets:
        return [TextContent(type="text", text="No tickets found matching the specified criteria.")]

    lines = [f"Found {len(tickets)} matching ticket(s):"]
    for t in tickets:
        archived_flag = " [ARCHIVED]" if t.get("is_archived") else ""
        lines.append(
            f"- [{t.get('status', 'open').upper()}]{archived_flag} {t['ticket_id']}: {t['title']} "
            f"(Priority: {t.get('priority')}, Assignee: @{t.get('assigned_to') or 'unassigned'})"
        )
        if t.get("description"):
            short_desc = t["description"][:120].replace("\n", " ")
            if len(t["description"]) > 120:
                short_desc += "..."
            lines.append(f"  Desc: {short_desc}")
    return [TextContent(type="text", text="\n".join(lines))]


@server.tool()
async def ak5_archive_ticket(
    ticket_id: str,
    unarchive: bool = False,
) -> list[TextContent]:
    """티켓을 아카이브(보관) 처리하여 일반 활성 보드 뷰에서 숨기거나, 필요 시 아카이브를 해제(unarchive=True)합니다."""
    client = get_client()
    try:
        await client.archive_ticket(ticket_id=ticket_id, unarchive=unarchive)
    except AK5APIError as exc:
        return _tool_error(exc)
    action_text = "unarchived" if unarchive else "archived"
    return [TextContent(type="text", text=f"✓ Ticket '{ticket_id}' successfully {action_text}.")]
