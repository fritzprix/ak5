"""DATABASE_URL normalization for SQLAlchemy async drivers (deployment.md §1.1).

Side-effect free so both the server engine and ``ak5 db`` can share it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import make_url

ASYNCPG_SCHEME = "postgresql+asyncpg"

# libpq query parameters that hosted providers (Neon, Supabase, ...) put in
# connection strings but asyncpg.connect() rejects as unknown keyword arguments.
# asyncpg has no channel-binding support and resolves CAs itself, so these can only be dropped.
_UNSUPPORTED_LIBPQ_PARAMS = ("channel_binding", "sslrootcert")


def normalize_database_url(url_str: str) -> str:
    """Map ``postgres://`` / ``postgresql://`` to the asyncpg driver scheme."""
    if url_str.startswith("postgresql://"):
        return f"{ASYNCPG_SCHEME}://" + url_str[len("postgresql://") :]
    if url_str.startswith("postgres://"):
        return f"{ASYNCPG_SCHEME}://" + url_str[len("postgres://") :]
    return url_str


def asyncpg_url_and_connect_args(url_str: str) -> tuple[str, dict[str, Any]]:
    """Normalize ``url_str`` and move libpq-only query params out of an asyncpg URL.

    ``sslmode`` becomes asyncpg's ``ssl`` connect argument (it accepts the same
    mode names). Non-asyncpg URLs are only scheme-normalized.
    """
    normalized = normalize_database_url(url_str)
    if not normalized.startswith(f"{ASYNCPG_SCHEME}://"):
        return normalized, {}

    url = make_url(normalized)
    query = dict(url.query)
    if "sslmode" not in query and not any(key in query for key in _UNSUPPORTED_LIBPQ_PARAMS):
        return normalized, {}

    connect_args: dict[str, Any] = {}
    sslmode = query.pop("sslmode", None)
    if sslmode is not None and "ssl" not in query:
        connect_args["ssl"] = sslmode if isinstance(sslmode, str) else sslmode[-1]
    for key in _UNSUPPORTED_LIBPQ_PARAMS:
        query.pop(key, None)
    return url.set(query=query).render_as_string(hide_password=False), connect_args
