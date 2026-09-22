import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ak5.database import get_db
from ak5.models.actor import Actor
from ak5.models.audit import AuditLog
from ak5.models.board import Board
from ak5.models.column import Column
from ak5.models.ticket import Ticket, TicketComment
from ak5.routers.auth import get_current_actor
from ak5.schemas.ticket import (
    TicketCommentCreate,
    TicketCommentOut,
    TicketCreate,
    TicketDelegateRequest,
    TicketDetailOut,
    TicketMoveRequest,
    TicketOut,
    TicketUpdate,
)
from ak5.services.event_bus import event_bus
from ak5.services.lexorank import rank_between

router = APIRouter(prefix="/tickets", tags=["tickets"])


async def _assert_wip_allows(
    db: AsyncSession,
    column: Column,
) -> None:
    """Reject when adding a ticket would exceed the column WIP limit (0 = unlimited)."""
    if column.wip_limit <= 0:
        return

    stmt = select(func.count()).select_from(Ticket).where(Ticket.column_id == column.column_id)
    count = (await db.execute(stmt)).scalar_one() or 0
    if count >= column.wip_limit:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"WIP limit ({column.wip_limit}) exceeded for column "
                f"'{column.name}' ({column.column_id})"
            ),
        )


async def _generate_ticket_id(db: AsyncSession) -> str:
    """Generate safe sequential ticket identifier e.g. TK-001."""
    stmt = select(func.count(Ticket.ticket_id))
    result = await db.execute(stmt)
    count = result.scalar_one() or 0
    # Try next numbers until unique to avoid collisions
    for offset in range(1, 50):
        candidate = f"TK-{(count + offset):03d}"
        exists = await db.get(Ticket, candidate)
        if not exists:
            return candidate
    return f"TK-{uuid.uuid4().hex[:6].upper()}"


def _format_ticket_out(t: Ticket, subtask_stats: tuple[int, int] = (0, 0)) -> TicketOut:
    count, done_count = subtask_stats
    return TicketOut(
        ticket_id=t.ticket_id,
        board_id=t.board_id,
        column_id=t.column_id,
        parent_ticket_id=t.parent_ticket_id,
        title=t.title,
        description=t.description,
        priority=t.priority,
        rank=t.rank,
        labels=t.label_list,
        assigned_to=t.assigned_to,
        created_by=t.created_by,
        status=t.status,
        blocked_by=t.blocked_by,
        execution_context=t.execution_context_dict,
        due_date=t.due_date,
        created_at=t.created_at,
        updated_at=t.updated_at,
        subtask_count=count,
        subtask_done_count=done_count,
    )


