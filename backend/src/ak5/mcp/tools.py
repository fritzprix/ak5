"""MCP tools — agent-only adapters for cli.md §4.3 / agent-safe §4.4 verbs.

MCP is an agent-only surface (cli.md §0): human-only verbs (T1 create, reopen,
reroute, human verdict, needs_human/escalation unblock) are not exposed.

Payload parameters use ``ak5.contracts`` Pydantic models so MCP ``input_schema``
matches the workflow payload JSON schemas. Errors surface as ``ErrorCode``
envelopes (``{"error": {"code", "message", "details"}}``).
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import TextContent

from ak5.contracts.errors import ErrorCode, error_body
from ak5.contracts.payloads import Deliverable, Postmortem, RoutePlan, Verdict
from ak5.contracts.stages import BlockedKind
from ak5.mcp.client import AK5APIError, AK5Client

server = MCPServer("ak5-orchestrator")
_client = AK5Client()

# Process-memory claim token cache (cli.md §4.3 ``--token`` omit semantics;
# unlike CLI, tokens are never persisted to disk).
_claim_tokens: dict[str, str] = {}

# Human-only blocked kinds — MCP must refuse unblock (cli.md §0 / B3).
_HUMAN_UNBLOCK_KINDS: frozenset[str] = frozenset(
    {BlockedKind.NEEDS_HUMAN.value, BlockedKind.ESCALATION.value}
)

# Agent-facing CLI verbs → MCP tool names (cli.md §0 agent surface).
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
    "ticket delegate": "ak5_ticket_delegate",
    "ticket block": "ak5_ticket_block",
    "ticket unblock": "ak5_ticket_unblock",
    "ticket history": "ak5_ticket_history",
}

V2_TOOL_NAMES: frozenset[str] = frozenset(V2_CLI_TOOL_MAP.values())

# Human-only tools that must never appear on the MCP agent surface.
HUMAN_ONLY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "ak5_ticket_create",
        "ak5_ticket_reopen",
        "ak5_ticket_reroute",
    }
)


def get_client() -> AK5Client:
    return _client


def clear_claim_token_cache() -> None:
    """Test helper — reset in-process claim token cache."""
    _claim_tokens.clear()


def _remember_claim(ticket_id: str, claim_token: str) -> None:
    _claim_tokens[ticket_id] = claim_token


def _forget_claim(ticket_id: str) -> None:
    _claim_tokens.pop(ticket_id, None)


def _resolve_claim_token(ticket_id: str, claim_token: str | None) -> str | None:
    if claim_token:
        return claim_token
    return _claim_tokens.get(ticket_id)


def _missing_claim_token_error(ticket_id: str) -> list[TextContent]:
    return _json_content(
        error_body(
            ErrorCode.PAYLOAD_INVALID,
            f"claim_token required for ticket '{ticket_id}' "
            "(omit only after a successful ak5_ticket_claim in this process)",
            details={"ticket_id": ticket_id},
        )
    )


def _json_content(data: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, default=str))]


def _tool_result(data: Any) -> list[TextContent]:
    return _json_content(data)


def _tool_error(exc: AK5APIError) -> list[TextContent]:
    return _json_content(exc.envelope())


def _cache_claim_from_result(result: dict[str, Any]) -> None:
    token = result.get("claim_token")
    ticket = result.get("ticket") if isinstance(result.get("ticket"), dict) else None
    ticket_id = ticket.get("ticket_id") if ticket else result.get("ticket_id")
    if isinstance(token, str) and token and isinstance(ticket_id, str) and ticket_id:
        _remember_claim(ticket_id, token)


# ---------------------------------------------------------------------------
# §4.3 work-loop verbs
# ---------------------------------------------------------------------------


@server.tool()
async def ak5_next(
    board_id: str = "inbox",
    capabilities: list[str] | None = None,
) -> list[TextContent]:
    """Workflow roles: triage, worker, gatekeeper.

    Return the oldest unclaimed unblocked ticket on a board (cli.md §4.3 ``next``).
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
    """Workflow roles: triage, worker, gatekeeper.

    Claim a ticket and receive ``claim_token`` + lease (cli.md ``ticket claim``).
    The token is cached in process memory so later tools may omit ``claim_token``.
    """
    client = get_client()
    try:
        result = await client.claim(ticket_id, capabilities=capabilities)
    except AK5APIError as exc:
        return _tool_error(exc)
    if isinstance(result, dict):
        _cache_claim_from_result(result)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_heartbeat(
    ticket_id: str,
    claim_token: str | None = None,
) -> list[TextContent]:
    """Workflow roles: triage, worker, gatekeeper.

    Extend the claim lease (cli.md ``ticket heartbeat``).
    ``claim_token`` may be omitted when cached from ``ak5_ticket_claim``.
    """
    token = _resolve_claim_token(ticket_id, claim_token)
    if not token:
        return _missing_claim_token_error(ticket_id)
    client = get_client()
    try:
        result = await client.heartbeat(ticket_id, claim_token=token)
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_release(
    ticket_id: str,
    reason: str,
    claim_token: str | None = None,
) -> list[TextContent]:
    """Workflow roles: triage, worker, gatekeeper.

    Release an in-progress claim back to backlog (cli.md ``ticket release``).
    ``claim_token`` may be omitted when cached from ``ak5_ticket_claim``.
    """
    token = _resolve_claim_token(ticket_id, claim_token)
    if not token:
        return _missing_claim_token_error(ticket_id)
    client = get_client()
    try:
        result = await client.release(ticket_id, claim_token=token, reason=reason)
    except AK5APIError as exc:
        return _tool_error(exc)
    _forget_claim(ticket_id)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_submit(
    ticket_id: str,
    deliverable: Deliverable,
    claim_token: str | None = None,
) -> list[TextContent]:
    """Workflow role: worker.

    Submit a worker deliverable → agent_review (cli.md ``ticket submit``).
    ``deliverable`` follows workflow.md §5.2 (contracts ``Deliverable`` schema).
    ``claim_token`` may be omitted when cached from ``ak5_ticket_claim``.
    """
    token = _resolve_claim_token(ticket_id, claim_token)
    if not token:
        return _missing_claim_token_error(ticket_id)
    client = get_client()
    try:
        result = await client.submit(ticket_id, claim_token=token, deliverable=deliverable)
    except AK5APIError as exc:
        return _tool_error(exc)
    _forget_claim(ticket_id)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_bounce(
    ticket_id: str,
    reason: str,
    claim_token: str | None = None,
) -> list[TextContent]:
    """Workflow role: worker.

    Bounce an in-progress ticket back to inbox (cli.md ``ticket bounce``).
    ``claim_token`` may be omitted when cached from ``ak5_ticket_claim``.
    """
    token = _resolve_claim_token(ticket_id, claim_token)
    if not token:
        return _missing_claim_token_error(ticket_id)
    client = get_client()
    try:
        result = await client.bounce(ticket_id, claim_token=token, reason=reason)
    except AK5APIError as exc:
        return _tool_error(exc)
    _forget_claim(ticket_id)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_route(
    ticket_id: str,
    plan: RoutePlan,
    claim_token: str | None = None,
) -> list[TextContent]:
    """Workflow role: triage.

    Route an unclassified inbox ticket to a board (cli.md ``ticket route``).
    ``plan`` follows workflow.md §5.1 (contracts ``RoutePlan`` schema).
    ``claim_token`` may be omitted when cached from ``ak5_ticket_claim``.
    """
    token = _resolve_claim_token(ticket_id, claim_token)
    if not token:
        return _missing_claim_token_error(ticket_id)
    client = get_client()
    try:
        result = await client.route(ticket_id, claim_token=token, plan=plan)
    except AK5APIError as exc:
        return _tool_error(exc)
    _forget_claim(ticket_id)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_verdict(
    ticket_id: str,
    verdict: Verdict,
    claim_token: str | None = None,
) -> list[TextContent]:
    """Workflow role: gatekeeper.

    Approve or reject at agent_review only (cli.md ``ticket verdict`` / T12–T14).
    Human verdict (T15/T16 at human_review) is not available via MCP.
    ``verdict`` uses contracts ``Verdict`` schema (workflow.md §5.3).
    ``claim_token`` is required (or cached from ``ak5_ticket_claim``).
    """
    token = _resolve_claim_token(ticket_id, claim_token)
    if not token:
        return _missing_claim_token_error(ticket_id)
    client = get_client()
    try:
        result = await client.verdict(ticket_id, verdict=verdict, claim_token=token)
    except AK5APIError as exc:
        return _tool_error(exc)
    _forget_claim(ticket_id)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_postmortem(
    ticket_id: str,
    postmortem: Postmortem,
    claim_token: str | None = None,
) -> list[TextContent]:
    """Workflow role: gatekeeper.

    Submit a postmortem after human reject / reopen (cli.md ``ticket postmortem``).
    ``postmortem`` follows workflow.md §5.4 (contracts ``Postmortem`` schema).
    ``claim_token`` may be omitted when cached from ``ak5_ticket_claim``.
    """
    token = _resolve_claim_token(ticket_id, claim_token)
    if not token:
        return _missing_claim_token_error(ticket_id)
    client = get_client()
    try:
        result = await client.postmortem(
            ticket_id, claim_token=token, postmortem=postmortem
        )
    except AK5APIError as exc:
        return _tool_error(exc)
    _forget_claim(ticket_id)
    return _tool_result(result)


