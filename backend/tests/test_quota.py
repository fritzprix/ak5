"""#116: Workspace quota enforcement & usage API (operations.md §3)."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from ak5.contracts.errors import HTTP_STATUS, ErrorCode
from ak5.contracts.stages import Role
from ak5.server.auth.principal import Principal
from ak5.server.auth.tokens import issue_access_token
from ak5.server.models.actor import Actor
from ak5.server.models.workspace import Membership, User, Workspace, WorkspaceUsage
from ak5.server.quota import (
    HOSTED_QUOTA_DEFAULTS,
    QuotaExceeded,
    get_effective_quotas,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _auth_headers(user_id: str, workspace_id: str, role: str = "owner") -> dict[str, str]:
    token = issue_access_token(
        Principal(
            principal_id=user_id,
            principal_type="user",
            workspace_id=workspace_id,
            workflow_role=Role.HUMAN,
            membership_role=role,
        )
    )
    return {"Authorization": f"Bearer {token}"}


async def _setup_user_workspace(
    test_db: AsyncSession, ws_id: str, user_id: str, policy: dict[str, Any] | None = None
) -> dict[str, str]:
    ws = Workspace(workspace_id=ws_id, name=f"WS {ws_id}", policy=json.dumps(policy or {}))
    user = User(user_id=user_id, display_name="Owner", email=f"{user_id}@example.com")
    actor = Actor(actor_id=user_id, name="Owner", actor_type="human", role="human")
    mem = Membership(membership_id=f"mem_{ws_id}_{user_id}", workspace_id=ws_id, user_id=user_id, role="owner")
    test_db.add(ws)
    test_db.add(user)
    test_db.add(actor)
    test_db.add(mem)
    from ak5.server.services.workspaces import provision_inbox_board

    await provision_inbox_board(test_db, workspace_id=ws_id, created_by=user_id)
    await test_db.commit()
    return _auth_headers(user_id, ws_id, role="owner")


def test_effective_quotas(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AK5_PROFILE", raising=False)
    # Local default: all 0 (unlimited)
    ws = Workspace(workspace_id="ws1", name="Test", policy="{}")
    quotas = get_effective_quotas(ws)
    assert quotas["max_boards"] == 0
    assert quotas["max_open_tickets"] == 0

    # Hosted profile default
    monkeypatch.setenv("AK5_PROFILE", "hosted")
    quotas_hosted = get_effective_quotas(ws)
    assert quotas_hosted["max_boards"] == HOSTED_QUOTA_DEFAULTS["max_boards"]
    assert quotas_hosted["max_open_tickets"] == HOSTED_QUOTA_DEFAULTS["max_open_tickets"]
    assert quotas_hosted["max_attachment_bytes_total"] == HOSTED_QUOTA_DEFAULTS["max_attachment_bytes_total"]

    # Explicit policy overrides hosted default
    ws.policy = json.dumps({"max_boards": 10, "max_open_tickets": 100})
    quotas_custom = get_effective_quotas(ws)
    assert quotas_custom["max_boards"] == 10
    assert quotas_custom["max_open_tickets"] == 100
    assert quotas_custom["max_members"] == 50


async def test_quota_exceeded_error_envelope():
    exc = QuotaExceeded("max_boards", 5, 5)
    assert exc.status_code == 403
    body = exc.body()
    assert body["error"]["code"] == ErrorCode.QUOTA_EXCEEDED
    assert body["error"]["details"] == {"quota": "max_boards", "limit": 5, "current": 5}
    assert HTTP_STATUS[ErrorCode.QUOTA_EXCEEDED] == 403


async def test_quota_ceiling_enforcement(client: AsyncClient, test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_QUOTA_CEILING", json.dumps({"max_boards": 20, "max_members": 10}))
    headers = await _setup_user_workspace(test_db, "ws_ceiling", "usr_ceiling_owner")

    # Within ceiling -> OK
    resp = await client.patch(
        "/api/v1/workspaces/ws_ceiling",
        headers=headers,
        json={"policy": {"max_boards": 15}},
    )
    assert resp.status_code == 200
    assert resp.json()["policy"]["max_boards"] == 15

    # Exceeds ceiling -> 422 PAYLOAD_INVALID
    resp_exceed = await client.patch(
        "/api/v1/workspaces/ws_ceiling",
        headers=headers,
        json={"policy": {"max_boards": 25}},
    )
    assert resp_exceed.status_code == 422
    data = resp_exceed.json()
    assert data["error"]["code"] == "PAYLOAD_INVALID"
    assert "exceeds server ceiling" in data["error"]["message"]


async def test_board_quota_enforcement(client: AsyncClient, test_db: AsyncSession):
    headers = await _setup_user_workspace(test_db, "ws_b_quota", "usr_b_owner", policy={"max_boards": 1})

    # 1st board -> Success
    resp1 = await client.post(
        "/api/v1/boards",
        headers=headers,
        json={"name": "Board 1", "template": "default"},
    )
    assert resp1.status_code == 200

    # 2nd board -> 403 QUOTA_EXCEEDED
    resp2 = await client.post(
        "/api/v1/boards",
        headers=headers,
        json={"name": "Board 2", "template": "default"},
    )
    assert resp2.status_code == 403
    assert resp2.json()["error"]["code"] == "QUOTA_EXCEEDED"
    assert resp2.json()["error"]["details"]["quota"] == "max_boards"


async def test_ticket_quota_enforcement(client: AsyncClient, test_db: AsyncSession):
    headers = await _setup_user_workspace(test_db, "ws_t_quota", "usr_t_owner", policy={"max_open_tickets": 2})

    # Create 2 tickets -> OK
    r1 = await client.post("/api/v1/tickets", headers=headers, json={"title": "Ticket 1"})
    assert r1.status_code == 201
    r2 = await client.post("/api/v1/tickets", headers=headers, json={"title": "Ticket 2"})
    assert r2.status_code == 201

    # 3rd ticket -> 403 QUOTA_EXCEEDED
    r3 = await client.post("/api/v1/tickets", headers=headers, json={"title": "Ticket 3"})
    assert r3.status_code == 403
    assert r3.json()["error"]["code"] == "QUOTA_EXCEEDED"
    assert r3.json()["error"]["details"]["quota"] == "max_open_tickets"


async def test_attachment_quota_and_deletion_decrement(client: AsyncClient, test_db: AsyncSession):
    # Set quota of 1000 bytes
    headers = await _setup_user_workspace(
        test_db, "ws_att_quota", "usr_att_owner", policy={"max_attachment_bytes_total": 1000}
    )

    # Create a ticket to attach to
    t_resp = await client.post("/api/v1/tickets", headers=headers, json={"title": "Attachment Ticket"})
    assert t_resp.status_code == 201
    ticket_id = t_resp.json()["ticket_id"]

    # Upload 600 bytes -> OK
    content_600 = b"x" * 600
    up1 = await client.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        headers=headers,
        files={"file": ("test1.bin", io.BytesIO(content_600), "application/octet-stream")},
    )
    assert up1.status_code == 201
    att1_id = up1.json()["attachment_id"]

    # Check counter in workspace_usage
    row = await test_db.get(WorkspaceUsage, ("ws_att_quota", "attachment_bytes"))
    assert row is not None
    assert row.value == 600

    # Upload 500 bytes -> 600 + 500 = 1100 > 1000 -> 403 QUOTA_EXCEEDED
    content_500 = b"y" * 500
    up2 = await client.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        headers=headers,
        files={"file": ("test2.bin", io.BytesIO(content_500), "application/octet-stream")},
    )
    assert up2.status_code == 403
    assert up2.json()["error"]["code"] == "QUOTA_EXCEEDED"
    assert up2.json()["error"]["details"]["quota"] == "max_attachment_bytes_total"

    # Counter remains 600
    await test_db.refresh(row)
    assert row.value == 600

    # Delete 1st attachment -> counter decrements to 0
    del_resp = await client.delete(f"/api/v1/tickets/{ticket_id}/attachments/{att1_id}", headers=headers)
    assert del_resp.status_code == 200
    await test_db.refresh(row)
    assert row.value == 0

    # Now 500 bytes can be uploaded successfully
    up3 = await client.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        headers=headers,
        files={"file": ("test2.bin", io.BytesIO(content_500), "application/octet-stream")},
    )
    assert up3.status_code == 201
    await test_db.refresh(row)
    assert row.value == 500


async def test_agent_and_member_quota(client: AsyncClient, test_db: AsyncSession):
    headers = await _setup_user_workspace(
        test_db, "ws_am_quota", "usr_am_owner", policy={"max_members": 2, "max_agents": 1}
    )

    # 1. Agent Quota (limit 1)
    # Create agent 1 -> OK
    ag1 = await client.post(
        "/api/v1/workspaces/ws_am_quota/agents",
        headers=headers,
        json={"name": "Agent 1", "workflow_role": "worker"},
    )
    assert ag1.status_code == 200

    # Create agent 2 -> 403 QUOTA_EXCEEDED
    ag2 = await client.post(
        "/api/v1/workspaces/ws_am_quota/agents",
        headers=headers,
        json={"name": "Agent 2", "workflow_role": "worker"},
    )
    assert ag2.status_code == 403
    assert ag2.json()["error"]["code"] == "QUOTA_EXCEEDED"
    assert ag2.json()["error"]["details"]["quota"] == "max_agents"

    # 2. Member Quota (limit 2, current members = 1)
    # Invite 1 member -> OK
    inv1 = await client.post(
        "/api/v1/workspaces/ws_am_quota/members",
        headers=headers,
        json={"email": "m1@example.com", "role": "member"},
    )
    assert inv1.status_code == 200

    # Add second member to reach limit 2
    user2 = User(user_id="usr_m2", display_name="User 2", email="m2@example.com")
    test_db.add(user2)
    test_db.add(Membership(membership_id="mem_m2", workspace_id="ws_am_quota", user_id="usr_m2", role="member"))
    await test_db.commit()

    # Invite when already at limit 2 -> 403 QUOTA_EXCEEDED
    inv2 = await client.post(
        "/api/v1/workspaces/ws_am_quota/members",
        headers=headers,
        json={"email": "m3@example.com", "role": "member"},
    )
    assert inv2.status_code == 403
    assert inv2.json()["error"]["code"] == "QUOTA_EXCEEDED"
    assert inv2.json()["error"]["details"]["quota"] == "max_members"


async def test_get_workspace_usage_endpoint(client: AsyncClient, test_db: AsyncSession):
    headers = await _setup_user_workspace(
        test_db, "ws_usage_test", "usr_usage_owner", policy={"max_boards": 10, "max_open_tickets": 50}
    )

    resp = await client.get("/api/v1/workspaces/ws_usage_test/usage", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["workspace_id"] == "ws_usage_test"
    assert "usage" in data
    assert data["usage"]["max_boards"]["limit"] == 10
    assert data["usage"]["max_boards"]["current"] == 0
    assert data["usage"]["max_open_tickets"]["limit"] == 50
    assert data["usage"]["max_members"]["current"] == 1


async def test_hosted_defaults_inherited_on_new_workspace(test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AK5_PROFILE", "hosted")
    from ak5.server.services.workspaces import create_workspace

    user = User(user_id="usr_new_owner", display_name="New Owner", email="new@example.com")
    test_db.add(user)
    await test_db.flush()

    ws, _ = await create_workspace(test_db, name="Hosted WS", owner=user)
    quotas = get_effective_quotas(ws)
    assert quotas["max_boards"] == 50
    assert quotas["max_open_tickets"] == 10000
    assert quotas["max_members"] == 50
    assert quotas["max_agents"] == 50
    assert quotas["max_attachment_bytes_total"] == 5 * 1024 * 1024 * 1024


async def test_ticket_quota_idempotency_replay(client: AsyncClient, test_db: AsyncSession):
    headers = await _setup_user_workspace(
        test_db, "ws_idem_quota", "usr_idem_owner", policy={"max_open_tickets": 1}
    )
    idem_headers = {**headers, "Idempotency-Key": "test-idem-key-1"}

    # 1. First ticket creation with Idempotency-Key -> 201 Created
    r1 = await client.post("/api/v1/tickets", headers=idem_headers, json={"title": "Idempotent Ticket"})
    assert r1.status_code == 201
    ticket_id = r1.json()["ticket_id"]

    # 2. Next distinct ticket creation fails because quota (1) is reached -> 403 QUOTA_EXCEEDED
    r2 = await client.post("/api/v1/tickets", headers=headers, json={"title": "Exceeding Ticket"})
    assert r2.status_code == 403
    assert r2.json()["error"]["code"] == "QUOTA_EXCEEDED"

    # 3. Replay of 1st ticket with same Idempotency-Key -> returns cached 201 even though quota is reached
    r3 = await client.post("/api/v1/tickets", headers=idem_headers, json={"title": "Idempotent Ticket"})
    assert r3.status_code == 201
    assert r3.json()["ticket_id"] == ticket_id


async def test_quota_rejection_increments_metric(client: AsyncClient, test_db: AsyncSession):
    from ak5.server.metrics import registry

    headers = await _setup_user_workspace(
        test_db, "ws_metric_quota", "usr_metric_owner", policy={"max_boards": 1}
    )

    # 1st board -> Success
    r1 = await client.post("/api/v1/boards", headers=headers, json={"name": "Board 1", "template": "default"})
    assert r1.status_code == 200

    # 2nd board -> 403 QUOTA_EXCEEDED
    r2 = await client.post("/api/v1/boards", headers=headers, json={"name": "Board 2", "template": "default"})
    assert r2.status_code == 403

    metrics_output = registry.collect_all()
    assert 'ak5_quota_rejections_total{quota="max_boards"}' in metrics_output


def test_record_attachment_bytes_compiles_on_postgres_and_sqlite():
    from ak5.server.quota import build_record_attachment_bytes_stmt
    from sqlalchemy.dialects import postgresql, sqlite

    # PostgreSQL compilation
    stmt_pg = build_record_attachment_bytes_stmt("postgresql", "ws_pg", 500)
    compiled_pg = str(stmt_pg.compile(dialect=postgresql.dialect()))
    assert "greatest" in compiled_pg.lower()
    assert "on conflict" in compiled_pg.lower()

    # SQLite compilation
    stmt_sqlite = build_record_attachment_bytes_stmt("sqlite", "ws_sqlite", 500)
    compiled_sqlite = str(stmt_sqlite.compile(dialect=sqlite.dialect()))
    assert "max" in compiled_sqlite.lower()
    assert "on conflict" in compiled_sqlite.lower()


async def test_attachment_upload_overflow_rolls_back_usage_counter(client: AsyncClient, test_db: AsyncSession):
    headers = await _setup_user_workspace(
        test_db, "ws_roll_quota", "usr_roll_owner", policy={"max_attachment_bytes_total": 500}
    )
    t_resp = await client.post("/api/v1/tickets", headers=headers, json={"title": "Rollback Test Ticket"})
    assert t_resp.status_code == 201
    ticket_id = t_resp.json()["ticket_id"]

    # Initial upload of 300 bytes -> OK
    up1 = await client.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        headers=headers,
        files={"file": ("test1.bin", io.BytesIO(b"a" * 300), "application/octet-stream")},
    )
    assert up1.status_code == 201

    row = await test_db.get(WorkspaceUsage, ("ws_roll_quota", "attachment_bytes"))
    assert row is not None
    assert row.value == 300

    # Second upload of 300 bytes exceeds quota (300 + 300 = 600 > 500) -> 403 QUOTA_EXCEEDED
    up2 = await client.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        headers=headers,
        files={"file": ("test2.bin", io.BytesIO(b"b" * 300), "application/octet-stream")},
    )
    assert up2.status_code == 403

    # Ensure counter was not committed/persisted and remains 300
    await test_db.refresh(row)
    assert row.value == 300


async def test_reconcile_attachment_bytes_backfill_and_drift(test_db: AsyncSession):
    """Test #134: reconcile_attachment_bytes corrects un-backfilled usage and drift."""
    from ak5.server.models.actor import Actor
    from ak5.server.models.board import Board
    from ak5.server.models.column import Column
    from ak5.server.models.ticket import Ticket, TicketAttachment
    from ak5.server.quota import reconcile_attachment_bytes

    # 1. Setup workspace, board, column, ticket, attachments
    ws1 = Workspace(workspace_id="ws_rec_1", name="Rec 1")
    ws2 = Workspace(workspace_id="ws_rec_2", name="Rec 2")
    act = Actor(actor_id="act_rec", actor_type="human", name="Rec User", role="dev")
    test_db.add_all([ws1, ws2, act])
    await test_db.flush()

    b1 = Board(board_id="b_rec_1", workspace_id="ws_rec_1", name="Board 1", created_by="act_rec")
    b2 = Board(board_id="b_rec_2", workspace_id="ws_rec_2", name="Board 2", created_by="act_rec")
    test_db.add_all([b1, b2])
    await test_db.flush()

    col1 = Column(column_id="c_rec_1", board_id="b_rec_1", workspace_id="ws_rec_1", name="Col 1", stage="backlog", position=1)
    col2 = Column(column_id="c_rec_2", board_id="b_rec_2", workspace_id="ws_rec_2", name="Col 2", stage="backlog", position=1)
    test_db.add_all([col1, col2])
    await test_db.flush()

    t1 = Ticket(ticket_id="TK_REC_1", workspace_id="ws_rec_1", board_id="b_rec_1", column_id="c_rec_1", title="T1", rank="0|hzzzzz:", created_by="act_rec")
    t2 = Ticket(ticket_id="TK_REC_2", workspace_id="ws_rec_2", board_id="b_rec_2", column_id="c_rec_2", title="T2", rank="0|hzzzzz:", created_by="act_rec")
    test_db.add_all([t1, t2])
    await test_db.flush()

    # ws1 has two attachments: 400 + 600 = 1000 bytes
    att1 = TicketAttachment(
        attachment_id="att_r1", workspace_id="ws_rec_1", ticket_id="TK_REC_1", actor_id="act_rec",
        filename="f1.bin", file_size=400, storage_path="p1"
    )
    att2 = TicketAttachment(
        attachment_id="att_r2", workspace_id="ws_rec_1", ticket_id="TK_REC_1", actor_id="act_rec",
        filename="f2.bin", file_size=600, storage_path="p2"
    )
    # ws2 has one attachment: 250 bytes
    att3 = TicketAttachment(
        attachment_id="att_r3", workspace_id="ws_rec_2", ticket_id="TK_REC_2", actor_id="act_rec",
        filename="f3.bin", file_size=250, storage_path="p3"
    )
    test_db.add_all([att1, att2, att3])

    # ws1 has NO usage row (pre-migration state)
    # ws2 has a DRIFTED usage row (e.g. 9999 bytes)
    # ws3 has a stale usage row (e.g. 500 bytes) but NO attachments
    ws3_usage = WorkspaceUsage(workspace_id="ws_rec_3", key="attachment_bytes", value=500)
    test_db.add(ws3_usage)
    ws2_usage = WorkspaceUsage(workspace_id="ws_rec_2", key="attachment_bytes", value=9999)
    test_db.add(ws2_usage)
    await test_db.commit()

    # Verify initial state
    assert await test_db.get(WorkspaceUsage, ("ws_rec_1", "attachment_bytes")) is None
    assert (await test_db.get(WorkspaceUsage, ("ws_rec_2", "attachment_bytes"))).value == 9999
    assert (await test_db.get(WorkspaceUsage, ("ws_rec_3", "attachment_bytes"))).value == 500

    # Reconcile all workspaces
    reconciled = await reconcile_attachment_bytes(test_db)
    assert reconciled["ws_rec_1"] == 1000
    assert reconciled["ws_rec_2"] == 250
    assert reconciled["ws_rec_3"] == 0
    await test_db.commit()

    # Verify rows in DB
    u1 = await test_db.get(WorkspaceUsage, ("ws_rec_1", "attachment_bytes"))
    assert u1 is not None and u1.value == 1000
    u2 = await test_db.get(WorkspaceUsage, ("ws_rec_2", "attachment_bytes"))
    assert u2 is not None and u2.value == 250
    u3 = await test_db.get(WorkspaceUsage, ("ws_rec_3", "attachment_bytes"))
    assert u3 is not None and u3.value == 0

    # Reconcile single workspace
    # Add new attachment to ws1: +300 -> 1300
    att4 = TicketAttachment(
        attachment_id="att_r4", workspace_id="ws_rec_1", ticket_id="TK_REC_1", actor_id="act_rec",
        filename="f4.bin", file_size=300, storage_path="p4"
    )
    test_db.add(att4)
    await test_db.commit()

    reconciled_single = await reconcile_attachment_bytes(test_db, workspace_id="ws_rec_1")
    assert reconciled_single == {"ws_rec_1": 1300}
    await test_db.commit()

    u1_after = await test_db.get(WorkspaceUsage, ("ws_rec_1", "attachment_bytes"))
    assert u1_after.value == 1300


def test_db_maintenance_cli_reconcile_usage(test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """Test #134: ak5 db maintenance --reconcile-usage runs usage reconciliation."""
    import ak5.cli.commands.db as db_mod
    from ak5.cli.commands.db import maintenance_cmd
    from click.testing import CliRunner
    from conftest import TestAsyncSessionLocal

    monkeypatch.setattr(db_mod, "AsyncSessionLocal", TestAsyncSessionLocal, raising=False)

    runner = CliRunner()
    res = runner.invoke(maintenance_cmd, ["--reconcile-usage"])
    assert res.exit_code == 0
    assert "Cleaned 0 stale attachment part file(s)." in res.output
    assert "Reconciled attachment bytes usage for" in res.output




