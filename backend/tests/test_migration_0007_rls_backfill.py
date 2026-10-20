"""#161: 0007 workspace_id backfill must bypass FORCE RLS on PostgreSQL."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import create_async_engine


def test_0007_upgrade_enables_bypass_rls_on_postgres():
    """Static guard: migration source must set ak5.bypass_rls before UPDATEs."""
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "ak5"
        / "server"
        / "migrations"
        / "versions"
        / "0007_fix_child_workspace_id.py"
    )
    source = path.read_text(encoding="utf-8")
    assert "set_config('ak5.bypass_rls'" in source
    assert "postgresql" in source


@pytest.mark.asyncio
async def test_live_postgres_0007_backfill_sees_rows_under_force_rls():
    """NOBYPASSRLS role: backfill without bypass is a no-op; with bypass it repairs."""
    pg_url = os.environ.get("AK5_TEST_POSTGRES_URL") or os.environ.get("AK5_POSTGRES_URL")
    if not pg_url:
        pytest.skip("AK5_TEST_POSTGRES_URL is not set; skipping live PostgreSQL RLS test")

    from ak5.server import migrations as mig
    from ak5.server.rls import apply_postgres_rls
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    admin_engine = create_async_engine(pg_url, future=True)
    await mig.ensure_schema(admin_engine)

    app_user = "ak5_mig_nonsuper"
    app_pass = "ak5_mig_password"
    board_id = f"b_mig_{uuid.uuid4().hex[:8]}"
    col_id = f"c_mig_{uuid.uuid4().hex[:8]}"
    ws_real = f"ws_mig_{uuid.uuid4().hex[:8]}"
    ws_wrong = "ws_local"

    async with admin_engine.begin() as conn:
        await conn.run_sync(apply_postgres_rls)
        await conn.execute(
            text(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{app_user}') THEN
                        CREATE ROLE {app_user} LOGIN PASSWORD '{app_pass}'
                            NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
                    END IF;
                    GRANT ALL PRIVILEGES ON SCHEMA public TO {app_user};
                    GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {app_user};
                    GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {app_user};
                END $$;
            """)
        )
        await conn.execute(text("SELECT set_config('ak5.bypass_rls', 'on', true)"))
        await conn.execute(
            text(
                "INSERT INTO workspaces (workspace_id, name, policy) VALUES (:id, 'mig', '{}') "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": ws_real},
        )
        await conn.execute(
            text(
                "INSERT INTO actors (actor_id, actor_type, name, role, capabilities, status) "
                "VALUES ('user_pm', 'human', 'PM', 'pm', '[]', 'idle') "
                "ON CONFLICT DO NOTHING"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO boards (board_id, workspace_id, name, created_by, kind, template, auto_created) "
                "VALUES (:bid, :ws, 'mig', 'user_pm', 'standard', 'default', false)"
            ),
            {"bid": board_id, "ws": ws_real},
        )
        await conn.execute(
            text(
                "INSERT INTO columns (column_id, workspace_id, board_id, name, stage, position, wip_limit) "
                "VALUES (:cid, :ws_wrong, :bid, 'Backlog', 'backlog', 1, 0)"
            ),
            {"cid": col_id, "ws_wrong": ws_wrong, "bid": board_id},
        )

    parsed = make_url(pg_url)
    app_url = parsed.set(username=app_user, password=app_pass).render_as_string(
        hide_password=False
    )
    if app_url.startswith("postgresql://"):
        app_url = "postgresql+asyncpg://" + app_url[len("postgresql://") :]
    elif app_url.startswith("postgres://"):
        app_url = "postgresql+asyncpg://" + app_url[len("postgres://") :]

    app_engine = create_async_engine(app_url, future=True)
    backfill_sql = text(
        """
        UPDATE columns
        SET workspace_id = (
            SELECT boards.workspace_id FROM boards WHERE boards.board_id = columns.board_id
        )
        WHERE columns.column_id = :cid
          AND EXISTS (
            SELECT 1 FROM boards
            WHERE boards.board_id = columns.board_id
              AND boards.workspace_id <> columns.workspace_id
          )
        """
    )

    async with app_engine.begin() as conn:
        await conn.execute(backfill_sql, {"cid": col_id})
    async with admin_engine.begin() as conn:
        await conn.execute(text("SELECT set_config('ak5.bypass_rls', 'on', true)"))
        wrong = (
            await conn.execute(
                text("SELECT workspace_id FROM columns WHERE column_id = :cid"),
                {"cid": col_id},
            )
        ).scalar_one()
        assert wrong == ws_wrong

    async with app_engine.begin() as conn:
        await conn.execute(text("SELECT set_config('ak5.bypass_rls', 'on', true)"))
        await conn.execute(backfill_sql, {"cid": col_id})
    async with admin_engine.begin() as conn:
        await conn.execute(text("SELECT set_config('ak5.bypass_rls', 'on', true)"))
        fixed = (
            await conn.execute(
                text("SELECT workspace_id FROM columns WHERE column_id = :cid"),
                {"cid": col_id},
            )
        ).scalar_one()
        assert fixed == ws_real
        await conn.execute(text("DELETE FROM columns WHERE column_id = :cid"), {"cid": col_id})
        await conn.execute(text("DELETE FROM boards WHERE board_id = :bid"), {"bid": board_id})
        await conn.execute(
            text("DELETE FROM workspaces WHERE workspace_id = :ws"), {"ws": ws_real}
        )

    await app_engine.dispose()
    await admin_engine.dispose()
