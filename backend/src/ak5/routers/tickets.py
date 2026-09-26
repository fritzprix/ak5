from datetime import datetime, timezone
import json
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ak5.config import settings
from ak5.database import get_db
from ak5.models.actor import Actor
from ak5.models.audit import AuditLog
from ak5.models.board import Board
from ak5.models.column import Column
from ak5.models.ticket import Ticket, TicketAttachment, TicketComment
from ak5.routers.auth import get_current_actor
from ak5.schemas.ticket import (
    TicketAttachmentOut,
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

    stmt = select(func.count()).select_from(Ticket).where(
        Ticket.column_id == column.column_id,
        Ticket.is_archived.is_(False),
    )
    count = (await db.execute(stmt)).scalar_one() or 0
    if count >= column.wip_limit:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"WIP limit ({column.wip_limit}) exceeded for column '{column.name}' ({column.column_id})"),
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
        is_archived=t.is_archived,
        archived_at=t.archived_at,
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Column '{req.column_id}' does not belong to board"
        )

    if req.assigned_to:
        assignee = await db.get(Actor, req.assigned_to)
        if not assignee:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Assignee '{req.assigned_to}' not found")

    await _assert_wip_allows(db, column)

    ticket_id = req.ticket_id or await _generate_ticket_id(db)

    # Find the last ticket in this column to place new ticket at the bottom
    stmt_last = select(Ticket).where(Ticket.column_id == req.column_id).order_by(Ticket.rank.desc()).limit(1)
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


