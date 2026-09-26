import json
from pathlib import Path
from typing import Any

import click
import httpx
from ak5.cli.config import get_api_url, get_auth_headers
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

console = Console()


def resolve_column(board_data: dict[str, Any], query: str) -> dict[str, Any] | None:
    """Resolve column from board data by column_id, name, or stage (case-insensitive)."""
    q = query.strip().lower()
    columns = board_data.get("columns", [])

    # Exact column_id match
    for col in columns:
        if col.get("column_id", "").lower() == q:
            return col

    # Exact name match
    for col in columns:
        if col.get("name", "").lower() == q:
            return col

    # Exact stage match
    for col in columns:
        if col.get("stage", "").lower() == q:
            return col

    # Partial / fuzzy match
    for col in columns:
        col_name = col.get("name", "").lower()
        col_stage = col.get("stage", "").lower()
        if q in col_name or q in col_stage:
            return col

    return None


@click.group("ticket")
def ticket_group() -> None:
    """Manage ticket lifecycle: create, view, move, comment, block, and update."""


@ticket_group.command("create")
@click.option("--title", "-t", required=True, help="Ticket title")
@click.option("--desc", "-d", "description", default="", help="Detailed ticket description")
@click.option(
    "--board", "-b", "board_id", default="proj-core-engine", help="Target Board ID (default: proj-core-engine)"
)
@click.option(
    "--column", "-c", "column_name", default=None, help="Target column name, stage, or ID (default: first open column)"
)
@click.option(
    "--priority", "-p", type=click.Choice(["low", "medium", "high", "urgent"]), default="medium", help="Priority"
)
@click.option(
    "--assign", "-a", "assigned_to", default=None, help="Assignee Actor ID (e.g. user_pm, agent_image_worker)"
)
@click.option("--labels", "-l", default="", help="Comma-separated labels (e.g. 'auth,backend,p1')")
@click.option("--due", default=None, help="Due date (ISO format, e.g. '2026-10-01T00:00:00')")
def create_ticket_cmd(
    title: str,
    description: str,
    board_id: str,
    column_name: str | None,
    priority: str,
    assigned_to: str | None,
    labels: str,
    due: str | None,
) -> None:
    """Create a new root or master ticket."""
    api_url = get_api_url()
    headers = get_auth_headers(api_url)

    try:
        with httpx.Client(timeout=10.0) as client:
            board_resp = client.get(f"{api_url}/boards/{board_id}")
            board_resp.raise_for_status()
            board_data = board_resp.json()

            target_col = None
            if column_name:
                target_col = resolve_column(board_data, column_name)
                if not target_col:
                    avail = ", ".join(f"'{c['name']}'" for c in board_data.get("columns", []))
                    console.print(
                        f"[bold red]✗ Column '{column_name}' not found on board '{board_id}'. Available: {avail}[/bold red]"
                    )
                    return
            else:
                # Default to first column (usually To Do / stage open)
                cols = board_data.get("columns", [])
                if not cols:
                    console.print(f"[bold red]✗ Board '{board_id}' has no columns.[/bold red]")
                    return
                target_col = cols[0]

            label_list = [lbl.strip() for lbl in labels.split(",") if lbl.strip()]
            payload = {
                "title": title,
                "description": description,
                "board_id": board_id,
                "column_id": target_col["column_id"],
                "priority": priority,
                "assigned_to": assigned_to,
                "labels": label_list,
                "due_date": due,
            }

            resp = client.post(f"{api_url}/tickets", json=payload, headers=headers)
            resp.raise_for_status()
            ticket = resp.json()

        p_color = "red" if priority in ("urgent", "high") else ("yellow" if priority == "medium" else "dim")
        console.print(
            f"[bold green]✓[/bold green] Ticket [cyan bold]{ticket['ticket_id']}[/cyan bold] created successfully!"
        )
        console.print(
            f"  Title: [bold white]{ticket['title']}[/bold white]\n"
            f"  Board: [cyan]{board_id}[/cyan] | Column: [yellow]{target_col['name']}[/yellow] | Priority: [{p_color}][{priority.upper()}][/{p_color}]\n"
            f"  Assignee: {('@' + ticket['assigned_to']) if ticket.get('assigned_to') else '[dim]Unassigned[/dim]'}"
        )
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Failed to create ticket ({e.response.status_code}):[/bold red] {e.response.text}")
    except Exception as e:
        console.print(f"[bold red]✗ Error:[/bold red] {e}")


