"""Issue #158: child rows must inherit parent workspace_id (no silent ws_local default)."""

from __future__ import annotations

import pytest
from ak5.contracts.stages import Role
from ak5.server.auth.principal import Principal
from ak5.server.auth.tokens import issue_access_token
from ak5.server.models.actor import Actor
from ak5.server.models.audit import AuditLog
from ak5.server.models.board import Board
from ak5.server.models.board_member import BoardMember
from ak5.server.models.column import Column
from ak5.server.models.ticket import Ticket, TicketComment
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID
from ak5.server.seed import LOCAL_USER_ID, ensure_local_tenancy
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _headers(workspace_id: str) -> dict[str, str]:
    token = issue_access_token(
        Principal(
            principal_id=LOCAL_USER_ID,
            principal_type="user",
            workspace_id=workspace_id,
            workflow_role=Role.HUMAN,
            membership_role="owner",
        )
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_new_workspace_children_match_parent_workspace(
    client: AsyncClient, test_db: AsyncSession
):
    await ensure_local_tenancy(test_db)
    await test_db.commit()

    created = await client.post(
        "/api/v1/workspaces",
        headers=_headers(LOCAL_WORKSPACE_ID),
        json={"name": "Tenant B"},
    )
    assert created.status_code == 200, created.text
    ws_b = created.json()["workspace_id"]
    inbox_id = created.json()["inbox_board_id"]
    assert ws_b != LOCAL_WORKSPACE_ID

    # Inbox columns must belong to B (issue repro #1).
    cols = (
        await test_db.execute(select(Column).where(Column.board_id == inbox_id))
    ).scalars().all()
    assert cols, "inbox must have columns"
    assert all(c.workspace_id == ws_b for c in cols), [c.workspace_id for c in cols]

    board_resp = await client.post(
        "/api/v1/boards",
        headers=_headers(ws_b),
        json={"name": "b-proj", "template": "default"},
    )
    assert board_resp.status_code == 200, board_resp.text
    board_id = board_resp.json()["board_id"]

    members = (
        await test_db.execute(select(BoardMember).where(BoardMember.board_id == board_id))
    ).scalars().all()
    assert members
    assert all(m.workspace_id == ws_b for m in members), [m.workspace_id for m in members]

    ticket_resp = await client.post(
        "/api/v1/tickets",
        headers=_headers(ws_b),
        json={
            "title": "T1 in B",
            "board_id": board_id,
            "spec": "Do the thing",
            "acceptance_criteria": ["done"],
        },
    )
    assert ticket_resp.status_code == 201, ticket_resp.text
    ticket_id = ticket_resp.json()["ticket_id"]

    comment_resp = await client.post(
        f"/api/v1/tickets/{ticket_id}/comments",
        headers=_headers(ws_b),
        json={"content": "hello from B"},
    )
    assert comment_resp.status_code == 201, comment_resp.text

    ticket = await test_db.get(Ticket, ticket_id)
    assert ticket is not None
    assert ticket.workspace_id == ws_b

    comments = (
        await test_db.execute(select(TicketComment).where(TicketComment.ticket_id == ticket_id))
    ).scalars().all()
    assert comments
    assert all(c.workspace_id == ws_b for c in comments)

    audits = (
        await test_db.execute(
            select(AuditLog).where(
                AuditLog.target_type == "ticket",
                AuditLog.target_id == ticket_id,
            )
        )
    ).scalars().all()
    assert audits, "create_direct should write TRANSITION audit"
    assert all(a.workspace_id == ws_b for a in audits), [a.workspace_id for a in audits]

    # No B-owned board/column/member/ticket/comment/audit should be tagged ws_local.
    for model, fk in (
        (Board, Board.board_id == board_id),
        (Column, Column.board_id == board_id),
        (BoardMember, BoardMember.board_id == board_id),
        (Ticket, Ticket.ticket_id == ticket_id),
        (TicketComment, TicketComment.ticket_id == ticket_id),
        (AuditLog, AuditLog.target_id == ticket_id),
    ):
        rows = (await test_db.execute(select(model).where(fk))).scalars().all()
        leaked = [r for r in rows if r.workspace_id == LOCAL_WORKSPACE_ID]
        assert not leaked, f"{model.__tablename__} leaked ws_local: {leaked}"


@pytest.mark.asyncio
async def test_migration_sql_backfills_mismatched_child_workspace_ids(tmp_path):
    """The 0006 UPDATE statements align child workspace_id to the parent board."""
    from ak5.server.models.base import Base

    db_path = tmp_path / "fix158.db"
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    engine = create_async_engine(url, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        if await session.get(Actor, "user_pm") is None:
            session.add(
                Actor(
                    actor_id="user_pm",
                    actor_type="human",
                    name="PM",
                    role="pm",
                )
            )
        session.add(
            Board(
                board_id="b_parent",
                workspace_id="ws_b",
                name="Parent",
                created_by="user_pm",
                kind="standard",
                template="default",
            )
        )
        session.add(
            Column(
                column_id="b_parent_backlog",
                workspace_id=LOCAL_WORKSPACE_ID,
                board_id="b_parent",
                name="Backlog",
                stage="backlog",
                position=1,
            )
        )
        session.add(
            BoardMember(
                board_id="b_parent",
                workspace_id=LOCAL_WORKSPACE_ID,
                actor_id="user_pm",
                role="admin",
            )
        )
        await session.commit()

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE columns
                SET workspace_id = (
                    SELECT boards.workspace_id FROM boards
                    WHERE boards.board_id = columns.board_id
                )
                WHERE EXISTS (
                    SELECT 1 FROM boards
                    WHERE boards.board_id = columns.board_id
                      AND boards.workspace_id <> columns.workspace_id
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE board_members
                SET workspace_id = (
                    SELECT boards.workspace_id FROM boards
                    WHERE boards.board_id = board_members.board_id
                )
                WHERE EXISTS (
                    SELECT 1 FROM boards
                    WHERE boards.board_id = board_members.board_id
                      AND boards.workspace_id <> board_members.workspace_id
                )
                """
            )
        )

    async with maker() as session:
        col = await session.get(Column, "b_parent_backlog")
        assert col is not None
        assert col.workspace_id == "ws_b"
        mem = (
            await session.execute(
                select(BoardMember).where(BoardMember.board_id == "b_parent")
            )
        ).scalar_one()
        assert mem.workspace_id == "ws_b"

    await engine.dispose()
