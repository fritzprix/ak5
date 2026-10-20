"""``ak5 demo`` — interactive v2 multi-agent collaboration simulation."""

from __future__ import annotations

import asyncio
from typing import Any

import click
import httpx
from ak5.cli.config import get_api_url
from ak5.daemon.credentials import load_credential
from rich.console import Console
from rich.panel import Panel

console = Console()

_DEMO_ACTORS: tuple[dict[str, Any], ...] = (
    {
        "profile": "me",
        "id": "user_pm",
        "type": "human",
        "name": "David (PM)",
        "role": "human",
        "caps": ["planning"],
    },
    {
        "profile": "triage",
        "id": "agent_triage",
        "type": "agent",
        "name": "Triage Agent",
        "role": "triage",
        "caps": ["triage", "routing"],
    },
    {
        "profile": "worker",
        "id": "agent_image_worker",
        "type": "agent",
        "name": "Image Worker",
        "role": "worker",
        "caps": ["image-resize", "webp"],
    },
    {
        "profile": "gatekeeper",
        "id": "agent_code_reviewer",
        "type": "agent",
        "name": "Gatekeeper Reviewer",
        "role": "gatekeeper",
        "caps": ["code-review", "security"],
    },
)


async def _resolve_demo_tokens(
    client: httpx.AsyncClient,
) -> dict[str, tuple[str, str]]:
    """Return ``{profile_key: (token, actor_label)}`` using local profiles or /auth/identify."""
    from ak5.security import identify_headers_from_env

    id_headers = identify_headers_from_env()
    resolved: dict[str, tuple[str, str]] = {}

    for spec in _DEMO_ACTORS:
        profile_name = spec["profile"]
        cred = load_credential(profile_name)
        if cred is not None and cred.status == "active" and cred.access_token:
            # Verify token works against the running server
            probe = await client.get(
                "/boards",
                headers={"Authorization": f"Bearer {cred.access_token}"},
            )
            if probe.status_code == 200:
                actor_label = cred.actor_id or profile_name
                resolved[profile_name] = (cred.access_token, actor_label)
                console.print(
                    f"  ✓ Profile [cyan]{profile_name}[/cyan] active "
                    f"([dim]@{actor_label}, role={spec['role']}[/dim])"
                )
                continue

        resp = await client.post(
            "/auth/identify",
            json={
                "actor_id": spec["id"],
                "actor_type": spec["type"],
                "name": spec["name"],
                "role": spec["role"],
                "capabilities": spec["caps"],
            },
            headers=id_headers,
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        resolved[profile_name] = (token, spec["id"])
        console.print(
            f"  ✓ Actor [cyan]@{spec['id']}[/cyan] active "
            f"([dim]role={spec['role']}[/dim])"
        )

    return resolved


async def run_collaboration_simulation(
    api_url: str,
    *,
    board_id: str = "proj-core-engine",
    auto_approve: bool = False,
    step_delay: float = 1.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Run E2E v2 autonomous multi-agent workflow scenario.

    1. Human PM drops a request into the workspace Inbox (`unclassified`).
    2. Triage Agent claims the Inbox item and routes it to ``board_id`` (`backlog`).
    3. Worker Agent claims the ticket (`in_progress`), posts progress, and submits (`agent_review`).
    4. Gatekeeper Agent claims the review and approves (`human_review`).
    5. (Optional ``--auto-approve``) Human PM approves the ticket to `done`.
    """
    console.print(
        Panel.fit(
            "[bold cyan]AK5 v2 Multi-Agent Workflow Simulation[/bold cyan]\n"
            "[dim]Inbox (Human) ➔ Route (Triage) ➔ Execute (Worker) ➔ Verdict (Gatekeeper) ➔ Human Review[/dim]",
            border_style="cyan",
        )
    )

    async with httpx.AsyncClient(
        base_url=api_url, timeout=30.0, transport=transport
    ) as client:
        console.print("\n[bold yellow][Phase 0] Resolving Local Profiles & Actors...[/bold yellow]")
        tokens = await _resolve_demo_tokens(client)
        pm_token, _pm_id = tokens["me"]
        triage_token, triage_id = tokens["triage"]
        worker_token, worker_id = tokens["worker"]
        gk_token, gk_id = tokens["gatekeeper"]

        # Step 1: Human creates an Inbox request
        console.print("\n[bold yellow][Step 1] Human drops a request into Inbox...[/bold yellow]")
        create_resp = await client.post(
            "/tickets",
            json={
                "title": "Profile Image Optimization System",
                "description": (
                    "Implement automated avatar resizing to 200x200 WebP format "
                    "with decompression-bomb protection."
                ),
            },
            headers={"Authorization": f"Bearer {pm_token}"},
        )
        create_resp.raise_for_status()
        ticket = create_resp.json()
        ticket_id = ticket["ticket_id"]
        console.print(
            f"  ✓ Created Inbox Ticket: [cyan]{ticket_id}[/cyan] "
            f"(stage=[magenta]{ticket.get('stage')}[/magenta])"
        )
        if step_delay > 0:
            await asyncio.sleep(step_delay)

        # Step 2: Triage agent claims and routes to target board backlog
        console.print(
            f"\n[bold yellow][Step 2] Triage Agent (@{triage_id}) routes ticket to {board_id}...[/bold yellow]"
        )
        triage_claim_resp = await client.post(
            f"/tickets/{ticket_id}/claim",
            headers={"Authorization": f"Bearer {triage_token}"},
        )
        triage_claim_resp.raise_for_status()
        triage_claim = triage_claim_resp.json()["claim_token"]

        route_resp = await client.post(
            f"/tickets/{ticket_id}/route",
            json={
                "claim_token": triage_claim,
                "plan": {
                    "target": {"board_id": board_id},
                    "refined_title": "Profile Image Optimization System (WebP 200x200)",
                    "refined_spec": (
                        "Convert avatar uploads to 200x200 WebP format at 85% quality "
                        "and reject images larger than 4096x4096 pixels."
                    ),
                    "acceptance_criteria": [
                        "Convert avatar uploads to 200x200 WebP (85% quality)",
                        "Enforce 4096px max input dimension guard",
                    ],
                },
            },
            headers={"Authorization": f"Bearer {triage_token}"},
        )
        route_resp.raise_for_status()
        ticket = route_resp.json()
        console.print(
            f"  ✓ Ticket [cyan]{ticket_id}[/cyan] routed to [cyan]{board_id}[/cyan] "
            f"➔ stage=[yellow]{ticket.get('stage')}[/yellow]"
        )
        if step_delay > 0:
            await asyncio.sleep(step_delay)

        # Step 3: Worker claims ticket from backlog -> in_progress
        console.print(
            f"\n[bold yellow][Step 3] Worker Agent (@{worker_id}) claims & executes work...[/bold yellow]"
        )
        worker_claim_resp = await client.post(
            f"/tickets/{ticket_id}/claim",
            headers={"Authorization": f"Bearer {worker_token}"},
        )
        worker_claim_resp.raise_for_status()
        claim_data = worker_claim_resp.json()
        worker_claim = claim_data["claim_token"]
        ticket = claim_data["ticket"]
        console.print(
            f"  ✓ {ticket_id} claimed by @{worker_id} "
            f"➔ stage=[yellow]{ticket.get('stage')}[/yellow]"
        )
        if step_delay > 0:
            await asyncio.sleep(step_delay)

        await client.post(
            f"/tickets/{ticket_id}/comments",
            json={
                "content": (
                    "Implemented Pillow WebP transform routine; benchmarked at 12ms "
                    "per 1024x1024 frame with 78% size reduction."
                ),
                "is_internal": True,
            },
            headers={"Authorization": f"Bearer {worker_token}"},
        )

        # Step 4: Worker submits deliverable -> agent_review
        submit_resp = await client.post(
            f"/tickets/{ticket_id}/submit",
            json={
                "claim_token": worker_claim,
                "deliverable": {
                    "summary": (
                        "Added 200x200 WebP converter with 85% quality factor and "
                        "4096px max-dimension check."
                    ),
                    "artifacts": [
                        {
                            "kind": "text",
                            "ref": "Benchmark: 12ms/image, 78% compression ratio, CVE-2023-4863 guard enabled.",
                        }
                    ],
                },
            },
            headers={"Authorization": f"Bearer {worker_token}"},
        )
        submit_resp.raise_for_status()
        ticket = submit_resp.json()
        console.print(
            f"  ✓ Deliverable submitted by @{worker_id} "
            f"➔ stage=[cyan]{ticket.get('stage')}[/cyan]"
        )
        if step_delay > 0:
            await asyncio.sleep(step_delay)

        # Step 5: Gatekeeper claims review and approves -> human_review
        console.print(
            f"\n[bold yellow][Step 4] Gatekeeper Agent (@{gk_id}) audits deliverable...[/bold yellow]"
        )
        gk_claim_resp = await client.post(
            f"/tickets/{ticket_id}/claim",
            headers={"Authorization": f"Bearer {gk_token}"},
        )
        gk_claim_resp.raise_for_status()
        gk_claim = gk_claim_resp.json()["claim_token"]

        verdict_resp = await client.post(
            f"/tickets/{ticket_id}/verdict",
            json={
                "claim_token": gk_claim,
                "verdict": {
                    "decision": "approve",
                    "reason": (
                        "LGTM! Verified 4096px decompression-bomb guard and WebP "
                        "buffer bounds."
                    ),
                    "findings": [
                        {
                            "criterion": "Convert avatar uploads to 200x200 WebP (85% quality)",
                            "pass": True,
                            "note": "Verified 200x200 output dimensions and quality factor.",
                        },
                        {
                            "criterion": "Enforce 4096px max input dimension guard",
                            "pass": True,
                            "note": "Verified CVE-2023-4863 pixel bound check.",
                        },
                    ],
                },
            },
            headers={"Authorization": f"Bearer {gk_token}"},
        )
        verdict_resp.raise_for_status()
        ticket = verdict_resp.json()
        console.print(
            f"  ✓ Gatekeeper approved {ticket_id} "
            f"➔ stage=[green]{ticket.get('stage')}[/green]"
        )

        if auto_approve:
            if step_delay > 0:
                await asyncio.sleep(step_delay)
            console.print(
                "\n[bold yellow][Step 5] Human PM approves final sign-off...[/bold yellow]"
            )
            rev_resp = await client.post(
                f"/tickets/{ticket_id}/verdict",
                json={
                    "verdict": {
                        "decision": "approve",
                        "reason": "Approved for production release.",
                    }
                },
                headers={"Authorization": f"Bearer {pm_token}"},
            )
            rev_resp.raise_for_status()
            ticket = rev_resp.json()
            console.print(
                f"  ✓ Ticket [cyan]{ticket_id}[/cyan] signed off "
                f"➔ stage=[bold green]{ticket.get('stage')}[/bold green]"
            )

        if auto_approve:
            next_hint = f"Ticket [cyan]{ticket_id}[/cyan] is now in [bold green]Done[/bold green]!"
        else:
            next_hint = (
                f"Ticket [cyan]{ticket_id}[/cyan] is now in [bold green]Human Review[/bold green]!\n"
                f"👉 Approve it in the Web Dashboard or run:\n"
                f"   [cyan]ak5 ticket verdict {ticket_id} --approve --reason \"Ship it\"[/cyan]"
            )

        console.print(
            Panel.fit(
                "[bold green]🎉 v2 Multi-Agent Simulation Completed![/bold green]\n"
                f"{next_hint}",
                border_style="green",
            )
        )
        return ticket


@click.command("demo")
@click.option(
    "--board",
    "board_id",
    default="proj-core-engine",
    show_default=True,
    help="Target board ID for routing the demo ticket",
)
@click.option(
    "--auto-approve",
    is_flag=True,
    help="Also run the final human sign-off step to move the ticket to Done",
)
def demo_command(board_id: str, auto_approve: bool) -> None:
    """Run an interactive v2 multi-agent collaboration demo on your local board."""
    api_url = get_api_url()
    try:
        asyncio.run(
            run_collaboration_simulation(
                api_url,
                board_id=board_id,
                auto_approve=auto_approve,
            )
        )
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Cannot connect to AK5 Gateway at {api_url}[/bold red]")
        console.print(
            "Start the local AK5 environment first in another terminal:\n"
            "  [bold cyan]ak5 up[/bold cyan]"
        )
    except Exception as exc:
        console.print(f"[bold red]✗ Simulation failed:[/bold red] {exc}")
