"""Board template registry CLI (``ak5 template list|show|apply``)."""

from __future__ import annotations

import click
from ak5.cli.client_factory import json_flag_from_ctx, open_client
from ak5.client.errors import ClientError
from ak5.client.output import handle_client_error, is_json_mode, print_result
from ak5.services.templates import (
    BUILTIN_TEMPLATES,
    get_template,
    list_templates,
    template_to_dict,
    workspace_templates_dir,
)
from rich.console import Console
from rich.table import Table

console = Console()


@click.group("template")
def template_group() -> None:
    """Inspect and apply board templates (workflow.md §2.2)."""


@template_group.command("list")
@click.pass_context
def template_list(ctx: click.Context) -> None:
    """List built-in and workspace board templates."""
    json_mode = is_json_mode(flag=json_flag_from_ctx(ctx))
    registry = list_templates()
    rows = [template_to_dict(t) for t in registry.values()]
    if json_mode:
        print_result(rows, json_mode=True)
        return
    table = Table(title="Board templates", show_lines=True)
    table.add_column("Name", style="cyan bold")
    table.add_column("Source", style="dim")
    table.add_column("Caps", style="yellow")
    table.add_column("Checklist", style="magenta")
    table.add_column("Max attempts", justify="right")
    for name, tmpl in sorted(registry.items()):
        source = "builtin" if name in BUILTIN_TEMPLATES and BUILTIN_TEMPLATES[name] is tmpl else "workspace"
        table.add_row(
            name,
            source,
            ", ".join(tmpl.worker_capabilities) or "—",
            str(len(tmpl.review_checklist)),
            str(tmpl.max_attempts),
        )
    console.print(table)
    console.print(f"[dim]Workspace dir: {workspace_templates_dir()}[/dim]")


@template_group.command("show")
@click.argument("name")
@click.pass_context
def template_show(ctx: click.Context, name: str) -> None:
    """Show one template (columns, capabilities, checklists)."""
    json_mode = is_json_mode(flag=json_flag_from_ctx(ctx))
    try:
        tmpl = get_template(name)
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc
    data = template_to_dict(tmpl)
    if json_mode:
        print_result(data, json_mode=True)
        return
    console.print(f"[bold cyan]{tmpl.name}[/bold cyan]  max_attempts={tmpl.max_attempts}")
    console.print(f"worker_capabilities: {list(tmpl.worker_capabilities) or '—'}")
    console.print(
        f"acceptance_criteria_defaults: {list(tmpl.acceptance_criteria_defaults) or '—'}"
    )
    console.print(f"review_checklist: {list(tmpl.review_checklist) or '—'}")
    cols = Table(title="Columns", show_header=True)
    cols.add_column("Stage")
    cols.add_column("Name")
    cols.add_column("Pos", justify="right")
    cols.add_column("WIP", justify="right")
    for c in tmpl.columns:
        cols.add_row(c.stage, c.name, str(c.position), str(c.wip_limit))
    console.print(cols)


@template_group.command("apply")
@click.argument("board_id")
@click.argument("template_name")
@click.pass_context
def template_apply(ctx: click.Context, board_id: str, template_name: str) -> None:
    """Apply a template to a board (adds missing columns; never deletes)."""
    json_mode = is_json_mode(flag=json_flag_from_ctx(ctx))
    try:
        get_template(template_name)
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc
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