@ticket_group.command("view")
@click.argument("ticket_id")
def view_ticket_cmd(ticket_id: str) -> None:
    """View detailed ticket context, description, subtasks, and comments."""
    api_url = get_api_url()

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{api_url}/tickets/{ticket_id}")
            resp.raise_for_status()
            t = resp.json()

        p_color = (
            "red" if t.get("priority") in ("urgent", "high") else ("yellow" if t.get("priority") == "medium" else "dim")
        )
        s_color = "green" if t.get("status") == "done" else ("red" if t.get("status") == "blocked" else "yellow")

        # Basic metadata table
        meta_table = Table(show_header=False, box=None, padding=(0, 2))
        meta_table.add_column("Field", style="bold cyan")
        meta_table.add_column("Value", style="white")

        meta_table.add_row("Status", f"[{s_color}]{t.get('status', 'open').upper()}[/{s_color}]")
        meta_table.add_row("Priority", f"[{p_color}][{t.get('priority', 'medium').upper()}][/{p_color}]")
        meta_table.add_row("Board", t.get("board_id", "N/A"))
        meta_table.add_row("Column ID", t.get("column_id", "N/A"))
        meta_table.add_row("Assignee", f"@{t['assigned_to']}" if t.get("assigned_to") else "[dim]Unassigned[/dim]")
        meta_table.add_row("Created By", f"@{t.get('created_by', 'system')}")
        if t.get("parent_ticket_id"):
            meta_table.add_row("Parent Ticket", f"[yellow]{t['parent_ticket_id']}[/yellow]")
        if t.get("due_date"):
            meta_table.add_row("Due Date", t["due_date"])
        labels = t.get("labels", [])
        if labels:
            meta_table.add_row("Labels", ", ".join(labels))
        if t.get("is_archived"):
            arch_at = t.get("archived_at") or ""
            arch_str = f" (at {arch_at[:19].replace('T', ' ')})" if arch_at else ""
            meta_table.add_row("Archived", f"[bold red]YES[/bold red]{arch_str}")

        desc = t.get("description") or "[dim](No description provided)[/dim]"

        console.print(Panel(meta_table, title=f"🎫 Ticket Context: {t['ticket_id']} - {t['title']}", expand=False))
        console.print(Panel(desc, title="📝 Description", expand=False))

        # Subtasks section
        subtasks = t.get("subtasks", [])
        if subtasks:
            sub_table = Table(title=f"Subtasks ({len(subtasks)} total)", show_lines=True)
            sub_table.add_column("ID", style="cyan", no_wrap=True)
            sub_table.add_column("Status", style="yellow")
            sub_table.add_column("Assignee", style="magenta")
            sub_table.add_column("Title", style="white")
            for s in subtasks:
                sub_status_col = "green" if s.get("status") == "done" else "yellow"
                sub_table.add_row(
                    s["ticket_id"],
                    f"[{sub_status_col}]{s.get('status', 'open')}[/{sub_status_col}]",
                    f"@{s['assigned_to']}" if s.get("assigned_to") else "[dim]Unassigned[/dim]",
                    s["title"],
                )
            console.print(sub_table)

        # Execution context section
        exec_ctx = t.get("execution_context")
        if exec_ctx:
            syntax = Syntax(json.dumps(exec_ctx, indent=2), "json", theme="monokai", line_numbers=False)
            console.print(Panel(syntax, title="⚡ Execution Context", expand=False))

        # Attachments section
        attachments = t.get("attachments", [])
        if attachments:
            att_table = Table(
                title=f"📎 Attachments ({len(attachments)})", show_header=True, header_style="bold magenta"
            )
            att_table.add_column("ID", style="dim")
            att_table.add_column("Filename", style="cyan")
            att_table.add_column("Size", style="green")
            att_table.add_column("Uploader", style="yellow")
            att_table.add_column("Uploaded At", style="dim")
            for a in attachments:
                size_bytes = a.get("file_size", 0)
                if size_bytes >= 1024 * 1024:
                    size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
                elif size_bytes >= 1024:
                    size_str = f"{size_bytes / 1024:.1f} KB"
                else:
                    size_str = f"{size_bytes} B"
                att_table.add_row(
                    a.get("attachment_id", ""),
                    a.get("filename", ""),
                    size_str,
                    f"@{a.get('actor_id', '')}",
                    str(a.get("created_at", "")),
                )
            console.print(att_table)

        # Comments section
        comments = t.get("comments", [])
        if comments:
            cmt_lines = []
            for c in comments:
                internal_badge = " [bold magenta][INTERNAL][/bold magenta]" if c.get("is_internal") else ""
                cmt_lines.append(
                    f"[bold cyan]@{c['actor_id']}[/bold cyan]{internal_badge} [dim]({c['created_at']})[/dim]:\n  {c['content']}"
                )
            console.print(Panel("\n\n".join(cmt_lines), title=f"💬 Recent Comments ({len(comments)})", expand=False))

    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Failed to retrieve ticket ({e.response.status_code}):[/bold red] {e.response.text}")
    except Exception as e:
        console.print(f"[bold red]✗ Error:[/bold red] {e}")


