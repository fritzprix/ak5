from typing import Annotated
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ak5.database import get_db
from ak5.models.actor import Actor
from ak5.models.board import Board
from ak5.models.column import Column
from ak5.models.ticket import Ticket
from ak5.routers.auth import get_current_actor
from ak5.schemas.board import BoardCreate, BoardDetailOut, BoardOut, ColumnWithTicketsOut
from ak5.schemas.column import ColumnCreate, ColumnOut
from ak5.schemas.ticket import TicketOut

router = APIRouter(prefix="/boards", tags=["boards"])

DEFAULT_COLUMNS = [
    {"name": "To Do", "stage": "open", "position": 1, "wip_limit": 0},
    {"name": "In Progress", "stage": "in_progress", "position": 2, "wip_limit": 3},
    {"name": "Review", "stage": "review", "position": 3, "wip_limit": 3},
    {"name": "Done", "stage": "done", "position": 4, "wip_limit": 0},
]


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
        created_at=t.created_at,
        updated_at=t.updated_at,
        subtask_count=count,
        subtask_done_count=done_count,
    )


@router.get("/{board_id}", response_model=BoardDetailOut)
async def get_board(
    board_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BoardDetailOut:
    """Retrieve full board details including columns and tickets ordered by rank."""
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
        sorted_tickets = sorted(col.tickets, key=lambda t: t.rank)
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

    await db.commit()
    await db.refresh(board)
    return BoardOut(
        board_id=board.board_id,
        name=board.name,
        description=board.description,
        created_by=board.created_by,
        created_at=board.created_at,
    )


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
