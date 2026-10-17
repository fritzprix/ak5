"""``ak5 daemon`` lifecycle commands (client.md §6)."""

from __future__ import annotations

import json
import sys

import click
from ak5.daemon.paths import pid_path, socket_path
from ak5.daemon.server import is_daemon_running, read_pid, run_daemon, start_daemon_background, stop_daemon
from rich.console import Console

console = Console()


@click.group("daemon")
def daemon_command() -> None:
    """Client daemon: token broker + event fan-out."""


@daemon_command.command("run")
def daemon_run() -> None:
    """Run the daemon in the foreground (UDS HTTP)."""
    console.print(f"[dim]daemon socket:[/dim] {socket_path()}")
    run_daemon()


@daemon_command.command("start")
def daemon_start() -> None:
    """Start the daemon in the background."""
    if is_daemon_running():
        console.print(f"[green]daemon already running[/green] (pid {read_pid()})")
        return
    pid = start_daemon_background()
    console.print(f"[green]daemon started[/green] (pid {pid})")
    console.print(f"[dim]socket:[/dim] {socket_path()}")


@daemon_command.command("stop")
def daemon_stop() -> None:
    """Stop the background daemon."""
    stop_daemon()
    console.print("[green]daemon stopped[/green]")


@daemon_command.command("status")
@click.pass_context
def daemon_status(ctx: click.Context) -> None:
    """Show daemon status."""
    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    running = is_daemon_running()
    body = {
        "running": running,
        "pid": read_pid(),
        "socket": str(socket_path()),
        "pid_file": str(pid_path()),
    }
    if running:
        try:
            from ak5.daemon.ipc import get_status

            body["status"] = get_status(ensure=False)
        except Exception as exc:  # noqa: BLE001
            body["status_error"] = str(exc)
    if json_mode:
        click.echo(json.dumps(body, default=str))
        if not running:
            sys.exit(1)
        return
    if running:
        console.print(f"[green]running[/green] pid={body['pid']} sock={body['socket']}")
    else:
        console.print("[yellow]stopped[/yellow]")
        sys.exit(1)


@daemon_command.command("install")
def daemon_install() -> None:
    """Install a user service unit (stub — prints unit path guidance)."""
    console.print(
        "[yellow]install[/yellow]: write a systemd user unit / launchd plist that runs "
        "`ak5 daemon run`. Not auto-installed in this build; use `ak5 daemon start`."
    )


@daemon_command.command("logs")
@click.option("--dead-letter", is_flag=True, help="Show subscriber dead-letter entries")
def daemon_logs(dead_letter: bool) -> None:
    log = pid_path().parent / "daemon.log"
    if dead_letter:
        from ak5.daemon.state import DaemonState

        state = DaemonState()
        try:
            for s in state.list_subscribers():
                if s.dead_letter:
                    console.print(f"{s.id}: {s.dead_letter}")
        finally:
            state.close()
        return
    if log.is_file():
        console.print(log.read_text(encoding="utf-8")[-8000:])
    else:
        console.print("[dim]no daemon.log yet[/dim]")


@daemon_command.command("replay")
@click.argument("event_id")
def daemon_replay(event_id: str) -> None:
    console.print(f"[yellow]replay[/yellow] of {event_id} is not implemented yet")


@daemon_command.command("import-legacy")
def daemon_import_legacy() -> None:
    """Import ``AK5_DATA_DIR/legacy_subscriptions.json`` as local subscribers."""

    from ak5.daemon.profiles import resolve_profile_name
    from ak5.daemon.state import DaemonState
    from ak5.paths import get_app_data_dir

    legacy = get_app_data_dir() / "legacy_subscriptions.json"
    if not legacy.is_file():
        console.print(f"[yellow]no legacy file[/yellow]: {legacy}")
        return
    import json

    rows = json.loads(legacy.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise click.ClickException("legacy_subscriptions.json must be a list")
    profile = resolve_profile_name(None) or "default"
    state = DaemonState()
    try:
        for row in rows:
            if not isinstance(row, dict):
                continue
            cmd = str(row.get("exec_command") or row.get("hook_exec") or "true")
            state.add_subscriber(
                profile=profile,
                exec_command=cmd,
                board_id=row.get("board_id"),
                events=None,
            )
        console.print(f"[green]imported {len(rows)}[/green] → profile={profile}")
    finally:
        state.close()
