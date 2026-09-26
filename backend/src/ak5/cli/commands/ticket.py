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
