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


async def test_event_retention_days_policy_purge(test_db: AsyncSession):
    """Test #138: workspace event_retention_days policy purges expired events while 0=unlimited."""
    from datetime import UTC, datetime, timedelta

    from ak5.server.models.event import Event
    from ak5.server.services.subscription_service import subscription_service

    # Workspace 1: 3 days retention
    ws1 = Workspace(workspace_id="ws_ret_3", name="Ret 3", policy=json.dumps({"event_retention_days": 3}))
    # Workspace 2: 0 = unlimited retention
    ws2 = Workspace(workspace_id="ws_unlim", name="Unlimited", policy=json.dumps({"event_retention_days": 0}))
    test_db.add_all([ws1, ws2])
    await test_db.commit()

    now = datetime.now(UTC).replace(tzinfo=None)
    # ws1 expired event (5 days old)
    e1_old = Event(
        event_id="ev_ws1_old",
        workspace_id="ws_ret_3",
        type="TEST_EVENT",
        created_at=now - timedelta(days=5),
    )
    # ws1 fresh event (1 day old)
    e1_fresh = Event(
        event_id="ev_ws1_fresh",
        workspace_id="ws_ret_3",
        type="TEST_EVENT",
        created_at=now - timedelta(days=1),
    )
    # ws2 old event (10 days old, but unlimited retention)
    e2_old = Event(
        event_id="ev_ws2_old",
        workspace_id="ws_unlim",
        type="TEST_EVENT",
        created_at=now - timedelta(days=10),
    )
    test_db.add_all([e1_old, e1_fresh, e2_old])
    await test_db.commit()

    deleted = await subscription_service.purge_expired_events(test_db, force=True)
    await test_db.commit()

    assert deleted == 1
    assert await test_db.get(Event, e1_old.seq) is None
    assert await test_db.get(Event, e1_fresh.seq) is not None
    assert await test_db.get(Event, e2_old.seq) is not None


async def test_event_retention_unexpired_preserved_and_expired_purged(test_db: AsyncSession):
    """Test #138: unexpired events are preserved and expired events are purged per workspace policy."""
    from datetime import UTC, datetime, timedelta

    from ak5.server.models.event import Event
    from ak5.server.services.subscription_service import SubscriptionServiceError, subscription_service

    ws = Workspace(workspace_id="ws_sub_guard", name="Sub Guard", policy=json.dumps({"event_retention_days": 2}))
    test_db.add(ws)
    await test_db.commit()

    now = datetime.now(UTC).replace(tzinfo=None)
    # Event 0 & 1 are 5 days old (older than 2 days retention -> expired)
    ev0 = Event(
        event_id="ev_guarded_0",
        workspace_id="ws_sub_guard",
        type="WORK_AVAILABLE",
        created_at=now - timedelta(days=5),
    )
    ev1 = Event(
        event_id="ev_guarded_1",
        workspace_id="ws_sub_guard",
        type="WORK_AVAILABLE",
        created_at=now - timedelta(days=5),
    )
    # Event 2 is 1 day old (within 2 days retention -> unexpired / preserved)
    ev2 = Event(
        event_id="ev_guarded_2",
        workspace_id="ws_sub_guard",
        type="WORK_AVAILABLE",
        created_at=now - timedelta(days=1),
    )
    test_db.add_all([ev0, ev1, ev2])
    await test_db.commit()

    deleted = await subscription_service.purge_expired_events(test_db, force=True)
    await test_db.commit()
    assert deleted == 2
    # ev0, ev1 are purged, ev2 is preserved
    assert await test_db.get(Event, ev0.seq) is None
    assert await test_db.get(Event, ev1.seq) is None
    assert await test_db.get(Event, ev2.seq) is not None

    # Stale cursor pointing below purged_through raises CURSOR_EXPIRED
    with pytest.raises(SubscriptionServiceError) as ei:
        await subscription_service.ensure_cursor_valid(test_db, cursor=ev0.seq, workspace_id="ws_sub_guard")
    assert ei.value.code == ErrorCode.CURSOR_EXPIRED


