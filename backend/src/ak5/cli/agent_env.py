"""Detect agent/harness runtimes that must not use blocking SSE watchers."""

from __future__ import annotations

import os
import sys

from rich.console import Console

console = Console(stderr=True)

_AGENT_ENV_TRUTHY = ("1", "true", "yes", "on")
_AGENT_MARKER_VARS = (
    "AK5_AGENT",
    "LIBRAGENT_SESSION",
    "LIBRAGENT_WORKSPACE",
    "CURSOR_AGENT_ID",
    "CURSOR_AGENT",
)


def agent_watch_override_allowed() -> bool:
    """Tests / rare human non-TTY escape hatch."""
    return os.environ.get("AK5_ALLOW_WATCH", "").strip().lower() in _AGENT_ENV_TRUTHY


def is_agent_runtime() -> bool:
    """True when CLI is likely driven by an autonomous agent harness."""
    if agent_watch_override_allowed():
        return False

    for key in _AGENT_MARKER_VARS:
        value = os.environ.get(key, "").strip()
        if not value:
            continue
        if key == "AK5_AGENT":
            if value.lower() in _AGENT_ENV_TRUTHY:
                return True
            continue
        return True

    # Harness spawnProcess / piped shells usually have no TTY.
    try:
        if not sys.stdout.isatty() or not sys.stdin.isatty():
            return True
    except Exception:
        return True

    return False


def refuse_blocking_watch_for_agents() -> None:
    """Exit 2 if an agent tries to run a blocking SSE watcher."""
    if not is_agent_runtime():
        return

    console.print(
        "[bold red]✗ Blocking event watch is not allowed in agent/harness runtimes.[/bold red]\n"
        "[yellow]Agents must Register & Return:[/yellow]\n"
        '  [cyan]ak5 subscribe create <BOARD_ID> --exec \'…\' --for-agent "$AK5_ACTOR_ID"[/cyan]\n'
        "[dim]Verify with:[/dim] [cyan]ak5 subscribe ls[/cyan]\n"
        "[dim]Human terminals only:[/dim] [cyan]ak5 events watch <BOARD_ID> --exec '…'[/cyan]\n"
        "[dim]Override (tests only):[/dim] [cyan]AK5_ALLOW_WATCH=1[/cyan]"
    )
    raise SystemExit(2)
