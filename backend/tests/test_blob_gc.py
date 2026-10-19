"""#139: Deferred blob_gc cleanup table and sweeper (deployment.md §2, operations.md §3)."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock

import pytest
from ak5.contracts.stages import Role
from ak5.server.auth.principal import Principal
from ak5.server.auth.tokens import issue_access_token
from ak5.server.blob import BlobError, FsBlobStore
from ak5.server.models.actor import Actor
from ak5.server.models.blob_gc import BlobGc
from ak5.server.models.ticket import TicketAttachment
from ak5.server.models.workspace import Membership, User, Workspace
from ak5.server.services.blob_gc import record_failed_blob_deletion, sweep_blob_gc
from click.testing import CliRunner
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _auth_headers(user_id: str, workspace_id: str) -> dict[str, str]:
    token = issue_access_token(
        Principal(
            principal_id=user_id,
            principal_type="user",
            workspace_id=workspace_id,
            workflow_role=Role.HUMAN,
            membership_role="owner",
        )
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_blob_gc_record_and_sweep(test_db: AsyncSession, tmp_path):
    store = FsBlobStore(tmp_path)

    # 1. Record two failed deletions into blob_gc
    await record_failed_blob_deletion(test_db, "ws/ws1/T-1/att_1", error="Network timeout", workspace_id="ws1")
    await record_failed_blob_deletion(test_db, "ws/ws1/T-1/att_2", error="503 Service Unavailable", workspace_id="ws1")
    await test_db.commit()

    rows = (await test_db.scalars(select(BlobGc))).all()
    assert len(rows) == 2
    assert rows[0].key == "ws/ws1/T-1/att_1"
    assert rows[0].attempts == 0

    # 2. Mock store.delete to fail on att_1 and succeed on att_2
    async def mock_delete(key: str) -> None:
        if "att_1" in key:
            raise BlobError("Backend down")

    store.delete = mock_delete  # type: ignore[assignment]

    stats = await sweep_blob_gc(test_db, store)
    assert stats["deleted"] == 1
    assert stats["failed"] == 1
    assert stats["total"] == 2

    # att_2 should be removed, att_1 should have attempts incremented
    remaining = (await test_db.scalars(select(BlobGc))).all()
    assert len(remaining) == 1
    assert remaining[0].key == "ws/ws1/T-1/att_1"
    assert remaining[0].attempts == 1
    assert "Backend down" in (remaining[0].last_error or "")

    # 3. Now let store.delete succeed for all keys
    store.delete = AsyncMock()  # type: ignore[assignment]
    stats2 = await sweep_blob_gc(test_db, store)
    assert stats2["deleted"] == 1
    assert stats2["failed"] == 0

    assert len((await test_db.scalars(select(BlobGc))).all()) == 0


@pytest.mark.asyncio
async def test_attachment_delete_failure_enqueues_blob_gc(
    client: AsyncClient, test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    ws = Workspace(workspace_id="ws_gc", name="GC Workspace")
    user = User(user_id="usr_gc", email="gc@test.com", display_name="GC User")
    member = Membership(membership_id="mem_gc", workspace_id="ws_gc", user_id="usr_gc", role="owner")
    actor = Actor(actor_id="usr_gc", actor_type="human", name="GC User", role="dev")
    test_db.add_all([ws, user, member, actor])
    from ak5.server.services.workspaces import provision_inbox_board

    await provision_inbox_board(test_db, workspace_id="ws_gc", created_by="usr_gc")
    await test_db.commit()

    headers = _auth_headers("usr_gc", "ws_gc")

    # Create ticket
    t_resp = await client.post("/api/v1/tickets", headers=headers, json={"title": "GC Ticket"})
    assert t_resp.status_code == 201, t_resp.text
    ticket_id = t_resp.json()["ticket_id"]

    # Upload attachment
    up_resp = await client.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        headers=headers,
        files={"file": ("gc_test.txt", io.BytesIO(b"gc content"), "text/plain")},
    )
    assert up_resp.status_code == 201
    attachment_id = up_resp.json()["attachment_id"]

    # Mock blob store delete to fail
    class FailingBlobStore:
        async def delete(self, key: str) -> None:
            raise BlobError("S3 Connection Refused")

    monkeypatch.setattr("ak5.server.routers.tickets.get_blob_store", lambda: FailingBlobStore())

    # Delete attachment
    del_resp = await client.delete(f"/api/v1/tickets/{ticket_id}/attachments/{attachment_id}", headers=headers)
    assert del_resp.status_code == 200
    assert del_resp.json() == {"deleted": True, "attachment_id": attachment_id}

    # Verify attachment row is deleted from ticket_attachments
    att_row = await test_db.get(TicketAttachment, attachment_id)
    assert att_row is None

    # Verify failed deletion was logged to blob_gc
    gc_rows = (await test_db.scalars(select(BlobGc).where(BlobGc.workspace_id == "ws_gc"))).all()
    assert len(gc_rows) == 1
    assert attachment_id in gc_rows[0].key
    assert "S3 Connection Refused" in (gc_rows[0].last_error or "")


def test_db_maintenance_cli_sweep_blob_gc(test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    import ak5.cli.commands.db as db_mod
    from ak5.cli.commands.db import maintenance_cmd
    from conftest import TestAsyncSessionLocal

    monkeypatch.setattr(db_mod, "AsyncSessionLocal", TestAsyncSessionLocal, raising=False)

    runner = CliRunner()
    res = runner.invoke(maintenance_cmd, ["--sweep-blob-gc"])
    assert res.exit_code == 0
    assert "Blob GC sweep finished:" in res.output
