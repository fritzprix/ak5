import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.contracts.stages import STANDARD_STAGES, Stage
from ak5.server.database import get_db
from ak5.server.inbox import is_inbox_board_id, resolve_board_alias
from ak5.server.models.actor import Actor
from ak5.server.models.board import Board
from ak5.server.models.board_member import BoardMember
from ak5.server.models.column import Column
from ak5.server.models.ticket import Ticket
from ak5.server.models.workspace import Workspace
from ak5.server.quota import QuotaExceeded, check_board_quota
from ak5.server.routers.actors import _to_actor_out
from ak5.server.routers.auth import get_auth_workspace_id, get_current_actor
from ak5.server.schemas.actor import ActorOut
from ak5.server.schemas.board import (
    BoardClaimOut,
    BoardCreate,
    BoardDetailOut,
    BoardMemberCreate,
    BoardMemberOut,
    BoardOut,
    BoardTicketOut,
    BoardUpdate,
    ColumnWithTicketsOut,
)
from ak5.server.schemas.column import ColumnCreate, ColumnOut
from ak5.server.schemas.ticket import SubtaskSummaryOut, TicketOut
from ak5.server.services import workflow as wf
from ak5.server.services.templates import (
    BoardTemplate,
    get_template,
    template_column_dicts,
)
from ak5.server.services.workflow import WorkflowError

_STANDARD_STAGE_VALUES = frozenset(s.value for s in STANDARD_STAGES)

router = APIRouter(prefix="/boards", tags=["boards"])


def _system_board_response(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_STATUS[ErrorCode.SYSTEM_BOARD],
        content=error_body(ErrorCode.SYSTEM_BOARD, message),
    )


def _payload_invalid_response(message: str, *, details: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_STATUS[ErrorCode.PAYLOAD_INVALID],
        content=error_body(ErrorCode.PAYLOAD_INVALID, message, details=details or {}),
    )


def _is_inbox(board: Board | str) -> bool:
    board_id = board if isinstance(board, str) else board.board_id
    if is_inbox_board_id(board_id):
        return True
    return isinstance(board, Board) and board.kind == "inbox"


async def _board_path_id(
    board_id: str,
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
) -> str:
    """``{board_id}`` with the ``inbox`` alias resolved to the caller's inbox (#76)."""
    return resolve_board_alias(board_id, workspace_id)


def _to_board_out(board: Board) -> BoardOut:
    kind = board.kind if board.kind in ("inbox", "standard") else "standard"
    return BoardOut(
        board_id=board.board_id,
        name=board.name,
        description=board.description,
        created_by=board.created_by,
        created_at=board.created_at,
        kind=kind,
        template=board.template,
        auto_created=board.auto_created,
    )


def _ticket_stage(t: Ticket) -> str | None:
    col = t.__dict__.get("column")
    return col.stage if col is not None else None


def _subtask_summary(child: Ticket) -> SubtaskSummaryOut:
    return SubtaskSummaryOut(
        ticket_id=child.ticket_id,
        title=child.title,
        stage=_ticket_stage(child),
        board_id=child.board_id,
        blocked=child.status == "blocked" or child.blocked_kind is not None,
        claimed=bool(child.claim_token or child.claimed_by),
    )


def _to_ticket_out(t: Ticket, subtask_stats: dict[str, tuple[int, int]]) -> TicketOut:
    count, done_count = subtask_stats.get(t.ticket_id, (0, 0))
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
        stage=_ticket_stage(t),
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


def _to_board_ticket_out(
    t: Ticket,
    subtask_stats: dict[str, tuple[int, int]],
    subtasks_by_parent: dict[str, list[SubtaskSummaryOut]],
) -> BoardTicketOut:
    base = _to_ticket_out(t, subtask_stats)
    return BoardTicketOut(
        **base.model_dump(),
        subtasks=list(subtasks_by_parent.get(t.ticket_id, [])),
    )


def _workspace_forbidden_board(board_id: str, workspace_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_STATUS[ErrorCode.WORKSPACE_FORBIDDEN],
        content=error_body(
            ErrorCode.WORKSPACE_FORBIDDEN,
            "Board is outside the caller's workspace",
            details={"board_id": board_id, "workspace_id": workspace_id},
        ),
    )


async def _load_board_in_workspace(
    db: AsyncSession, board_id: str, workspace_id: str
) -> Board | JSONResponse:
    board = await db.get(Board, board_id)
    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Board '{board_id}' not found",
        )
    if board.workspace_id != workspace_id:
        return _workspace_forbidden_board(board_id, workspace_id)
    return board