@ticket_group.command("move")
@click.argument("ticket_id")
@click.argument("target_column")
@click.option("--note", "-n", default=None, help="Optional status transition note / comment")
def move_ticket_cmd(ticket_id: str, target_column: str, note: str | None) -> None:
    """Move ticket to a target column (e.g. 'In Progress', 'Done', 'Review', 'To Do')."""
    execute_move_ticket(ticket_id, target_column, note)


def execute_move_ticket(ticket_id: str, target_column: str, note: str | None = None) -> None:
    api_url = get_api_url()
    headers = get_auth_headers(api_url)

    try:
        with httpx.Client(timeout=10.0) as client:
            t_resp = client.get(f"{api_url}/tickets/{ticket_id}")
            t_resp.raise_for_status()
            ticket = t_resp.json()
            board_id = ticket["board_id"]

            b_resp = client.get(f"{api_url}/boards/{board_id}")
            b_resp.raise_for_status()
            board_data = b_resp.json()

            matched_col = resolve_column(board_data, target_column)
            if not matched_col:
                avail = ", ".join(f"'{c['name']}'" for c in board_data.get("columns", []))
                console.print(
                    f"[bold red]✗ Target column '{target_column}' not found on board '{board_id}'. Available: {avail}[/bold red]"
                )
                return

            # Move ticket
            m_resp = client.patch(
                f"{api_url}/tickets/{ticket_id}/move",
                json={"target_column_id": matched_col["column_id"]},
                headers=headers,
            )
            m_resp.raise_for_status()
            updated = m_resp.json()

            # Optional note comment
            if note:
                client.post(
                    f"{api_url}/tickets/{ticket_id}/comments",
                    json={"content": f"[Status -> {matched_col['name']}] {note}", "is_internal": False},
                    headers=headers,
                )

        console.print(
            f"[bold green]✓[/bold green] Ticket [cyan]{ticket_id}[/cyan] moved to "
            f"[bold yellow]{matched_col['name']}[/bold yellow] (status: [green]{updated['status']}[/green])"
        )
        if note:
            console.print(f"  Note: {note}")
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Move failed ({e.response.status_code}):[/bold red] {e.response.text}")
    except Exception as e:
        console.print(f"[bold red]✗ Error:[/bold red] {e}")


@ticket_group.command("comment")
@click.argument("ticket_id")
@click.argument("content")
@click.option("--internal", "-i", is_flag=True, help="Mark comment as internal agent reasoning")
def comment_ticket_cmd(ticket_id: str, content: str, internal: bool) -> None:
    """Add a discussion or reasoning comment to a ticket."""
    execute_comment_ticket(ticket_id, content, internal)


