import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import TextContent

from ak5.mcp.client import AK5Client

server = MCPServer("ak5-orchestrator")
_client = AK5Client()


def get_client() -> AK5Client:
    return _client


@server.tool()
async def ak5_list_boards() -> list[TextContent]:
    """현재 시스템에 등록된 전체 칸반 보드(Board) 목록을 조회합니다.

    각 보드의 ID, 이름, 설명, 생성자 정보를 반환합니다.
    """
    client = get_client()
    boards = await client.list_boards()
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
    agents = await client.list_available_agents(capability=capability, search_query=search_query)

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
    """특정 에이전트에게 하위 티켓(Sub-ticket)을 생성하여 작업을 위임합니다.

    부모 티켓과 종속 관계가 자동으로 맺어지며, 보드 상에 서브태스크로 등록됩니다.
    """
    client = get_client()
    subtask = await client.delegate_subtask(
        parent_ticket_id=parent_ticket_id,
        target_agent_id=target_agent_id,
        title=title,
        description=description,
        priority=priority,
    )
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
    ticket = await client.get_ticket_context(ticket_id)

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
            lines.append(f"  - [{s['status'].upper()}] {s['ticket_id']}: {s['title']} (@{s.get('assigned_to')})")

    comments = ticket.get("comments", [])
    if comments:
        lines.append(f"\nRecent Comments ({len(comments)} total):")
        for c in comments[-5:]:
            int_flag = " [INTERNAL]" if c.get("is_internal") else ""
            lines.append(f"  - @{c['actor_id']}{int_flag}: {c['content']}")

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
    """티켓의 진행 상태를 전이하고, 작업 결과(링크, 로그 등)를 기록합니다."""
    client = get_client()
    ticket = await client.update_ticket_status(
        ticket_id=ticket_id,
        column_name=column_name,
        status_note=status_note,
        execution_context=execution_context,
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
) -> list[TextContent]:
    """외부 의존성 결여나 권한 부족으로 작업 진행이 불가능할 때 티켓을 Blocked 상태로 전환하고 담당자/PM을 멘션합니다."""
    client = get_client()
    await client.report_block(
        ticket_id=ticket_id,
        blocking_reason=blocking_reason,
        required_actor_id=required_actor_id,
    )
    mention_str = f" @{required_actor_id}" if required_actor_id else ""
    result_text = (
        f"⚠️ Ticket '{ticket_id}' is now BLOCKED.{mention_str}\n"
        f"  Reason: {blocking_reason}"
    )
    return [TextContent(type="text", text=result_text)]
