"""#104: BlobStore abstraction (deployment.md §2) — fs backend + API wiring."""

from __future__ import annotations

import io
import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from ak5.config import settings
from ak5.server.blob import (
    BlobError,
    BlobNotFoundError,
    BlobTooLargeError,
    FsBlobStore,
    S3BlobStore,
    VercelBlobStore,
    attachment_key,
    get_blob_store,
)
from helpers_workflow import board_ticket
from httpx import AsyncClient


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def _read(store, key: str) -> bytes:
    return b"".join([c async for c in store.open(key)])


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def test_attachment_key_layout_and_rejects_unsafe_parts():
    assert attachment_key("ws_local", "TK-1", "att_abc") == "ws/ws_local/TK-1/att_abc"
    for bad in ("..", "a/b", "../x", "", ".hidden", "a b"):
        with pytest.raises(ValueError):
            attachment_key("ws_local", bad, "att_abc")


@pytest.mark.asyncio
async def test_fs_put_open_exists_delete_roundtrip(tmp_path: Path):
    store = FsBlobStore(tmp_path)
    key = attachment_key("ws_local", "TK-1", "att_1")
    assert not await store.exists(key)

    size = await store.put(
        key, _chunks(b"hello ", b"world"), content_type="text/plain", max_bytes=100
    )
    assert size == 11
    assert await store.exists(key)
    assert await _read(store, key) == b"hello world"
    assert store.local_path(key) == (tmp_path / "ws/ws_local/TK-1/att_1").resolve()

    await store.delete(key)
    await store.delete(key)  # missing is not an error
    assert not await store.exists(key)


@pytest.mark.asyncio
async def test_fs_put_over_limit_keeps_nothing(tmp_path: Path):
    store = FsBlobStore(tmp_path)
    key = attachment_key("ws_local", "TK-1", "att_big")
    with pytest.raises(BlobTooLargeError):
        await store.put(key, _chunks(b"A" * 10, b"B" * 10), content_type="x/y", max_bytes=15)
    assert not await store.exists(key)
    assert _files(tmp_path) == []  # no partial / temp files


@pytest.mark.asyncio
async def test_fs_put_cleans_up_when_the_source_fails(tmp_path: Path):
    async def boom() -> AsyncIterator[bytes]:
        yield b"partial"
        raise RuntimeError("client went away")

    store = FsBlobStore(tmp_path)
    with pytest.raises(RuntimeError):
        await store.put("ws/a/b/c", boom(), content_type="x/y", max_bytes=100)
    assert _files(tmp_path) == []


