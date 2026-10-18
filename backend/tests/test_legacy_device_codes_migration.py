"""#35: the v1 ``device_codes`` table is dropped on startup migration."""

from __future__ import annotations

import pytest
from ak5.server.database import run_sqlite_schema_migrations
from ak5.server.models.base import Base
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_startup_migration_drops_legacy_device_codes(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'v.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("CREATE TABLE device_codes (device_code VARCHAR(128) PRIMARY KEY, user_code VARCHAR(32))")
        )
        await conn.execute(text("INSERT INTO device_codes VALUES ('d1', 'AK5-ABCD')"))

    await run_sqlite_schema_migrations(engine)
    await run_sqlite_schema_migrations(engine)  # idempotent

    async with engine.connect() as conn:
        names = {
            r[0]
            for r in (
                await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            ).fetchall()
        }
    await engine.dispose()
    assert "device_codes" not in names
    assert "oauth_device_codes" in names
