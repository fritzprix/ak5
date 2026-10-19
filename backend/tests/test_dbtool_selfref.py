"""#105 review: import must cope with self-referential FKs regardless of row order."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from ak5.server import migrations as mig
from ak5.server.dbtool import export_jsonl, import_jsonl
from ak5.server.models.base import Base
from sqlalchemy import Boolean, DateTime, Integer, event, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

NOW = datetime(2026, 10, 19, 9, 0, 0)


def _row(table_name: str, **overrides: Any) -> dict[str, Any]:
    """Minimal valid row: fill NOT NULL columns that have no default."""
    table = Base.metadata.tables[table_name]
    row: dict[str, Any] = {}
    for col in table.columns:
        if col.name in overrides:
            row[col.name] = overrides[col.name]
        elif col.nullable or col.default is not None or col.server_default is not None:
            continue
        elif isinstance(col.type, DateTime):
            row[col.name] = NOW
        elif isinstance(col.type, Boolean):
            row[col.name] = False
        elif isinstance(col.type, Integer):
            row[col.name] = 0
        else:
            row[col.name] = "x"
    return row


def _engine(tmp_path: Path, name: str, *, foreign_keys: bool) -> AsyncEngine:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}", future=True)
    if foreign_keys:

        @event.listens_for(engine.sync_engine, "connect")
        def _fk_on(dbapi_connection, _record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


async def _insert(engine: AsyncEngine, table_name: str, rows: list[dict[str, Any]]) -> None:
    table = Base.metadata.tables[table_name]
    async with engine.begin() as conn:
        await conn.execute(table.insert(), rows)


@pytest.mark.asyncio
async def test_import_links_parents_that_sort_after_their_children(tmp_path: Path):
    src = _engine(tmp_path, "src.db", foreign_keys=False)
    dst = _engine(tmp_path, "dst.db", foreign_keys=True)  # immediate FK checks, like Postgres
    dump = tmp_path / "dump.jsonl"
    try:
        await mig.ensure_schema(src)
        await _insert(src, "actors", [_row("actors", actor_id="usr_1", actor_type="human", status="idle")])
        await _insert(src, "workspaces", [_row("workspaces", workspace_id="ws_1")])
        await _insert(src, "boards", [_row("boards", board_id="b1", kind="standard", created_by="usr_1")])
        await _insert(src, "columns", [_row("columns", column_id="c1", board_id="b1", stage="backlog")])
        common = {"board_id": "b1", "column_id": "c1", "workspace_id": "ws_1", "created_by": "usr_1", "priority": "low"}
        # "t_a" sorts before "t_z" but its parent is "t_z"; t_m is a child of t_a (a chain).
        await _insert(
            src,
            "tickets",
            [
                _row("tickets", ticket_id="t_a", parent_ticket_id="t_z", **common),
                _row("tickets", ticket_id="t_m", parent_ticket_id="t_a", **common),
                _row("tickets", ticket_id="t_z", parent_ticket_id=None, **common),
            ],
        )
        counts = await export_jsonl(src, dump)
        assert counts["tickets"] == 3
        # the dump really has the child before its parent
        order = [line for line in dump.read_text(encoding="utf-8").splitlines() if '"table": "tickets"' in line]
        assert order[0].find('"ticket_id": "t_a"') != -1 and order[-1].find('"ticket_id": "t_z"') != -1

        await import_jsonl(dst, dump)

        async with dst.connect() as conn:
            tickets = Base.metadata.tables["tickets"]
            rows = (await conn.execute(select(tickets.c.ticket_id, tickets.c.parent_ticket_id).order_by(tickets.c.ticket_id))).all()
            assert [tuple(r) for r in rows] == [("t_a", "t_z"), ("t_m", "t_a"), ("t_z", None)]
            assert (await conn.execute(text("PRAGMA foreign_key_check"))).fetchall() == []
    finally:
        await src.dispose()
        await dst.dispose()