# ---------------------------------------------------------------------------
# Agent-safe §4.4 verbs (no human-only create / reopen / reroute)
# ---------------------------------------------------------------------------


@server.tool()
async def ak5_ticket_delegate(
    parent_ticket_id: str,
    title: str,
    description: str | None = None,
    acceptance_criteria: list[str] | None = None,
    priority: str = "medium",
    labels: list[str] | None = None,
) -> list[TextContent]:
    """Workflow roles: triage, worker.

    Create a child ticket via parented create_direct
    (``POST /tickets/{id}/delegate`` — cli.md pull-model delegate).
    Replaces inbox T1 ``ticket create`` on the MCP surface.
    """
    client = get_client()
    try:
        result = await client.delegate(
            parent_ticket_id,
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            priority=priority,
            labels=labels,
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
    """Workflow roles: triage, worker, gatekeeper.

    Block a ticket (B1) — cli.md ``ticket block``.
    ``claim_token`` may be omitted when cached from ``ak5_ticket_claim``.
    """
    token = _resolve_claim_token(ticket_id, claim_token)
    client = get_client()
    try:
        result = await client.block(
            ticket_id, kind=kind, reason=reason, claim_token=token
        )
    except AK5APIError as exc:
        return _tool_error(exc)
    _forget_claim(ticket_id)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_unblock(
    ticket_id: str,
    resolution: str | None = None,
) -> list[TextContent]:
    """Workflow roles: triage, worker, gatekeeper.

    Unblock a ticket for ``dependency`` / ``external`` only (B2).
    ``needs_human`` / ``escalation`` (B3) are human-only and rejected here.
    """
    client = get_client()
    try:
        ticket = await client.get_ticket_context(ticket_id)
    except AK5APIError as exc:
        return _tool_error(exc)

    kind = ticket.get("blocked_kind") if isinstance(ticket, dict) else None
    if kind in _HUMAN_UNBLOCK_KINDS:
        return _json_content(
            error_body(
                ErrorCode.ROLE_FORBIDDEN,
                f"MCP cannot unblock kind={kind!r}; needs_human/escalation "
                "require a human principal (cli.md §0 / workflow.md B3)",
                details={"ticket_id": ticket_id, "blocked_kind": kind},
            )
        )

    try:
        result = await client.unblock(ticket_id, resolution=resolution)
    except AK5APIError as exc:
        return _tool_error(exc)
    return _tool_result(result)


@server.tool()
async def ak5_ticket_history(ticket_id: str) -> list[TextContent]:
    """Workflow roles: triage, worker, gatekeeper.

    Revision / review / postmortem / audit history (cli.md ``ticket history``).
    """
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
    """Workflow roles: triage, worker, gatekeeper.

    현재 시스템에 등록된 전체 칸반 보드(Board) 목록을 조회합니다.

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
    """Workflow roles: triage, worker, gatekeeper.

    현재 시스템에 등록된 에이전트 목록을 조회하여 적절한 작업 위임 대상을 탐색합니다.

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
    title: str,
    description: str = "",
    target_agent_id: str | None = None,
    priority: str = "medium",
) -> list[TextContent]:
    """[DEPRECATED] Alias of ``ak5_ticket_delegate`` (parented create_direct).

    Workflow roles: triage, worker.
    ``target_agent_id`` is ignored (pull model).
    """
    _ = target_agent_id
    return await ak5_ticket_delegate(
        parent_ticket_id=parent_ticket_id,
        title=title,
        description=description or None,
        priority=priority,
    )


