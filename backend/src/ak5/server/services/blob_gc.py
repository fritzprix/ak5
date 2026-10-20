"""Deferred blob cleanup service and sweeper (deployment.md §2, #139)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from ak5.server.blob import BlobStore
from ak5.server.models.blob_gc import BlobGc

logger = logging.getLogger(__name__)


async def record_failed_blob_deletion(
    db: AsyncSession,
    key: str,
    *,
    error: str | None = None,
    workspace_id: str | None = None,
) -> BlobGc:
    """Record a failed blob deletion into the blob_gc queue for deferred cleanup."""
    entry = BlobGc(
        key=key,
        workspace_id=workspace_id,
        attempts=0,
        last_error=error,
    )
    db.add(entry)
    await db.flush()
    return entry


async def sweep_blob_gc(
    db: AsyncSession,
    store: BlobStore,
    *,
    max_attempts: int = 5,
    limit: int = 100,
) -> dict[str, int]:
    """Process pending deferred blob deletions from blob_gc queue."""
    query = (
        select(BlobGc)
        .where(BlobGc.attempts < max_attempts)
        .order_by(BlobGc.id.asc())
        .limit(limit)
    )
    rows = (await db.scalars(query)).all()

    deleted = 0
    failed = 0

    for item in rows:
        try:
            await store.delete(item.key)
            await db.delete(item)
            deleted += 1
        except Exception as exc:
            item.attempts += 1
            item.last_attempt_at = func.now()
            item.last_error = str(exc)
            if item.attempts >= max_attempts:
                logger.error(
                    "Blob GC exceeded max attempts (%d) for key %s: %s (dead-lettered)",
                    max_attempts,
                    item.key,
                    exc,
                )
            else:
                logger.warning(
                    "Blob GC sweep failed for key %s (attempt %d): %s",
                    item.key,
                    item.attempts,
                    exc,
                )
            failed += 1

    await db.flush()
    dead_letters_query = select(func.count()).select_from(BlobGc).where(BlobGc.attempts >= max_attempts)
    dead_letters = int((await db.scalar(dead_letters_query)) or 0)

    await db.commit()
    return {
        "deleted": deleted,
        "failed": failed,
        "total": len(rows),
        "dead_letters": dead_letters,
    }
