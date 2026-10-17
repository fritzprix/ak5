"""Issue #33: board template registry + claim checklist fields."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ak5.models.actor import Actor
from ak5.paths import get_app_data_dir
from ak5.services.templates import (
    CODING_TEMPLATE,
    RESEARCH_TEMPLATE,
    get_template,
    list_templates,
)
from helpers_workflow import board_ticket
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_actors(db: AsyncSession) -> None:
    actors = [
        Actor(
            actor_id="agent_worker_tpl",
            actor_type="agent",
            name="Worker",
            role="worker",
            capabilities=json.dumps(["python", "git", "research"]),
            status="idle",
        ),
        Actor(
            actor_id="agent_gk_tpl",
            actor_type="agent",
            name="Gatekeeper",
            role="gatekeeper",
            capabilities=json.dumps([]),
            status="idle",
        ),
    ]
    for a in actors:
        if await db.get(Actor, a.actor_id) is None:
            db.add(a)
    await db.commit()


def _h(auth_headers, actor_id: str) -> dict[str, str]:
    return auth_headers(actor_id, "agent")


def test_builtin_templates_have_distinct_checklists():
    coding = get_template("coding")
    research = get_template("research")
    assert coding is CODING_TEMPLATE
    assert research is RESEARCH_TEMPLATE
    assert coding.review_checklist != research.review_checklist
    assert coding.review_checklist
    assert research.review_checklist
    assert "coding" in list_templates()
    assert "research" in list_templates()


@pytest.mark.asyncio
async def test_unknown_template_create_board_payload_invalid(
    client: AsyncClient, auth_headers
):
    resp = await client.post(
        "/api/v1/boards",
        headers=auth_headers("user_pm", "human"),
        json={"name": "Nope", "board_id": "board-bad-tmpl", "template": "no-such-tmpl"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "PAYLOAD_INVALID"


@pytest.mark.asyncio
async def test_gatekeeper_claim_checklist_differs_by_template(
    client: AsyncClient, auth_headers, test_db: AsyncSession
):
    """Completion condition: two templates → different gatekeeper claim checklists."""
    await _seed_actors(test_db)
    pm = auth_headers("user_pm", "human")
    worker = _h(auth_headers, "agent_worker_tpl")
    gk = _h(auth_headers, "agent_gk_tpl")

    for board_id, template in (
        ("board-coding-tpl", "coding"),
        ("board-research-tpl", "research"),
    ):
        created = await client.post(
            "/api/v1/boards",
            headers=pm,
            json={"name": board_id, "board_id": board_id, "template": template},
        )
        assert created.status_code == 200, created.text
        assert created.json()["template"] == template

    checklists: dict[str, list[str]] = {}
    for board_id, template in (
        ("board-coding-tpl", "coding"),
        ("board-research-tpl", "research"),
    ):
        created = await client.post(
            "/api/v1/tickets",
            headers=pm,
            json=board_ticket(f"work-{template}", board_id=board_id),
        )
        assert created.status_code in (200, 201), created.text
        tid = created.json()["ticket_id"]

        wclaim = await client.post(f"/api/v1/tickets/{tid}/claim", headers=worker)
        assert wclaim.status_code == 200, wclaim.text
        wbody = wclaim.json()
        assert wbody["acceptance_criteria_defaults"] == list(
            get_template(template).acceptance_criteria_defaults
        )
        token = wbody["claim_token"]

        submitted = await client.post(
            f"/api/v1/tickets/{tid}/submit",
            headers=worker,
            json={
                "claim_token": token,
                "deliverable": {
                    "summary": "done",
                    "artifacts": [],
                    "criteria_self_check": [],
                },
            },
        )
        assert submitted.status_code == 200, submitted.text

        gclaim = await client.post(f"/api/v1/tickets/{tid}/claim", headers=gk)
        assert gclaim.status_code == 200, gclaim.text
        gbody = gclaim.json()
        checklists[template] = gbody["review_checklist"]
        assert checklists[template] == list(get_template(template).review_checklist)

    assert checklists["coding"] != checklists["research"]


@pytest.mark.asyncio
async def test_apply_template_adds_columns_without_deleting(
    client: AsyncClient, auth_headers
):
    pm = auth_headers("user_pm", "human")
    created = await client.post(
        "/api/v1/boards",
        headers=pm,
        json={"name": "Apply Me", "board_id": "board-apply-tpl", "template": "default"},
    )
    assert created.status_code == 200
    before = await client.get("/api/v1/boards/board-apply-tpl", headers=pm)
    stages_before = {c["stage"] for c in before.json()["columns"]}

    patched = await client.patch(
        "/api/v1/boards/board-apply-tpl",
        headers=pm,
        json={"template": "coding"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["template"] == "coding"

    after = await client.get("/api/v1/boards/board-apply-tpl", headers=pm)
    stages_after = {c["stage"] for c in after.json()["columns"]}
    assert stages_before <= stages_after


@pytest.mark.asyncio
async def test_templates_api_list_and_get(client: AsyncClient, auth_headers):
    pm = auth_headers("user_pm", "human")
    listed = await client.get("/api/v1/templates", headers=pm)
    assert listed.status_code == 200, listed.text
    names = {row["name"] for row in listed.json()}
    assert {"default", "coding", "research", "inbox"} <= names
    coding = next(r for r in listed.json() if r["name"] == "coding")
    assert coding["source"] == "builtin"
    assert coding["review_checklist"]

    shown = await client.get("/api/v1/templates/coding", headers=pm)
    assert shown.status_code == 200
    assert shown.json()["name"] == "coding"
    assert shown.json()["columns"]

    missing = await client.get("/api/v1/templates/no-such-tmpl", headers=pm)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_workspace_template_visible_via_api_and_apply(
    client: AsyncClient, auth_headers
):
    """Server-side workspace JSON appears in GET /templates and can be applied."""
    pm = auth_headers("user_pm", "human")
    tmpl_dir = get_app_data_dir() / "templates"
    tmpl_dir.mkdir(parents=True, exist_ok=True)
    custom = {
        "name": "docs-ws",
        "max_attempts": 2,
        "worker_capabilities": ["docs"],
        "acceptance_criteria_defaults": ["outline complete"],
        "review_checklist": ["structure ok", "links valid"],
        "columns": [
            {"name": "Backlog", "stage": "backlog", "position": 1, "wip_limit": 0},
            {"name": "In Progress", "stage": "in_progress", "position": 2, "wip_limit": 2},
            {"name": "Agent Review", "stage": "agent_review", "position": 3, "wip_limit": 2},
            {
                "name": "Postmortem Pending",
                "stage": "postmortem_pending",
                "position": 4,
                "wip_limit": 0,
            },
            {"name": "Human Review", "stage": "human_review", "position": 5, "wip_limit": 0},
            {"name": "Done", "stage": "done", "position": 6, "wip_limit": 0},
        ],
    }
    Path(tmpl_dir / "docs-ws.json").write_text(json.dumps(custom), encoding="utf-8")

    listed = await client.get("/api/v1/templates", headers=pm)
    assert listed.status_code == 200
    row = next(r for r in listed.json() if r["name"] == "docs-ws")
    assert row["source"] == "workspace"
    assert row["review_checklist"] == ["structure ok", "links valid"]

    shown = await client.get("/api/v1/templates/docs-ws", headers=pm)
    assert shown.status_code == 200
    assert shown.json()["worker_capabilities"] == ["docs"]

    created = await client.post(
        "/api/v1/boards",
        headers=pm,
        json={"name": "Docs Board", "board_id": "board-docs-ws", "template": "default"},
    )
    assert created.status_code == 200
    applied = await client.patch(
        "/api/v1/boards/board-docs-ws",
        headers=pm,
        json={"template": "docs-ws"},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["template"] == "docs-ws"
