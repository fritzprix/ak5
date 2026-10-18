import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.contracts.payloads import Finding, Verdict
from ak5.contracts.stages import BlockedKind
from ak5.server.config import settings
from ak5.server.database import get_db
from ak5.server.models.actor import Actor
from ak5.server.models.audit import AuditLog
from ak5.server.models.column import Column
from ak5.server.models.ticket import Ticket, TicketAttachment, TicketComment
from ak5.server.routers.auth import (
    get_auth_workspace_id,
    get_current_actor,
    get_current_actor_optional,
)
from ak5.server.schemas.ticket import (
    AuditLogOut,
    BlockRequest,
    BounceRequest,
    ClaimRequest,
    HeartbeatRequest,
    PostmortemRequest,
    ReleaseRequest,
    ReopenRequest,
    RerouteRequest,
    RouteRequest,
    SubmitRequest,
    TicketAttachmentOut,
    TicketClaimOut,
    TicketCommentCreate,
    TicketCommentOut,
    TicketCreate,
    TicketDelegateRequest,
    TicketDetailOut,
    TicketHistoryOut,
    TicketMoveRequest,
    TicketOut,
    TicketPostmortemOut,
    TicketReviewOut,
    TicketRevisionOut,
    TicketUpdate,
    UnblockRequest,
    VerdictRequest,
)
from ak5.server.services import workflow as wf
from ak5.server.services.event_log import record_event
from ak5.server.services.ticket_status import derive_status
from ak5.server.services.workflow import WorkflowError

router = APIRouter(prefix="/tickets", tags=["tickets"])


def _workflow_error_response(exc: WorkflowError) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_STATUS[exc.code],
        content=error_body(exc.code, exc.message, details=exc.details),
    )


def _workspace_forbidden_ticket(ticket_id: str, workspace_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_STATUS[ErrorCode.WORKSPACE_FORBIDDEN],
        content=error_body(
            ErrorCode.WORKSPACE_FORBIDDEN,
            "Ticket is outside the caller's workspace",
            details={"ticket_id": ticket_id, "workspace_id": workspace_id},
        ),
    )


async def _reject_cross_workspace_ticket(
    db: AsyncSession, ticket_id: str, workspace_id: str
) -> JSONResponse | None:
    ticket = await db.get(Ticket, ticket_id)
    if ticket is not None and ticket.workspace_id != workspace_id:
        return _workspace_forbidden_ticket(ticket_id, workspace_id)
    return None


def _format_ticket_out(t: Ticket, subtask_stats: tuple[int, int] = (0, 0)) -> TicketOut:
    """Build TicketOut. Caller must not pass an expired ticket (refresh/reload first)."""
    count, done_count = subtask_stats
    col = t.__dict__.get("column")
    stage = col.stage if col is not None else None
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
        stage=stage,
        attempt=t.attempt,
        max_attempts=t.max_attempts,
        claimed_by=t.claimed_by,
        lease_expires_at=t.lease_expires_at,
        lease_expirations=t.lease_expirations,
        submitted_by=t.submitted_by,
        blocked_kind=t.blocked_kind,
        blocked_since=t.blocked_since,
        blocked_actor=t.blocked_actor,
        original_request=t.original_request,
        subtask_count=count,
        subtask_done_count=done_count,
    )


def _revision_out(rev: Any) -> TicketRevisionOut:
    return TicketRevisionOut(
        revision_id=rev.revision_id,
        ticket_id=rev.ticket_id,
        spec=rev.spec,
        acceptance_criteria=rev.acceptance_criteria,
        author=rev.author,
        reason=rev.reason,
        source=rev.source,
        created_at=rev.created_at,
    )


def _review_out(rev: Any) -> TicketReviewOut:
    return TicketReviewOut(
        review_id=rev.review_id,
        ticket_id=rev.ticket_id,
        stage=rev.stage,
        decision=rev.decision,
        findings=rev.findings,
        reviewer=rev.reviewer,
        attempt=rev.attempt,
        reason=rev.reason,
        misrouted=rev.misrouted,
        created_at=rev.created_at,
    )


