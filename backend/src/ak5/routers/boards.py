import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ak5.database import get_db
from ak5.models.actor import Actor
from ak5.models.board import Board
from ak5.models.board_member import BoardMember
from ak5.models.column import Column
from ak5.models.ticket import Ticket
from ak5.routers.actors import _to_actor_out
from ak5.routers.auth import get_current_actor
from ak5.schemas.actor import ActorOut
from ak5.schemas.board import (
    BoardCreate,
    BoardDetailOut,
    BoardOut,
    ColumnWithTicketsOut,
)
from ak5.schemas.column import ColumnCreate, ColumnOut
from ak5.schemas.device_auth import BoardMemberCreate, BoardMemberOut
from ak5.schemas.ticket import TicketOut

router = APIRouter(prefix="/boards", tags=["boards"])

DEFAULT_COLUMNS = [
    {"name": "To Do", "stage": "open", "position": 1, "wip_limit": 0},
    {"name": "In Progress", "stage": "in_progress", "position": 2, "wip_limit": 3},
    {"name": "Review", "stage": "review", "position": 3, "wip_limit": 3},
    {"name": "Done", "stage": "done", "position": 4, "wip_limit": 0},
]


def _to_board_out(board: Board) -> BoardOut:
    return BoardOut(
        board_id=board.board_id,
        name=board.name,
        description=board.description,
        created_by=board.created_by,
        created_at=board.created_at,
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
        subtask_count=count,
        subtask_done_count=done_count,
    )


@router.get("", response_model=list[BoardOut])
async def list_boards(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[BoardOut]:
    """List all boards (summary without columns/tickets)."""
    stmt = select(Board).order_by(Board.created_at.asc(), Board.name.asc())
    result = await db.execute(stmt)
    boards = result.scalars().all()
    return [_to_board_out(b) for b in boards]


@router.get("/{board_id}", response_model=BoardDetailOut)
async def get_board(
    board_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    include_archived: bool = False,
    done_limit: int | None = 10,
) -> BoardDetailOut:
    """Retrieve full board details including columns and tickets ordered by rank.

    - include_archived: If False (default), filters out archived tickets.
    - done_limit: If specified (default 10), limits tickets in 'done' stage columns to the most recent N tickets.
    """
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

    # Compute subtask statistics across the board
    subtask_stats: dict[str, tuple[int, int]] = {}
    for t in board.tickets:
        if t.parent_ticket_id:
            count, done = subtask_stats.get(t.parent_ticket_id, (0, 0))
            is_done = 1 if t.status == "done" else 0
            subtask_stats[t.parent_ticket_id] = (count + 1, done + is_done)

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

        ticket_outs = [_to_ticket_out(t, subtask_stats) for t in sorted_tickets]
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

    return BoardDetailOut(
        board_id=board.board_id,
        name=board.name,
        description=board.description,
        created_by=board.created_by,
        created_at=board.created_at,
        columns=columns_out,
    )


@router.post("", response_model=BoardOut)
async def create_board(
    req: BoardCreate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BoardOut:
    """Create a new board and seed standard columns."""
    board_id = req.board_id or f"board_{uuid.uuid4().hex[:8]}"

    existing = await db.get(Board, board_id)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Board '{board_id}' already exists")

    board = Board(
        board_id=board_id,
        name=req.name,
        description=req.description,
        created_by=current_actor.actor_id,
    )
    db.add(board)

    # Seed default columns
    for def_col in DEFAULT_COLUMNS:
        col_id = f"{board_id}_{def_col['stage']}"
        col = Column(
            column_id=col_id,
            board_id=board_id,
            name=def_col["name"],
            stage=def_col["stage"],
            position=def_col["position"],
            wip_limit=def_col["wip_limit"],
        )
        db.add(col)

    # Add creator as initial board member (admin)
    initial_member = BoardMember(
        board_id=board_id,
        actor_id=current_actor.actor_id,
        role="admin",
    )
    db.add(initial_member)

    await db.commit()
    await db.refresh(board)
    return _to_board_out(board)


@router.post("/{board_id}/columns", response_model=ColumnOut)
async def add_column(
    board_id: str,
    req: ColumnCreate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ColumnOut:
    """Add a custom column to an existing board."""
    board = await db.get(Board, board_id)
    if not board:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Board '{board_id}' not found")

    existing_col = await db.get(Column, req.column_id)
    if existing_col:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Column '{req.column_id}' already exists")

    col = Column(
        column_id=req.column_id,
        board_id=board_id,
        name=req.name,
        stage=req.stage,
        position=req.position,
        wip_limit=req.wip_limit,
    )
    db.add(col)
    await db.commit()
    await db.refresh(col)
    return ColumnOut(
        column_id=col.column_id,
        board_id=col.board_id,
        name=col.name,
        stage=col.stage,
        position=col.position,
        wip_limit=col.wip_limit,
        created_at=col.created_at,
    )


@router.get("/{board_id}/members", response_model=list[ActorOut])
async def list_board_members(
    board_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ActorOut]:
    """List all actors enrolled as members of the specified board."""
    board = await db.get(Board, board_id)
    if not board:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Board '{board_id}' not found")

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
                auto_mem = BoardMember(board_id=board_id, actor_id=creator.actor_id, role="admin")
                db.add(auto_mem)
                await db.commit()
                members = [creator]
            except Exception:
                await db.rollback()
                res = await db.execute(stmt)
                members = res.scalars().all()

    return [_to_actor_out(a) for a in members]


async def _assert_board_admin(board: Board, actor: Actor, db: AsyncSession) -> None:
    """Verify that actor is system admin, board creator, or has admin role on board."""
    from ak5.authz import is_admin
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


@router.post("/{board_id}/members", response_model=BoardMemberOut)
async def add_board_member(
    board_id: str,
    req: BoardMemberCreate,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BoardMemberOut:
    """Add an actor to a board (board administrator required)."""
    board = await db.get(Board, board_id)
    if not board:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Board '{board_id}' not found")

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


@router.delete("/{board_id}/members/{actor_id}")
async def remove_board_member(
    board_id: str,
    actor_id: str,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Remove an actor from a board (board administrator or self-removal required)."""
    board = await db.get(Board, board_id)
    if not board:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Board '{board_id}' not found")

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
