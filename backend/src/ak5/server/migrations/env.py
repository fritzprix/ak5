"""Alembic environment (deployment.md §1.2, #105).

Migrations always run on a connection handed in by ``ak5.server.migrations`` (the app's
own engine), never from an ``alembic.ini`` URL, so the CLI, server startup and tests all
migrate exactly the database the server uses.
"""

from __future__ import annotations

from alembic import context

import ak5.server.models  # noqa: F401  (registers every table on Base.metadata)
from ak5.server.models.base import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError(
            "ak5 migrations need a connection: use ak5.server.migrations.upgrade_sync()"
            " or `ak5 db upgrade`"
        )
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline (--sql) migrations are not supported")
run_migrations_online()
