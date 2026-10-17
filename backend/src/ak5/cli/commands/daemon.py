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
@click.option("--enable", is_flag=True, help="systemctl --user enable --now after writing the unit")
def daemon_install(enable: bool) -> None:
    """Write a systemd user unit (Linux) that runs ``ak5 daemon run``."""
    import shutil
    import sys
    from pathlib import Path

    if sys.platform != "linux":
        console.print(
            "[yellow]install[/yellow]: non-Linux — write a launchd plist manually that runs "
            "`ak5 daemon run`, or use `ak5 daemon start`."
        )
        return
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = unit_dir / "ak5-daemon.service"
    exe = shutil.which("ak5") or sys.executable
    exec_start = f"{exe} daemon run" if shutil.which("ak5") else f"{sys.executable} -m ak5.cli.main daemon run"
    unit.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=AK5 client daemon",
                "[Service]",
                "Type=simple",
                f"ExecStart={exec_start}",
                "Restart=on-failure",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        ),
        encoding="utf-8",
    )
    console.print(f"[green]wrote[/green] {unit}")
    if enable:
        import subprocess

        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", "ak5-daemon.service"], check=False)
        console.print("[green]enabled[/green] ak5-daemon.service")


@daemon_command.command("logs")
@click.option("--dead-letter", is_flag=True, help="Show dead-letter.jsonl entries")
@click.option("--tail", default=40, type=int, help="Lines to show from dead-letter.jsonl")
def daemon_logs(dead_letter: bool, tail: int) -> None:
    log = pid_path().parent / "daemon.log"
    if dead_letter:
        from ak5.daemon.paths import dead_letter_path

        path = dead_letter_path()
        if not path.is_file():
            console.print(f"[dim]no dead-letter yet[/dim] ({path})")
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines[-max(1, tail) :]:
            console.print(line)
        return
    if log.is_file():
        console.print(log.read_text(encoding="utf-8")[-8000:])
    else:
        console.print("[dim]no daemon.log yet[/dim]")


@daemon_command.command("replay")
@click.argument("event_id")
@click.option("--profile", default=None, help="Profile whose subscribers should re-run the event")
def daemon_replay(event_id: str, profile: str | None) -> None:
    """Re-dispatch a dead-lettered (or spool) event to local subscribers."""
    import json

    from ak5.daemon.hooks import dispatch_new_events
    from ak5.daemon.paths import dead_letter_path
    from ak5.daemon.profiles import resolve_profile_name
    from ak5.daemon.state import DaemonState

    path = dead_letter_path()
    target: dict | None = None
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("event_id")) == event_id or str(row.get("seq")) == event_id:
                target = row.get("event") if isinstance(row.get("event"), dict) else row
                profile = profile or row.get("profile")
                break
    if target is None:
        console.print(f"[red]event not found in dead-letter:[/red] {event_id}")
        raise SystemExit(1)
    name = resolve_profile_name(profile) or "default"
    state = DaemonState()
    try:
        state.append_spool(
            profile=name,
            event_type=str(target.get("type") or "WORK_AVAILABLE"),
            payload=target.get("data") if isinstance(target.get("data"), dict) else dict(target),
            ticket_id=target.get("ticket_id"),
            board_id=target.get("board_id"),
        )
        import asyncio

        asyncio.run(dispatch_new_events(state, name))
        console.print(f"[green]replayed[/green] {event_id} → profile={name}")
    finally:
        state.close()


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
