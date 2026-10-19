"""Attachment blob storage abstraction (deployment.md §2, #104).

``BlobStore`` is the only thing the API talks to for attachment bytes. Only the
``fs`` backend exists today; ``s3`` / ``vercel-blob`` land with #109 behind the
same interface. Authorization stays in the API layer (#75): a backend never
decides who may read a key.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

from ak5.server.config import settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
_KEY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class BlobError(Exception):
    """Storage failure that is not the caller's fault."""


class BlobTooLargeError(BlobError):
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        super().__init__(f"Blob exceeds maximum allowed size ({max_bytes} bytes)")


class BlobNotFoundError(BlobError):
    pass


def attachment_key(workspace_id: str, ticket_id: str, attachment_id: str) -> str:
    """``ws/<workspace_id>/<ticket_id>/<attachment_id>`` — never contains user filenames."""
    parts = (workspace_id, ticket_id, attachment_id)
    for part in parts:
        if not _KEY_PART.match(part) or ".." in part:
            raise ValueError(f"Unsafe blob key component: {part!r}")
    return "ws/" + "/".join(parts)


class BlobStore(Protocol):
    async def put(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        content_type: str,
        max_bytes: int,
    ) -> int:
        """Store ``chunks`` under ``key``; return the byte count.

        Raises :class:`BlobTooLargeError` (nothing is kept) when ``max_bytes`` is exceeded.
        """

    def open(self, key: str) -> AsyncIterator[bytes]:
        """Stream the blob. Raises :class:`BlobNotFoundError` when it is missing."""

    async def delete(self, key: str) -> None:
        """Remove the blob; a missing blob is not an error."""

    async def exists(self, key: str) -> bool: ...

    def local_path(self, key: str) -> Path | None:
        """Filesystem path when the blob is a local file (enables range/sendfile), else ``None``."""


class FsBlobStore:
    """Files under ``root``.

    ``key`` is a relative path (``ws/…``). Rows written before #104 hold an absolute
    path to a file directly inside ``root``; those are still readable, but only when
    they resolve inside ``root`` — anything else is treated as missing.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()

    def _resolve(self, key: str) -> Path | None:
        if not key or "\x00" in key:
            return None
        candidate = Path(key)
        path = candidate if candidate.is_absolute() else self.root / candidate
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            return None
        if resolved == self.root or not resolved.is_relative_to(self.root):
            return None
        return resolved

    def local_path(self, key: str) -> Path | None:
        path = self._resolve(key)
        return path if path is not None and path.is_file() else None

    async def exists(self, key: str) -> bool:
        return self.local_path(key) is not None

    async def prepare(self) -> None:
        """Create the root. Done once at startup (lifespan), never from a public probe."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    async def check_ready(self) -> None:
        """Readiness (/readyz): the root exists and is writable. Read-only: it creates nothing."""

        def _check() -> None:
            if not self.root.is_dir():
                raise BlobError(f"attachments root does not exist: {self.root}")
            if not os.access(self.root, os.W_OK):
                raise BlobError(f"attachments root is not writable: {self.root}")

        await asyncio.to_thread(_check)

    async def put(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        content_type: str,
        max_bytes: int,
    ) -> int:
        _ = content_type
        if Path(key).is_absolute():
            raise ValueError("put() needs a relative key")
        dest = self._resolve(key)
        if dest is None:
            raise ValueError(f"Key escapes the storage root: {key!r}")
        await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.part")
        total = 0
        handle = await asyncio.to_thread(open, tmp, "wb")
        try:
            async for chunk in chunks:
                total += len(chunk)
                if total > max_bytes:
                    raise BlobTooLargeError(max_bytes)
                await asyncio.to_thread(handle.write, chunk)
            await asyncio.to_thread(handle.close)
            await asyncio.to_thread(os.replace, tmp, dest)
        except BaseException:
            await asyncio.to_thread(handle.close)
            await asyncio.to_thread(tmp.unlink, missing_ok=True)
            raise
        return total

    async def open(self, key: str) -> AsyncIterator[bytes]:  # type: ignore[override]
        path = self.local_path(key)
        if path is None:
            raise BlobNotFoundError(key)
        handle = await asyncio.to_thread(open, path, "rb")
        try:
            while chunk := await asyncio.to_thread(handle.read, CHUNK_SIZE):
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path is not None:
            await asyncio.to_thread(path.unlink, missing_ok=True)


def blob_backend_name() -> str:
    return (os.environ.get("AK5_BLOB_BACKEND") or "fs").strip().lower()


def get_blob_store() -> BlobStore:
    """Backend selected by ``AK5_BLOB_BACKEND`` (default ``fs``); resolved per call so
    ``settings.ATTACHMENTS_DIR`` overrides (tests, CLI) take effect."""
    name = blob_backend_name()
    if name == "fs":
        return FsBlobStore(settings.ATTACHMENTS_DIR)
    raise RuntimeError(
        f"Unsupported AK5_BLOB_BACKEND={name!r} (only 'fs' is available; s3 / vercel-blob: #109)"
    )