@pytest.mark.asyncio
async def test_fs_confines_keys_to_root(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    store = FsBlobStore(root)

    for hostile in ("../outside.txt", str(outside), "/etc/passwd", "ws/../../outside.txt", ""):
        assert store.local_path(hostile) is None
        assert not await store.exists(hostile)
        await store.delete(hostile)  # must not touch the file
        with pytest.raises(BlobNotFoundError):
            await _read(store, hostile)
    assert outside.read_text() == "secret"
    with pytest.raises(ValueError):
        await store.put("../escape", _chunks(b"x"), content_type="x/y", max_bytes=10)
    with pytest.raises(ValueError):
        await store.put(str(outside), _chunks(b"x"), content_type="x/y", max_bytes=10)


@pytest.mark.asyncio
async def test_fs_still_reads_pre_104_absolute_paths_inside_root(tmp_path: Path):
    legacy = tmp_path / "att_old_report.md"
    legacy.write_bytes(b"old data")
    store = FsBlobStore(tmp_path)
    assert await store.exists(str(legacy))
    assert await _read(store, str(legacy)) == b"old data"
    await store.delete(str(legacy))
    assert not legacy.exists()


def test_backend_selection_and_validation(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_BLOB_BACKEND", "magic_unknown")
    with pytest.raises(RuntimeError, match="Unsupported AK5_BLOB_BACKEND='magic_unknown'"):
        get_blob_store()

    monkeypatch.setenv("AK5_BLOB_BACKEND", "s3")
    monkeypatch.delenv("AK5_S3_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="requires AK5_S3_BUCKET"):
        get_blob_store()

    monkeypatch.setenv("AK5_S3_BUCKET", "my-bucket")
    assert isinstance(get_blob_store(), S3BlobStore)

    monkeypatch.setenv("AK5_BLOB_BACKEND", "vercel-blob")
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="requires BLOB_READ_WRITE_TOKEN"):
        get_blob_store()

    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_teststore_123")
    assert isinstance(get_blob_store(), VercelBlobStore)

    monkeypatch.setenv("AK5_BLOB_BACKEND", "FS")
    assert isinstance(get_blob_store(), FsBlobStore)


async def _ticket(client: AsyncClient) -> str:
    created = await client.post("/api/v1/tickets", json=board_ticket("Blob ticket"))
    assert created.status_code == 201, created.text
    return created.json()["ticket_id"]


@pytest.mark.asyncio
async def test_upload_stores_under_key_layout_without_the_user_filename(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "ATTACHMENTS_DIR", str(tmp_path))
    tid = await _ticket(client)
    resp = await client.post(
        f"/api/v1/tickets/{tid}/attachments",
        files={"file": ("../../evil name.txt", io.BytesIO(b"payload"), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    att_id = resp.json()["attachment_id"]
    assert resp.json()["filename"] == "evil name.txt"

    assert [p.relative_to(tmp_path).as_posix() for p in _files(tmp_path)] == [
        f"ws/ws_local/{tid}/{att_id}"
    ]

    dl = await client.get(f"/api/v1/tickets/{tid}/attachments/{att_id}")
    assert dl.status_code == 200 and dl.content == b"payload"
    assert "evil%20name.txt" in dl.headers["content-disposition"]

    assert (await client.delete(f"/api/v1/tickets/{tid}/attachments/{att_id}")).status_code == 200
    assert _files(tmp_path) == []


@pytest.mark.asyncio
async def test_missing_blob_is_404_but_delete_still_removes_the_row(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "ATTACHMENTS_DIR", str(tmp_path))
    tid = await _ticket(client)
    att = (
        await client.post(
            f"/api/v1/tickets/{tid}/attachments",
            files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
        )
    ).json()["attachment_id"]
    for p in _files(tmp_path):
        p.unlink()
    assert (await client.get(f"/api/v1/tickets/{tid}/attachments/{att}")).status_code == 404
    assert (await client.delete(f"/api/v1/tickets/{tid}/attachments/{att}")).status_code == 200
    assert (await client.get(f"/api/v1/tickets/{tid}/attachments")).json() == []


class MemoryBlobStore:
    """Non-local backend: forces the streaming download path used by s3 / vercel-blob."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.deleted: list[str] = []

    async def put(self, key, chunks, *, content_type, max_bytes):
        data = b""
        async for chunk in chunks:
            data += chunk
            if len(data) > max_bytes:
                raise BlobTooLargeError(max_bytes)
        self.blobs[key] = data
        return len(data)

    async def open(self, key):
        if key not in self.blobs:
            raise BlobNotFoundError(key)
        yield self.blobs[key]

    async def delete(self, key):
        self.deleted.append(key)
        self.blobs.pop(key, None)

    async def exists(self, key):
        return key in self.blobs

    def local_path(self, key):
        return None


@pytest.mark.asyncio
async def test_api_works_against_a_non_local_backend(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    mem = MemoryBlobStore()
    monkeypatch.setattr("ak5.server.routers.tickets.get_blob_store", lambda: mem)
    monkeypatch.setattr(settings, "MAX_ATTACHMENT_SIZE_BYTES", 1024)
    tid = await _ticket(client)

    big = await client.post(
        f"/api/v1/tickets/{tid}/attachments",
        files={"file": ("big.bin", io.BytesIO(b"A" * 2048), "application/octet-stream")},
    )
    assert big.status_code == 413
    assert mem.blobs == {}

    resp = await client.post(
        f"/api/v1/tickets/{tid}/attachments",
        files={"file": ("ünïcode.txt", io.BytesIO(b"streamed"), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    att = resp.json()["attachment_id"]
    key = attachment_key("ws_local", tid, att)
    assert list(mem.blobs) == [key]

    dl = await client.get(f"/api/v1/tickets/{tid}/attachments/{att}")
    assert dl.status_code == 200 and dl.content == b"streamed"
    assert dl.headers["content-length"] == str(len(b"streamed"))
    assert "filename*=UTF-8" in dl.headers["content-disposition"]

    assert (await client.delete(f"/api/v1/tickets/{tid}/attachments/{att}")).status_code == 200
    assert mem.deleted == [key]


def test_clean_stale_parts(tmp_path: Path):
    from ak5.server.blob import clean_stale_parts

    parts_dir = tmp_path / "ws" / "ws_test" / "t1"
    parts_dir.mkdir(parents=True)

    # 1. Stale part (older than 1 hour)
    stale_part = parts_dir / ".att_1.abc123.part"
    stale_part.write_bytes(b"stale")
    stale_time = time.time() - 7200
    os.utime(stale_part, (stale_time, stale_time))

    # 2. Fresh part (just created)
    fresh_part = parts_dir / ".att_2.def456.part"
    fresh_part.write_bytes(b"fresh")
    fresh_time = time.time() - 60
    os.utime(fresh_part, (fresh_time, fresh_time))

    # 3. Regular file
    regular_file = parts_dir / "att_3"
    regular_file.write_bytes(b"regular")

    cleaned = clean_stale_parts(tmp_path, older_than_seconds=3600.0)
    assert cleaned == 1
    assert not stale_part.exists()
    assert fresh_part.exists()
    assert regular_file.exists()


def test_db_maintenance_cli_cleans_stale_parts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from ak5.cli.commands.db import db_group
    from ak5.server.config import settings
    from click.testing import CliRunner

    monkeypatch.setattr(settings, "ATTACHMENTS_DIR", str(tmp_path))
    stale_part = tmp_path / ".test.1234.part"
    stale_part.write_bytes(b"stale")
    stale_time = time.time() - 7200
    os.utime(stale_part, (stale_time, stale_time))

    runner = CliRunner()
    result = runner.invoke(db_group, ["maintenance", "--clean-parts-older-than", "3600"])
    assert result.exit_code == 0
    assert "Cleaned 1 stale attachment part file(s)." in result.output
    assert not stale_part.exists()


@pytest.mark.asyncio
async def test_s3_blob_store_contract(monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("moto")
    pytest.importorskip("boto3")
    import boto3
    from moto import mock_aws

    with mock_aws():
        # Setup S3 bucket
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-bucket")

        store = S3BlobStore("test-bucket", region_name="us-east-1")
        await store.check_ready()

        key = attachment_key("ws_s3", "TK-1", "att_s3")
        assert not await store.exists(key)
        assert store.local_path(key) is None

        # Put
        size = await store.put(key, _chunks(b"s3 ", b"content"), content_type="text/plain", max_bytes=100)
        assert size == 10
        assert await store.exists(key)

        # Open
        data = await _read(store, key)
        assert data == b"s3 content"

        # Over limit
        big_key = attachment_key("ws_s3", "TK-1", "att_big")
        with pytest.raises(BlobTooLargeError):
            await store.put(big_key, _chunks(b"A" * 20), content_type="text/plain", max_bytes=15)
        assert not await store.exists(big_key)

        # Missing key
        with pytest.raises(BlobNotFoundError):
            await _read(store, "missing_key")

        # Delete
        await store.delete(key)
        await store.delete(key)  # Idempotent
        assert not await store.exists(key)


@pytest.mark.asyncio
async def test_vercel_blob_store_contract(monkeypatch: pytest.MonkeyPatch):
    blobs: dict[str, tuple[bytes, str]] = {}
    store_id = "store123"
    token = f"vercel_blob_rw_{store_id}_secretxyz"
    public_edge_host = f"{store_id}.public.blob.vercel-storage.com"

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path

        # 1. Edge host requests (HEAD / GET directly from public edge URL)
        if host == public_edge_host:
            key = path.lstrip("/")
            if request.method == "HEAD":
                if key in blobs:
                    return httpx.Response(200)
                return httpx.Response(404)
            if request.method == "GET":
                if key in blobs:
                    data, ct = blobs[key]
                    return httpx.Response(200, content=data, headers={"content-type": ct})
                return httpx.Response(404)
            return httpx.Response(405)

        # 2. Management API requests (PUT, POST /delete, GET / probe)
        assert request.headers.get("authorization") == f"Bearer {token}"
        assert request.headers.get("x-api-version") == "7"

        if request.method == "GET" and path == "/":
            return httpx.Response(200, json={"blobs": [], "hasMore": False})

        if request.method == "PUT":
            assert request.headers.get("x-add-random-suffix") == "false"
            key = path.lstrip("/")
            data = request.read()
            ct = request.headers.get("content-type", "application/octet-stream")
            blobs[key] = (data, ct)
            blob_url = f"https://{public_edge_host}/{key}"
            return httpx.Response(
                200,
                json={
                    "url": blob_url,
                    "downloadUrl": f"{blob_url}?download=1",
                    "pathname": key,
                    "contentType": ct,
                },
            )

        if request.method == "POST" and path == "/delete":
            payload = json.loads(request.read())
            for url in payload.get("urls", []):
                key = url.split(f"https://{public_edge_host}/")[-1].lstrip("/")
                blobs.pop(key, None)
            return httpx.Response(200)

        return httpx.Response(400)

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return orig_client(*args, **kwargs)

    monkeypatch.setattr("ak5.server.blob.httpx.AsyncClient", mock_client)

    store = VercelBlobStore(token)
    await store.check_ready()

    key = "ws/ws_v/TK-1/att_v"
    assert not await store.exists(key)
    assert store.local_path(key) is None

    # Put
    size = await store.put(key, _chunks(b"vercel ", b"blob"), content_type="text/plain", max_bytes=100)
    assert size == 11
    assert await store.exists(key)

    # Open (reads from store's public edge host)
    assert await _read(store, key) == b"vercel blob"

    # Over limit
    with pytest.raises(BlobTooLargeError):
        await store.put("ws/v/big", _chunks(b"X" * 50), content_type="text/plain", max_bytes=20)
    assert not await store.exists("ws/v/big")

    # Missing
    with pytest.raises(BlobNotFoundError):
        await _read(store, "non_existent")

    # Delete (calls management API /delete with public edge URL)
    await store.delete(key)
    assert not await store.exists(key)


@pytest.mark.asyncio
async def test_vercel_blob_store_check_ready_failure(monkeypatch: pytest.MonkeyPatch):
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    orig_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(error_handler)
        return orig_client(*args, **kwargs)

    monkeypatch.setattr("ak5.server.blob.httpx.AsyncClient", mock_client)
    store = VercelBlobStore("vercel_blob_rw_test_secret")
    with pytest.raises(BlobError, match="Vercel Blob probe failed"):
        await store.check_ready()


def test_vercel_blob_store_token_validation():
    # Valid token extracts lowercase store id
    store = VercelBlobStore("vercel_blob_rw_MyStore123_secret")
    assert store.base_url == "https://mystore123.public.blob.vercel-storage.com"

    # Explicit base_url overrides derivation
    custom = VercelBlobStore("custom_token", base_url="https://custom.edge.blob.store")
    assert custom.base_url == "https://custom.edge.blob.store"

    # Invalid token format without base_url fails fast
    with pytest.raises(ValueError, match="Invalid Vercel Blob token format"):
        VercelBlobStore("invalid_token")


@pytest.mark.asyncio
async def test_vercel_blob_store_restarted_process_derived_url(monkeypatch: pytest.MonkeyPatch):
    """Test #141: across process restarts when _url_cache is empty, reads and deletes succeed using derived URL."""
    blobs: dict[str, tuple[bytes, str]] = {}
    store_id = "testproc"
    token = f"vercel_blob_rw_{store_id}_secretxyz"
    public_edge_host = f"{store_id}.public.blob.vercel-storage.com"

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == public_edge_host:
            key = path.lstrip("/")
            if request.method == "HEAD":
                return httpx.Response(200 if key in blobs else 404)
            if request.method == "GET":
                if key in blobs:
                    data, ct = blobs[key]
                    return httpx.Response(200, content=data, headers={"content-type": ct})
                return httpx.Response(404)
            return httpx.Response(405)

        if request.headers.get("authorization") == f"Bearer {token}":
            if request.method == "PUT":
                key = path.lstrip("/")
                blobs[key] = (request.read(), request.headers.get("content-type", "application/octet-stream"))
                return httpx.Response(200, json={"url": f"https://{public_edge_host}/{key}"})
            if request.method == "POST" and path == "/delete":
                payload = json.loads(request.read())
                for url in payload.get("urls", []):
                    key = url.split(f"https://{public_edge_host}/")[-1].lstrip("/")
                    blobs.pop(key, None)
                return httpx.Response(200)
        return httpx.Response(400)

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return orig_client(*args, **kwargs)

    monkeypatch.setattr("ak5.server.blob.httpx.AsyncClient", mock_client)

    # Process 1: upload blob (populates memory cache)
    store_p1 = VercelBlobStore(token)
    key = "ws/ws_p1/TK-1/att_persisted"
    await store_p1.put(key, _chunks(b"persisted data"), content_type="text/plain", max_bytes=100)
    assert key in store_p1._url_cache

    # Process 2: fresh instance with empty _url_cache (simulating restart)
    store_p2 = VercelBlobStore(token)
    assert len(store_p2._url_cache) == 0
    assert await store_p2.exists(key)
    assert await _read(store_p2, key) == b"persisted data"
    await store_p2.delete(key)
    assert not await store_p2.exists(key)


@pytest.mark.live_vercel_blob
@pytest.mark.skipif(
    not os.environ.get("BLOB_READ_WRITE_TOKEN"),
    reason="Live Vercel Blob test requires BLOB_READ_WRITE_TOKEN",
)
@pytest.mark.asyncio
async def test_live_vercel_blob_smoke():
    """Test #141: opt-in live smoke test against real Vercel Blob store."""
    token = os.environ["BLOB_READ_WRITE_TOKEN"]
    store = VercelBlobStore(token)

    # 1. Probe readiness
    await store.check_ready()

    # 2. Key layout with timestamp to ensure isolation
    test_id = f"smoke_{int(time.time())}"
    key = f"ws/smoke_ws/TK-smoke/{test_id}"
    content = b"live vercel blob smoke test payload"

    try:
        # 3. Put
        size = await store.put(key, _chunks(content), content_type="text/plain", max_bytes=1024)
        assert size == len(content)

        # 4. Exists
        assert await store.exists(key)

        # 5. Open / Read
        data = await _read(store, key)
        assert data == content

        # 6. Verify restarted-process reading without memory cache
        clean_store = VercelBlobStore(token)
        assert len(clean_store._url_cache) == 0
        assert await clean_store.exists(key)
        assert await _read(clean_store, key) == content
    finally:
        # 7. Delete cleanup
        await store.delete(key)
        assert not await store.exists(key)



