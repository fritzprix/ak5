import click
from rich.console import Console
from ak5_cli.commands.agents import agents_command
from ak5_cli.commands.board import board_command
from ak5_cli.commands.delegate import delegate_command
from ak5_cli.commands.demo import demo_command
from ak5_cli.commands.login import login_command

console = Console()


@click.group()
@click.version_option(version="1.0.0", prog_name="ak5")
def cli():
    """AK5 (Agent K5) - Agent-Orchestrated Kanban Command Line Interface."""
    pass


cli.add_command(login_command)
cli.add_command(agents_command)
cli.add_command(delegate_command)
cli.add_command(board_command)
cli.add_command(demo_command)


if __name__ == "__main__":
    cli()