@router.post("", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    req: TicketCreate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketOut:
    """Create a new ticket."""
    board = await db.get(Board, req.board_id)
    if not board:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Board '{req.board_id}' not found")

    column = await db.get(Column, req.column_id)
    if not column or column.board_id != req.board_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Column '{req.column_id}' does not belong to board")

    if req.assigned_to:
        assignee = await db.get(Actor, req.assigned_to)
        if not assignee:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Assignee '{req.assigned_to}' not found")

    await _assert_wip_allows(db, column)

    ticket_id = req.ticket_id or await _generate_ticket_id(db)

    # Find the last ticket in this column to place new ticket at the bottom
    stmt_last = (
        select(Ticket)
        .where(Ticket.column_id == req.column_id)
        .order_by(Ticket.rank.desc())
        .limit(1)
    )
    res_last = await db.execute(stmt_last)
    last_ticket = res_last.scalar_one_or_none()
    new_rank = rank_between(last_ticket.rank if last_ticket else None, None)

    ticket = Ticket(
        ticket_id=ticket_id,
        board_id=req.board_id,
        column_id=req.column_id,
        parent_ticket_id=req.parent_ticket_id,
        title=req.title,
        description=req.description,
        priority=req.priority,
        rank=new_rank,
        labels=json.dumps(req.labels),
        assigned_to=req.assigned_to,
        created_by=current_actor.actor_id,
        status="open",
        execution_context=json.dumps(req.execution_context) if req.execution_context else None,
        due_date=req.due_date,
    )
    db.add(ticket)

    # Audit log
    audit = AuditLog(
        actor_id=current_actor.actor_id,
        action="CREATED",
        target_type="ticket",
        target_id=ticket_id,
        payload=json.dumps({"title": req.title, "column_id": req.column_id, "priority": req.priority}),
    )
    db.add(audit)

    await db.commit()
    await db.refresh(ticket)

    ticket_out = _format_ticket_out(ticket)

    # Publish SSE event
    await event_bus.publish(
        event_type="TICKET_CREATED",
        data={"ticket": ticket_out.model_dump(mode="json"), "actor_id": current_actor.actor_id},
    )

    return ticket_out


@router.get("/{ticket_id}", response_model=TicketDetailOut)
async def get_ticket_detail(
    ticket_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketDetailOut:
    """Get ticket detail, subtasks, and comments."""
    stmt = (
        select(Ticket)
        .where(Ticket.ticket_id == ticket_id)
        .options(
            selectinload(Ticket.comments),
            selectinload(Ticket.subtasks),
        )
    )
    result = await db.execute(stmt)
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket '{ticket_id}' not found")

    subtask_done = sum(1 for s in ticket.subtasks if s.status == "done")
    subtask_stats = (len(ticket.subtasks), subtask_done)

    base_out = _format_ticket_out(ticket, subtask_stats)
    comments_out = [
        TicketCommentOut(
            comment_id=c.comment_id,
            ticket_id=c.ticket_id,
            actor_id=c.actor_id,
            content=c.content,
            is_internal=c.is_internal,
            metadata=json.loads(c.metadata_json or "{}"),
            created_at=c.created_at,
        )
        for c in ticket.comments
    ]
    subtasks_out = [_format_ticket_out(s) for s in ticket.subtasks]

    return TicketDetailOut(
        **base_out.model_dump(),
        comments=comments_out,
        subtasks=subtasks_out,
    )


@router.patch("/{ticket_id}", response_model=TicketOut)
async def update_ticket(
    ticket_id: str,
    req: TicketUpdate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketOut:
    """Update ticket fields, status, or assignee."""
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket '{ticket_id}' not found")

    changes: dict[str, Any] = {}

    if req.title is not None:
        changes["title"] = (ticket.title, req.title)
        ticket.title = req.title
    if req.description is not None:
        ticket.description = req.description
    if req.priority is not None:
        changes["priority"] = (ticket.priority, req.priority)
        ticket.priority = req.priority
    if req.labels is not None:
        ticket.label_list = req.labels
    if req.assigned_to is not None:
        changes["assigned_to"] = (ticket.assigned_to, req.assigned_to)
        ticket.assigned_to = req.assigned_to
    if req.status is not None:
        changes["status"] = (ticket.status, req.status)
        ticket.status = req.status
    if req.blocked_by is not None:
        ticket.blocked_by = req.blocked_by
    if req.execution_context is not None:
        ticket.execution_context_dict = req.execution_context
    if req.due_date is not None:
        ticket.due_date = req.due_date

    action = "STATUS_CHANGE" if "status" in changes else "UPDATED"
    audit = AuditLog(
        actor_id=current_actor.actor_id,
        action=action,
        target_type="ticket",
        target_id=ticket_id,
        payload=json.dumps(changes, default=str),
    )
    db.add(audit)

    await db.commit()
    await db.refresh(ticket)

    ticket_out = _format_ticket_out(ticket)

    await event_bus.publish(
        event_type="TICKET_UPDATED",
        data={"ticket": ticket_out.model_dump(mode="json"), "actor_id": current_actor.actor_id, "changes": changes},
    )

    return ticket_out


@router.patch("/{ticket_id}/move", response_model=TicketOut)
async def move_ticket(
    ticket_id: str,
    req: TicketMoveRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketOut:
    """Move ticket to a target column and calculate new Lexorank between neighbors."""
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket '{ticket_id}' not found")

    target_column = await db.get(Column, req.target_column_id)
    if not target_column:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Target column '{req.target_column_id}' not found")

    # WIP applies only when entering a different column (reorders within a column are allowed)
    if ticket.column_id != req.target_column_id:
        await _assert_wip_allows(db, target_column)

    prev_rank: str | None = None
    next_rank: str | None = None

    if req.previous_ticket_id:
        prev_ticket = await db.get(Ticket, req.previous_ticket_id)
        if prev_ticket:
            prev_rank = prev_ticket.rank

    if req.next_ticket_id:
        next_ticket = await db.get(Ticket, req.next_ticket_id)
        if next_ticket:
            next_rank = next_ticket.rank

    # Calculate new Lexorank
    new_rank = rank_between(prev_rank, next_rank)

    prev_col_id = ticket.column_id
    ticket.column_id = req.target_column_id
    ticket.rank = new_rank

    # Synchronize ticket status with target column stage
    stage_to_status = {
        "open": "open",
        "in_progress": "in_progress",
        "review": "in_progress",
        "done": "done",
    }
    if target_column.stage in stage_to_status:
        ticket.status = stage_to_status[target_column.stage]

    audit = AuditLog(
        actor_id=current_actor.actor_id,
        action="MOVED",
        target_type="ticket",
        target_id=ticket_id,
        payload=json.dumps({
            "from_column": prev_col_id,
            "to_column": req.target_column_id,
            "new_rank": new_rank,
            "status": ticket.status,
        }),
    )
    db.add(audit)

    await db.commit()
    await db.refresh(ticket)

    ticket_out = _format_ticket_out(ticket)

    await event_bus.publish(
        event_type="TICKET_MOVED",
        data={"ticket": ticket_out.model_dump(mode="json"), "from_column": prev_col_id, "to_column": req.target_column_id, "actor_id": current_actor.actor_id},
    )

    return ticket_out


@router.post("/{ticket_id}/delegate", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
async def delegate_subtask(
    ticket_id: str,
    req: TicketDelegateRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketOut:
    """Delegate a subtask to an agent, creating a linked sub-ticket."""
    parent = await db.get(Ticket, ticket_id)
    if not parent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Parent ticket '{ticket_id}' not found")

    target_actor = await db.get(Actor, req.target_actor_id)
    if not target_actor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Target actor '{req.target_actor_id}' not found")

    subtask_id = await _generate_ticket_id(db)

    # Place in board's open column
    stmt_col = select(Column).where(Column.board_id == parent.board_id, Column.stage == "open").limit(1)
    res_col = await db.execute(stmt_col)
    target_col = res_col.scalar_one_or_none() or await db.get(Column, parent.column_id)
    if not target_col:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No target column available for subtask")

    await _assert_wip_allows(db, target_col)

    # Determine rank in target column
    stmt_last = (
        select(Ticket)
        .where(Ticket.column_id == target_col.column_id)
        .order_by(Ticket.rank.desc())
        .limit(1)
    )
    res_last = await db.execute(stmt_last)
    last_ticket = res_last.scalar_one_or_none()
    new_rank = rank_between(last_ticket.rank if last_ticket else None, None)

    subtask = Ticket(
        ticket_id=subtask_id,
        board_id=parent.board_id,
        column_id=target_col.column_id,
        parent_ticket_id=parent.ticket_id,
        title=req.subtask_title,
        description=req.subtask_description,
        priority=req.priority,
        rank=new_rank,
        labels=json.dumps(req.labels),
        assigned_to=target_actor.actor_id,
        created_by=current_actor.actor_id,
        status="open",
        execution_context=json.dumps(req.execution_context) if req.execution_context else None,
    )
    db.add(subtask)

    # Add a delegation note comment on the parent ticket
    comment = TicketComment(
        comment_id=f"cmt_{uuid.uuid4().hex[:8]}",
        ticket_id=parent.ticket_id,
        actor_id=current_actor.actor_id,
        content=f"Delegated subtask {subtask_id} ('{req.subtask_title}') to @{target_actor.actor_id} ({target_actor.name})",
        is_internal=True,
        metadata_json=json.dumps({"subtask_id": subtask_id, "target_actor_id": target_actor.actor_id}),
    )
    db.add(comment)

    # Audit log
    audit = AuditLog(
        actor_id=current_actor.actor_id,
        action="DELEGATED",
        target_type="ticket",
        target_id=subtask_id,
        payload=json.dumps({
            "parent_ticket_id": parent.ticket_id,
            "target_actor_id": target_actor.actor_id,
            "title": req.subtask_title,
        }),
    )
    db.add(audit)

    await db.commit()
    await db.refresh(subtask)

    subtask_out = _format_ticket_out(subtask)

    await event_bus.publish(
        event_type="TICKET_DELEGATED",
        data={
            "parent_ticket_id": parent.ticket_id,
            "subtask": subtask_out.model_dump(mode="json"),
            "actor_id": current_actor.actor_id,
            "target_actor_id": target_actor.actor_id,
        },
    )

    return subtask_out


@router.post("/{ticket_id}/comments", response_model=TicketCommentOut, status_code=status.HTTP_201_CREATED)
async def add_comment(
    ticket_id: str,
    req: TicketCommentCreate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketCommentOut:
    """Add a discussion or reasoning comment to a ticket."""
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket '{ticket_id}' not found")

    comment_id = f"cmt_{uuid.uuid4().hex[:8]}"
    comment = TicketComment(
        comment_id=comment_id,
        ticket_id=ticket_id,
        actor_id=current_actor.actor_id,
        content=req.content,
        is_internal=req.is_internal,
        metadata_json=json.dumps(req.metadata),
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    comment_out = TicketCommentOut(
        comment_id=comment.comment_id,
        ticket_id=comment.ticket_id,
        actor_id=comment.actor_id,
        content=comment.content,
        is_internal=comment.is_internal,
        metadata=req.metadata,
        created_at=comment.created_at,
    )

    await event_bus.publish(
        event_type="COMMENT_ADDED",
        data={"ticket_id": ticket_id, "comment": comment_out.model_dump(mode="json")},
    )

    return comment_out
