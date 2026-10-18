"""Board view / create / adopt (cli.md §4.5)."""

from __future__ import annotations

import asyncio
from typing import Any

import click
import httpx
from ak5.cli.client_factory import json_flag_from_ctx, open_client
from ak5.cli.commands.boards import _adopt_board_impl, _create_board_impl, render_boards_table
from ak5.cli.config import get_api_url, require_auth_headers
from ak5.client.errors import EXIT_AUTH, EXIT_DAEMON, EXIT_GENERIC, ClientError
from ak5.client.output import handle_client_error, is_json_mode, print_result
from ak5.contracts.errors import ErrorCode
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Back-compat alias for tests/callers monkeypatching get_auth_headers
get_auth_headers = require_auth_headers

console = Console()

_BOARD_SUBCOMMANDS = frozenset({"create", "adopt"})


def render_board_view(board_data: dict) -> Table:
    """Render Kanban board as a Rich Table."""
    main_table = Table(
        title=f"Kanban Board: {board_data['name']} ({board_data['board_id']})",
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )

    stage_colors = {
        "unclassified": "white",
        "backlog": "blue",
        "open": "blue",
        "in_progress": "yellow",
        "agent_review": "magenta",
        "review": "magenta",
        "postmortem_pending": "red",
        "human_review": "cyan",
        "done": "green",
    }

    for col in board_data.get("columns", []):
        color = stage_colors.get(col.get("stage"), "white")
        count = len(col.get("tickets", []))
        total = col.get("total_ticket_count", count)
        if col.get("wip_limit", 0) > 0:
            wip_info = f" [WIP: {count}/{col['wip_limit']}]"
        elif total > count:
            wip_info = f" ({count}/{total})"
        else:
            wip_info = f" ({count})"
        main_table.add_column(f"[{color}]{col['name']}{wip_info}[/{color}]")

    max_tickets = (
        max(len(col.get("tickets", [])) for col in board_data.get("columns", []))
        if board_data.get("columns")
        else 0
    )

    if max_tickets == 0:
        empty_row = [Panel("[dim](Empty)[/dim]", expand=True) for _ in board_data.get("columns", [])]
        if empty_row:
            main_table.add_row(*empty_row)
        return main_table

    for row_idx in range(max_tickets):
        row_cells = []
        for col in board_data.get("columns", []):
            tickets = col.get("tickets", [])
            if row_idx < len(tickets):
                t = tickets[row_idx]
                p_color = (
                    "red"
                    if t["priority"] in ("urgent", "high")
                    else ("yellow" if t["priority"] == "medium" else "dim")
                )
                status_badge = f"[{p_color}][{t['priority'].upper()}][/{p_color}]"
                assignee = f"@{t['assigned_to']}" if t.get("assigned_to") else "[dim]Unassigned[/dim]"
                subtask_badge = ""
                if t.get("subtask_count", 0) > 0:
                    subtask_badge = (
                        f"\n[bold cyan]Subtasks: {t['subtask_done_count']}/{t['subtask_count']} Done[/bold cyan]"
                    )
                archived_badge = " [bold red][ARCHIVED][/bold red]" if t.get("is_archived") else ""
                content = (
                    f"[bold white]{t['title']}[/bold white]{archived_badge}\n"
                    f"{status_badge} [dim]{t['ticket_id']}[/dim] | {assignee}"
                    f"{subtask_badge}"
                )
                border_color = "yellow" if col.get("stage") == "in_progress" else "white"
                row_cells.append(Panel(content, border_style=border_color, expand=True))
            else:
                row_cells.append(Text(""))
        main_table.add_row(*row_cells)

    has_more = any(
        col.get("total_ticket_count", 0) > len(col.get("tickets", []))
        for col in board_data.get("columns", [])
    )
    if has_more:
        more_cells = []
        for col in board_data.get("columns", []):
            diff = col.get("total_ticket_count", 0) - len(col.get("tickets", []))
            if diff > 0:
                more_cells.append(
                    Panel(
                        f"[dim]+ {diff} more tickets\n(--done-limit=0 to see all)[/dim]",
                        border_style="dim",
                        expand=True,
                    )
                )
            else:
                more_cells.append(Text(""))
        main_table.add_row(*more_cells)

    return main_table


def fetch_board(
    api_url: str,
    board_id: str,
    include_archived: bool = False,
    done_limit: int | None = 10,
) -> dict:
    params: dict[str, Any] = {}
    if include_archived:
        params["include_archived"] = "true"
    if done_limit is not None and done_limit > 0:
        params["done_limit"] = done_limit
    elif done_limit == 0:
        params["done_limit"] = 0

    with open_client(api_url=api_url) as client:
        return client.get_board(board_id, params=params)