def execute_comment_ticket(ticket_id: str, content: str, internal: bool = False) -> None:
    api_url = get_api_url()
    headers = get_auth_headers(api_url)

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{api_url}/tickets/{ticket_id}/comments",
                json={"content": content, "is_internal": internal},
                headers=headers,
            )
            resp.raise_for_status()
            c = resp.json()

        int_flag = " [INTERNAL]" if c.get("is_internal") else ""
        console.print(
            f"[bold green]✓[/bold green] Comment added to [cyan]{ticket_id}[/cyan]{int_flag} by [magenta]@{c['actor_id']}[/magenta]"
        )
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Comment failed ({e.response.status_code}):[/bold red] {e.response.text}")
    except Exception as e:
        console.print(f"[bold red]✗ Error:[/bold red] {e}")


@ticket_group.command("block")
@click.argument("ticket_id")
@click.option("--reason", "-r", required=True, help="Reason why ticket is blocked")
@click.option("--mention", "-m", default=None, help="Actor ID to mention (e.g. user_pm)")
def block_ticket_cmd(ticket_id: str, reason: str, mention: str | None) -> None:
    """Mark a ticket as BLOCKED and notify PM/collaborators."""
    api_url = get_api_url()
    headers = get_auth_headers(api_url)

    try:
        with httpx.Client(timeout=10.0) as client:
            # Update status to blocked
            upd_resp = client.patch(
                f"{api_url}/tickets/{ticket_id}",
                json={"status": "blocked"},
                headers=headers,
            )
            upd_resp.raise_for_status()

            # Post comment
            mention_str = f" @{mention}" if mention else ""
            comment_content = f"⚠️ [BLOCKED]{mention_str} Reason: {reason}"
            client.post(
                f"{api_url}/tickets/{ticket_id}/comments",
                json={"content": comment_content, "is_internal": False},
                headers=headers,
            )

        mention_info = f" (@{mention})" if mention else ""
        console.print(
            f"[bold red]⚠️[/bold red] Ticket [cyan]{ticket_id}[/cyan] marked as [bold red]BLOCKED[/bold red]{mention_info}"
        )
        console.print(f"  Reason: {reason}")
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Block failed ({e.response.status_code}):[/bold red] {e.response.text}")
    except Exception as e:
        console.print(f"[bold red]✗ Error:[/bold red] {e}")


@ticket_group.command("update")
@click.argument("ticket_id")
@click.option("--title", default=None, help="New title")
@click.option("--desc", "description", default=None, help="New description")
@click.option("--priority", type=click.Choice(["low", "medium", "high", "urgent"]), default=None)
@click.option("--assign", "assigned_to", default=None, help="New assignee Actor ID")
@click.option("--status", type=click.Choice(["open", "in_progress", "done", "blocked"]), default=None)
def update_ticket_cmd(
    ticket_id: str,
    title: str | None,
    description: str | None,
    priority: str | None,
    assigned_to: str | None,
    status: str | None,
) -> None:
    """Update ticket title, description, priority, assignee, or status."""
    api_url = get_api_url()
    headers = get_auth_headers(api_url)

    payload = {}
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description
    if priority is not None:
        payload["priority"] = priority
    if assigned_to is not None:
        payload["assigned_to"] = assigned_to
    if status is not None:
        payload["status"] = status

    if not payload:
        console.print("[yellow]No fields provided to update.[/yellow]")
        return

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.patch(
                f"{api_url}/tickets/{ticket_id}",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            _ = resp.json()

        console.print(f"[bold green]✓[/bold green] Ticket [cyan]{ticket_id}[/cyan] updated successfully.")
        for k, v in payload.items():
            console.print(f"  {k}: [white]{v}[/white]")
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Update failed ({e.response.status_code}):[/bold red] {e.response.text}")
    except Exception as e:
        console.print(f"[bold red]✗ Error:[/bold red] {e}")


@ticket_group.command("attach")
@click.argument("ticket_id")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def attach_cmd(ticket_id: str, filepath: Path) -> None:
    """Attach a deliverable or file to a ticket."""
    api_url = get_api_url()
    headers = get_auth_headers(api_url)

    try:
        with open(filepath, "rb") as f:
            files = {"file": (filepath.name, f, "application/octet-stream")}
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    f"{api_url}/tickets/{ticket_id}/attachments",
                    files=files,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

        console.print(f"[bold green]✓ File attached successfully:[/bold green] [cyan]{data['filename']}[/cyan]")
        console.print(f"  Attachment ID: [dim]{data['attachment_id']}[/dim]")
        console.print(f"  Size: {data['file_size']} bytes")
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Attachment upload failed ({e.response.status_code}):[/bold red] {e.response.text}")
    except Exception as e:
        console.print(f"[bold red]✗ Error:[/bold red] {e}")


@ticket_group.command("download-attachment")
@click.argument("ticket_id")
@click.argument("attachment_id")
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output file destination path (default: original filename in current directory)",
)
def download_attachment_cmd(ticket_id: str, attachment_id: str, output: str | None) -> None:
    """Download an attachment file from a ticket."""
    api_url = get_api_url()
    headers = get_auth_headers(api_url)

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                f"{api_url}/tickets/{ticket_id}/attachments/{attachment_id}",
                headers=headers,
            )
            resp.raise_for_status()

            # Determine destination filename
            if output:
                out_path = Path(output)
            else:
                cd_header = resp.headers.get("content-disposition", "")
                filename = f"{attachment_id}.bin"
                if "filename=" in cd_header:
                    import re

                    match = re.search(r'filename="?([^";]+)"?', cd_header)
                    if match:
                        filename = Path(match.group(1)).name or f"{attachment_id}.bin"
                out_path = Path.cwd() / filename

            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(resp.content)

        console.print(
            f"[bold green]✓ Downloaded attachment to:[/bold green] [cyan]{out_path.resolve()}[/cyan] ({len(resp.content)} bytes)"
        )
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(
            f"[bold red]✗ Attachment download failed ({e.response.status_code}):[/bold red] {e.response.text}"
        )
    except Exception as e:
        console.print(f"[bold red]✗ Error:[/bold red] {e}")


