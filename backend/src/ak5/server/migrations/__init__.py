"""Schema management on Alembic (deployment.md §1.2, #105).

* New databases start from the ``0001_baseline`` revision, then ``upgrade head``.
* Databases created before Alembic (no ``alembic_version`` table, tables present) are
  brought to the baseline shape by the frozen legacy path (``create_all`` + the
  ``migrate_sqlite_*`` fixes in ``ak5.server.database``) and then stamped at head.
  SQLite only — a non-SQLite database without ``alembic_version`` is refused.
* Every later schema change is an Alembic revision. ``tests/test_migrations.py`` fails
  when the models drift from ``upgrade head``.
* ``AK5_AUTO_MIGRATE=0`` (serverless) turns startup upgrades off; the server then only
  verifies the database is at head and points to ``ak5 db upgrade``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

BASELINE_REVISION = "0001_baseline"
_ALEMBIC_TABLE = "alembic_version"
_LOCK_TIMEOUT_SECONDS = 120.0
_LOCK_STALE_SECONDS = 600.0
_PG_LOCK_KEY = 0x414B3544  # "AK5D"


class SchemaError(RuntimeError):
    """The database cannot be brought to (or is not at) the expected schema."""


def auto_migrate_enabled() -> bool:
    return (os.environ.get("AK5_AUTO_MIGRATE", "1").strip().lower()) not in ("0", "false", "no", "off")


def alembic_config(connection: Connection | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    assert head is not None
    return head


def current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def has_user_tables(connection: Connection) -> bool:
    names = set(inspect(connection).get_table_names())
    return bool(names - {_ALEMBIC_TABLE})


def upgrade_sync(connection: Connection, target: str = "head") -> None:
    command.upgrade(alembic_config(connection), target)


def stamp_sync(connection: Connection, target: str = "head") -> None:
    command.stamp(alembic_config(connection), target)


@asynccontextmanager
async def migration_lock(engine: AsyncEngine) -> AsyncIterator[None]:
    """One migrator at a time: Postgres advisory lock / SQLite lock file."""
    dialect = engine.dialect.name
    if dialect == "postgresql":
        async with engine.connect() as conn:
            await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _PG_LOCK_KEY})
            try:
                yield
            finally:
                await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _PG_LOCK_KEY})
                await conn.commit()
        return
    database = make_url(str(engine.url)).database if dialect == "sqlite" else None
    if not database or database == ":memory:":
        yield
        return
    lock_path = Path(f"{database}.migrate.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - lock_path.stat().st_mtime > _LOCK_STALE_SECONDS:
                    lock_path.unlink()  # crashed migrator
                    continue
            if time.monotonic() > deadline:
                raise SchemaError(f"Timed out waiting for migration lock {lock_path}") from None
            await asyncio.sleep(0.2)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_path.unlink()


async def ensure_schema(
    engine: AsyncEngine,
    *,
    legacy_upgrade: Callable[[AsyncEngine], Awaitable[None]] | None = None,
) -> str:
    """Bring the database to head and return the revision it ended at.

    ``legacy_upgrade`` upgrades a pre-Alembic SQLite database to the baseline shape.
    """
    async with migration_lock(engine):
        async with engine.connect() as conn:
            revision = await conn.run_sync(current_revision)
            has_tables = await conn.run_sync(has_user_tables)
            await conn.rollback()
        if revision is None and has_tables:
            if engine.dialect.name != "sqlite" or legacy_upgrade is None:
                raise SchemaError(
                    "Database has tables but no alembic_version: it was not created by ak5."
                    " Restore a backup or import a dump with `ak5 db import`."
                )
            logger.info("Pre-Alembic database detected: applying the legacy upgrade, then stamping")
            await legacy_upgrade(engine)
            async with engine.begin() as conn:
                await conn.run_sync(stamp_sync)
        else:
            async with engine.begin() as conn:
                await conn.run_sync(upgrade_sync)
        async with engine.connect() as conn:
            final = await conn.run_sync(current_revision)
            await conn.rollback()
    assert final is not None
    return final


async def assert_schema_current(engine: AsyncEngine) -> None:
    """``AK5_AUTO_MIGRATE=0``: refuse to run against a database that is not at head."""
    async with engine.connect() as conn:
        revision = await conn.run_sync(current_revision)
        await conn.rollback()
    expected = head_revision()
    if revision != expected:
        raise SchemaError(
            f"Database schema is at {revision or 'no revision'}, expected {expected}."
            " Run `ak5 db upgrade` (AK5_AUTO_MIGRATE=0 disables startup migrations)."
        )
