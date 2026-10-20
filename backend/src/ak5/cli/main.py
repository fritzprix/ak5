import os

import click
from rich.console import Console

from ak5.cli.commands.agents import agents_command
from ak5.cli.commands.board import board_command
from ak5.cli.commands.boards import boards_command, claim_board_command, create_board_command
from ak5.cli.commands.daemon import daemon_command
from ak5.cli.commands.db import db_group
from ak5.cli.commands.delegate import delegate_command
from ak5.cli.commands.demo import demo_command
from ak5.cli.commands.events import events_command, watch_command
from ak5.cli.commands.login import login_command
from ak5.cli.commands.logout import logout_command
from ak5.cli.commands.mcp import mcp_command
from ak5.cli.commands.migrate_legacy import migrate_legacy_command
from ak5.cli.commands.next import next_command
from ak5.cli.commands.profile import profile_command
from ak5.cli.commands.reset import reset_command
from ak5.cli.commands.serve import serve_command
from ak5.cli.commands.subscribe import subscribe_command
from ak5.cli.commands.template import template_group
from ak5.cli.commands.ticket import (
    comment_shortcut,
    move_shortcut,
    ticket_group,
)
from ak5.cli.commands.up import up_command
from ak5.cli.commands.web import web_command
from ak5.cli.commands.whoami import whoami_command
from ak5.client.errors import ClientError
from ak5.client.output import handle_client_error

console = Console()


class AK5Group(click.Group):
    """Catch :class:`ClientError` at the CLI entry point (cli.md §3)."""

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except ClientError as exc:
            json_mode = None
            if isinstance(ctx.obj, dict) and "json" in ctx.obj:
                json_mode = bool(ctx.obj["json"])
            handle_client_error(exc, json_mode=json_mode)


@click.group(cls=AK5Group)
@click.version_option(package_name="ak5", prog_name="ak5")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Emit JSON only on stdout (also: AK5_OUTPUT=json).",
)
@click.option(
    "--profile",
    "profile_name",
    default=None,
    help="Daemon profile to use for this command (also: AK5_PROFILE).",
)
@click.pass_context
def cli(ctx: click.Context, json_output: bool, profile_name: str | None) -> None:
    """AK5 (Agent K5) - Agent-Orchestrated Kanban Command Line Interface.

    \b
    Local quick start (zero-config):
      ak5 up                                               # start Web UI + Daemon + default profiles
      ak5 demo                                             # watch live multi-agent workflow on board

    \b
    Agent happy path (pull model):
      ak5 login --agent worker-1 --role worker --caps ..   # or use `ak5 --profile worker ...` locally
      export AK5_PROFILE=worker-1
      # loop: ak5 next --wait 25 --json → ak5 ticket claim → work → ak5 ticket submit
      # or a wake hook: ak5 subscribe add --events WORK_AVAILABLE --exec '<curl or harness CLI>'

    Stuck? run `ak5 <command> --help`. Prefer this CLI over inventing relative REST URLs.
    """
    ctx.ensure_object(dict)
    env_json = os.environ.get("AK5_OUTPUT", "").strip().lower() == "json"
    ctx.obj["json"] = bool(json_output or env_json)
    if profile_name:
        prev_profile = os.environ.get("AK5_PROFILE")
        os.environ["AK5_PROFILE"] = profile_name

        def _restore_profile() -> None:
            if prev_profile is None:
                os.environ.pop("AK5_PROFILE", None)
            else:
                os.environ["AK5_PROFILE"] = prev_profile

        ctx.call_on_close(_restore_profile)


cli.add_command(up_command)
cli.add_command(serve_command)
cli.add_command(mcp_command)
cli.add_command(board_command)
cli.add_command(boards_command)
cli.add_command(create_board_command)
cli.add_command(claim_board_command)
cli.add_command(agents_command)
cli.add_command(ticket_group)
cli.add_command(next_command)
cli.add_command(delegate_command)
cli.add_command(move_shortcut)
cli.add_command(comment_shortcut)
cli.add_command(demo_command)
cli.add_command(login_command)
cli.add_command(logout_command)
cli.add_command(whoami_command)
cli.add_command(web_command)
cli.add_command(migrate_legacy_command)
cli.add_command(subscribe_command)
cli.add_command(events_command)
cli.add_command(watch_command)  # alias → events watch (human terminal only)
cli.add_command(daemon_command)
cli.add_command(profile_command)
cli.add_command(template_group)
cli.add_command(reset_command)
cli.add_command(db_group)


if __name__ == "__main__":
    cli()
