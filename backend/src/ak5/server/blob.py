"""Attachment blob storage abstraction (deployment.md §2, #104).

``BlobStore`` is the only thing the API talks to for attachment bytes. Only the
``fs`` backend exists today; ``s3`` / ``vercel-blob`` land with #109 behind the
same interface. Authorization stays in the API layer (#75): a backend never
decides who may read a key.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

import httpx

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


def clean_stale_parts(root: Path | str, *, older_than_seconds: float = 3600.0) -> int:
    """Remove orphaned ``.*.*.part`` upload files older than ``older_than_seconds``."""
    base = Path(root).resolve()
    if not base.is_dir():
        return 0
    now = time.time()
    cleaned = 0
    for path in base.rglob(".*.part"):
        try:
            if path.is_file():
                stat = path.stat()
                if now - stat.st_mtime >= older_than_seconds:
                    path.unlink(missing_ok=True)
                    cleaned += 1
        except OSError:
            logger.debug("Failed to inspect/remove stale part: %s", path, exc_info=True)
    return cleaned


class S3BlobStore:
    """Store attachments in an S3-compatible bucket (AWS, MinIO, Cloudflare R2)."""

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region_name: str | None = None,
    ) -> None:
        self.bucket = bucket.strip()
        if not self.bucket:
            raise ValueError("S3BlobStore requires a non-empty bucket name")
        self.endpoint_url = endpoint_url
        self.region_name = region_name
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as exc:
            raise BlobError(
                "S3 blob backend requires 'boto3': pip install 'ak5[s3]'"
            ) from exc

        kwargs: dict[str, Any] = {}
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        if self.region_name:
            kwargs["region_name"] = self.region_name
        self._client = boto3.client("s3", **kwargs)
        return self._client

    def local_path(self, key: str) -> Path | None:
        return None

    async def exists(self, key: str) -> bool:
        def _head() -> bool:
            from botocore.exceptions import ClientError

            client = self._get_client()
            try:
                client.head_object(Bucket=self.bucket, Key=key)
                return True
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise BlobError(f"S3 head_object failed: {exc}") from exc

        return await asyncio.to_thread(_head)

    async def check_ready(self) -> None:
        def _check() -> None:
            from botocore.exceptions import ClientError

            client = self._get_client()
            try:
                client.head_bucket(Bucket=self.bucket)
            except ClientError as exc:
                raise BlobError(f"S3 head_bucket failed for {self.bucket}: {exc}") from exc

        await asyncio.to_thread(_check)

    async def put(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        content_type: str,
        max_bytes: int,
    ) -> int:
        """Upload stream to S3.

        Buffers chunks in memory up to `max_bytes` (bounded by settings.MAX_ATTACHMENT_SIZE_BYTES)
        before issuing put_object.
        """
        buffer = io.BytesIO()
        total = 0
        async for chunk in chunks:
            total += len(chunk)
            if total > max_bytes:
                raise BlobTooLargeError(max_bytes)
            buffer.write(chunk)

        data = buffer.getvalue()

        def _upload() -> None:
            client = self._get_client()
            client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )

        await asyncio.to_thread(_upload)
        return total

    async def open(self, key: str) -> AsyncIterator[bytes]:
        def _fetch():
            from botocore.exceptions import ClientError

            client = self._get_client()
            try:
                resp = client.get_object(Bucket=self.bucket, Key=key)
                return resp["Body"]
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in ("404", "NoSuchKey", "NotFound"):
                    raise BlobNotFoundError(key) from exc
                raise BlobError(f"S3 get_object failed: {exc}") from exc

        body = await asyncio.to_thread(_fetch)
        try:
            while True:
                chunk = await asyncio.to_thread(body.read, CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(body.close)

    async def delete(self, key: str) -> None:
        def _del() -> None:
            from botocore.exceptions import ClientError

            client = self._get_client()
            try:
                client.delete_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in ("404", "NoSuchKey", "NotFound"):
                    return
                raise BlobError(f"S3 delete_object failed: {exc}") from exc

        await asyncio.to_thread(_del)


class VercelBlobStore:
    """Store attachments via Vercel Blob REST API (deployment.md §2, #109).

    Vercel Blob architecture:
    - PUT requests go to the management API (api_url, default https://blob.vercel-storage.com/<pathname>).
      The PUT response returns a JSON payload with `url` and `downloadUrl`, which point to the store's
      public edge host (e.g. https://<store-id>.public.blob.vercel-storage.com/<pathname>).
    - Content reads (GET) and presence checks (HEAD) go to the public store URL.
    - Token format: `vercel_blob_rw_<storeId>_<secret>`. The store host is derived from storeId or
      from the PUT response payload.
    - Delete requests go to {api_url}/delete with body {"urls": [blob_url]}.
    """

    def __init__(
        self,
        token: str,
        *,
        api_url: str = "https://blob.vercel-storage.com",
        base_url: str | None = None,
    ) -> None:
        self.token = token.strip()
        if not self.token:
            raise ValueError("VercelBlobStore requires a non-empty token")
        self.api_url = api_url.rstrip("/")

        if base_url:
            self.base_url = base_url.rstrip("/")
        else:
            match = re.match(r"^vercel_blob_rw_([A-Za-z0-9]+)_", self.token)
            if match:
                store_id = match.group(1).lower()
                self.base_url = f"https://{store_id}.public.blob.vercel-storage.com"
            else:
                self.base_url = self.api_url

        self._url_cache: dict[str, str] = {}

    def local_path(self, key: str) -> Path | None:
        return None

    def _headers(self, **extra: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "x-api-version": "7",
        }
        headers.update(extra)
        return headers

    def _get_blob_url(self, key: str) -> str:
        if key in self._url_cache:
            return self._url_cache[key]
        return f"{self.base_url}/{key}"

    async def exists(self, key: str) -> bool:
        url = self._get_blob_url(key)
        async with httpx.AsyncClient() as client:
            resp = await client.head(url)
            return resp.status_code == 200

    async def check_ready(self) -> None:
        url = f"{self.api_url}"
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code != 200:
                    raise BlobError(f"Vercel Blob probe failed (HTTP {resp.status_code}): {resp.text}")
            except httpx.RequestError as exc:
                raise BlobError(f"Vercel Blob probe failed: {exc}") from exc

    async def put(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        content_type: str,
        max_bytes: int,
    ) -> int:
        buffer = io.BytesIO()
        total = 0
        async for chunk in chunks:
            total += len(chunk)
            if total > max_bytes:
                raise BlobTooLargeError(max_bytes)
            buffer.write(chunk)

        data = buffer.getvalue()
        url = f"{self.api_url}/{key}"
        headers = self._headers(
            **{
                "Content-Type": content_type,
                "x-add-random-suffix": "false",
            }
        )
        async with httpx.AsyncClient() as client:
            resp = await client.put(url, headers=headers, content=data)
            if resp.status_code not in (200, 201):
                raise BlobError(f"Vercel Blob PUT failed ({resp.status_code}): {resp.text}")
            try:
                payload = resp.json()
                if isinstance(payload, dict) and "url" in payload:
                    self._url_cache[key] = str(payload["url"])
            except Exception:
                pass
        return total

    async def open(self, key: str) -> AsyncIterator[bytes]:
        url = self._get_blob_url(key)
        client = httpx.AsyncClient()
        try:
            req = client.build_request("GET", url)
            resp = await client.send(req, stream=True)
            if resp.status_code in (404, 403):
                await resp.aclose()
                await client.aclose()
                raise BlobNotFoundError(key)
            if resp.status_code != 200:
                await resp.aclose()
                await client.aclose()
                raise BlobError(f"Vercel Blob GET failed ({resp.status_code})")

            async for chunk in resp.aiter_bytes(chunk_size=CHUNK_SIZE):
                yield chunk
            await resp.aclose()
        finally:
            await client.aclose()

    async def delete(self, key: str) -> None:
        url = f"{self.api_url}/delete"
        blob_url = self._get_blob_url(key)
        self._url_cache.pop(key, None)
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=self._headers(), json={"urls": [blob_url]})
            if resp.status_code not in (200, 204, 404):
                raise BlobError(f"Vercel Blob delete failed ({resp.status_code}): {resp.text}")


def blob_backend_name() -> str:
    return (os.environ.get("AK5_BLOB_BACKEND") or "fs").strip().lower()


def get_blob_store() -> BlobStore:
    """Backend selected by ``AK5_BLOB_BACKEND`` (default ``fs``); resolved per call so
    ``settings.ATTACHMENTS_DIR`` overrides (tests, CLI) take effect."""
    name = blob_backend_name()
    if name == "fs":
        return FsBlobStore(settings.ATTACHMENTS_DIR)
    if name == "s3":
        bucket = os.environ.get("AK5_S3_BUCKET")
        if not bucket:
            raise RuntimeError("AK5_BLOB_BACKEND=s3 requires AK5_S3_BUCKET to be set")
        endpoint = os.environ.get("AK5_S3_ENDPOINT")
        region = os.environ.get("AK5_S3_REGION")
        return S3BlobStore(bucket, endpoint_url=endpoint, region_name=region)
    if name in ("vercel-blob", "vercel_blob", "vercel"):
        token = os.environ.get("BLOB_READ_WRITE_TOKEN")
        if not token:
            raise RuntimeError("AK5_BLOB_BACKEND=vercel-blob requires BLOB_READ_WRITE_TOKEN to be set")
        return VercelBlobStore(token)
    raise RuntimeError(
        f"Unsupported AK5_BLOB_BACKEND={name!r} (supported: 'fs', 's3', 'vercel-blob')"
    )
