"""libpq-style DATABASE_URL params vs asyncpg (Neon ``sslmode`` / ``channel_binding``)."""

from __future__ import annotations

import inspect

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
    url, connect_args = asyncpg_url_and_connect_args("postgres://u:p@h/db?sslmode=verify-full&application_name=ak5")
    assert dict(make_url(url).query) == {"application_name": "ak5"}
    assert connect_args == {"ssl": "verify-full"}


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


def test_asyncpg_accepts_every_connect_kwarg() -> None:
    asyncpg = pytest.importorskip("asyncpg")
    from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg

    url, connect_args = asyncpg_url_and_connect_args(NEON)
    _, kwargs = PGDialect_asyncpg().create_connect_args(make_url(url))
    accepted = set(inspect.signature(asyncpg.connect).parameters)
    assert set(kwargs) | set(connect_args) <= accepted