def test_db_maintenance_cli_purges_events(test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """Test #138: ak5 db maintenance runs routine storage and event-log maintenance."""
    import ak5.cli.commands.db as db_mod
    from ak5.cli.commands.db import maintenance_cmd
    from click.testing import CliRunner
    from conftest import TestAsyncSessionLocal

    monkeypatch.setattr(db_mod, "AsyncSessionLocal", TestAsyncSessionLocal, raising=False)

    runner = CliRunner()
    res = runner.invoke(maintenance_cmd, ["--clean-parts-older-than", "3600"])
    assert res.exit_code == 0, res.output
    assert "Cleaned" in res.output
    assert "Purged" in res.output


async def test_event_retention_lagging_subscription_safety_rule(test_db: AsyncSession):
    """Test #138: un-acked old events survive while acked old events are purged (safety rule)."""
    from datetime import UTC, datetime, timedelta

    from ak5.server.models.event import Event
    from ak5.server.models.subscription import Subscription
    from ak5.server.services.subscription_service import subscription_service

    ws = Workspace(
        workspace_id="ws_lag_safety",
        name="Lag Safety",
        policy=json.dumps({"event_retention_days": 2}),
    )
    test_db.add(ws)
    await test_db.commit()

    now = datetime.now(UTC).replace(tzinfo=None)
    # Create 3 events, all 5 days old (exceeding 2 days retention window)
    e1 = Event(
        event_id="ev_lag_1",
        workspace_id="ws_lag_safety",
        type="WORK_AVAILABLE",
        created_at=now - timedelta(days=5),
    )
    e2 = Event(
        event_id="ev_lag_2",
        workspace_id="ws_lag_safety",
        type="WORK_AVAILABLE",
        created_at=now - timedelta(days=5),
    )
    e3 = Event(
        event_id="ev_lag_3",
        workspace_id="ws_lag_safety",
        type="WORK_AVAILABLE",
        created_at=now - timedelta(days=5),
    )
    test_db.add_all([e1, e2, e3])
    await test_db.commit()

    # Create active subscription with ack_cursor = 0 (has not acked any events yet)
    sub = Subscription(
        subscription_id="sub_lag_1",
        workspace_id="ws_lag_safety",
        ack_cursor=0,
    )
    test_db.add(sub)
    await test_db.commit()

    # Step 1: Purge runs, but because ack_cursor=0, none of the events may be purged
    del_0 = await subscription_service.purge_expired_events(test_db, force=True)
    await test_db.commit()
    assert del_0 == 0
    assert await test_db.get(Event, e1.seq) is not None
    assert await test_db.get(Event, e2.seq) is not None
    assert await test_db.get(Event, e3.seq) is not None

    # Step 2: Consumer advances ack_cursor to e1.seq
    sub.ack_cursor = e1.seq
    await test_db.commit()

    del_1 = await subscription_service.purge_expired_events(test_db, force=True)
    await test_db.commit()
    assert del_1 == 1
    # e1 (seq <= e1.seq) is purged; e2, e3 (seq > e1.seq) survive despite being 5 days old!
    assert await test_db.get(Event, e1.seq) is None
    assert await test_db.get(Event, e2.seq) is not None
    assert await test_db.get(Event, e3.seq) is not None

    # Step 3: Consumer advances ack_cursor past e3.seq
    sub.ack_cursor = e3.seq
    await test_db.commit()

    del_2 = await subscription_service.purge_expired_events(test_db, force=True)
    await test_db.commit()
    assert del_2 == 2
    assert await test_db.get(Event, e2.seq) is None
    assert await test_db.get(Event, e3.seq) is None


async def test_event_retention_cross_workspace_cursor_isolation(test_db: AsyncSession):
    """Test #138: purging workspace A must not cause CURSOR_EXPIRED on workspace B (retention 0)."""
    from datetime import UTC, datetime, timedelta

    from ak5.server.models.event import Event
    from ak5.server.services.subscription_service import SubscriptionServiceError, subscription_service

    ws_a = Workspace(
        workspace_id="ws_iso_a",
        name="Workspace A",
        policy=json.dumps({"event_retention_days": 2}),
    )
    ws_b = Workspace(
        workspace_id="ws_iso_b",
        name="Workspace B",
        policy=json.dumps({"event_retention_days": 0}),  # 0 = unlimited
    )
    test_db.add_all([ws_a, ws_b])
    await test_db.commit()

    now = datetime.now(UTC).replace(tzinfo=None)
    # Old events for ws_a
    ev_a1 = Event(
        event_id="ev_a1",
        workspace_id="ws_iso_a",
        type="WORK_AVAILABLE",
        created_at=now - timedelta(days=5),
    )
    ev_a2 = Event(
        event_id="ev_a2",
        workspace_id="ws_iso_a",
        type="WORK_AVAILABLE",
        created_at=now - timedelta(days=5),
    )
    # Events for ws_b
    ev_b1 = Event(
        event_id="ev_b1",
        workspace_id="ws_iso_b",
        type="WORK_AVAILABLE",
        created_at=now - timedelta(days=5),
    )
    test_db.add_all([ev_a1, ev_a2, ev_b1])
    await test_db.commit()

    # ws_a has no active subscription -> purge deletes ev_a1, ev_a2
    del_count = await subscription_service.purge_expired_events(test_db, force=True)
    await test_db.commit()
    assert del_count == 2
    assert await test_db.get(Event, ev_a1.seq) is None
    assert await test_db.get(Event, ev_a2.seq) is None
    # ws_b event is preserved (retention 0)
    assert await test_db.get(Event, ev_b1.seq) is not None

    # ws_a cursor check raises CURSOR_EXPIRED for purged events
    with pytest.raises(SubscriptionServiceError) as ei:
        await subscription_service.ensure_cursor_valid(test_db, cursor=ev_a1.seq, workspace_id="ws_iso_a")
    assert ei.value.code == ErrorCode.CURSOR_EXPIRED

    # ws_b cursor check with a low cursor (e.g. 1, below ws_a's purged seq) must NOT raise
    purged_b = await subscription_service.get_purged_through_seq(test_db, workspace_id="ws_iso_b")
    assert purged_b == 0
    await subscription_service.ensure_cursor_valid(test_db, cursor=1, workspace_id="ws_iso_b")


async def test_event_retention_abandoned_subscription_does_not_block_purge(test_db: AsyncSession):
    """Test #149: abandoned subscriptions (lease expired or created > 300s ago without lease)
    must not pin event retention; expired events are purged and CURSOR_EXPIRED is raised on access."""
    from datetime import UTC, datetime, timedelta

    from ak5.contracts.errors import ErrorCode
    from ak5.server.models.event import Event
    from ak5.server.models.subscription import Subscription
    from ak5.server.services.subscription_service import SubscriptionServiceError, subscription_service

    ws = Workspace(
        workspace_id="ws_abandoned_test",
        name="Abandoned Test",
        policy=json.dumps({"event_retention_days": 2}),
    )
    test_db.add(ws)
    await test_db.commit()

    now = datetime.now(UTC).replace(tzinfo=None)
    ev1 = Event(
        event_id="ev_ab_1",
        workspace_id="ws_abandoned_test",
        type="WORK_AVAILABLE",
        created_at=now - timedelta(days=5),
    )
    ev2 = Event(
        event_id="ev_ab_2",
        workspace_id="ws_abandoned_test",
        type="WORK_AVAILABLE",
        created_at=now - timedelta(days=5),
    )
    test_db.add_all([ev1, ev2])
    await test_db.commit()

    # Sub A: lease expired 1 minute ago, ack_cursor = 0
    sub_expired_lease = Subscription(
        subscription_id="sub_ab_lease_expired",
        workspace_id="ws_abandoned_test",
        ack_cursor=0,
        consumer_id="old_daemon",
        consumer_lease_expires_at=now - timedelta(seconds=60),
        created_at=now - timedelta(hours=2),
    )
    # Sub B: created 10 minutes ago, never polled/leased, ack_cursor = 0
    sub_never_leased = Subscription(
        subscription_id="sub_ab_never_leased",
        workspace_id="ws_abandoned_test",
        ack_cursor=0,
        consumer_lease_expires_at=None,
        created_at=now - timedelta(minutes=10),
    )
    test_db.add_all([sub_expired_lease, sub_never_leased])
    await test_db.commit()

    # Purge runs: both subscriptions are abandoned (inactive), so they do NOT hold back retention.
    del_count = await subscription_service.purge_expired_events(test_db, force=True)
    await test_db.commit()

    assert del_count == 2
    assert await test_db.get(Event, ev1.seq) is None
    assert await test_db.get(Event, ev2.seq) is None

    # Abandoned subscriptions attempting to poll with old cursor get CURSOR_EXPIRED
    with pytest.raises(SubscriptionServiceError) as exc_info:
        await subscription_service.ensure_cursor_valid(
            test_db, cursor=ev1.seq, workspace_id="ws_abandoned_test"
        )
    assert exc_info.value.code == ErrorCode.CURSOR_EXPIRED


async def test_event_retention_active_and_abandoned_subscription_coexistence(test_db: AsyncSession):
    """Test #149: active subscription's ack_cursor governs retention safety rule;
    abandoned subscription's low cursor is ignored."""
    from datetime import UTC, datetime, timedelta

    from ak5.server.models.event import Event
    from ak5.server.models.subscription import Subscription
    from ak5.server.services.subscription_service import subscription_service

    ws = Workspace(
        workspace_id="ws_coexist_test",
        name="Coexistence Test",
        policy=json.dumps({"event_retention_days": 2}),
    )
    test_db.add(ws)
    await test_db.commit()

    now = datetime.now(UTC).replace(tzinfo=None)
    e1 = Event(
        event_id="ev_co_1",
        workspace_id="ws_coexist_test",
        type="WORK_AVAILABLE",
        created_at=now - timedelta(days=5),
    )
    e2 = Event(
        event_id="ev_co_2",
        workspace_id="ws_coexist_test",
        type="WORK_AVAILABLE",
        created_at=now - timedelta(days=5),
    )
    test_db.add_all([e1, e2])
    await test_db.commit()

    # Sub Abandoned: ack_cursor = 0, lease expired
    sub_abandoned = Subscription(
        subscription_id="sub_co_abandoned",
        workspace_id="ws_coexist_test",
        ack_cursor=0,
        consumer_id="dead_worker",
        consumer_lease_expires_at=now - timedelta(seconds=120),
        created_at=now - timedelta(days=1),
    )
    # Sub Active: ack_cursor = e1.seq, lease active for next 60s
    sub_active = Subscription(
        subscription_id="sub_co_active",
        workspace_id="ws_coexist_test",
        ack_cursor=e1.seq,
        consumer_id="live_worker",
        consumer_lease_expires_at=now + timedelta(seconds=60),
        created_at=now - timedelta(minutes=1),
    )
    test_db.add_all([sub_abandoned, sub_active])
    await test_db.commit()

    # Purge runs:
    # Abandoned sub (ack_cursor=0) is ignored!
    # Active sub (ack_cursor=e1.seq) protects e2 (un-acked), but allows e1 (acked) to be purged!
    del_count = await subscription_service.purge_expired_events(test_db, force=True)
    await test_db.commit()

    assert del_count == 1
    assert await test_db.get(Event, e1.seq) is None
    assert await test_db.get(Event, e2.seq) is not None