@router.get("", response_model=list[BoardOut])
async def list_boards(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
) -> list[BoardOut]:
    """List boards in the caller's workspace. Requires Bearer JWT."""
    _ = current_actor
    stmt = (
        select(Board)
        .where(Board.workspace_id == workspace_id)
        .order_by(Board.created_at.asc(), Board.name.asc())
    )
    result = await db.execute(stmt)
    boards = result.scalars().all()
    return [_to_board_out(b) for b in boards]


@router.get("/{board_id}/next", response_model=None)
async def next_work_item(
    board_id: Annotated[str, Depends(_board_path_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    capabilities: str | None = None,
) -> TicketOut | JSONResponse:
    """Oldest unclaimed unblocked ticket for this board (incl. inbox). 204 if none."""
    loaded = await _load_board_in_workspace(db, board_id, workspace_id)
    if isinstance(loaded, JSONResponse):
        return loaded
    caps = [c.strip() for c in capabilities.split(",") if c.strip()] if capabilities else None
    try:
        ticket = await wf.next_ticket(db, current_actor, board_id, capabilities=caps)
        await db.commit()
    except WorkflowError as exc:
        await db.rollback()
        return JSONResponse(
            status_code=HTTP_STATUS[exc.code],
            content=error_body(exc.code, exc.message, details=exc.details),
        )
    if ticket is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return _to_ticket_out(ticket, {})


@router.get("/{board_id}", response_model=BoardDetailOut)
async def get_board(
    board_id: Annotated[str, Depends(_board_path_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    include_archived: bool = False,
    done_limit: int | None = 10,
) -> BoardDetailOut | JSONResponse:
    """Retrieve full board details including columns and tickets ordered by rank.

    Requires Bearer JWT. Cross-workspace access → ``WORKSPACE_FORBIDDEN``.
    """
    _ = current_actor
    stmt = (
        select(Board)
        .where(Board.board_id == board_id)
        .options(
            selectinload(Board.columns).selectinload(Column.tickets),
            selectinload(Board.tickets),
        )
    )
    result = await db.execute(stmt)
    board = result.scalar_one_or_none()
    if not board:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Board '{board_id}' not found")
    if board.workspace_id != workspace_id:
        return JSONResponse(
            status_code=HTTP_STATUS[ErrorCode.WORKSPACE_FORBIDDEN],
            content=error_body(
                ErrorCode.WORKSPACE_FORBIDDEN,
                "Board is outside the caller's workspace",
                details={"board_id": board_id, "workspace_id": workspace_id},
            ),
        )

    # One query for all children of tickets on this board (children may live on other boards).
    parent_ids = [t.ticket_id for t in board.tickets]
    subtasks_by_parent: dict[str, list[SubtaskSummaryOut]] = {pid: [] for pid in parent_ids}
    subtask_stats: dict[str, tuple[int, int]] = {}
    if parent_ids:
        child_stmt = (
            select(Ticket)
            .where(Ticket.parent_ticket_id.in_(parent_ids))
            .options(selectinload(Ticket.column))
            .order_by(Ticket.created_at.asc(), Ticket.ticket_id.asc())
        )
        child_result = await db.execute(child_stmt)
        for child in child_result.scalars().all():
            parent_id = child.parent_ticket_id
            if parent_id is None:
                continue
            subtasks_by_parent.setdefault(parent_id, []).append(_subtask_summary(child))
            count, done = subtask_stats.get(parent_id, (0, 0))
            is_done = 1 if child.status == "done" else 0
            subtask_stats[parent_id] = (count + 1, done + is_done)

    # Build response with tickets ordered by rank
    columns_out = []
    # Sort columns by position
    sorted_columns = sorted(board.columns, key=lambda c: c.position)
    for col in sorted_columns:
        col_tickets = [
            t for t in col.tickets
            if include_archived or not t.is_archived
        ]
        total_count = len(col_tickets)

        if col.stage == "done" and done_limit is not None and done_limit > 0 and len(col_tickets) > done_limit:
            # Pick most recently updated done tickets, then sort by rank
            recent_done = sorted(col_tickets, key=lambda t: t.updated_at, reverse=True)[:done_limit]
            sorted_tickets = sorted(recent_done, key=lambda t: t.rank)
        else:
            sorted_tickets = sorted(col_tickets, key=lambda t: t.rank)

        ticket_outs = [
            _to_board_ticket_out(t, subtask_stats, subtasks_by_parent) for t in sorted_tickets
        ]
        columns_out.append(
            ColumnWithTicketsOut(
                column_id=col.column_id,
                board_id=col.board_id,
                name=col.name,
                stage=col.stage,
                position=col.position,
                wip_limit=col.wip_limit,
                created_at=col.created_at,
                tickets=ticket_outs,
                total_ticket_count=total_count,
            )
        )

    kind = board.kind if board.kind in ("inbox", "standard") else "standard"
    return BoardDetailOut(
        board_id=board.board_id,
        name=board.name,
        description=board.description,
        created_by=board.created_by,
        created_at=board.created_at,
        kind=kind,
        template=board.template,
        auto_created=board.auto_created,
        columns=columns_out,
        has_human_admin=await _board_has_human_admin(board, db),
    )


@router.post("", response_model=None)
async def create_board(
    req: BoardCreate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BoardOut | JSONResponse:
    """Create a new board and seed columns from the requested template.

    Optionally enroll human owners as board admins via ``owner_actor_ids`` so
    agents can hand admin to humans at creation time (preferred over claim).
    """
    board_id = req.board_id or f"board_{uuid.uuid4().hex[:8]}"

    if is_inbox_board_id(board_id) or req.template == "inbox":
        return _system_board_response(
            "Cannot create or recreate the system inbox board via the API"
        )

    try:
        tmpl = get_template(req.template)
    except KeyError as exc:
        return _payload_invalid_response(str(exc), details={"template": req.template})

    existing = await db.get(Board, board_id)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Board '{board_id}' already exists")

    ws = await db.get(Workspace, workspace_id)
    if ws is not None:
        try:
            await check_board_quota(db, ws)
        except QuotaExceeded as exc:
            return exc.response()

    # Validate owners before mutating
    owner_ids = list(dict.fromkeys(req.owner_actor_ids))  # preserve order, dedupe
    owner_actors: list[Actor] = []
    for owner_id in owner_ids:
        owner = await db.get(Actor, owner_id)
        if not owner:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Owner actor '{owner_id}' not found",
            )
        if owner.actor_type != "human":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"owner_actor_ids must be human actors; '{owner_id}' is '{owner.actor_type}'",
            )
        owner_actors.append(owner)

    board = Board(
        board_id=board_id,
        workspace_id=workspace_id,
        name=req.name,
        description=req.description,
        created_by=current_actor.actor_id,
        kind="standard",
        template=tmpl.name,
        auto_created=False,
    )
    db.add(board)

    for def_col in template_column_dicts(tmpl):
        col_id = f"{board_id}_{def_col['stage']}"
        col = Column(
            column_id=col_id,
            workspace_id=workspace_id,
            board_id=board_id,
            name=def_col["name"],
            stage=def_col["stage"],
            position=def_col["position"],
            wip_limit=def_col["wip_limit"],
        )
        db.add(col)

    # Add creator as initial board member (admin)
    db.add(
        BoardMember(
            board_id=board_id,
            actor_id=current_actor.actor_id,
            role="admin",
        )
    )

    # Enroll human owners as admins (skip creator already enrolled above)
    for owner in owner_actors:
        if owner.actor_id == current_actor.actor_id:
            continue
        db.add(
            BoardMember(
                board_id=board_id,
                actor_id=owner.actor_id,
                role="admin",
            )
        )

    await db.commit()
    await db.refresh(board)
    return _to_board_out(board)


async def _apply_template_columns(
    db: AsyncSession, board: Board, tmpl: BoardTemplate
) -> None:
    """Add missing template stage columns; never delete existing columns."""
    cols = (
        await db.execute(select(Column).where(Column.board_id == board.board_id))
    ).scalars().all()
    present_stages = {c.stage for c in cols}
    for spec in tmpl.columns:
        if spec.stage in present_stages:
            continue
        col_id = f"{board.board_id}_{spec.stage}"
        existing = await db.get(Column, col_id)
        if existing is not None:
            col_id = f"{board.board_id}_{spec.stage}_{uuid.uuid4().hex[:6]}"
        db.add(
            Column(
                column_id=col_id,
                workspace_id=board.workspace_id,
                board_id=board.board_id,
                name=spec.name,
                stage=spec.stage,
                position=spec.position,
                wip_limit=spec.wip_limit,
            )
        )
        present_stages.add(spec.stage)
    board.template = tmpl.name


@router.patch("/{board_id}", response_model=None)
async def update_board(
    board_id: Annotated[str, Depends(_board_path_id)],
    req: BoardUpdate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BoardOut | JSONResponse:
    """Update board name/description/template. Inbox is immutable (SYSTEM_BOARD)."""
    loaded = await _load_board_in_workspace(db, board_id, workspace_id)
    if isinstance(loaded, JSONResponse):
        return loaded
    board = loaded
    if _is_inbox(board):
        return _system_board_response("Cannot modify the system inbox board")

    await _assert_board_admin(board, current_actor, db)

    if req.name is not None:
        board.name = req.name
    if req.description is not None:
        board.description = req.description
    if req.template is not None:
        if req.template == "inbox":
            return _system_board_response("Cannot apply the inbox template to a standard board")
        try:
            tmpl = get_template(req.template)
        except KeyError as exc:
            return _payload_invalid_response(str(exc), details={"template": req.template})
        await _apply_template_columns(db, board, tmpl)

    await db.commit()
    await db.refresh(board)
    return _to_board_out(board)


@router.post("/{board_id}/columns", response_model=None)
async def add_column(
    board_id: Annotated[str, Depends(_board_path_id)],
    req: ColumnCreate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ColumnOut | JSONResponse:
    """Add a custom column to an existing board. Inbox rejects (SYSTEM_BOARD)."""
    loaded = await _load_board_in_workspace(db, board_id, workspace_id)
    if isinstance(loaded, JSONResponse):
        return loaded
    board = loaded
    if _is_inbox(board):
        return _system_board_response("Cannot add columns to the system inbox board")

    await _assert_board_admin(board, current_actor, db)

    # Invariant 7: standard boards only host STANDARD_STAGES (not unclassified).
    if req.stage == Stage.UNCLASSIFIED.value or req.stage not in _STANDARD_STAGE_VALUES:
        return _payload_invalid_response(
            f"Standard boards only allow stages: {', '.join(sorted(_STANDARD_STAGE_VALUES))}",
            details={"stage": req.stage, "allowed": sorted(_STANDARD_STAGE_VALUES)},
        )

    existing_col = await db.get(Column, req.column_id)
    if existing_col:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Column '{req.column_id}' already exists")

    col = Column(
        column_id=req.column_id,
        workspace_id=board.workspace_id,
        board_id=board_id,
        name=req.name,
        stage=req.stage,
        position=req.position,
        wip_limit=req.wip_limit,
    )
    db.add(col)
    await db.commit()
    await db.refresh(col)
    return ColumnOut.model_validate(col)


@router.get("/{board_id}/members", response_model=None)
async def list_board_members(
    board_id: Annotated[str, Depends(_board_path_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
) -> list[ActorOut] | JSONResponse:
    """List all actors enrolled as members of the specified board. Requires Bearer JWT."""
    _ = current_actor
    loaded = await _load_board_in_workspace(db, board_id, workspace_id)
    if isinstance(loaded, JSONResponse):
        return loaded
    board = loaded

    stmt = (
        select(Actor)
        .join(BoardMember, BoardMember.actor_id == Actor.actor_id)
        .where(BoardMember.board_id == board_id)
        .order_by(Actor.actor_type, Actor.name)
    )
    result = await db.execute(stmt)
    members = result.scalars().all()

    # Fallback for unmigrated/legacy boards without explicit members:
    # auto-enroll board creator if present
    if not members and board.created_by:
        creator = await db.get(Actor, board.created_by)
        if creator:
            try:
                auto_mem = BoardMember(
                    board_id=board_id,
                    workspace_id=board.workspace_id,
                    actor_id=creator.actor_id,
                    role="admin",
                )
                db.add(auto_mem)
                await db.commit()
                members = [creator]
            except Exception as exc:
                # Duplicate key / race on concurrent auto-enroll — re-read members.
                from sqlalchemy.exc import IntegrityError

                await db.rollback()
                if not isinstance(exc, IntegrityError):
                    raise
                res = await db.execute(stmt)
                members = res.scalars().all()

    return [_to_actor_out(a) for a in members]


async def _assert_board_admin(board: Board, actor: Actor, db: AsyncSession) -> None:
    """Verify that actor is system admin, board creator, or has admin role on board."""
    from ak5.server.authz import is_admin
    if is_admin(actor) or board.created_by == actor.actor_id:
        return
    stmt = select(BoardMember).where(
        BoardMember.board_id == board.board_id,
        BoardMember.actor_id == actor.actor_id,
        BoardMember.role == "admin",
    )
    res = await db.execute(stmt)
    if not res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Board administrator privileges required",
        )


async def _actor_is_board_admin(board: Board, actor: Actor, db: AsyncSession) -> bool:
    """Return True if actor already has board admin privileges."""
    from ak5.server.authz import is_admin
    if is_admin(actor) or board.created_by == actor.actor_id:
        return True
    stmt = select(BoardMember).where(
        BoardMember.board_id == board.board_id,
        BoardMember.actor_id == actor.actor_id,
        BoardMember.role == "admin",
    )
    res = await db.execute(stmt)
    return res.scalar_one_or_none() is not None


async def _board_has_human_admin(board: Board, db: AsyncSession) -> bool:
    """True if a human already owns admin on this board (creator or member)."""
    if board.created_by:
        creator = await db.get(Actor, board.created_by)
        if creator and creator.actor_type == "human":
            return True
    stmt = (
        select(BoardMember.id)
        .join(Actor, Actor.actor_id == BoardMember.actor_id)
        .where(
            BoardMember.board_id == board.board_id,
            BoardMember.role == "admin",
            Actor.actor_type == "human",
        )
        .limit(1)
    )
    res = await db.execute(stmt)
    return res.scalar_one_or_none() is not None


@router.post("/{board_id}/claim", response_model=None)
async def claim_board(
    board_id: Annotated[str, Depends(_board_path_id)],
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BoardClaimOut | JSONResponse:
    """Claim board admin as a human when no human admin exists yet.

    Preferred path is ``owner_actor_ids`` on ``POST /boards``. This endpoint is
    the escape hatch for agent-created boards that were not given a human owner.
    """
    if current_actor.actor_type != "human":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only human actors can claim a board",
        )

    loaded = await _load_board_in_workspace(db, board_id, workspace_id)
    if isinstance(loaded, JSONResponse):
        return loaded
    board = loaded

    if await _actor_is_board_admin(board, current_actor, db):
        return BoardClaimOut(
            board_id=board_id,
            actor_id=current_actor.actor_id,
            role="admin",
            message="Already board admin",
        )

    if await _board_has_human_admin(board, db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Board already has a human admin; ask them to enroll you",
        )

    stmt = select(BoardMember).where(
        BoardMember.board_id == board_id,
        BoardMember.actor_id == current_actor.actor_id,
    )
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()
    if existing:
        existing.role = "admin"
    else:
        db.add(
            BoardMember(
                board_id=board_id,
                actor_id=current_actor.actor_id,
                role="admin",
            )
        )
    await db.commit()

    return BoardClaimOut(
        board_id=board_id,
        actor_id=current_actor.actor_id,
        role="admin",
        message="Board admin claimed",
    )


@router.post("/{board_id}/members", response_model=None)
async def add_board_member(
    board_id: Annotated[str, Depends(_board_path_id)],
    req: BoardMemberCreate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BoardMemberOut | JSONResponse:
    """Add an actor to a board (board administrator required)."""
    loaded = await _load_board_in_workspace(db, board_id, workspace_id)
    if isinstance(loaded, JSONResponse):
        return loaded
    board = loaded

    await _assert_board_admin(board, current_actor, db)

    target_actor = await db.get(Actor, req.actor_id)
    if not target_actor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Actor '{req.actor_id}' not found")

    stmt = select(BoardMember).where(
        BoardMember.board_id == board_id,
        BoardMember.actor_id == req.actor_id,
    )
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()
    if existing:
        return BoardMemberOut(
            id=existing.id,
            board_id=existing.board_id,
            actor_id=existing.actor_id,
            role=existing.role,
            created_at=existing.created_at,
            actor=_to_actor_out(target_actor),
        )

    new_member = BoardMember(
        board_id=board_id,
        workspace_id=board.workspace_id,
        actor_id=req.actor_id,
        role=req.role or "member",
    )
    db.add(new_member)
    await db.commit()
    await db.refresh(new_member)
    return BoardMemberOut(
        id=new_member.id,
        board_id=new_member.board_id,
        actor_id=new_member.actor_id,
        role=new_member.role,
        created_at=new_member.created_at,
        actor=_to_actor_out(target_actor),
    )


@router.delete("/{board_id}/members/{actor_id}", response_model=None)
async def remove_board_member(
    board_id: Annotated[str, Depends(_board_path_id)],
    actor_id: str,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    workspace_id: Annotated[str, Depends(get_auth_workspace_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict | JSONResponse:
    """Remove an actor from a board (board administrator or self-removal required)."""
    loaded = await _load_board_in_workspace(db, board_id, workspace_id)
    if isinstance(loaded, JSONResponse):
        return loaded
    board = loaded

    if actor_id == board.created_by:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove the creator of the board",
        )

    # Require board admin unless the actor is removing themselves
    if current_actor.actor_id != actor_id:
        await _assert_board_admin(board, current_actor, db)

    stmt = select(BoardMember).where(
        BoardMember.board_id == board_id,
        BoardMember.actor_id == actor_id,
    )
    res = await db.execute(stmt)
    member = res.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found on this board")

    await db.delete(member)
    await db.commit()
    return {"success": True, "board_id": board_id, "actor_id": actor_id}
