"""Ticket CLI commands — workflow verbs + legacy helpers (cli.md §4.3–§4.4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
import httpx
from ak5.cli.client_factory import json_flag_from_ctx, open_client
from ak5.cli.config import get_api_url, require_auth_headers
from ak5.client.claims import clear_claim_token, resolve_claim_token, save_claim_token
from ak5.client.errors import EXIT_USAGE, ClientError
from ak5.client.output import handle_client_error, is_json_mode, print_result
from ak5.client.payload import load_json_object
from ak5.contracts.errors import ErrorCode
from ak5.contracts.stages import BlockedKind
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

console = Console()


def _jm(ctx: click.Context) -> bool:
    return is_json_mode(flag=json_flag_from_ctx(ctx))


def _ok(data: Any, *, json_mode: bool, human: str | None = None) -> None:
    if json_mode:
        print_result(data, json_mode=True)
    elif human:
        console.print(human)
    else:
        print_result(data, json_mode=False)


def resolve_column(board_data: dict[str, Any], query: str) -> dict[str, Any] | None:
    """Resolve column from board data by column_id, name, or stage (case-insensitive)."""
    q = query.strip().lower()
    columns = board_data.get("columns", [])

    for col in columns:
        if col.get("column_id", "").lower() == q:
            return col
    for col in columns:
        if col.get("name", "").lower() == q:
            return col
    for col in columns:
        if col.get("stage", "").lower() == q:
            return col
    for col in columns:
        col_name = col.get("name", "").lower()
        col_stage = col.get("stage", "").lower()
        if q in col_name or q in col_stage:
            return col
    return None


@click.group("ticket")
def ticket_group() -> None:
    """Manage ticket lifecycle: create, claim, submit, block, history, …"""


# ---------------------------------------------------------------------------
# create (T1 inbox default / T2 create_direct)
# ---------------------------------------------------------------------------


@ticket_group.command("create")
@click.argument("title", required=False, default=None)
@click.option("--title", "-t", "title_opt", default=None, help="Ticket title (alt to positional)")
@click.option("--desc", "-d", "description", default="", help="Description / original request")
@click.option(
    "--board",
    "-b",
    "board_id",
    default=None,
    help="Target board (omit → inbox / T1). With board → create_direct (T2).",
)
@click.option("--spec", default=None, help="Spec for create_direct (T2); defaults to description")
@click.option(
    "--criteria",
    "criteria_raw",
    default=None,
    help="Acceptance criteria: comma-separated or @file.json / -",
)
@click.option(
    "--priority",
    "-p",
    type=click.Choice(["low", "medium", "high", "urgent"]),
    default="medium",
)
@click.option("--assign", "-a", "assigned_to", default=None, help="Assignee Actor ID")
@click.option("--labels", "-l", default="", help="Comma-separated labels")
@click.option("--due", default=None, help="Due date (ISO)")
@click.option("--column", "-c", "column_name", default=None, help="Ignored (compat); stage via workflow")
@click.pass_context
def create_ticket_cmd(
    ctx: click.Context,
    title: str | None,
    title_opt: str | None,
    description: str,
    board_id: str | None,
    spec: str | None,
    criteria_raw: str | None,
    priority: str,
    assigned_to: str | None,
    labels: str,
    due: str | None,
    column_name: str | None,
) -> None:
    """Create a ticket. Default destination is inbox (T1); ``--board`` → T2."""
    _ = column_name
    json_mode = _jm(ctx)
    resolved_title = title_opt or title
    if not resolved_title:
        raise click.UsageError("TITLE is required (positional or --title)")

    label_list = [lbl.strip() for lbl in labels.split(",") if lbl.strip()]
    payload: dict[str, Any] = {
        "title": resolved_title,
        "priority": priority,
        "labels": label_list,
    }
    if description:
        payload["description"] = description
    if due:
        payload["due_date"] = due
    if assigned_to:
        payload["assigned_to"] = assigned_to

    if board_id:
        payload["board_id"] = board_id
        payload["spec"] = spec or description or resolved_title
        if criteria_raw:
            if criteria_raw.startswith("@") or criteria_raw == "-":
                from ak5.client.payload import load_json_payload

                crit = load_json_payload(criteria_raw)
                if isinstance(crit, list):
                    payload["acceptance_criteria"] = [str(c) for c in crit]
                elif isinstance(crit, dict) and "acceptance_criteria" in crit:
                    payload["acceptance_criteria"] = list(crit["acceptance_criteria"])
                else:
                    raise ClientError(
                        "criteria file must be a JSON array or object with acceptance_criteria",
                        code=ErrorCode.PAYLOAD_INVALID,
                        exit_code=EXIT_USAGE,
                    )
            else:
                payload["acceptance_criteria"] = [
                    c.strip() for c in criteria_raw.split(",") if c.strip()
                ]
        else:
            payload["acceptance_criteria"] = ["done"]
    else:
        payload["original_request"] = description or resolved_title

    try:
        with open_client() as client:
            ticket = client.create_ticket(payload)
        _ok(
            ticket,
            json_mode=json_mode,
            human=(
                f"[bold green]✓[/bold green] Ticket [cyan bold]{ticket['ticket_id']}[/cyan bold] "
                f"created on [cyan]{ticket.get('board_id')}[/cyan] "
                f"(stage={ticket.get('stage')})"
            ),
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


# ---------------------------------------------------------------------------
# Workflow loop verbs (§4.3)
# ---------------------------------------------------------------------------


@ticket_group.command("claim")
@click.argument("ticket_id")
@click.option("--caps", default="", help="Capabilities to advertise (worker claim)")
@click.pass_context
def claim_cmd(ctx: click.Context, ticket_id: str, caps: str) -> None:
    """Claim a ticket; stores claim token under ``~/.ak5/claims/<id>``."""
    json_mode = _jm(ctx)
    capabilities = [c.strip() for c in caps.split(",") if c.strip()] or None
    try:
        with open_client() as client:
            result = client.claim_ticket(ticket_id, capabilities=capabilities)
        token = result.get("claim_token")
        if isinstance(token, str) and token:
            save_claim_token(ticket_id, token)
        _ok(
            result,
            json_mode=json_mode,
            human=(
                f"[bold green]✓[/bold green] Claimed [cyan]{ticket_id}[/cyan] "
                f"(lease until {result.get('lease_expires_at')})"
            ),
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("heartbeat")
@click.argument("ticket_id")
@click.option("--token", "token", default=None, help="Claim token (default: local store)")
@click.pass_context
def heartbeat_cmd(ctx: click.Context, ticket_id: str, token: str | None) -> None:
    """Extend claim lease."""
    json_mode = _jm(ctx)
    try:
        claim_token = resolve_claim_token(ticket_id, token)
        with open_client() as client:
            result = client.heartbeat(ticket_id, claim_token=claim_token)
        _ok(
            result,
            json_mode=json_mode,
            human=f"[bold green]✓[/bold green] Heartbeat for [cyan]{ticket_id}[/cyan]",
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("release")
@click.argument("ticket_id")
@click.option("--token", "token", default=None)
@click.option("--reason", "-r", required=True, help="Why the claim is released")
@click.pass_context
def release_cmd(ctx: click.Context, ticket_id: str, token: str | None, reason: str) -> None:
    """Release claim back to backlog."""
    json_mode = _jm(ctx)
    try:
        claim_token = resolve_claim_token(ticket_id, token)
        with open_client() as client:
            result = client.release(ticket_id, claim_token=claim_token, reason=reason)
        clear_claim_token(ticket_id)
        _ok(
            result,
            json_mode=json_mode,
            human=f"[bold green]✓[/bold green] Released [cyan]{ticket_id}[/cyan]",
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("submit")
@click.argument("ticket_id")
@click.option("--token", "token", default=None)
@click.option(
    "--deliverable",
    required=True,
    help="Deliverable JSON: @file.json or - (stdin)",
)
@click.pass_context
def submit_cmd(
    ctx: click.Context,
    ticket_id: str,
    token: str | None,
    deliverable: str,
) -> None:
    """Submit deliverable → agent_review; clears local claim token."""
    json_mode = _jm(ctx)
    try:
        body = load_json_object(deliverable)
        claim_token = resolve_claim_token(ticket_id, token)
        with open_client() as client:
            result = client.submit(ticket_id, claim_token=claim_token, deliverable=body)
        clear_claim_token(ticket_id)
        _ok(
            result,
            json_mode=json_mode,
            human=(
                f"[bold green]✓[/bold green] Submitted [cyan]{ticket_id}[/cyan] "
                f"(stage={result.get('stage')})"
            ),
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("bounce")
@click.argument("ticket_id")
@click.option("--token", "token", default=None)
@click.option("--reason", "-r", required=True)
@click.pass_context
def bounce_cmd(ctx: click.Context, ticket_id: str, token: str | None, reason: str) -> None:
    """Bounce ticket back to inbox (mis-assigned work)."""
    json_mode = _jm(ctx)
    try:
        claim_token = resolve_claim_token(ticket_id, token)
        with open_client() as client:
            result = client.bounce(ticket_id, claim_token=claim_token, reason=reason)
        clear_claim_token(ticket_id)
        _ok(
            result,
            json_mode=json_mode,
            human=f"[bold green]✓[/bold green] Bounced [cyan]{ticket_id}[/cyan] to inbox",
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("route")
@click.argument("ticket_id")
@click.option("--token", "token", default=None)
@click.option("--plan", required=True, help="Route plan JSON: @file.json or -")
@click.pass_context
def route_cmd(ctx: click.Context, ticket_id: str, token: str | None, plan: str) -> None:
    """Route unclassified inbox ticket to a board (triage)."""
    json_mode = _jm(ctx)
    try:
        plan_body = load_json_object(plan)
        claim_token = resolve_claim_token(ticket_id, token)
        with open_client() as client:
            result = client.route(ticket_id, claim_token=claim_token, plan=plan_body)
        clear_claim_token(ticket_id)
        _ok(
            result,
            json_mode=json_mode,
            human=(
                f"[bold green]✓[/bold green] Routed [cyan]{ticket_id}[/cyan] → "
                f"[cyan]{result.get('board_id')}[/cyan]"
            ),
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("verdict")
@click.argument("ticket_id")
@click.option("--token", "token", default=None)
@click.option("--approve", "decision_approve", is_flag=True, help="Approve")
@click.option("--reject", "decision_reject", is_flag=True, help="Reject")
@click.option("--misrouted", is_flag=True, help="Reject as misrouted → inbox")
@click.option("--findings", default=None, help="Findings JSON: @file.json or -")
@click.option("--reason", "-r", default="", help="Verdict reason")
@click.pass_context
def verdict_cmd(
    ctx: click.Context,
    ticket_id: str,
    token: str | None,
    decision_approve: bool,
    decision_reject: bool,
    misrouted: bool,
    findings: str | None,
    reason: str,
) -> None:
    """Record gatekeeper/human verdict."""
    json_mode = _jm(ctx)
    if decision_approve == decision_reject:
        raise click.UsageError("Specify exactly one of --approve or --reject")
    decision = "approve" if decision_approve else "reject"
    findings_list: list[Any] = []
    if findings:
        from ak5.client.payload import load_json_payload

        raw = load_json_payload(findings)
        if isinstance(raw, list):
            findings_list = raw
        elif isinstance(raw, dict) and "findings" in raw:
            findings_list = list(raw["findings"])
        else:
            raise ClientError(
                "findings must be a JSON array or object with findings",
                code=ErrorCode.PAYLOAD_INVALID,
                exit_code=EXIT_USAGE,
            )

    verdict_body: dict[str, Any] = {
        "decision": decision,
        "reason": reason,
        "misrouted": misrouted,
        "findings": findings_list,
    }
    try:
        claim_token: str | None
        try:
            claim_token = resolve_claim_token(ticket_id, token)
        except ClientError:
            claim_token = token  # human_review may omit token
        with open_client() as client:
            result = client.verdict(ticket_id, claim_token=claim_token, verdict=verdict_body)
        clear_claim_token(ticket_id)
        _ok(
            result,
            json_mode=json_mode,
            human=(
                f"[bold green]✓[/bold green] Verdict [bold]{decision}[/bold] on "
                f"[cyan]{ticket_id}[/cyan] (stage={result.get('stage')})"
            ),
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("postmortem")
@click.argument("ticket_id")
@click.option("--token", "token", default=None)
@click.option("--body", required=True, help="Postmortem JSON: @file.json or -")
@click.pass_context
def postmortem_cmd(ctx: click.Context, ticket_id: str, token: str | None, body: str) -> None:
    """Submit postmortem → backlog."""
    json_mode = _jm(ctx)
    try:
        pm = load_json_object(body)
        claim_token = resolve_claim_token(ticket_id, token)
        with open_client() as client:
            result = client.postmortem(ticket_id, claim_token=claim_token, postmortem=pm)
        clear_claim_token(ticket_id)
        _ok(
            result,
            json_mode=json_mode,
            human=f"[bold green]✓[/bold green] Postmortem submitted for [cyan]{ticket_id}[/cyan]",
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


# ---------------------------------------------------------------------------
# Human / common (§4.4)
# ---------------------------------------------------------------------------


@ticket_group.command("block")
@click.argument("ticket_id")
@click.option(
    "--kind",
    type=click.Choice([k.value for k in BlockedKind if k != BlockedKind.ESCALATION]),
    required=True,
    help="Block kind (escalation is system-only)",
)
@click.option("--reason", "-r", required=True)
@click.option("--token", "token", default=None, help="Claim token if acting as claim holder")
@click.option("--mention", "-m", default=None, help="Deprecated; ignored")
@click.pass_context
def block_ticket_cmd(
    ctx: click.Context,
    ticket_id: str,
    kind: str,
    reason: str,
    token: str | None,
    mention: str | None,
) -> None:
    """Block a ticket (releases claim; in_progress → backlog)."""
    _ = mention
    json_mode = _jm(ctx)
    try:
        claim_token = None
        if token:
            claim_token = token
        else:
            from ak5.client.claims import load_claim_token

            claim_token = load_claim_token(ticket_id)
        with open_client() as client:
            result = client.block(
                ticket_id,
                kind=kind,
                reason=reason,
                claim_token=claim_token,
            )
        clear_claim_token(ticket_id)
        _ok(
            result,
            json_mode=json_mode,
            human=(
                f"[bold red]⚠[/bold red] Ticket [cyan]{ticket_id}[/cyan] blocked "
                f"([bold]{kind}[/bold]): {reason}"
            ),
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("unblock")
@click.argument("ticket_id")
@click.option("--resolution", default=None, help="Required for needs_human / escalation")
@click.pass_context
def unblock_cmd(ctx: click.Context, ticket_id: str, resolution: str | None) -> None:
    """Clear blocked overlay."""
    json_mode = _jm(ctx)
    try:
        with open_client() as client:
            result = client.unblock(ticket_id, resolution=resolution)
        _ok(
            result,
            json_mode=json_mode,
            human=f"[bold green]✓[/bold green] Unblocked [cyan]{ticket_id}[/cyan]",
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("reopen")
@click.argument("ticket_id")
@click.option("--reason", "-r", required=True)
@click.pass_context
def reopen_cmd(ctx: click.Context, ticket_id: str, reason: str) -> None:
    """Reopen a done ticket → postmortem_pending."""
    json_mode = _jm(ctx)
    try:
        with open_client() as client:
            result = client.reopen(ticket_id, reason=reason)
        _ok(
            result,
            json_mode=json_mode,
            human=f"[bold green]✓[/bold green] Reopened [cyan]{ticket_id}[/cyan]",
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("reroute")
@click.argument("ticket_id")
@click.option(
    "--to",
    "destination",
    required=True,
    help="inbox or a board id",
)
@click.option("--reason", "-r", required=True)
@click.pass_context
def reroute_cmd(
    ctx: click.Context,
    ticket_id: str,
    destination: str,
    reason: str,
) -> None:
    """Reroute ticket to inbox or another board backlog."""
    json_mode = _jm(ctx)
    to_inbox = destination.strip().lower() == "inbox"
    target = None if to_inbox else destination.strip()
    try:
        with open_client() as client:
            result = client.reroute(
                ticket_id,
                reason=reason,
                to_inbox=to_inbox,
                target_board_id=target,
            )
        clear_claim_token(ticket_id)
        _ok(
            result,
            json_mode=json_mode,
            human=(
                f"[bold green]✓[/bold green] Rerouted [cyan]{ticket_id}[/cyan] → "
                f"[cyan]{result.get('board_id')}[/cyan]"
            ),
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("history")
@click.argument("ticket_id")
@click.pass_context
def history_cmd(ctx: click.Context, ticket_id: str) -> None:
    """Show revision, review, postmortem, and audit history."""
    json_mode = _jm(ctx)
    try:
        with open_client() as client:
            result = client.history(ticket_id)
        if json_mode:
            print_result(result, json_mode=True)
            return
        console.print(Panel(f"History for [cyan]{ticket_id}[/cyan]", expand=False))
        for label, key in (
            ("Revisions", "revisions"),
            ("Reviews", "reviews"),
            ("Postmortems", "postmortems"),
            ("Audit", "audit"),
        ):
            items = result.get(key) or []
            console.print(f"[bold]{label}[/bold] ({len(items)})")
            for item in items:
                console.print(f"  {json.dumps(item, default=str)}")
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


# ---------------------------------------------------------------------------
# view / move / comment / update / attach / list / archive (legacy + constrained)
# ---------------------------------------------------------------------------


@ticket_group.command("view")
@click.argument("ticket_id")
@click.pass_context
def view_ticket_cmd(ctx: click.Context, ticket_id: str) -> None:
    """View detailed ticket context, description, subtasks, and comments."""
    json_mode = _jm(ctx)
    try:
        with open_client() as client:
            t = client.get_ticket(ticket_id)
        if json_mode:
            print_result(t, json_mode=True)
            return

        p_color = (
            "red" if t.get("priority") in ("urgent", "high") else ("yellow" if t.get("priority") == "medium" else "dim")
        )
        s_color = "green" if t.get("status") == "done" else ("red" if t.get("status") == "blocked" else "yellow")

        meta_table = Table(show_header=False, box=None, padding=(0, 2))
        meta_table.add_column("Field", style="bold cyan")
        meta_table.add_column("Value", style="white")

        meta_table.add_row("Status", f"[{s_color}]{t.get('status', 'open').upper()}[/{s_color}]")
        meta_table.add_row("Stage", str(t.get("stage") or "—"))
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

        desc = t.get("description") or t.get("original_request") or "[dim](No description)[/dim]"

        console.print(Panel(meta_table, title=f"Ticket: {t['ticket_id']} - {t['title']}", expand=False))
        console.print(Panel(desc, title="Description", expand=False))

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

        exec_ctx = t.get("execution_context")
        if exec_ctx:
            syntax = Syntax(json.dumps(exec_ctx, indent=2), "json", theme="monokai", line_numbers=False)
            console.print(Panel(syntax, title="Execution Context", expand=False))

        attachments = t.get("attachments", [])
        if attachments:
            att_table = Table(title=f"Attachments ({len(attachments)})", show_header=True, header_style="bold magenta")
            att_table.add_column("ID", style="dim")
            att_table.add_column("Filename", style="cyan")
            att_table.add_column("Size", style="green")
            att_table.add_column("Uploader", style="yellow")
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
                )
            console.print(att_table)

        comments = t.get("comments", [])
        if comments:
            cmt_lines = []
            for c in comments:
                internal_badge = " [bold magenta][INTERNAL][/bold magenta]" if c.get("is_internal") else ""
                cmt_lines.append(
                    f"[bold cyan]@{c['actor_id']}[/bold cyan]{internal_badge} [dim]({c['created_at']})[/dim]:\n  {c['content']}"
                )
            console.print(Panel("\n\n".join(cmt_lines), title=f"Comments ({len(comments)})", expand=False))
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("move")
@click.argument("ticket_id")
@click.argument("target_column")
@click.option("--note", "-n", default=None, help="Optional note / reason for human stage moves")
@click.pass_context
def move_ticket_cmd(
    ctx: click.Context,
    ticket_id: str,
    target_column: str,
    note: str | None,
) -> None:
    """Move within the same stage, or human-allowed stage pairs only.

    Stage progression must use workflow verbs (claim/submit/verdict/…), not move.
    """
    execute_move_ticket(ticket_id, target_column, note, json_mode=_jm(ctx))


def execute_move_ticket(
    ticket_id: str,
    target_column: str,
    note: str | None = None,
    *,
    json_mode: bool = False,
) -> None:
    try:
        with open_client() as client:
            ticket = client.get_ticket(ticket_id)
            board_id = ticket["board_id"]
            board_data = client.get_board(board_id)
            matched_col = resolve_column(board_data, target_column)
            if not matched_col:
                avail = ", ".join(f"'{c['name']}'" for c in board_data.get("columns", []))
                raise ClientError(
                    f"Target column '{target_column}' not found on board '{board_id}'. Available: {avail}",
                    exit_code=EXIT_USAGE,
                )
            updated = client.move_ticket(
                ticket_id,
                target_column_id=matched_col["column_id"],
                reason=note,
            )
        _ok(
            updated,
            json_mode=json_mode,
            human=(
                f"[bold green]✓[/bold green] Ticket [cyan]{ticket_id}[/cyan] moved to "
                f"[bold yellow]{matched_col['name']}[/bold yellow] "
                f"(stage={updated.get('stage')}, status={updated.get('status')})"
            ),
        )
    except ClientError as exc:
        # Surface the workflow restriction message clearly for agents.
        if exc.code and str(exc.code) == "INVALID_TRANSITION":
            msg = (
                f"{exc.message}. Stage changes must use workflow commands "
                "(claim/submit/verdict/reopen/reroute), not `ak5 ticket move`."
            )
            handle_client_error(
                ClientError(msg, code=exc.code, exit_code=exc.exit_code, details=exc.details),
                json_mode=json_mode,
            )
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("comment")
@click.argument("ticket_id")
@click.argument("content")
@click.option("--internal", "-i", is_flag=True, help="Mark comment as internal agent reasoning")
@click.pass_context
def comment_ticket_cmd(ctx: click.Context, ticket_id: str, content: str, internal: bool) -> None:
    """Add a discussion or reasoning comment to a ticket."""
    execute_comment_ticket(ticket_id, content, internal, json_mode=_jm(ctx))


def execute_comment_ticket(
    ticket_id: str,
    content: str,
    internal: bool = False,
    *,
    json_mode: bool = False,
) -> None:
    api_url = get_api_url()
    headers = require_auth_headers(api_url)
    try:
        with open_client() as client:
            # ensure version negotiation even for comment path
            _ = client
        with httpx.Client(timeout=10.0) as http:
            resp = http.post(
                f"{api_url}/tickets/{ticket_id}/comments",
                json={"content": content, "is_internal": internal},
                headers=headers,
            )
            if resp.status_code == 401:
                raise ClientError("Authentication required", exit_code=8, http_status=401)
            resp.raise_for_status()
            c = resp.json()
        _ok(
            c,
            json_mode=json_mode,
            human=(
                f"[bold green]✓[/bold green] Comment added to [cyan]{ticket_id}[/cyan] "
                f"by [magenta]@{c['actor_id']}[/magenta]"
            ),
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("update")
@click.argument("ticket_id")
@click.option("--title", default=None, help="New title")
@click.option("--desc", "description", default=None, help="New description")
@click.option("--priority", type=click.Choice(["low", "medium", "high", "urgent"]), default=None)
@click.option("--assign", "assigned_to", default=None, help="New assignee Actor ID")
@click.option("--status", type=click.Choice(["open", "in_progress", "done", "blocked"]), default=None)
@click.pass_context
def update_ticket_cmd(
    ctx: click.Context,
    ticket_id: str,
    title: str | None,
    description: str | None,
    priority: str | None,
    assigned_to: str | None,
    status: str | None,
) -> None:
    """Update ticket title, description, priority, or assignee."""
    json_mode = _jm(ctx)
    payload: dict[str, Any] = {}
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
        raise click.UsageError("No fields provided to update.")
    try:
        with open_client() as client:
            result = client.patch(f"/tickets/{ticket_id}", json=payload)
        _ok(
            result,
            json_mode=json_mode,
            human=f"[bold green]✓[/bold green] Ticket [cyan]{ticket_id}[/cyan] updated.",
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("attach")
@click.argument("ticket_id")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_context
def attach_cmd(ctx: click.Context, ticket_id: str, filepath: Path) -> None:
    """Attach a deliverable or file to a ticket."""
    json_mode = _jm(ctx)
    api_url = get_api_url()
    headers = require_auth_headers(api_url)
    try:
        with open_client() as client:
            _ = client  # version negotiate
        with open(filepath, "rb") as f:
            files = {"file": (filepath.name, f, "application/octet-stream")}
            with httpx.Client(timeout=30.0) as http:
                resp = http.post(
                    f"{api_url}/tickets/{ticket_id}/attachments",
                    files=files,
                    headers=headers,
                )
                if resp.status_code == 401:
                    raise ClientError("Authentication required", exit_code=8, http_status=401)
                resp.raise_for_status()
                data = resp.json()
        _ok(
            data,
            json_mode=json_mode,
            human=f"[bold green]✓ File attached:[/bold green] [cyan]{data['filename']}[/cyan]",
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("download-attachment")
@click.argument("ticket_id")
@click.argument("attachment_id")
@click.option("--output", "-o", default=None, help="Output path")
@click.pass_context
def download_attachment_cmd(
    ctx: click.Context,
    ticket_id: str,
    attachment_id: str,
    output: str | None,
) -> None:
    """Download an attachment file from a ticket."""
    json_mode = _jm(ctx)
    api_url = get_api_url()
    headers = require_auth_headers(api_url)
    try:
        with open_client() as client:
            _ = client
        with httpx.Client(timeout=30.0) as http:
            resp = http.get(
                f"{api_url}/tickets/{ticket_id}/attachments/{attachment_id}",
                headers=headers,
            )
            if resp.status_code == 401:
                raise ClientError("Authentication required", exit_code=8, http_status=401)
            resp.raise_for_status()
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
        payload = {"path": str(out_path.resolve()), "bytes": len(resp.content)}
        _ok(
            payload,
            json_mode=json_mode,
            human=f"[bold green]✓ Downloaded to:[/bold green] [cyan]{out_path.resolve()}[/cyan]",
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("list")
@click.option("--board", "-b", "board_id", default=None, help="Filter by Board ID")
@click.option("--archived", is_flag=True, default=False, help="List only archived tickets")
@click.option("--all", "include_all", is_flag=True, default=False, help="List all tickets")
@click.option(
    "--status",
    "-s",
    default=None,
    type=click.Choice(["open", "in_progress", "blocked", "done"]),
)
@click.option("--stage", default=None, help="Filter by column stage")
@click.option("--assign", "-a", "assigned_to", default=None)
@click.option("--creator", default=None)
@click.option("--label", "-l", "labels", multiple=True)
@click.option("--query", "-q", default=None)
@click.option("--limit", default=20, type=int)
@click.pass_context
def list_tickets_cmd(
    ctx: click.Context,
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
    """List tickets with filtering options."""
    json_mode = _jm(ctx)
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
        with open_client() as client:
            tickets = client.get("/tickets", params=params)
        if json_mode:
            print_result(tickets, json_mode=True)
            return
        if not tickets:
            console.print("[yellow]No tickets found matching criteria.[/yellow]")
            return
        table = Table(
            title=f"Tickets ({len(tickets)})",
            show_header=True,
            header_style="bold cyan",
            expand=True,
        )
        table.add_column("ID", style="bold cyan", width=10)
        table.add_column("Title", style="bold white")
        table.add_column("Stage", width=14)
        table.add_column("Status", width=12)
        table.add_column("Priority", width=10)
        table.add_column("Assignee", width=15)
        for t in tickets:
            p_color = (
                "red"
                if t.get("priority") in ("urgent", "high")
                else ("yellow" if t.get("priority") == "medium" else "dim")
            )
            s_color = "green" if t.get("status") == "done" else ("red" if t.get("status") == "blocked" else "yellow")
            table.add_row(
                t["ticket_id"],
                t["title"],
                str(t.get("stage") or ""),
                f"[{s_color}]{t.get('status', 'open').upper()}[/{s_color}]",
                f"[{p_color}][{t.get('priority', 'medium').upper()}][/{p_color}]",
                f"@{t['assigned_to']}" if t.get("assigned_to") else "[dim]Unassigned[/dim]",
            )
        console.print(table)
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("archive")
@click.argument("ticket_id")
@click.pass_context
def archive_ticket_cmd(ctx: click.Context, ticket_id: str) -> None:
    """Archive a ticket."""
    json_mode = _jm(ctx)
    try:
        with open_client() as client:
            t = client.post(f"/tickets/{ticket_id}/archive")
        _ok(
            t,
            json_mode=json_mode,
            human=f"[bold green]✓ Ticket '{ticket_id}' archived.[/bold green]",
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


@ticket_group.command("unarchive")
@click.argument("ticket_id")
@click.pass_context
def unarchive_ticket_cmd(ctx: click.Context, ticket_id: str) -> None:
    """Unarchive a ticket."""
    json_mode = _jm(ctx)
    try:
        with open_client() as client:
            t = client.post(f"/tickets/{ticket_id}/unarchive")
        _ok(
            t,
            json_mode=json_mode,
            human=f"[bold green]✓ Ticket '{ticket_id}' unarchived.[/bold green]",
        )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except Exception as exc:
        handle_client_error(exc, json_mode=json_mode)


# Shortcuts for top-level convenience
@click.command("move")
@click.argument("ticket_id")
@click.argument("target_column")
@click.option("--note", "-n", default=None)
@click.pass_context
def move_shortcut(ctx: click.Context, ticket_id: str, target_column: str, note: str | None) -> None:
    """Quick shortcut to move a ticket to another column."""
    execute_move_ticket(ticket_id, target_column, note, json_mode=_jm(ctx))


@click.command("comment")
@click.argument("ticket_id")
@click.argument("content")
@click.option("--internal", "-i", is_flag=True)
@click.pass_context
def comment_shortcut(ctx: click.Context, ticket_id: str, content: str, internal: bool) -> None:
    """Quick shortcut to post a comment to a ticket."""
    execute_comment_ticket(ticket_id, content, internal, json_mode=_jm(ctx))