def _postmortem_out(pm: Any) -> TicketPostmortemOut:
    return TicketPostmortemOut(
        postmortem_id=pm.postmortem_id,
        ticket_id=pm.ticket_id,
        what_went_wrong=pm.what_went_wrong,
        root_cause=pm.root_cause,
        revision_id=pm.revision_id,
        human_review_id=pm.human_review_id,
        author=pm.author,
        created_at=pm.created_at,
    )


async def _idempotent(
    db: AsyncSession,
    *,
    key: str | None,
    actor_id: str,
    op: str,
    response_builder,
    ticket_id: str | None = None,
    body: Any = None,
) -> tuple[Any, bool]:
    """Run ``response_builder`` once per Idempotency-Key.

    Returns ``(result, from_cache)``. Caller must ``commit`` when ``from_cache``
    is False. Cached hits return the stored JSON dict. Request fingerprint
    (ticket_id + body hash) must match on replay.
    """
    fingerprint = wf.compute_idempotency_fingerprint(ticket_id, body)
    if key:
        cached = await wf.get_idempotent_response(
            db, key=key, actor_id=actor_id, op=op, fingerprint=fingerprint
        )
        if cached is not None:
            return cached, True
    result = await response_builder()
    if key:
        if hasattr(result, "model_dump"):
            payload = result.model_dump(mode="json")
        elif isinstance(result, dict):
            payload = result
        else:
            payload = {"ok": True}
        await wf.store_idempotent_response(
            db,
            key=key,
            actor_id=actor_id,
            op=op,
            response=payload,
            fingerprint=fingerprint,
        )
    return result, False


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    req: TicketCreate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut | JSONResponse:
    """Create a ticket — T1 (no board → inbox) or T2 (board → backlog + revision)."""

    async def _run() -> TicketOut:
        if req.assigned_to is not None:
            raise WorkflowError(
                ErrorCode.PAYLOAD_INVALID,
                "assignee is derived from claim; do not set assigned_to on create",
            )
        if req.board_id is None:
            original = req.original_request or req.description or req.title
            ticket = await wf.create_inbox(
                db,
                current_actor,
                original_request=original,
                title=req.title,
                priority=req.priority,
                labels=req.labels,
                parent_ticket_id=req.parent_ticket_id,
            )
        else:
            spec = req.spec or req.description
            criteria = req.acceptance_criteria
            if not spec:
                raise WorkflowError(ErrorCode.PAYLOAD_INVALID, "spec (or description) is required for create_direct")
            if not criteria:
                raise WorkflowError(
                    ErrorCode.PAYLOAD_INVALID,
                    "acceptance_criteria is required for create_direct",
                )
            ticket = await wf.create_direct(
                db,
                current_actor,
                board_id=req.board_id,
                title=req.title,
                spec=spec,
                acceptance_criteria=criteria,
                priority=req.priority,
                labels=req.labels,
                parent_ticket_id=req.parent_ticket_id,
                max_attempts=req.max_attempts,
                workspace_id=workspace_id,
            )
            if req.due_date is not None:
                ticket.due_date = req.due_date
            if req.execution_context is not None:
                ticket.execution_context_dict = req.execution_context
        await db.flush()
        # Server onupdate expires updated_at under AsyncSession; keep a local value.
        ticket.updated_at = datetime.now(UTC).replace(tzinfo=None)
        return _format_ticket_out(ticket)

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op="ticket.create",
            response_builder=_run,
            ticket_id=None,
            body=req.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)

    if from_cache:
        return TicketOut.model_validate(out)
    return out