async def watch_board_live(
    api_url: str,
    board_id: str,
    include_archived: bool = False,
    done_limit: int | None = 10,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Stream SSE events and re-render board on changes."""
    try:
        headers = get_auth_headers(api_url)
    except SystemExit as exc:
        raw = exc.code if isinstance(exc.code, int) else EXIT_AUTH
        if raw == 1:
            raw = EXIT_AUTH
        if raw == EXIT_DAEMON:
            raise ClientError(
                "ak5 daemon is not running",
                code=ErrorCode.DAEMON_UNAVAILABLE,
                exit_code=EXIT_DAEMON,
            ) from exc
        raise ClientError(
            "Authentication required",
            code=ErrorCode.UNAUTHENTICATED,
            exit_code=raw,
        ) from exc

    if not headers or not headers.get("Authorization"):
        raise ClientError(
            "Authentication required",
            code=ErrorCode.UNAUTHENTICATED,
            exit_code=EXIT_AUTH,
            http_status=401,
        )

    console.print(f"[bold cyan]Connecting to real-time event stream for board '{board_id}'...[/bold cyan]")
    board_data = fetch_board(api_url, board_id, include_archived=include_archived, done_limit=done_limit)

    params: dict[str, str] = {"board_id": board_id}
    with Live(render_board_view(board_data), console=console, refresh_per_second=4) as live:
        async with (
            httpx.AsyncClient(timeout=None, transport=transport) as client,
            client.stream(
                "GET",
                f"{api_url}/events/stream",
                headers=headers,
                params=params,
            ) as stream,
        ):
            if stream.status_code == 401:
                raise ClientError(
                    "Authentication required",
                    code=ErrorCode.UNAUTHENTICATED,
                    exit_code=EXIT_AUTH,
                    http_status=401,
                )
            if stream.status_code == 403:
                raise ClientError(
                    "Forbidden",
                    code=ErrorCode.ROLE_FORBIDDEN,
                    exit_code=4,
                    http_status=403,
                )
            if stream.status_code == 404:
                raise ClientError(
                    f"Board '{board_id}' not found",
                    code=ErrorCode.NOT_FOUND,
                    exit_code=4,
                    http_status=404,
                )
            if stream.status_code >= 400:
                raise ClientError(
                    f"Event stream failed (HTTP {stream.status_code})",
                    exit_code=EXIT_GENERIC,
                    http_status=stream.status_code,
                )
            async for line in stream.aiter_lines():
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                    if event_type in (
                        "TICKET_CREATED",
                        "TICKET_MOVED",
                        "TICKET_DELEGATED",
                        "TICKET_UPDATED",
                        "TICKET_ARCHIVED",
                        "TICKET_UNARCHIVED",
                        "COMMENT_ADDED",
                        "TICKET_TRANSITIONED",
                        "WORK_AVAILABLE",
                    ):
                        try:
                            board_data = fetch_board(
                                api_url,
                                board_id,
                                include_archived=include_archived,
                                done_limit=done_limit,
                            )
                            live.update(render_board_view(board_data))
                        except (httpx.HTTPError, OSError, ClientError):
                            pass


def _show_board(
    board_id: str,
    *,
    watch: bool,
    include_archived: bool,
    done_limit: int,
    json_mode: bool,
) -> None:
    api_url = get_api_url()
    try:
        if watch:
            if json_mode:
                raise ClientError(
                    "--watch is for human terminals; not compatible with --json",
                    exit_code=2,
                )
            asyncio.run(
                watch_board_live(
                    api_url,
                    board_id,
                    include_archived=include_archived,
                    done_limit=done_limit,
                )
            )
            return

        params: dict[str, Any] = {}
        if include_archived:
            params["include_archived"] = "true"
        if done_limit is not None and done_limit > 0:
            params["done_limit"] = done_limit
        elif done_limit == 0:
            params["done_limit"] = 0
        with open_client() as client:
            board_data = client.get_board(board_id, params=params or None)
        if json_mode:
            print_result(board_data, json_mode=True)
        else:
            console.print(render_board_view(board_data))
            console.print(
                f"[dim]Active Board: [bold cyan]{board_data['board_id']}[/bold cyan] | "
                "Run [bold cyan]ak5 boards[/bold cyan] to list all[/dim]"
            )
    except ClientError as exc:
        handle_client_error(exc, json_mode=json_mode)
    except KeyboardInterrupt:
        console.print("\n[yellow]Watch stopped.[/yellow]")
    except Exception as e:
        handle_client_error(e, json_mode=json_mode)


class BoardGroup(click.Group):
    """``ak5 board <id>`` views; ``create`` / ``adopt`` are real subcommands."""

    def resolve_command(self, ctx: click.Context, args: list[str]):
        if args and args[0] not in _BOARD_SUBCOMMANDS and not args[0].startswith("-"):
            # Treat first token as board id → synthetic "view" command.
            return "view", self.get_command(ctx, "view"), args
        return super().resolve_command(ctx, args)


@click.group(
    cls=BoardGroup,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option("--board-id", default=None, help="Target Board ID")
@click.option(
    "--watch",
    is_flag=True,
    help="Human terminal SSE only (blocking). Agents: do NOT use — use subscribe/daemon.",
)
@click.option("--list", "-l", "list_boards_flag", is_flag=True, help="List all boards")
@click.option("--done-limit", default=10, type=int, help="Limit Done column tickets (0 = all)")
@click.option("--include-archived", is_flag=True, default=False)
@click.pass_context
def board_command(
    ctx: click.Context,
    board_id: str | None,
    watch: bool,
    list_boards_flag: bool,
    done_limit: int,
    include_archived: bool,
) -> None:
    """View a Kanban board, or use subcommands ``create`` / ``adopt``.

    Examples:
      ak5 board proj-core-engine
      ak5 board create my-board --template default --name "My Board"
      ak5 board adopt my-board
    """
    ctx.ensure_object(dict)
    ctx.obj["board_watch"] = watch
    ctx.obj["board_done_limit"] = done_limit
    ctx.obj["board_include_archived"] = include_archived

    if ctx.invoked_subcommand is not None:
        return

    json_mode = is_json_mode(flag=json_flag_from_ctx(ctx))

    if list_boards_flag:
        try:
            with open_client() as client:
                boards = client.list_boards()
            if json_mode:
                print_result(boards, json_mode=True)
                return
            if not boards:
                console.print("[yellow]No boards found.[/yellow]")
                return
            console.print(render_boards_table(boards))
            return
        except ClientError as exc:
            handle_client_error(exc, json_mode=json_mode)
        except Exception as e:
            handle_client_error(e, json_mode=json_mode)
        return

    resolved = board_id or "proj-core-engine"
    _show_board(
        resolved,
        watch=watch,
        include_archived=include_archived,
        done_limit=done_limit,
        json_mode=json_mode,
    )


@board_command.command("view", hidden=True)
@click.argument("board_id")
@click.option("--watch", is_flag=True, default=None)
@click.option("--done-limit", default=None, type=int)
@click.option("--include-archived", is_flag=True, default=None)
@click.pass_context
def board_view_cmd(
    ctx: click.Context,
    board_id: str,
    watch: bool | None,
    done_limit: int | None,
    include_archived: bool | None,
) -> None:
    """Internal: view board by id (routed from ``ak5 board <id>``)."""
    obj = ctx.obj if isinstance(ctx.obj, dict) else {}
    _show_board(
        board_id,
        watch=bool(obj.get("board_watch")) if watch is None else watch,
        include_archived=(
            bool(obj.get("board_include_archived"))
            if include_archived is None
            else include_archived
        ),
        done_limit=(
            int(obj.get("board_done_limit", 10)) if done_limit is None else done_limit
        ),
        json_mode=is_json_mode(flag=json_flag_from_ctx(ctx)),
    )


@board_command.command("create")
@click.argument("board_id")
@click.option("--template", required=True, help="Board template name (e.g. default)")
@click.option("--name", "-n", default=None, help="Board display name (default: board_id)")
@click.option("--desc", "-d", "description", default=None, help="Board description")
@click.option(
    "--owner",
    "owners",
    multiple=True,
    help="Human actor ID to enroll as board admin (repeatable).",
)
@click.pass_context
def board_create_cmd(
    ctx: click.Context,
    board_id: str,
    template: str,
    name: str | None,
    description: str | None,
    owners: tuple[str, ...],
) -> None:
    """Create a board from a template."""
    _create_board_impl(
        board_id,
        name or board_id,
        description,
        template,
        owners,
        json_mode=is_json_mode(flag=json_flag_from_ctx(ctx)),
    )


@board_command.command("adopt")
@click.argument("board_id")
@click.pass_context
def board_adopt_cmd(ctx: click.Context, board_id: str) -> None:
    """Claim board admin as a human when no human admin exists yet."""
    _adopt_board_impl(
        board_id,
        json_mode=is_json_mode(flag=json_flag_from_ctx(ctx)),
    )
