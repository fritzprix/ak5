"""libpq-style DATABASE_URL params vs asyncpg (Neon ``sslmode`` / ``channel_binding``)."""

from __future__ import annotations

import inspect
import logging
import ssl
from pathlib import Path
from typing import Any

import pytest
from ak5.server.database import get_engine_options
from ak5.server.db_url import asyncpg_url_and_connect_args
from sqlalchemy.engine import make_url

NEON = "postgresql://app:p%40ss@ep-x-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"


def test_neon_url_moves_sslmode_and_drops_channel_binding() -> None:
    url, connect_args = asyncpg_url_and_connect_args(NEON)
    parsed = make_url(url)
    assert parsed.drivername == "postgresql+asyncpg"
    assert dict(parsed.query) == {}
    assert parsed.password == "p@ss"
    assert parsed.host == "ep-x-pooler.ap-southeast-1.aws.neon.tech"
    assert connect_args == {"ssl": "require"}


def test_other_query_params_are_kept() -> None:
    url, connect_args = asyncpg_url_and_connect_args("postgres://u:p@h/db?sslmode=prefer&application_name=ak5")
    assert dict(make_url(url).query) == {"application_name": "ak5"}
    assert connect_args == {"ssl": "prefer"}


def test_verify_full_uses_system_store_with_hostname_check(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="ak5.server.db_url")
    url, connect_args = asyncpg_url_and_connect_args("postgresql://u:p@h/db?sslmode=verify-full&sslrootcert=system")
    assert dict(make_url(url).query) == {}
    context = connect_args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert caplog.records == []


def test_verify_ca_loads_sslrootcert_file() -> None:
    certifi = pytest.importorskip("certifi")
    _, connect_args = asyncpg_url_and_connect_args(f"postgresql://u:p@h/db?sslmode=verify-ca&sslrootcert={certifi.where()}")
    context = connect_args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is False
    assert context.get_ca_certs()


def test_missing_sslrootcert_file_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing-ca.pem"
    with pytest.raises(FileNotFoundError):
        asyncpg_url_and_connect_args(f"postgresql://u:p@h/db?sslmode=verify-full&sslrootcert={missing}")


def test_explicit_ssl_wins_over_sslmode() -> None:
    url, connect_args = asyncpg_url_and_connect_args("postgresql+asyncpg://u:p@h/db?ssl=disable&sslmode=require")
    assert dict(make_url(url).query) == {"ssl": "disable"}
    assert connect_args == {}


def test_plain_urls_are_unchanged() -> None:
    assert asyncpg_url_and_connect_args("postgresql://u:p@h:5432/db") == ("postgresql+asyncpg://u:p@h:5432/db", {})
    assert asyncpg_url_and_connect_args("sqlite+aiosqlite:///x.db?sslmode=require") == (
        "sqlite+aiosqlite:///x.db?sslmode=require",
        {},
    )


def test_get_engine_options_carries_ssl_connect_arg() -> None:
    url, connect_args, engine_kwargs = get_engine_options(NEON)
    assert "sslmode" not in url and "channel_binding" not in url
    assert connect_args == {"ssl": "require"}
    assert "pool_size" in engine_kwargs


def test_dropped_params_are_logged_without_secrets(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="ak5.server.db_url")
    asyncpg_url_and_connect_args(NEON + "&sslrootcert=/etc/ssl/neon-ca.pem")
    messages = [r.getMessage() for r in caplog.records]
    assert any("'channel_binding'" in m for m in messages)
    assert any("'sslrootcert'" in m for m in messages)
    joined = "\n".join(messages)
    assert "p@ss" not in joined and "p%40ss" not in joined
    assert "neon.tech" not in joined and "neon-ca.pem" not in joined


def test_plain_sslmode_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="ak5.server.db_url")
    asyncpg_url_and_connect_args("postgresql://u:p@h/db?sslmode=require")
    assert caplog.records == []


def test_ak5_db_engine_matches_server_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlalchemy.ext.asyncio
    from ak5.cli.commands.db import _make_engine

    captured: dict[str, Any] = {}

    def fake_create_async_engine(url: str, **kwargs: Any) -> str:
        captured["url"] = url
        captured.update(kwargs)
        return "engine"

    monkeypatch.setattr(sqlalchemy.ext.asyncio, "create_async_engine", fake_create_async_engine)
    assert _make_engine(NEON) == "engine"

    server_url, server_connect_args, _ = get_engine_options(NEON)
    assert captured["url"] == server_url
    assert captured["connect_args"] == server_connect_args == {"ssl": "require"}


def test_asyncpg_accepts_every_connect_kwarg() -> None:
    asyncpg = pytest.importorskip("asyncpg")
    from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg

    url, connect_args = asyncpg_url_and_connect_args(NEON)
    _, kwargs = PGDialect_asyncpg().create_connect_args(make_url(url))
    accepted = set(inspect.signature(asyncpg.connect).parameters)
    assert set(kwargs) | set(connect_args) <= accepted