@server.tool()
async def ak5_get_ticket_context(ticket_id: str) -> list[TextContent]:
    """Workflow roles: triage, worker, gatekeeper.

    티켓의 전체 세부사항, 부모 및 자식 서브태스크들의 상태, 최근 코멘트 내역을 압축된 요약 형태로 조회합니다.
    """
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

    Workflow roles: triage, worker, gatekeeper.
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

    Workflow roles: triage, worker, gatekeeper.
    Defaults to ``kind=external`` and posts a mention comment.
    """
    token = _resolve_claim_token(ticket_id, claim_token)
    client = get_client()
    try:
        await client.report_block(
            ticket_id=ticket_id,
            blocking_reason=blocking_reason,
            required_actor_id=required_actor_id,
            kind=kind,
            claim_token=token,
        )
    except AK5APIError as exc:
        return _tool_error(exc)
    _forget_claim(ticket_id)
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
    """Workflow roles: triage, worker, gatekeeper.

    티켓 목록을 다양한 조건(검색어, 아카이브 여부, 상태, 담당자, 라벨 등)으로 필터링/검색합니다.

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
    """Workflow roles: triage, worker, gatekeeper.

    티켓을 아카이브(보관) 처리하여 일반 활성 보드 뷰에서 숨기거나, 필요 시 아카이브를 해제(unarchive=True)합니다.
    """
    client = get_client()
    try:
        await client.archive_ticket(ticket_id=ticket_id, unarchive=unarchive)
    except AK5APIError as exc:
        return _tool_error(exc)
    action_text = "unarchived" if unarchive else "archived"
    return [TextContent(type="text", text=f"✓ Ticket '{ticket_id}' successfully {action_text}.")]