@router.get("", response_model=list[TicketOut])
async def list_tickets(
    db: Annotated[AsyncSession, Depends(get_db)],
    board_id: str | None = None,
    is_archived: bool = False,
    include_all: bool = False,
    stage: str | None = None,
    status: str | None = None,
    assigned_to: str | None = None,
    created_by: str | None = None,
    priority: str | None = None,
    labels: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[TicketOut]:
    """List and filter tickets with rich selection parameters.

    - is_archived: False by default (only active). Set True for archived only. Ignored if include_all is True.
    - include_all: If True, returns both active and archived tickets without filtering by archive status.
    - q: Text search across ticket title and description.
    - stage: Stage name (open, in_progress, review, done).
    - labels: Comma-separated list of labels to filter by.
    """
    safe_limit = min(max(1, limit), 200)
    stmt = select(Ticket).options(selectinload(Ticket.subtasks))
    if board_id:
        stmt = stmt.where(Ticket.board_id == board_id)
    if not include_all:
        stmt = stmt.where(Ticket.is_archived == is_archived)
    if status:
        stmt = stmt.where(Ticket.status == status)
    if stage:
        stmt = stmt.join(Column, Ticket.column_id == Column.column_id).where(Column.stage == stage)
    if assigned_to:
        stmt = stmt.where(Ticket.assigned_to == assigned_to)
    if created_by:
        stmt = stmt.where(Ticket.created_by == created_by)
    if priority:
        stmt = stmt.where(Ticket.priority == priority)
    if q:
        escaped_q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        search_pat = f"%{escaped_q}%"
        stmt = stmt.where(
            (Ticket.title.ilike(search_pat, escape="\\")) | (Ticket.description.ilike(search_pat, escape="\\"))
        )
    if labels:
        target_labels = [lbl.strip() for lbl in labels.split(",") if lbl.strip()]
        for lbl in target_labels:
            stmt = stmt.where(Ticket.labels.contains(f'"{lbl}"'))

    stmt = stmt.order_by(Ticket.updated_at.desc(), Ticket.created_at.desc()).limit(safe_limit).offset(max(0, offset))
    result = await db.execute(stmt)
    tickets = result.scalars().all()

    outs = []
    for t in tickets:
        subtasks = t.subtasks or []
        subtask_done = sum(1 for s in subtasks if s.status == "done")
        outs.append(_format_ticket_out(t, (len(subtasks), subtask_done)))
    return outs


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
            selectinload(Ticket.attachments),
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
    attachments_out = [TicketAttachmentOut.model_validate(a) for a in ticket.attachments]

    return TicketDetailOut(
        **base_out.model_dump(),
        comments=comments_out,
        subtasks=subtasks_out,
        attachments=attachments_out,
    )


@router.patch("/{ticket_id}", response_model=TicketOut)
async def update_ticket(
    ticket_id: str,
    req: TicketUpdate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketOut:
    """Update ticket fields, status, or assignee.

    Uses exclude_unset so explicit JSON null can clear nullable fields
    (e.g. assigned_to, blocked_by) without requiring a sentinel value.
    """
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket '{ticket_id}' not found")

    data = req.model_dump(exclude_unset=True)
    if not data:
        return _format_ticket_out(ticket)

    changes: dict[str, Any] = {}

    if "title" in data:
        title = data["title"]
        if title is None or not str(title).strip():
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Title cannot be empty")
        changes["title"] = (ticket.title, title)
        ticket.title = title
    if "description" in data:
        changes["description"] = (ticket.description, data["description"])
        ticket.description = data["description"]
    if "priority" in data and data["priority"] is not None:
        changes["priority"] = (ticket.priority, data["priority"])
        ticket.priority = data["priority"]
    if "labels" in data and data["labels"] is not None:
        changes["labels"] = (ticket.label_list, data["labels"])
        ticket.label_list = data["labels"]
    if "assigned_to" in data:
        # Normalize "" → None so actor FK is never set to an empty string.
        new_assignee = data["assigned_to"] or None
        if new_assignee:
            assignee = await db.get(Actor, new_assignee)
            if not assignee:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Assignee '{new_assignee}' not found",
                )
        changes["assigned_to"] = (ticket.assigned_to, new_assignee)
        ticket.assigned_to = new_assignee
    if "status" in data and data["status"] is not None:
        changes["status"] = (ticket.status, data["status"])
        ticket.status = data["status"]
    if "blocked_by" in data:
        raw_blocked = data["blocked_by"]
        new_blocked_by = raw_blocked.strip() if isinstance(raw_blocked, str) else raw_blocked
        if new_blocked_by == "":
            new_blocked_by = None
        changes["blocked_by"] = (ticket.blocked_by, new_blocked_by)
        ticket.blocked_by = new_blocked_by
    if "execution_context" in data:
        changes["execution_context"] = True
        ticket.execution_context_dict = data["execution_context"]
    if "due_date" in data:
        changes["due_date"] = (ticket.due_date, data["due_date"])
        ticket.due_date = data["due_date"]
    if "is_archived" in data and data["is_archived"] is not None:
        new_archived = bool(data["is_archived"])
        if ticket.is_archived != new_archived:
            changes["is_archived"] = (ticket.is_archived, new_archived)
            ticket.is_archived = new_archived
            ticket.archived_at = datetime.now(timezone.utc) if new_archived else None

    action = (
        "ARCHIVED"
        if ("is_archived" in changes and ticket.is_archived)
        else "UNARCHIVED"
        if ("is_archived" in changes and not ticket.is_archived)
        else ("STATUS_CHANGE" if "status" in changes else "UPDATED")
    )
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


@router.post("/{ticket_id}/archive", response_model=TicketOut)
async def archive_ticket(
    ticket_id: str,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketOut:
    """Archive a ticket and record audit log."""
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket '{ticket_id}' not found")

    changes = {"is_archived": (ticket.is_archived, True)}
    ticket.is_archived = True
    ticket.archived_at = datetime.now(timezone.utc)

    audit = AuditLog(
        actor_id=current_actor.actor_id,
        action="ARCHIVED",
        target_type="ticket",
        target_id=ticket_id,
        payload=json.dumps(changes, default=str),
    )
    db.add(audit)
    await db.commit()
    await db.refresh(ticket)

    ticket_out = _format_ticket_out(ticket)
    await event_bus.publish(
        event_type="TICKET_ARCHIVED",
        data={"ticket": ticket_out.model_dump(mode="json"), "actor_id": current_actor.actor_id},
    )
    return ticket_out


@router.post("/{ticket_id}/unarchive", response_model=TicketOut)
async def unarchive_ticket(
    ticket_id: str,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketOut:
    """Unarchive an archived ticket back to active view."""
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket '{ticket_id}' not found")

    changes = {"is_archived": (ticket.is_archived, False)}
    ticket.is_archived = False
    ticket.archived_at = None

    audit = AuditLog(
        actor_id=current_actor.actor_id,
        action="UNARCHIVED",
        target_type="ticket",
        target_id=ticket_id,
        payload=json.dumps(changes, default=str),
    )
    db.add(audit)
    await db.commit()
    await db.refresh(ticket)

    ticket_out = _format_ticket_out(ticket)
    await event_bus.publish(
        event_type="TICKET_UNARCHIVED",
        data={"ticket": ticket_out.model_dump(mode="json"), "actor_id": current_actor.actor_id},
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Target column '{req.target_column_id}' not found"
        )

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
        payload=json.dumps(
            {
                "from_column": prev_col_id,
                "to_column": req.target_column_id,
                "new_rank": new_rank,
                "status": ticket.status,
            }
        ),
    )
    db.add(audit)

    await db.commit()
    await db.refresh(ticket)

    ticket_out = _format_ticket_out(ticket)

    await event_bus.publish(
        event_type="TICKET_MOVED",
        data={
            "ticket": ticket_out.model_dump(mode="json"),
            "from_column": prev_col_id,
            "to_column": req.target_column_id,
            "actor_id": current_actor.actor_id,
        },
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Target actor '{req.target_actor_id}' not found"
        )

    subtask_id = await _generate_ticket_id(db)

    # Place in board's open column
    stmt_col = select(Column).where(Column.board_id == parent.board_id, Column.stage == "open").limit(1)
    res_col = await db.execute(stmt_col)
    target_col = res_col.scalar_one_or_none() or await db.get(Column, parent.column_id)
    if not target_col:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No target column available for subtask")

    await _assert_wip_allows(db, target_col)

    # Determine rank in target column
    stmt_last = select(Ticket).where(Ticket.column_id == target_col.column_id).order_by(Ticket.rank.desc()).limit(1)
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
        payload=json.dumps(
            {
                "parent_ticket_id": parent.ticket_id,
                "target_actor_id": target_actor.actor_id,
                "title": req.subtask_title,
            }
        ),
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
        data={
            "board_id": ticket.board_id,
            "ticket_id": ticket_id,
            "comment": comment_out.model_dump(mode="json"),
        },
    )

    return comment_out


@router.post(
    "/{ticket_id}/attachments",
    response_model=TicketAttachmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    ticket_id: str,
    file: Annotated[UploadFile, File(...)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketAttachmentOut:
    """Upload an attachment file to a ticket."""
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket '{ticket_id}' not found",
        )

    raw_filename = file.filename or "attachment.bin"
    # Extract only the base name to prevent directory traversal
    clean_filename = Path(raw_filename).name or "attachment.bin"
    attachment_id = f"att_{uuid.uuid4().hex[:12]}"
    safe_disk_filename = f"{attachment_id}_{clean_filename}"

    storage_dir = Path(settings.ATTACHMENTS_DIR).resolve()
    storage_dir.mkdir(parents=True, exist_ok=True)
    dest_path = storage_dir / safe_disk_filename

    total_size = 0
    try:
        with open(dest_path, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):  # 1MB buffer
                total_size += len(chunk)
                if total_size > settings.MAX_ATTACHMENT_SIZE_BYTES:
                    buffer.close()
                    dest_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"File exceeds maximum allowed size ({settings.MAX_ATTACHMENT_SIZE_BYTES} bytes)",
                    )
                buffer.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save uploaded file: {exc}",
        ) from exc

    attachment = TicketAttachment(
        attachment_id=attachment_id,
        ticket_id=ticket_id,
        actor_id=current_actor.actor_id,
        filename=clean_filename,
        file_size=total_size,
        content_type=file.content_type or "application/octet-stream",
        storage_path=str(dest_path),
    )
    db.add(attachment)

    audit = AuditLog(
        actor_id=current_actor.actor_id,
        action="ATTACHMENT_UPLOADED",
        target_type="ticket",
        target_id=ticket_id,
        payload=json.dumps({"attachment_id": attachment_id, "filename": clean_filename, "size": total_size}),
    )
    db.add(audit)
    await db.commit()
    await db.refresh(attachment)

    attachment_out = TicketAttachmentOut.model_validate(attachment)
    await event_bus.publish(
        event_type="ATTACHMENT_ADDED",
        data={
            "board_id": ticket.board_id,
            "ticket_id": ticket_id,
            "attachment": attachment_out.model_dump(mode="json"),
        },
    )
    return attachment_out


