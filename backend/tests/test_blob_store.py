"""#104: BlobStore abstraction (deployment.md §2) — fs backend + API wiring."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from ak5.config import settings
from ak5.server.blob import (
    BlobNotFoundError,
    BlobTooLargeError,
    FsBlobStore,
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


def test_unknown_backend_is_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_BLOB_BACKEND", "s3")
    with pytest.raises(RuntimeError, match="#109"):
        get_blob_store()
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
