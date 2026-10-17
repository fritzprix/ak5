"""Board template registry CLI (``ak5 template list|show|apply``) — thin REST adapter."""

from __future__ import annotations

import click
from ak5.cli.client_factory import json_flag_from_ctx, open_client
from ak5.client.errors import ClientError
from ak5.client.output import handle_client_error, is_json_mode, print_result
from rich.console import Console
from rich.table import Table

console = Console()


@click.group("template")
def template_group() -> None:
    """Inspect and apply board templates (workflow.md §2.2)."""


@template_group.command("list")
@click.pass_context
def template_list(ctx: click.Context) -> None:
    """List built-in and server workspace board templates."""
    json_mode = is_json_mode(flag=json_flag_from_ctx(ctx))
    try:
        with open_client() as client:
            rows = client.list_templates()
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
        return
    if json_mode:
        print_result(rows, json_mode=True)
        return
    table = Table(title="Board templates", show_lines=True)
    table.add_column("Name", style="cyan bold")
    table.add_column("Source", style="dim")
    table.add_column("Caps", style="yellow")
    table.add_column("Checklist", style="magenta")
    table.add_column("Max attempts", justify="right")
    for row in sorted(rows, key=lambda r: str(r.get("name") or "")):
        caps = row.get("worker_capabilities") or []
        checklist = row.get("review_checklist") or []
        table.add_row(
            str(row.get("name") or ""),
            str(row.get("source") or ""),
            ", ".join(str(c) for c in caps) or "—",
            str(len(checklist)),
            str(row.get("max_attempts") or ""),
        )
    console.print(table)


@template_group.command("show")
@click.argument("name")
@click.pass_context
def template_show(ctx: click.Context, name: str) -> None:
    """Show one template from the server registry."""
    json_mode = is_json_mode(flag=json_flag_from_ctx(ctx))
    try:
        with open_client() as client:
            data = client.get_template(name)
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
        return
    if json_mode:
        print_result(data, json_mode=True)
        return
    console.print(
        f"[bold cyan]{data.get('name')}[/bold cyan]  "
        f"source={data.get('source')}  max_attempts={data.get('max_attempts')}"
    )
    console.print(f"worker_capabilities: {data.get('worker_capabilities') or '—'}")
    console.print(
        f"acceptance_criteria_defaults: {data.get('acceptance_criteria_defaults') or '—'}"
    )
    console.print(f"review_checklist: {data.get('review_checklist') or '—'}")
    cols = Table(title="Columns", show_header=True)
    cols.add_column("Stage")
    cols.add_column("Name")
    cols.add_column("Pos", justify="right")
    cols.add_column("WIP", justify="right")
    for c in data.get("columns") or []:
        cols.add_row(
            str(c.get("stage") or ""),
            str(c.get("name") or ""),
            str(c.get("position") or ""),
            str(c.get("wip_limit") or ""),
        )
    console.print(cols)


@template_group.command("apply")
@click.argument("board_id")
@click.argument("template_name")
@click.pass_context
def template_apply(ctx: click.Context, board_id: str, template_name: str) -> None:
    """Apply a template to a board (adds missing columns; never deletes)."""
    json_mode = is_json_mode(flag=json_flag_from_ctx(ctx))
    try:
        with open_client() as client:
            board = client.update_board(board_id, {"template": template_name})
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
        return
    if json_mode:
        print_result(board, json_mode=True)
        return
    console.print(
        f"[green]Applied template[/green] [cyan]{template_name}[/cyan] "
        f"→ board [cyan]{board.get('board_id', board_id)}[/cyan]"
    )