@ticket_group.command("list")
@click.option("--board", "-b", "board_id", default=None, help="Filter by Board ID")
@click.option("--archived", is_flag=True, default=False, help="List only archived tickets")
@click.option("--all", "include_all", is_flag=True, default=False, help="List all tickets (active and archived)")
@click.option(
    "--status", "-s", default=None, type=click.Choice(["open", "in_progress", "blocked", "done"]), help="Filter by status"
)
@click.option(
    "--stage", default=None, type=click.Choice(["open", "in_progress", "review", "done"]), help="Filter by column stage"
)
@click.option("--assign", "-a", "assigned_to", default=None, help="Filter by assignee actor ID")
@click.option("--creator", default=None, help="Filter by creator actor ID")
@click.option("--label", "-l", "labels", multiple=True, help="Filter by labels (repeatable)")
@click.option("--query", "-q", default=None, help="Search text across title and description")
@click.option("--limit", default=20, type=int, help="Maximum tickets to return (default: 20)")
def list_tickets_cmd(
    board_id: str | None,
    archived: bool,
    include_all: bool,
    status: str | None,
    stage: str | None,
    assigned_to: str | None,
    creator: str | None,
    labels: tuple[str, ...],
    query: str | None,
    limit: int,
) -> None:
    """List tickets with rich selection and filtering options."""
    api_url = get_api_url()
    params: dict[str, Any] = {"limit": limit}
    if board_id:
        params["board_id"] = board_id
    if include_all:
        params["include_all"] = "true"
    elif archived:
        params["is_archived"] = "true"
    else:
        params["is_archived"] = "false"

    if status:
        params["status"] = status
    if stage:
        params["stage"] = stage
    if assigned_to:
        params["assigned_to"] = assigned_to
    if creator:
        params["created_by"] = creator
    if labels:
        params["labels"] = ",".join(labels)
    if query:
        params["q"] = query

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{api_url}/tickets", params=params)
            resp.raise_for_status()
            tickets = resp.json()

        if not tickets:
            console.print("[yellow]No tickets found matching criteria.[/yellow]")
            return

        table = Table(
            title=f"📋 Tickets ({len(tickets)} result{'s' if len(tickets) > 1 else ''})",
            show_header=True,
            header_style="bold cyan",
            expand=True,
        )
        table.add_column("ID", style="bold cyan", width=10)
        table.add_column("Title", style="bold white")
        table.add_column("Status", width=12)
        table.add_column("Priority", width=10)
        table.add_column("Assignee", width=15)
        table.add_column("Archived", width=10)
        table.add_column("Updated", width=20, style="dim")

        for t in tickets:
            p_color = (
                "red"
                if t.get("priority") in ("urgent", "high")
                else ("yellow" if t.get("priority") == "medium" else "dim")
            )
            s_color = "green" if t.get("status") == "done" else ("red" if t.get("status") == "blocked" else "yellow")
            status_text = f"[{s_color}]{t.get('status', 'open').upper()}[/{s_color}]"
            priority_text = f"[{p_color}][{t.get('priority', 'medium').upper()}][/{p_color}]"
            assignee = f"@{t['assigned_to']}" if t.get("assigned_to") else "[dim]Unassigned[/dim]"
            archived_text = "[bold red]YES[/bold red]" if t.get("is_archived") else "[dim]No[/dim]"
            updated = t.get("updated_at", "")[:19].replace("T", " ")

            table.add_row(
                t["ticket_id"],
                t["title"],
                status_text,
                priority_text,
                assignee,
                archived_text,
                updated,
            )

        console.print(table)
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Query failed ({e.response.status_code}):[/bold red] {e.response.text}")
    except Exception as e:
        console.print(f"[bold red]✗ Error:[/bold red] {e}")


