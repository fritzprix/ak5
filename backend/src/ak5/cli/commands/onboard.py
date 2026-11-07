"""``ak5 onboard`` — read-only setup check that tells an agent what to do next."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import click
import httpx
from ak5 import __version__
from ak5.cli.commands.skill import installed_skill_paths
from ak5.cli.config import get_api_url
from ak5.client.http import api_root_from_url, versions_compatible
from ak5.daemon.credentials import load_credential
from ak5.daemon.profiles import list_profiles
from ak5.daemon.server import is_daemon_running

PROBE_TIMEOUT_SECONDS = 3.0


@dataclass
class ServerCheck:
    api_url: str
    ready: bool = False
    api_version: str | None = None
    compatible: bool | None = None
    error: str | None = None


@dataclass
class ProfileCheck:
    name: str
    role: str
    principal: str
    server: str
    logged_in: bool


@dataclass
class OnboardReport:
    cli_version: str
    server: ServerCheck
    daemon_running: bool
    profiles: list[ProfileCheck]
    skill_paths: list[str]
    next_steps: list[str] = field(default_factory=list)


def _probe_server(api_url: str) -> ServerCheck:
    check = ServerCheck(api_url=api_url)
    root = api_root_from_url(api_url)
    try:
        with httpx.Client(base_url=root, timeout=PROBE_TIMEOUT_SECONDS) as client:
            ready = client.get("/readyz")
            check.ready = ready.status_code == 200
            meta = client.get("/meta")
            if meta.is_success:
                body = meta.json()
                check.api_version = str(body.get("api_version", ""))
                check.compatible = versions_compatible(
                    server_api_version=check.api_version,
                    server_min_client=str(body.get("min_client", "0.0.0")),
                )
    except httpx.HTTPError as exc:
        check.error = f"{type(exc).__name__}: {exc}"
    return check


def _same_server(a: str, b: str) -> bool:
    return api_root_from_url(a) == api_root_from_url(b)


def _next_steps(report: OnboardReport) -> list[str]:
    server = report.server
    if not server.ready:
        return [
            f"ASK THE HUMAN: no AK5 server is ready at {server.api_url}. Ask them to start one in "
            "another terminal (`uvx --from 'ak5[server]' ak5 up` for a local setup) or to give you "
            "the server URL. Do not start it in your own session. Then re-run `ak5 onboard` "
            "(add `--server <URL>` for a remote server).",
        ]
    if server.compatible is False:
        return [
            f"STOP: this CLI ({report.cli_version}) does not match the server API "
            f"({server.api_version}). Tell the human; run `uvx ak5@latest onboard` once they confirm.",
        ]

    steps: list[str] = []
    if not report.daemon_running:
        steps.append("Run `ak5 daemon start` (non-blocking; it holds your credentials).")
    if not report.skill_paths:
        steps.append(
            "Run `ak5 skill show` and follow it for all ticket work. Ask the human whether to save it "
            "for your harness with `ak5 skill install --harness <agents|claude|cursor>`."
        )
    reusable = [
        p.name
        for p in report.profiles
        if p.principal == "agent" and p.logged_in and _same_server(p.server, server.api_url)
    ]
    reuse_hint = f" Logged-in agent profiles you could reuse: {', '.join(reusable)}." if reusable else ""
    steps.extend(
        [
            "ASK THE HUMAN, in one message with your recommended defaults: "
            "(1) role: triage (inbox to spec), worker (build), or gatekeeper (review); "
            "(2) board: show them `ak5 boards` (triage uses the inbox); "
            "(3) identity: reuse a profile or register a new one;"
            f"{reuse_hint} "
            "(4) capabilities, e.g. py,web; "
            "(5) loop mode: manual, session loop (`ak5 next --wait 25`), or wake hook. "
            "Wait for the answers.",
            f"New identity: run `ak5 login --url {server.api_url} --agent <NAME> --role <ROLE> --caps <CAPS>`, "
            "show the human the approval URL it prints, and wait for them to approve it. You never "
            "approve yourself. Done when `ak5 whoami --profile <NAME>` shows the role.",
            "Verify without claiming: `ak5 --json --profile <NAME> next --board <BOARD>` "
            "(`--inbox` for triage). Exit 0 or 6 means you are set up.",
            "Wake hook only: `ak5 subscribe add <BOARD> --profile <NAME> --events WORK_AVAILABLE "
            "--preset <claude|cursor>` (or `--exec '<command that wakes you>'`), then `ak5 subscribe ls`.",
            "Report to the human: profile, role, board, loop mode, and how to undo it "
            "(`ak5 subscribe rm <ID>`, `ak5 logout --profile <NAME>`).",
        ]
    )
    return steps


def _logged_in(profile_name: str) -> bool:
    cred = load_credential(profile_name)
    return cred is not None and cred.status == "active"


def build_report(api_url: str) -> OnboardReport:
    profiles = [
        ProfileCheck(
            name=p.name,
            role=p.role,
            principal=p.principal,
            server=p.server,
            logged_in=_logged_in(p.name),
        )
        for p in list_profiles()
    ]
    report = OnboardReport(
        cli_version=__version__,
        server=_probe_server(api_url),
        daemon_running=is_daemon_running(),
        profiles=profiles,
        skill_paths=[str(p) for p in installed_skill_paths([Path.cwd(), Path.home()])],
    )
    report.next_steps = _next_steps(report)
    return report


def _print_human(report: OnboardReport) -> None:
    server = report.server
    if server.ready:
        server_line = f"ready (api {server.api_version})"
    else:
        server_line = f"not ready ({server.error or 'readyz failed'})"
    click.echo(f"AK5 CLI    {report.cli_version}")
    click.echo(f"Server     {server.api_url}  {server_line}")
    click.echo(f"Daemon     {'running' if report.daemon_running else 'stopped'}")
    if report.profiles:
        for p in report.profiles:
            state = "logged in" if p.logged_in else "not logged in"
            click.echo(f"Profile    {p.name}  {p.principal}/{p.role or '-'}  {state}  {p.server}")
    else:
        click.echo("Profile    none")
    click.echo(f"Skill      {', '.join(report.skill_paths) or 'not installed'}")
    click.echo("")
    click.echo("Next steps:")
    for i, step in enumerate(report.next_steps, 1):
        click.echo(f"{i}. {step}")


@click.command("onboard")
@click.option("--server", "server_url", default=None, help="AK5 API URL (default: active profile or http://127.0.0.1:8000/api/v1)")
@click.pass_context
def onboard_command(ctx: click.Context, server_url: str | None) -> None:
    """Check this machine's AK5 setup and print what an agent should do next.

    \b
    Read-only and non-blocking. Agents: run it first, follow the steps,
    ask the human whenever a step says so, and re-run it after each fix.
    """
    report = build_report(server_url or get_api_url())
    if ctx.obj and ctx.obj.get("json"):
        click.echo(json.dumps(asdict(report)))
        return
    _print_human(report)
