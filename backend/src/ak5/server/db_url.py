"""DATABASE_URL normalization for SQLAlchemy async drivers (deployment.md §1.1).

No I/O beyond loading CA certificates for ``verify-ca`` / ``verify-full`` URLs, so both
the server engine and ``ak5 db`` can share it.
"""

from __future__ import annotations

import logging
import ssl
from typing import Any

from sqlalchemy.engine import make_url

logger = logging.getLogger(__name__)

ASYNCPG_SCHEME = "postgresql+asyncpg"

_VERIFYING_SSLMODES = ("verify-ca", "verify-full")

# libpq query parameters that hosted providers (Neon, Supabase, ...) put in
# connection strings but asyncpg.connect() rejects as unknown keyword arguments.
# Dropping them weakens what the URL asked for, so each drop is logged.
_CHANNEL_BINDING_DROPPED = "asyncpg has no SCRAM channel binding; the connection is made without it"
_SSLROOTCERT_DROPPED = "only verify-ca / verify-full check certificates; this CA file is not used"


def normalize_database_url(url_str: str) -> str:
    """Map ``postgres://`` / ``postgresql://`` to the asyncpg driver scheme."""
    if url_str.startswith("postgresql://"):
        return f"{ASYNCPG_SCHEME}://" + url_str[len("postgresql://") :]
    if url_str.startswith("postgres://"):
        return f"{ASYNCPG_SCHEME}://" + url_str[len("postgres://") :]
    return url_str


def _query_value(value: str | tuple[str, ...]) -> str:
    return value if isinstance(value, str) else value[-1]


def _verifying_context(sslmode: str, sslrootcert: str | None) -> ssl.SSLContext:
    """libpq semantics: ``sslrootcert=<file>`` or ``system`` (also the default here)."""
    cafile = None if sslrootcert in (None, "system") else sslrootcert
    context = ssl.create_default_context(cafile=cafile)
    context.check_hostname = sslmode == "verify-full"
    return context


def _drop(key: str, consequence: str) -> None:
    logger.warning("Database URL parameter %r is not supported by asyncpg and was dropped: %s", key, consequence)


def asyncpg_url_and_connect_args(url_str: str) -> tuple[str, dict[str, Any]]:
    """Normalize ``url_str`` and move libpq-only query params out of an asyncpg URL.

    ``sslmode`` becomes asyncpg's ``ssl`` connect argument. ``verify-ca`` /
    ``verify-full`` get an ``SSLContext`` built from ``sslrootcert`` (a file, or
    ``system`` / unset for the system store), because asyncpg itself would only
    look at ``~/.postgresql/root.crt``. ``channel_binding`` (and ``sslrootcert``
    under weaker modes) is dropped with a warning naming the parameter only.
    Non-asyncpg URLs are only scheme-normalized.
    """
    normalized = normalize_database_url(url_str)
    if not normalized.startswith(f"{ASYNCPG_SCHEME}://"):
        return normalized, {}

    url = make_url(normalized)
    query = dict(url.query)
    if not {"sslmode", "sslrootcert", "channel_binding"} & query.keys():
        return normalized, {}

    connect_args: dict[str, Any] = {}
    raw_sslmode = query.pop("sslmode", None)
    raw_sslrootcert = query.pop("sslrootcert", None)
    sslmode = _query_value(raw_sslmode).lower() if raw_sslmode is not None else None
    sslrootcert = _query_value(raw_sslrootcert) if raw_sslrootcert is not None else None

    if sslmode is not None and "ssl" not in query:
        if sslmode in _VERIFYING_SSLMODES:
            connect_args["ssl"] = _verifying_context(sslmode, sslrootcert)
            sslrootcert = None
        else:
            connect_args["ssl"] = sslmode
    if sslrootcert is not None:
        _drop("sslrootcert", _SSLROOTCERT_DROPPED)
    if query.pop("channel_binding", None) is not None:
        _drop("channel_binding", _CHANNEL_BINDING_DROPPED)
    return url.set(query=query).render_as_string(hide_password=False), connect_args
