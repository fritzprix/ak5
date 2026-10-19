"""Logical export / import of the whole database (deployment.md §1.4, #105).

JSON Lines: a header line, then one ``{"table": …, "row": {…}}`` line per row in foreign-key
order. This is how an existing SQLite file is moved onto the v2 schema (or onto Postgres)
once — it is not an online migration and the target must be empty.
"""

from __future__ import annotations

import base64
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Column, DateTime, LargeBinary, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

import ak5.server.models  # noqa: F401  (registers every table on Base.metadata)
from ak5.server.migrations import (
    SchemaError,
    current_revision,
    ensure_schema,
    head_revision,
)
from ak5.server.models.base import Base

FORMAT = "ak5-db-export"
FORMAT_VERSION = 1
_BATCH = 500
_SKIP_TABLES = frozenset({"alembic_version"})


def _tables():
    return [t for t in Base.metadata.sorted_tables if t.name not in _SKIP_TABLES]


def _encode(value: Any, column: Column) -> Any:
    if value is None:
        return None
    if isinstance(column.type, DateTime):
        return value.isoformat()
    if isinstance(column.type, LargeBinary):
        return base64.b64encode(value).decode("ascii")
    return value


def _decode(value: Any, column: Column) -> Any:
    if value is None:
        return None
    if isinstance(column.type, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column.type, LargeBinary):
        return base64.b64decode(value)
    return value


async def export_jsonl(engine: AsyncEngine, path: Path | str) -> dict[str, int]:
    """Write every table to ``path``; returns rows per table. The schema must be at head."""
    counts: dict[str, int] = {}
    async with engine.connect() as conn:
        revision = await conn.run_sync(current_revision)
        if revision != head_revision():
            raise SchemaError(
                f"Database is at {revision or 'no revision'}, export needs {head_revision()}:"
                " run `ak5 db upgrade` first."
            )
        header = {
            "format": FORMAT,
            "version": FORMAT_VERSION,
            "schema_revision": revision,
            "created_at": datetime.now(UTC).isoformat(),
        }
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(header) + "\n")
            for table in _tables():
                order = [table.c[c.name] for c in table.primary_key.columns]
                stmt = select(table).order_by(*order)
                result = await conn.stream(stmt)
                count = 0
                async for row in result:
                    record = {
                        col.name: _encode(row._mapping[col.name], col) for col in table.columns
                    }
                    fh.write(json.dumps({"table": table.name, "row": record}) + "\n")
                    count += 1
                counts[table.name] = count
        await conn.rollback()
    return counts


async def _row_counts(engine: AsyncEngine) -> Counter[str]:
    counts: Counter[str] = Counter()
    async with engine.connect() as conn:
        for table in _tables():
            counts[table.name] = (await conn.execute(select(func.count()).select_from(table))).scalar_one()
        await conn.rollback()
    return counts


async def import_jsonl(engine: AsyncEngine, path: Path | str) -> dict[str, int]:
    """Load a dump into an *empty* database (created/upgraded to head if needed)."""
    path = Path(path)
    with open(path, encoding="utf-8") as fh:
        try:
            header = json.loads(fh.readline())
        except json.JSONDecodeError as exc:
            raise SchemaError(f"{path} is not an ak5 export (bad header)") from exc
    if header.get("format") != FORMAT or header.get("version") != FORMAT_VERSION:
        raise SchemaError(f"{path} is not an ak5 export (format/version mismatch)")
    if header.get("schema_revision") != head_revision():
        raise SchemaError(
            f"Dump was made at schema {header.get('schema_revision')}, this ak5 expects"
            f" {head_revision()}. Export with the matching ak5 version."
        )

    await ensure_schema(engine)
    non_empty = {name: n for name, n in (await _row_counts(engine)).items() if n}
    if non_empty:
        raise SchemaError(
            "Import target must be empty; found rows in: " + ", ".join(sorted(non_empty))
        )

    by_name = {t.name: t for t in _tables()}
    loaded: dict[str, int] = {name: 0 for name in by_name}
    async with engine.begin() as conn:
        batch: list[dict[str, Any]] = []
        current = None

        async def flush() -> None:
            nonlocal batch
            if batch and current is not None:
                await conn.execute(insert(current), batch)
                loaded[current.name] += len(batch)
            batch = []

        with open(path, encoding="utf-8") as fh:
            fh.readline()  # header
            for line_no, line in enumerate(fh, start=2):
                if not line.strip():
                    continue
                record = json.loads(line)
                table = by_name.get(record["table"])
                if table is None:
                    raise SchemaError(f"{path}:{line_no}: unknown table {record['table']!r}")
                if table is not current or len(batch) >= _BATCH:
                    await flush()
                    current = table
                batch.append({col.name: _decode(record["row"].get(col.name), col) for col in table.columns})
            await flush()

        if conn.dialect.name == "postgresql":
            for table in _tables():
                col = table.autoincrement_column
                if col is None:
                    continue
                await conn.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', '{col.name}'),"
                        f" COALESCE((SELECT MAX({col.name}) FROM {table.name}), 1))"
                    )
                )
    return loaded
