import asyncio

import click
import httpx
from ak5.cli.config import get_api_url
from rich.console import Console
from rich.panel import Panel

console = Console()


async def run_collaboration_simulation(api_url: str) -> None:
    """Run E2E autonomous multi-agent collaboration scenario.

    1. Human PM creates a feature ticket: "Profile Image Optimization System"
    2. Orchestrator Agent inspects ticket, discovers available agents via capability search
    3. Orchestrator delegates Subtask 1 to @agent_image_worker ("WebP Conversion Routine")
    4. Orchestrator delegates Subtask 2 to @agent_code_reviewer ("Review & Security Audit")
    5. @agent_image_worker moves subtask to In Progress, writes execution context, moves to Done
    6. @agent_code_reviewer reviews subtask, signs off with comment, moves to Done
    7. Orchestrator verifies all subtasks are done, closes parent ticket to Done.
    """
    console.print(Panel.fit(
        "[bold cyan]AK5 Autonomous Multi-Agent Collaboration Simulation[/bold cyan]\n"
        "[dim]Human PM ➔ Orchestrator Agent ➔ Image Worker Agent & Reviewer Agent[/dim]",
        border_style="cyan",
    ))

    async with httpx.AsyncClient(base_url=api_url, timeout=30.0) as client:
        # Step 0: Ensure actors
        console.print("\n[bold yellow][Phase 0] Registering Actors...[/bold yellow]")
        for actor in [
            {"id": "user_pm", "type": "human", "name": "David (PM)", "role": "PM", "caps": ["planning"]},
            {"id": "agent_orchestrator", "type": "agent", "name": "Orchestrator Agent", "role": "Lead Architect", "caps": ["orchestration", "breakdown"]},
            {"id": "agent_image_worker", "type": "agent", "name": "Image Worker", "role": "Media Specialist", "caps": ["image-resize", "webp"]},
            {"id": "agent_code_reviewer", "type": "agent", "name": "Code Reviewer", "role": "Senior Reviewer", "caps": ["code-review", "security"]},
        ]:
            resp = await client.post("/auth/identify", json={
                "actor_id": actor["id"],
                "actor_type": actor["type"],
                "name": actor["name"],
                "role": actor["role"],
                "capabilities": actor["caps"],
            })
            resp.raise_for_status()
            token = resp.json()["access_token"]
            if actor["id"] == "user_pm":
                pm_token = token
            elif actor["id"] == "agent_orchestrator":
                orch_token = token
            elif actor["id"] == "agent_image_worker":
                worker_token = token
            elif actor["id"] == "agent_code_reviewer":
                reviewer_token = token
            console.print(f"  ✓ Actor @{actor['id']} active [{actor['type']}]")

        board_id = "proj-core-engine"
        board_resp = await client.get(f"/boards/{board_id}")
        board_resp.raise_for_status()
        board_data = board_resp.json()
        col_map = {c["stage"]: c["column_id"] for c in board_data["columns"]}

        # Step 1: Human PM registers master ticket
        console.print("\n[bold yellow][Step 1] Human PM registers master ticket...[/bold yellow]")
        t_resp = await client.post(
            "/tickets",
            json={
                "title": "Profile Image Optimization System",
                "description": "Implement automated avatar resizing to 200x200 WebP format with security audit.",
                "board_id": board_id,
                "column_id": col_map["open"],
                "priority": "high",
                "assigned_to": "agent_orchestrator",
                "labels": ["avatar", "optimization"],
            },
            headers={"Authorization": f"Bearer {pm_token}"},
        )
        t_resp.raise_for_status()
        parent_ticket = t_resp.json()
        parent_id = parent_ticket["ticket_id"]
        console.print(f"  ✓ Created Parent Ticket: [cyan]{parent_id}[/cyan] ('{parent_ticket['title']}')")
        await asyncio.sleep(1)

        # Step 2: Orchestrator agent picks up ticket & moves to In Progress
        console.print("\n[bold yellow][Step 2] Orchestrator analyzes requirements & searches capabilities...[/bold yellow]")
        await client.patch(
            f"/tickets/{parent_id}/move",
            json={"target_column_id": col_map["in_progress"]},
            headers={"Authorization": f"Bearer {orch_token}"},
        )
        console.print(f"  ✓ Ticket {parent_id} moved to [yellow]In Progress[/yellow] by @agent_orchestrator")

        # Discovery query: find image worker
        disc_img = (await client.get("/actors/discovery?capability=image-resize")).json()
        image_agent_id = disc_img[0]["actor_id"]
        console.print(f"  ✓ Discovered specialist agent for image optimization: [magenta]@{image_agent_id}[/magenta]")

        # Discovery query: find reviewer
        disc_rev = (await client.get("/actors/discovery?capability=code-review")).json()
        reviewer_agent_id = disc_rev[0]["actor_id"]
        console.print(f"  ✓ Discovered specialist agent for review: [magenta]@{reviewer_agent_id}[/magenta]")
        await asyncio.sleep(1)

        # Step 3: Orchestrator delegates Subtask 1 to Image Worker
        console.print("\n[bold yellow][Step 3] Orchestrator delegates Subtask 1 to Worker...[/bold yellow]")
        del1_resp = await client.post(
            f"/tickets/{parent_id}/delegate",
            json={
                "target_actor_id": image_agent_id,
                "subtask_title": "Implement WebP Converter Routine",
                "subtask_description": "Convert raw PNG/JPEG buffer to 200x200 WebP with 85% quality factor.",
                "priority": "high",
                "labels": ["worker", "webp"],
            },
            headers={"Authorization": f"Bearer {orch_token}"},
        )
        sub1 = del1_resp.json()
        sub1_id = sub1["ticket_id"]
        console.print(f"  ✓ Subtask [cyan]{sub1_id}[/cyan] delegated to [magenta]@{image_agent_id}[/magenta]")
        await asyncio.sleep(1)

        # Step 4: Worker processes Subtask 1
        console.print("\n[bold yellow][Step 4] Image Worker processes Subtask 1...[/bold yellow]")
        await client.patch(
            f"/tickets/{sub1_id}/move",
            json={"target_column_id": col_map["in_progress"]},
            headers={"Authorization": f"Bearer {worker_token}"},
        )
        console.print(f"  ✓ {sub1_id} status ➔ [yellow]In Progress[/yellow]")

        await client.post(
            f"/tickets/{sub1_id}/comments",
            json={
                "content": "Generated PIL image transform logic; benchmarked at 12ms per 1024x1024 input.",
                "is_internal": True,
                "metadata": {"execution_time_ms": 12, "compression_ratio": "78%"},
            },
            headers={"Authorization": f"Bearer {worker_token}"},
        )

        await client.patch(
            f"/tickets/{sub1_id}/move",
            json={"target_column_id": col_map["done"]},
            headers={"Authorization": f"Bearer {worker_token}"},
        )
        console.print(f"  ✓ {sub1_id} status ➔ [green]Done[/green] with artifact context")
        await asyncio.sleep(1)

        # Step 5: Orchestrator delegates Subtask 2 to Code Reviewer
        console.print("\n[bold yellow][Step 5] Orchestrator delegates Subtask 2 for Review...[/bold yellow]")
        del2_resp = await client.post(
            f"/tickets/{parent_id}/delegate",
            json={
                "target_actor_id": reviewer_agent_id,
                "subtask_title": "Security & Boundary Review for WebP Converter",
                "subtask_description": "Audit buffer overflow checks and decompression bomb protection.",
                "priority": "urgent",
                "labels": ["security", "audit"],
            },
            headers={"Authorization": f"Bearer {orch_token}"},
        )
        sub2 = del2_resp.json()
        sub2_id = sub2["ticket_id"]
        console.print(f"  ✓ Subtask [cyan]{sub2_id}[/cyan] delegated to [magenta]@{reviewer_agent_id}[/magenta]")
        await asyncio.sleep(1)

        # Step 6: Code Reviewer processes Subtask 2
        console.print("\n[bold yellow][Step 6] Code Reviewer audits implementation...[/bold yellow]")
        await client.patch(
            f"/tickets/{sub2_id}/move",
            json={"target_column_id": col_map["in_progress"]},
            headers={"Authorization": f"Bearer {reviewer_token}"},
        )

        await client.post(
            f"/tickets/{sub2_id}/comments",
            json={
                "content": "LGTM! Input pixel limit set to 4096 to prevent memory denial-of-service. Approved.",
                "is_internal": False,
                "metadata": {"approved": True, "cve_checked": ["CVE-2023-4863"]},
            },
            headers={"Authorization": f"Bearer {reviewer_token}"},
        )

        await client.patch(
            f"/tickets/{sub2_id}/move",
            json={"target_column_id": col_map["done"]},
            headers={"Authorization": f"Bearer {reviewer_token}"},
        )
        console.print(f"  ✓ {sub2_id} status ➔ [green]Done[/green]")
        await asyncio.sleep(1)

        # Step 7: Orchestrator closes Parent Ticket
        console.print("\n[bold yellow][Step 7] Orchestrator closes Parent Ticket...[/bold yellow]")
        final_check = (await client.get(f"/tickets/{parent_id}")).json()
        console.print(f"  Subtasks completion status: [green]{final_check['subtask_done_count']}/{final_check['subtask_count']} Done[/green]")

        await client.patch(
            f"/tickets/{parent_id}/move",
            json={"target_column_id": col_map["done"]},
            headers={"Authorization": f"Bearer {orch_token}"},
        )
        await client.post(
            f"/tickets/{parent_id}/comments",
            json={
                "content": "All subtasks (implementation + security review) successfully completed. Closing ticket.",
                "is_internal": False,
            },
            headers={"Authorization": f"Bearer {orch_token}"},
        )
        console.print(f"  ✓ Master Ticket [cyan]{parent_id}[/cyan] successfully resolved and moved to [green]Done[/green]!")

        console.print(Panel.fit(
            "[bold green]🎉 E2E Collaboration Simulation Completed Successfully![/bold green]\n"
            f"View live board: [cyan]ak5 board --board-id {board_id}[/cyan]",
            border_style="green",
        ))


@click.command("demo")
def demo_command() -> None:
    """Run automated autonomous multi-agent collaboration demonstration."""
    api_url = get_api_url()
    try:
        asyncio.run(run_collaboration_simulation(api_url))
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Cannot connect to AK5 Gateway at {api_url}[/bold red]")
        console.print("Please start the backend server in another terminal:\n  [yellow]uv run uvicorn ak5.main:app[/yellow]")
    except Exception as e:
        console.print(f"[bold red]✗ Simulation failed:[/bold red] {e}")
