"""Issue #33: board template registry + claim checklist fields."""

from __future__ import annotations

import json

import pytest
from ak5.models.actor import Actor
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
