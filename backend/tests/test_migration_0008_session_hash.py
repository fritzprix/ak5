"""0008: web session ``cookie:csrf`` digests (129 chars) fit ``user_sessions`` on PostgreSQL."""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import pytest
from ak5.server import migrations as mig
from ak5.server.auth.sessions import _hash_token
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

PREVIOUS = "0007_fix_child_workspace_id"
CURRENT = "0008_widen_session_token_hash"


def _pg_url() -> str:
    url = os.environ.get("AK5_TEST_POSTGRES_URL") or os.environ.get("AK5_POSTGRES_URL")
    if not url:
        pytest.skip("AK5_TEST_POSTGRES_URL is not set; skipping live PostgreSQL test")
    return url


def _downgrade_to_previous(connection: Connection) -> None:
    from alembic import command

    command.downgrade(mig.alembic_config(connection), PREVIOUS)


async def _hash_column_length(conn: AsyncConnection) -> int:
    return (
        await conn.execute(
            text(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_name = 'user_sessions' AND column_name = 'refresh_token_hash'"
            )
        )
    ).scalar_one()


async def _insert_session(conn: AsyncConnection, session_id: str, token_hash: str, client: str) -> None:
    await conn.execute(
        text(
            "INSERT INTO user_sessions (session_id, user_id, refresh_token_hash, client, expires_at) "
            "VALUES (:sid, 'user_0008', :hash, :client, :exp)"
        ),
        {"sid": session_id, "hash": token_hash, "client": client, "exp": datetime(2099, 1, 1)},
    )


@pytest.mark.asyncio
async def test_live_postgres_web_session_hash_and_downgrade():
    engine = create_async_engine(_pg_url(), future=True)
    web_sid = f"sess_web_{uuid.uuid4().hex[:8]}"
    cli_sid = f"sess_cli_{uuid.uuid4().hex[:8]}"
    web_hash = f"{_hash_token('cookie')}:{_hash_token('csrf')}"
    cli_hash = _hash_token("refresh")
    assert len(web_hash) == 129

    try:
        assert await mig.ensure_schema(engine) == CURRENT
        async with engine.begin() as conn:
            assert await _hash_column_length(conn) == 256
            await _insert_session(conn, web_sid, web_hash, "web")
            await _insert_session(conn, cli_sid, cli_hash, "cli")

        async with engine.begin() as conn:
            await conn.run_sync(_downgrade_to_previous)
        async with engine.begin() as conn:
            assert await _hash_column_length(conn) == 128
            remaining = set(
                (
                    await conn.execute(
                        text("SELECT session_id FROM user_sessions WHERE session_id IN (:w, :c)"),
                        {"w": web_sid, "c": cli_sid},
                    )
                ).scalars()
            )
            assert remaining == {cli_sid}
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(mig.upgrade_sync)
            await conn.execute(
                text("DELETE FROM user_sessions WHERE session_id IN (:w, :c)"), {"w": web_sid, "c": cli_sid}
            )
        async with engine.connect() as conn:
            assert await _hash_column_length(conn) == 256
        await engine.dispose()
