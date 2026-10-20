"""#105: `ak5 db` export/import + upgrade/current (deployment.md §1.4)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from ak5.cli.main import cli
from ak5.server import migrations as mig
from ak5.server.dbtool import export_jsonl, import_jsonl
from ak5.server.models.base import Base
from click.testing import CliRunner
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

PASSKEY_BYTES = bytes(range(256))


def _url(tmp_path: Path, name: str) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


async def _seed(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO workspaces (workspace_id, name, policy) VALUES (:i, :n, :p)"),
            [
                {"i": "ws_a", "n": "Alpha", "p": '{"max_auto_boards": 7}'},
                {"i": "ws_b", "n": "Bravo", "p": "{}"},
            ],
        )
        await conn.execute(
            text(
                "INSERT INTO passkey_credentials (passkey_id, user_id, credential_id, public_key,"
                " sign_count, label, created_at, last_used_at)"
                " VALUES ('pk_1', 'usr_1', :cid, :pub, 5, 'Key', :created, NULL)"
            ),
            {"cid": PASSKEY_BYTES, "pub": PASSKEY_BYTES[::-1], "created": datetime(2026, 10, 19, 8, 30, 15, 123456).isoformat(sep=" ")},
        )
        await conn.execute(
            text(
                "INSERT INTO events (seq, event_id, type, workspace_id, data_json)"
                " VALUES (41, 'evt_41', 'TICKET_CREATED', 'ws_a', '{}'), (57, 'evt_57', 'TICKET_CREATED', 'ws_b', '{\"x\": 1}')"
            )
        )


async def _dump_tables(engine: AsyncEngine) -> dict[str, list[tuple]]:
    out: dict[str, list[tuple]] = {}
    async with engine.connect() as conn:
        for name in ("workspaces", "passkey_credentials", "events"):
            table = Base.metadata.tables[name]  # typed read: datetimes compare as datetimes
            rows = (await conn.execute(select(table).order_by(*table.primary_key.columns))).all()
            out[name] = [tuple(r) for r in rows]
    return out


@pytest.mark.asyncio
async def test_export_import_roundtrip_preserves_rows_types_and_event_seq(tmp_path: Path):
    src = create_async_engine(_url(tmp_path, "src.db"), future=True)
    dst = create_async_engine(_url(tmp_path, "dst.db"), future=True)
    dump = tmp_path / "dump.jsonl"
    try:
        await mig.ensure_schema(src)
        await _seed(src)
        counts = await export_jsonl(src, dump)
        assert counts["workspaces"] == 2 and counts["events"] == 2 and counts["passkey_credentials"] == 1

        header = json.loads(dump.read_text(encoding="utf-8").splitlines()[0])
        assert header["format"] == "ak5-db-export"
        assert header["schema_revision"] == mig.head_revision()

        loaded = await import_jsonl(dst, dump)  # creates the schema on the empty target
        assert loaded == counts
        assert await _dump_tables(dst) == await _dump_tables(src)

        async with dst.connect() as conn:
            seqs = [r[0] for r in (await conn.execute(text("SELECT seq FROM events ORDER BY seq"))).all()]
            raw = (await conn.execute(text("SELECT credential_id FROM passkey_credentials"))).scalar_one()
        assert seqs == [41, 57]
        assert bytes(raw) == PASSKEY_BYTES
    finally:
        await src.dispose()
        await dst.dispose()


@pytest.mark.asyncio
async def test_import_refuses_non_empty_target_and_foreign_files(tmp_path: Path):
    src = create_async_engine(_url(tmp_path, "src.db"), future=True)
    dst = create_async_engine(_url(tmp_path, "dst.db"), future=True)
    dump = tmp_path / "dump.jsonl"
    try:
        await mig.ensure_schema(src)
        await _seed(src)
        await export_jsonl(src, dump)

        with pytest.raises(mig.SchemaError, match="must be empty"):
            await import_jsonl(src, dump)  # source still has rows

        junk = tmp_path / "junk.jsonl"
        junk.write_text("not json\n", encoding="utf-8")
        with pytest.raises(mig.SchemaError, match="not an ak5 export"):
            await import_jsonl(dst, junk)

        stale = tmp_path / "stale.jsonl"
        lines = dump.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        header["schema_revision"] = "0000_old"
        stale.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n", encoding="utf-8")
        with pytest.raises(mig.SchemaError, match="Export with the matching ak5"):
            await import_jsonl(dst, stale)

        unknown = tmp_path / "unknown.jsonl"
        unknown.write_text(
            lines[0] + "\n" + json.dumps({"table": "nope", "row": {}}) + "\n", encoding="utf-8"
        )
        with pytest.raises(mig.SchemaError, match="unknown table"):
            await import_jsonl(dst, unknown)
        # the failed import left the target empty
        async with dst.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM workspaces"))).scalar_one() == 0
    finally:
        await src.dispose()
        await dst.dispose()


@pytest.mark.asyncio
async def test_export_requires_a_database_at_head(tmp_path: Path):
    engine = create_async_engine(_url(tmp_path, "empty.db"), future=True)
    try:
        with pytest.raises(mig.SchemaError, match="ak5 db upgrade"):
            await export_jsonl(engine, tmp_path / "x.jsonl")
    finally:
        await engine.dispose()


def test_cli_upgrade_current_export_import(tmp_path: Path):
    runner = CliRunner()
    a, b = _url(tmp_path, "a.db"), _url(tmp_path, "b.db")

    out = runner.invoke(cli, ["db", "current", "--database-url", a])
    assert out.exit_code == 0, out.output
    assert "(none)" in out.output and "needs `ak5 db upgrade`" in out.output

    out = runner.invoke(cli, ["db", "upgrade", "--database-url", a])
    assert out.exit_code == 0, out.output
    assert mig.head_revision() in out.output

    out = runner.invoke(cli, ["db", "current", "--database-url", a])
    assert "up to date" in out.output

    dump = tmp_path / "cli.jsonl"
    out = runner.invoke(cli, ["db", "export", str(dump), "--database-url", a])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["rows"]["workspaces"] == 0

    out = runner.invoke(cli, ["db", "import", str(dump), "--to", b])
    assert out.exit_code == 0, out.output

    bad = runner.invoke(cli, ["db", "import", str(dump), "--to", b])  # b is at head, still empty: fine
    assert bad.exit_code == 0, bad.output

    missing = runner.invoke(cli, ["db", "export", str(tmp_path / "y.jsonl"), "--database-url", _url(tmp_path, "new.db")])
    assert missing.exit_code != 0
    assert "ak5 db upgrade" in missing.output
