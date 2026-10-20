"""``ak5 db`` — schema upgrade and logical export/import (deployment.md §1, #105).

Server code is imported lazily: the base client install has no SQLAlchemy/Alembic.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import click


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _require_server_extra() -> None:
    try:
        import alembic  # noqa: F401
        import sqlalchemy  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on install
        raise click.ClickException(
            "`ak5 db` needs the server extra: pip install 'ak5[server]'"
        ) from exc


def _database_url(option: str | None) -> str:
    if option:
        return option
    from ak5.server.config import settings

    return settings.DATABASE_URL


def _make_engine(url: str):
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(url, future=True)


def _fail(exc: Exception) -> click.ClickException:
    return click.ClickException(str(exc))


@click.group("db")
def db_group() -> None:
    """Database schema (Alembic) and logical export/import."""


_url_option = click.option(
    "--database-url",
    envvar="DATABASE_URL",
    default=None,
    help="Target database (default: the server's DATABASE_URL).",
)


@db_group.command("upgrade")
@_url_option
def upgrade_cmd(database_url: str | None) -> None:
    """Create or upgrade the schema to the latest revision (always runs, ignores AK5_AUTO_MIGRATE)."""
    _require_server_extra()
    from ak5.server.database import run_legacy_schema_upgrade
    from ak5.server.migrations import SchemaError, ensure_schema

    async def go() -> str:
        engine = _make_engine(_database_url(database_url))
        try:
            return await ensure_schema(engine, legacy_upgrade=run_legacy_schema_upgrade)
        finally:
            await engine.dispose()

    try:
        revision = _run(go())
    except SchemaError as exc:
        raise _fail(exc) from exc
    click.echo(f"Schema is at {revision}")


@db_group.command("current")
@_url_option
def current_cmd(database_url: str | None) -> None:
    """Show the database revision and the latest known revision."""
    _require_server_extra()
    from ak5.server.migrations import current_revision, head_revision

    async def go() -> str | None:
        engine = _make_engine(_database_url(database_url))
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(current_revision)
        finally:
            await engine.dispose()

    revision = _run(go())
    head = head_revision()
    state = "up to date" if revision == head else "needs `ak5 db upgrade`"
    click.echo(f"current: {revision or '(none)'}  head: {head}  ({state})")


@db_group.command("export")
@click.argument("out", type=click.Path(dir_okay=False, path_type=Path))
@_url_option
def export_cmd(out: Path, database_url: str | None) -> None:
    """Write the whole database to OUT (JSON Lines)."""
    _require_server_extra()
    from ak5.server.dbtool import export_jsonl
    from ak5.server.migrations import SchemaError

    async def go() -> dict[str, int]:
        engine = _make_engine(_database_url(database_url))
        try:
            return await export_jsonl(engine, out)
        finally:
            await engine.dispose()

    try:
        counts = _run(go())
    except SchemaError as exc:
        raise _fail(exc) from exc
    click.echo(json.dumps({"file": str(out), "rows": counts}, indent=2))


@db_group.command("import")
@click.argument("dump", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--to",
    "database_url",
    envvar="DATABASE_URL",
    default=None,
    help="Target database; it must be empty (default: the server's DATABASE_URL).",
)
def import_cmd(dump: Path, database_url: str | None) -> None:
    """Load an export into an empty database (created at the latest schema if needed)."""
    _require_server_extra()
    from ak5.server.dbtool import import_jsonl
    from ak5.server.migrations import SchemaError

    async def go() -> dict[str, int]:
        engine = _make_engine(_database_url(database_url))
        try:
            return await import_jsonl(engine, dump)
        finally:
            await engine.dispose()

    try:
        counts = _run(go())
    except SchemaError as exc:
        raise _fail(exc) from exc
    click.echo(json.dumps({"file": str(dump), "rows": counts}, indent=2))


@db_group.command("maintenance")
@click.option(
    "--clean-parts-older-than",
    type=float,
    default=3600.0,
    help="Clean stale temporary upload part files older than N seconds (default: 3600).",
)
@click.option(
    "--purge-events/--no-purge-events",
    default=True,
    help="Purge expired events based on workspace retention policy (default: True).",
)
@click.option(
    "--reconcile-usage",
    is_flag=True,
    default=False,
    help="Reconcile workspace_usage attachment_bytes counters against ticket_attachments.",
)
@click.option(
    "--sweep-blob-gc",
    is_flag=True,
    default=False,
    help="Sweep deferred blob_gc deletion queue.",
)
def maintenance_cmd(
    clean_parts_older_than: float,
    purge_events: bool = True,
    sweep_blob_gc: bool = False,
    reconcile_usage: bool = False,
) -> None:
    """Run routine storage and event-log maintenance (ensures database schema before purging)."""
    _require_server_extra()
    from ak5.server.blob import clean_stale_parts
    from ak5.server.config import settings

    cleaned_parts = clean_stale_parts(
        settings.ATTACHMENTS_DIR, older_than_seconds=clean_parts_older_than
    )
    click.echo(f"Cleaned {cleaned_parts} stale attachment part file(s).")

    if reconcile_usage:
        import asyncio

        from ak5.server.database import ensure_database_schema, open_admin_session
        from ak5.server.quota import reconcile_attachment_bytes

        async def _reconcile() -> dict[str, int]:
            await ensure_database_schema()
            async with open_admin_session() as session, session.begin():
                return await reconcile_attachment_bytes(session)

        reconciled = asyncio.run(_reconcile())
        click.echo(f"Reconciled attachment bytes usage for {len(reconciled)} workspace(s).")

    if sweep_blob_gc:
        import asyncio

        from ak5.server.blob import get_blob_store
        from ak5.server.database import ensure_database_schema, open_admin_session
        from ak5.server.services.blob_gc import sweep_blob_gc as run_sweep_blob_gc

        async def _sweep() -> dict[str, int]:
            await ensure_database_schema()
            store = get_blob_store()
            async with open_admin_session() as session:
                return await run_sweep_blob_gc(session, store)

        stats = asyncio.run(_sweep())
        click.echo(
            f"Blob GC sweep finished: {stats['deleted']} deleted, {stats['failed']} failed, {stats.get('dead_letters', 0)} dead-lettered (processed {stats['total']})."
        )

    if purge_events:
        import asyncio

        from ak5.server.database import ensure_database_schema, open_admin_session
        from ak5.server.services.subscription_service import subscription_service

        async def _run_purge():
            await ensure_database_schema()
            async with open_admin_session() as session:
                deleted = await subscription_service.purge_expired_events(session, force=True)
                await session.commit()
                return deleted

        purged_count = asyncio.run(_run_purge())
        click.echo(f"Purged {purged_count} expired event(s).")