@router.get("/{ticket_id}/attachments", response_model=list[TicketAttachmentOut])
async def list_attachments(
    ticket_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[TicketAttachmentOut]:
    """List all attachments for a ticket."""
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket '{ticket_id}' not found",
        )

    stmt = (
        select(TicketAttachment)
        .where(TicketAttachment.ticket_id == ticket_id)
        .order_by(TicketAttachment.created_at.asc())
    )
    result = await db.execute(stmt)
    attachments = result.scalars().all()
    return [TicketAttachmentOut.model_validate(a) for a in attachments]


@router.get("/{ticket_id}/attachments/{attachment_id}")
async def download_attachment(
    ticket_id: str,
    attachment_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FileResponse:
    """Download an attachment file from a ticket."""
    attachment = await db.get(TicketAttachment, attachment_id)
    if not attachment or attachment.ticket_id != ticket_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attachment '{attachment_id}' not found on ticket '{ticket_id}'",
        )

    storage_dir = Path(settings.ATTACHMENTS_DIR).resolve()
    file_path = Path(attachment.storage_path).resolve()
    if not file_path.is_relative_to(storage_dir) or not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment file not found on storage",
        )

    return FileResponse(
        path=file_path,
        filename=attachment.filename,
        media_type=attachment.content_type,
    )


@router.delete("/{ticket_id}/attachments/{attachment_id}")
async def delete_attachment(
    ticket_id: str,
    attachment_id: str,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Delete an attachment file from a ticket."""
    attachment = await db.get(TicketAttachment, attachment_id)
    if not attachment or attachment.ticket_id != ticket_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attachment '{attachment_id}' not found on ticket '{ticket_id}'",
        )

    storage_dir = Path(settings.ATTACHMENTS_DIR).resolve()
    file_path = Path(attachment.storage_path).resolve()
    if file_path.is_relative_to(storage_dir) and file_path.is_file():
        file_path.unlink(missing_ok=True)

    ticket = await db.get(Ticket, ticket_id)
    board_id = ticket.board_id if ticket else None

    await db.delete(attachment)
    audit = AuditLog(
        actor_id=current_actor.actor_id,
        action="ATTACHMENT_DELETED",
        target_type="ticket",
        target_id=ticket_id,
        payload=json.dumps({"attachment_id": attachment_id, "filename": attachment.filename}),
    )
    db.add(audit)
    await db.commit()

    await event_bus.publish(
        event_type="ATTACHMENT_DELETED",
        data={"board_id": board_id, "ticket_id": ticket_id, "attachment_id": attachment_id},
    )

    return {"deleted": True, "attachment_id": attachment_id}