@ticket_group.command("archive")
@click.argument("ticket_id")
def archive_ticket_cmd(ticket_id: str) -> None:
    """Archive a ticket to hide it from active Kanban board view."""
    api_url = get_api_url()
    headers = get_auth_headers(api_url)

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(f"{api_url}/tickets/{ticket_id}/archive", headers=headers)
            resp.raise_for_status()
            t = resp.json()

        console.print(f"[bold green]✓ Ticket '{ticket_id}' archived successfully.[/bold green]")
        console.print(f"  Title: {t['title']}")
        console.print("  [dim]This ticket is now hidden from standard board view. Use 'ak5 ticket list --archived' to view.[/dim]")
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Archive failed ({e.response.status_code}):[/bold red] {e.response.text}")
    except Exception as e:
        console.print(f"[bold red]✗ Error:[/bold red] {e}")


@ticket_group.command("unarchive")
@click.argument("ticket_id")
def unarchive_ticket_cmd(ticket_id: str) -> None:
    """Unarchive an archived ticket back to active Kanban board view."""
    api_url = get_api_url()
    headers = get_auth_headers(api_url)

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(f"{api_url}/tickets/{ticket_id}/unarchive", headers=headers)
            resp.raise_for_status()
            t = resp.json()

        console.print(f"[bold green]✓ Ticket '{ticket_id}' unarchived successfully.[/bold green]")
        console.print(f"  Title: {t['title']}")
        console.print("  [dim]This ticket is now restored to the active board view.[/dim]")
    except httpx.ConnectError:
        console.print(f"[bold red]✗ Failed to connect to AK5 Gateway at {api_url}[/bold red]")
    except httpx.HTTPStatusError as e:
        console.print(f"[bold red]✗ Unarchive failed ({e.response.status_code}):[/bold red] {e.response.text}")
    except Exception as e:
        console.print(f"[bold red]✗ Error:[/bold red] {e}")


# Shortcuts for top-level convenience
@click.command("move")
@click.argument("ticket_id")
@click.argument("target_column")
@click.option("--note", "-n", default=None, help="Optional status transition note / comment")
def move_shortcut(ticket_id: str, target_column: str, note: str | None) -> None:
    """Quick shortcut to move a ticket to another column."""
    execute_move_ticket(ticket_id, target_column, note)


@click.command("comment")
@click.argument("ticket_id")
@click.argument("content")
@click.option("--internal", "-i", is_flag=True, help="Mark comment as internal agent reasoning")
def comment_shortcut(ticket_id: str, content: str, internal: bool) -> None:
    """Quick shortcut to post a comment to a ticket."""
    execute_comment_ticket(ticket_id, content, internal)