@router.get("", response_model=list[TicketOut])
async def list_tickets(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
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

    Requires Bearer JWT. Lazy-reclaims expired leases (T8/T20) first.
    Scoped to the caller's workspace.
    """
    _ = current_actor
    await wf.reclaim_expired_leases(db, board_id=board_id)
    await db.commit()
    safe_limit = min(max(1, limit), 200)
    stmt = (
        select(Ticket)
        .options(selectinload(Ticket.subtasks), selectinload(Ticket.column))
        .where(Ticket.workspace_id == workspace_id)
    )
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
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
) -> TicketDetailOut | JSONResponse:
    """Get ticket detail, subtasks, and comments. Requires Bearer JWT.

    Cross-workspace access → ``WORKSPACE_FORBIDDEN``.
    """
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
    _ = current_actor
    stmt = (
        select(Ticket)
        .where(Ticket.ticket_id == ticket_id)
        .options(
            selectinload(Ticket.comments),
            selectinload(Ticket.subtasks),
            selectinload(Ticket.attachments),
            selectinload(Ticket.column),
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


@router.patch("/{ticket_id}", response_model=None)
async def update_ticket(
    ticket_id: str,
    req: TicketUpdate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketOut | JSONResponse:
    """Update ticket fields, status, or assignee.

    Uses exclude_unset so explicit JSON null can clear nullable fields
    (e.g. assigned_to, blocked_by) without requiring a sentinel value.

    Setting ``status=blocked`` / non-empty ``blocked_by`` delegates to
    ``block_ticket`` (B1, kind=external). Clearing blocked delegates to
    ``unblock_ticket``.
    """
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket '{ticket_id}' not found")
    if ticket.workspace_id != workspace_id:
        return _workspace_forbidden_ticket(ticket_id, workspace_id)

    data = req.model_dump(exclude_unset=True)
    if not data:
        return _format_ticket_out(ticket)

    if "assigned_to" in data:
        return JSONResponse(
            status_code=HTTP_STATUS[ErrorCode.PAYLOAD_INVALID],
            content=error_body(
                ErrorCode.PAYLOAD_INVALID,
                "assignee is derived from claim; do not set assigned_to via PATCH",
            ),
        )

    should_block = False
    should_unblock = False
    block_reason = "blocked via PATCH"

    if "status" in data and data["status"] == "blocked":
        should_block = True
        if "blocked_by" in data:
            raw = data["blocked_by"]
            cleaned = raw.strip() if isinstance(raw, str) else raw
            if cleaned:
                block_reason = cleaned
            data.pop("blocked_by", None)
        elif ticket.blocked_by:
            block_reason = ticket.blocked_by
        data.pop("status", None)
    elif "blocked_by" in data:
        raw_blocked = data["blocked_by"]
        new_blocked_by = raw_blocked.strip() if isinstance(raw_blocked, str) else raw_blocked
        if new_blocked_by == "":
            new_blocked_by = None
        data.pop("blocked_by")
        if new_blocked_by:
            should_block = True
            block_reason = new_blocked_by
            if data.get("status") == "blocked":
                data.pop("status", None)
        elif ticket.blocked_kind or ticket.blocked_by:
            should_unblock = True
            if data.get("status") == "blocked":
                data.pop("status", None)

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
    if "status" in data and data["status"] is not None:
        requested_status = data["status"]
        # Non-blocked status writes are derived; never bypass B1/B2 via PATCH.
        if ticket.blocked_kind is None and ticket.blocked_by:
            ticket.blocked_by = None
        column = await db.get(Column, ticket.column_id)
        if column is not None:
            ticket.column = column
        synced_status = derive_status(ticket)
        changes["status"] = (ticket.status, synced_status)
        if requested_status != synced_status:
            changes["status_coerced_from"] = requested_status
        ticket.status = synced_status
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
            ticket.archived_at = datetime.now(UTC) if new_archived else None

    try:
        if should_block:
            ticket = await wf.block_ticket(
                db,
                current_actor,
                ticket_id,
                kind=BlockedKind.EXTERNAL,
                reason=block_reason,
            )
            changes["blocked"] = {"kind": BlockedKind.EXTERNAL.value, "reason": block_reason}
        elif should_unblock:
            ticket = await wf.unblock_ticket(db, current_actor, ticket_id, resolution=None)
            changes["unblocked"] = True
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)

    action = (
        "ARCHIVED"
        if ("is_archived" in changes and ticket.is_archived)
        else "UNARCHIVED"
        if ("is_archived" in changes and not ticket.is_archived)
        else ("STATUS_CHANGE" if "status" in changes or should_block or should_unblock else "UPDATED")
    )
    if not should_block and not should_unblock:
        audit = AuditLog(
            actor_id=current_actor.actor_id,
            action=action,
            target_type="ticket",
            target_id=ticket_id,
            payload=json.dumps(changes, default=str),
        )
        db.add(audit)

    actor_id = current_actor.actor_id
    ticket_out = _format_ticket_out(ticket)
    await record_event(
        db,
        "TICKET_UPDATED",
        board_id=ticket.board_id,
        ticket_id=ticket_id,
        actor_id=actor_id,
        data={
            "ticket": ticket_out.model_dump(mode="json"),
            "actor_id": actor_id,
            "changes": changes,
        },
    )
    await db.commit()
    # Reload with column for stage field (avoid expired attrs after commit).
    stmt = (
        select(Ticket)
        .where(Ticket.ticket_id == ticket_id)
        .options(selectinload(Ticket.column))
    )
    ticket = (await db.execute(stmt)).scalar_one()
    return _format_ticket_out(ticket)


@router.post("/{ticket_id}/archive", response_model=TicketOut)
async def archive_ticket(
    ticket_id: str,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketOut | JSONResponse:
    """Archive a ticket and record audit log."""
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket '{ticket_id}' not found")

    if ticket.workspace_id != workspace_id:
        return _workspace_forbidden_ticket(ticket_id, workspace_id)
    changes = {"is_archived": (ticket.is_archived, True)}
    ticket.is_archived = True
    ticket.archived_at = datetime.now(UTC)

    audit = AuditLog(
        actor_id=current_actor.actor_id,
        action="ARCHIVED",
        target_type="ticket",
        target_id=ticket_id,
        payload=json.dumps(changes, default=str),
    )
    db.add(audit)
    ticket_out = _format_ticket_out(ticket)
    await record_event(
        db,
        "TICKET_ARCHIVED",
        board_id=ticket.board_id,
        ticket_id=ticket_id,
        actor_id=current_actor.actor_id,
        data={"ticket": ticket_out.model_dump(mode="json"), "actor_id": current_actor.actor_id},
    )
    await db.commit()
    await db.refresh(ticket)
    return _format_ticket_out(ticket)


@router.post("/{ticket_id}/unarchive", response_model=TicketOut)
async def unarchive_ticket(
    ticket_id: str,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketOut | JSONResponse:
    """Unarchive an archived ticket back to active view."""
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket '{ticket_id}' not found")

    if ticket.workspace_id != workspace_id:
        return _workspace_forbidden_ticket(ticket_id, workspace_id)
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
    ticket_out = _format_ticket_out(ticket)
    await record_event(
        db,
        "TICKET_UNARCHIVED",
        board_id=ticket.board_id,
        ticket_id=ticket_id,
        actor_id=current_actor.actor_id,
        data={"ticket": ticket_out.model_dump(mode="json"), "actor_id": current_actor.actor_id},
    )
    await db.commit()
    await db.refresh(ticket)
    return _format_ticket_out(ticket)


@router.patch("/{ticket_id}/move", response_model=None)
async def move_ticket(
    ticket_id: str,
    req: TicketMoveRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut | JSONResponse:
    """Move ticket under workflow.md §8 (same-stage free; human stage pairs only)."""
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied

    async def _run() -> TicketOut:
        prev = await db.get(Ticket, ticket_id)
        if not prev:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket '{ticket_id}' not found")
        ticket = await wf.move_ticket_constrained(
            db,
            current_actor,
            ticket_id,
            target_column_id=req.target_column_id,
            previous_ticket_id=req.previous_ticket_id,
            next_ticket_id=req.next_ticket_id,
            reason=req.reason,
        )
        out = _format_ticket_out(ticket)
        await record_event(
            db,
            "TICKET_MOVED",
            board_id=ticket.board_id,
            ticket_id=ticket_id,
            actor_id=current_actor.actor_id,
            data={
                "ticket": out.model_dump(mode="json"),
                "to_column": req.target_column_id,
                "actor_id": current_actor.actor_id,
            },
        )
        return out

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op=f"ticket.move:{ticket_id}",
            response_builder=_run,
            ticket_id=ticket_id,
            body=req.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)

    return TicketOut.model_validate(out) if from_cache else out


@router.post("/{ticket_id}/delegate", response_model=None, status_code=status.HTTP_201_CREATED)
async def delegate_subtask(
    ticket_id: str,
    req: TicketDelegateRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut | JSONResponse:
    """Delegate as create_direct with parent (cli.md §5 pull model)."""
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied

    async def _run() -> TicketOut:
        parent = await db.get(Ticket, ticket_id)
        if not parent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Parent ticket '{ticket_id}' not found")
        board_id = req.board_id or parent.board_id
        spec = req.spec or req.subtask_description or req.subtask_title
        criteria = req.acceptance_criteria or ["Subtask accepted when deliverable meets parent criteria"]
        subtask = await wf.create_direct(
            db,
            current_actor,
            board_id=board_id,
            title=req.subtask_title,
            spec=spec,
            acceptance_criteria=criteria,
            priority=req.priority,
            labels=req.labels,
            parent_ticket_id=parent.ticket_id,
        )
        comment = TicketComment(
            comment_id=f"cmt_{uuid.uuid4().hex[:8]}",
            ticket_id=parent.ticket_id,
            actor_id=current_actor.actor_id,
            content=f"Delegated subtask {subtask.ticket_id} ('{req.subtask_title}') to board backlog (pull model)",
            is_internal=True,
            metadata_json=json.dumps({"subtask_id": subtask.ticket_id}),
        )
        db.add(comment)
        await db.flush()
        subtask_out = _format_ticket_out(subtask)
        await record_event(
            db,
            "TICKET_DELEGATED",
            board_id=subtask.board_id,
            ticket_id=subtask.ticket_id,
            actor_id=current_actor.actor_id,
            data={
                "parent_ticket_id": ticket_id,
                "subtask": subtask_out.model_dump(mode="json"),
                "ticket": subtask_out.model_dump(mode="json"),
                "actor_id": current_actor.actor_id,
            },
        )
        return subtask_out

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op=f"ticket.delegate:{ticket_id}",
            response_builder=_run,
            ticket_id=ticket_id,
            body=req.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)

    if from_cache:
        return TicketOut.model_validate(out)
    return out


@router.post("/{ticket_id}/comments", response_model=TicketCommentOut, status_code=status.HTTP_201_CREATED)
async def add_comment(
    ticket_id: str,
    req: TicketCommentCreate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketCommentOut | JSONResponse:
    """Add a discussion or reasoning comment to a ticket."""
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticket '{ticket_id}' not found")
    if ticket.workspace_id != workspace_id:
        return _workspace_forbidden_ticket(ticket_id, workspace_id)

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
    await db.flush()
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

    ticket_out = _format_ticket_out(ticket)
    await record_event(
        db,
        "COMMENT_ADDED",
        board_id=ticket.board_id,
        ticket_id=ticket_id,
        actor_id=current_actor.actor_id,
        data={
            "board_id": ticket.board_id,
            "ticket_id": ticket_id,
            "ticket": ticket_out.model_dump(mode="json"),
            "comment": comment_out.model_dump(mode="json"),
            "actor_id": current_actor.actor_id,
        },
    )
    await db.commit()
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
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketAttachmentOut:
    """Upload an attachment file to a ticket."""
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket '{ticket_id}' not found",
        )
    if ticket.workspace_id != workspace_id:
        return _workspace_forbidden_ticket(ticket_id, workspace_id)

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
    await db.flush()
    await db.refresh(attachment)

    attachment_out = TicketAttachmentOut.model_validate(attachment)
    ticket_out = _format_ticket_out(ticket)
    await record_event(
        db,
        "ATTACHMENT_ADDED",
        board_id=ticket.board_id,
        ticket_id=ticket_id,
        actor_id=current_actor.actor_id,
        data={
            "board_id": ticket.board_id,
            "ticket_id": ticket_id,
            "ticket": ticket_out.model_dump(mode="json"),
            "attachment": attachment_out.model_dump(mode="json"),
            "actor_id": current_actor.actor_id,
        },
    )
    await db.commit()
    return attachment_out


@router.get("/{ticket_id}/attachments", response_model=None)
async def list_attachments(
    ticket_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
) -> list[TicketAttachmentOut] | JSONResponse:
    """List all attachments for a ticket. Requires Bearer JWT."""
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
    _ = current_actor
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
    current_actor: Annotated[Actor | None, Depends(get_current_actor_optional)],
    access_token: Annotated[str | None, Query()] = None,
) -> FileResponse:
    """Download an attachment. Requires Bearer JWT or ``access_token`` query (for ``<a href>``)."""
    import jwt

    from ak5.server.security import get_jwt_secret

    actor = current_actor
    if actor is None and access_token:
        try:
            payload = jwt.decode(
                access_token, get_jwt_secret(), algorithms=[settings.JWT_ALGORITHM]
            )
            actor_id = payload.get("sub")
            if actor_id:
                actor = await db.get(Actor, actor_id)
        except jwt.PyJWTError:
            actor = None
    if actor is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header or access_token query param required",
            headers={"WWW-Authenticate": "Bearer"},
        )

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


@router.delete("/{ticket_id}/attachments/{attachment_id}", response_model=None)
async def delete_attachment(
    ticket_id: str,
    attachment_id: str,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any] | JSONResponse:
    """Delete an attachment file from a ticket."""
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
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

    filename = attachment.filename
    await db.delete(attachment)
    audit = AuditLog(
        actor_id=current_actor.actor_id,
        action="ATTACHMENT_DELETED",
        target_type="ticket",
        target_id=ticket_id,
        payload=json.dumps({"attachment_id": attachment_id, "filename": filename}),
    )
    db.add(audit)

    ticket_out = _format_ticket_out(ticket) if ticket else None
    await record_event(
        db,
        "ATTACHMENT_DELETED",
        board_id=board_id,
        ticket_id=ticket_id,
        actor_id=current_actor.actor_id,
        data={
            "board_id": board_id,
            "ticket_id": ticket_id,
            "ticket": ticket_out.model_dump(mode="json") if ticket_out else None,
            "attachment_id": attachment_id,
            "actor_id": current_actor.actor_id,
        },
    )
    await db.commit()

    return {"deleted": True, "attachment_id": attachment_id}


# ---------------------------------------------------------------------------
# Workflow transition commands (workflow.md §3 / §4)
# ---------------------------------------------------------------------------


@router.post("/{ticket_id}/claim", response_model=None)
async def claim_ticket_endpoint(
    ticket_id: str,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    req: ClaimRequest | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketClaimOut | JSONResponse:
    body = req or ClaimRequest()
    existing = await db.get(Ticket, ticket_id)
    if existing is not None and existing.workspace_id != workspace_id:
        return _workspace_forbidden_ticket(ticket_id, workspace_id)

    async def _run() -> TicketClaimOut:
        result = await wf.claim_ticket(
            db, current_actor, ticket_id, capabilities=body.capabilities
        )
        return TicketClaimOut(
            ticket=_format_ticket_out(result["ticket"]),
            claim_token=result["claim_token"],
            lease_expires_at=result["lease_expires_at"],
            revision=_revision_out(result["revision"]) if result["revision"] else None,
            postmortems=[_postmortem_out(p) for p in result["postmortems"]],
            reviews=[_review_out(r) for r in result["reviews"]],
            review_checklist=list(result.get("review_checklist") or []),
            acceptance_criteria_defaults=list(
                result.get("acceptance_criteria_defaults") or []
            ),
        )

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op=f"ticket.claim:{ticket_id}",
            response_builder=_run,
            ticket_id=ticket_id,
            body=body.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)
    return TicketClaimOut.model_validate(out) if from_cache else out


@router.post("/{ticket_id}/heartbeat", response_model=None)
async def heartbeat_endpoint(
    ticket_id: str,
    req: HeartbeatRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut | JSONResponse:
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
    async def _run() -> TicketOut:
        ticket = await wf.heartbeat(db, current_actor, ticket_id, claim_token=req.claim_token)
        return _format_ticket_out(ticket)

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op=f"ticket.heartbeat:{ticket_id}",
            response_builder=_run,
            ticket_id=ticket_id,
            body=req.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)
    return TicketOut.model_validate(out) if from_cache else out


@router.post("/{ticket_id}/release", response_model=None)
async def release_endpoint(
    ticket_id: str,
    req: ReleaseRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut | JSONResponse:
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
    async def _run() -> TicketOut:
        ticket = await wf.release(
            db, current_actor, ticket_id, claim_token=req.claim_token, reason=req.reason
        )
        return _format_ticket_out(ticket)

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op=f"ticket.release:{ticket_id}",
            response_builder=_run,
            ticket_id=ticket_id,
            body=req.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)
    return TicketOut.model_validate(out) if from_cache else out


@router.post("/{ticket_id}/submit", response_model=None)
async def submit_endpoint(
    ticket_id: str,
    req: SubmitRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut | JSONResponse:
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
    async def _run() -> TicketOut:
        ticket = await wf.submit(
            db,
            current_actor,
            ticket_id,
            claim_token=req.claim_token,
            deliverable=req.deliverable,
        )
        return _format_ticket_out(ticket)

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op=f"ticket.submit:{ticket_id}",
            response_builder=_run,
            ticket_id=ticket_id,
            body=req.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)
    return TicketOut.model_validate(out) if from_cache else out


@router.post("/{ticket_id}/bounce", response_model=None)
async def bounce_endpoint(
    ticket_id: str,
    req: BounceRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut | JSONResponse:
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
    async def _run() -> TicketOut:
        ticket = await wf.bounce(
            db, current_actor, ticket_id, claim_token=req.claim_token, reason=req.reason
        )
        return _format_ticket_out(ticket)

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op=f"ticket.bounce:{ticket_id}",
            response_builder=_run,
            ticket_id=ticket_id,
            body=req.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)
    return TicketOut.model_validate(out) if from_cache else out


@router.post("/{ticket_id}/route", response_model=None)
async def route_endpoint(
    ticket_id: str,
    req: RouteRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut | JSONResponse:
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
    async def _run() -> TicketOut:
        ticket = await wf.route_ticket(
            db, current_actor, ticket_id, claim_token=req.claim_token, plan=req.plan
        )
        return _format_ticket_out(ticket)

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op=f"ticket.route:{ticket_id}",
            response_builder=_run,
            ticket_id=ticket_id,
            body=req.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)
    return TicketOut.model_validate(out) if from_cache else out


@router.post("/{ticket_id}/verdict", response_model=None)
async def verdict_endpoint(
    ticket_id: str,
    req: VerdictRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut | JSONResponse:
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
    async def _run() -> TicketOut:
        findings = [
            Finding.model_validate(f) if not isinstance(f, Finding) else f
            for f in req.verdict.findings
        ]
        # Build contracts Verdict without triggering reject-findings validator
        # for human T16 (empty findings + reason). Gatekeeper paths validate
        # inside workflow.verdict.
        body = Verdict.model_construct(
            decision=req.verdict.decision,
            reason=req.verdict.reason,
            misrouted=req.verdict.misrouted,
            findings=findings,
        )
        ticket = await wf.verdict(
            db,
            current_actor,
            ticket_id,
            claim_token=req.claim_token,
            body=body,
        )
        return _format_ticket_out(ticket)

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op=f"ticket.verdict:{ticket_id}",
            response_builder=_run,
            ticket_id=ticket_id,
            body=req.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)
    return TicketOut.model_validate(out) if from_cache else out


@router.post("/{ticket_id}/postmortem", response_model=None)
async def postmortem_endpoint(
    ticket_id: str,
    req: PostmortemRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut | JSONResponse:
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
    async def _run() -> TicketOut:
        ticket = await wf.submit_postmortem(
            db,
            current_actor,
            ticket_id,
            claim_token=req.claim_token,
            body=req.postmortem,
        )
        return _format_ticket_out(ticket)

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op=f"ticket.postmortem:{ticket_id}",
            response_builder=_run,
            ticket_id=ticket_id,
            body=req.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)
    return TicketOut.model_validate(out) if from_cache else out


@router.post("/{ticket_id}/block", response_model=None)
async def block_endpoint(
    ticket_id: str,
    req: BlockRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut | JSONResponse:
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
    async def _run() -> TicketOut:
        ticket = await wf.block_ticket(
            db,
            current_actor,
            ticket_id,
            kind=req.kind,
            reason=req.reason,
            claim_token=req.claim_token,
        )
        return _format_ticket_out(ticket)

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op=f"ticket.block:{ticket_id}",
            response_builder=_run,
            ticket_id=ticket_id,
            body=req.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)
    return TicketOut.model_validate(out) if from_cache else out


@router.post("/{ticket_id}/unblock", response_model=None)
async def unblock_endpoint(
    ticket_id: str,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    req: UnblockRequest | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut | JSONResponse:
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
    body = req or UnblockRequest()

    async def _run() -> TicketOut:
        ticket = await wf.unblock_ticket(
            db, current_actor, ticket_id, resolution=body.resolution
        )
        return _format_ticket_out(ticket)

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op=f"ticket.unblock:{ticket_id}",
            response_builder=_run,
            ticket_id=ticket_id,
            body=body.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)
    return TicketOut.model_validate(out) if from_cache else out


@router.post("/{ticket_id}/reopen", response_model=None)
async def reopen_endpoint(
    ticket_id: str,
    req: ReopenRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut | JSONResponse:
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
    async def _run() -> TicketOut:
        ticket = await wf.reopen(db, current_actor, ticket_id, reason=req.reason)
        return _format_ticket_out(ticket)

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op=f"ticket.reopen:{ticket_id}",
            response_builder=_run,
            ticket_id=ticket_id,
            body=req.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)
    return TicketOut.model_validate(out) if from_cache else out


@router.post("/{ticket_id}/reroute", response_model=None)
async def reroute_endpoint(
    ticket_id: str,
    req: RerouteRequest,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut | JSONResponse:
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
    async def _run() -> TicketOut:
        ticket = await wf.reroute(
            db,
            current_actor,
            ticket_id,
            reason=req.reason,
            to_inbox=req.to_inbox,
            target_board_id=req.target_board_id,
        )
        return _format_ticket_out(ticket)

    try:
        out, from_cache = await _idempotent(
            db,
            key=idempotency_key,
            actor_id=current_actor.actor_id,
            op=f"ticket.reroute:{ticket_id}",
            response_builder=_run,
            ticket_id=ticket_id,
            body=req.model_dump(mode="json"),
        )
        if not from_cache:
            await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return _workflow_error_response(exc)
    return TicketOut.model_validate(out) if from_cache else out


@router.get("/{ticket_id}/history", response_model=None)
async def history_endpoint(
    ticket_id: str,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TicketHistoryOut | JSONResponse:
    denied = await _reject_cross_workspace_ticket(db, ticket_id, workspace_id)
    if denied is not None:
        return denied
    _ = current_actor
    try:
        hist = await wf.ticket_history(db, ticket_id)
    except WorkflowError as exc:
        return _workflow_error_response(exc)
    return TicketHistoryOut(
        ticket_id=hist["ticket_id"],
        revisions=[_revision_out(r) for r in hist["revisions"]],
        reviews=[_review_out(r) for r in hist["reviews"]],
        postmortems=[_postmortem_out(p) for p in hist["postmortems"]],
        audit=[
            AuditLogOut(
                log_id=a.log_id,
                actor_id=a.actor_id,
                action=a.action,
                target_type=a.target_type,
                target_id=a.target_id,
                payload=a.payload_dict,
                timestamp=a.timestamp,
            )
            for a in hist["audit"]
        ],
    )
