from pathlib import Path

import click
from ak5.paths import check_legacy_cwd_db, get_app_data_dir, migrate_legacy_cwd_data
from rich.console import Console

console = Console()


@click.command("migrate-legacy")
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing global database if already present",
)
def migrate_legacy_command(force: bool) -> None:
    """Migrate local CWD ak5.db and attachments to permanent global app storage."""
    app_dir = get_app_data_dir()
    local_db = check_legacy_cwd_db()
    local_attach = Path.cwd() / "data" / "attachments"
    has_attachments = local_attach.is_dir() and any(local_attach.rglob("*"))

    if not local_db and not has_attachments:
        console.print(
            "[yellow]No local ./ak5.db or ./data/attachments found in current directory.[/yellow]"
        )
        raise SystemExit(1)

    console.print(
        f"[bold cyan]Migrating local CWD data to global app storage ({app_dir})...[/bold cyan]"
    )
    res = migrate_legacy_cwd_data(target_dir=app_dir, force=force, opt_in=True)

    db_migrated = bool(res.get("db_migrated"))
    copied_attachments = int(res.get("attachments_copied") or 0)
    skipped_reason = res.get("skipped_reason")

    if db_migrated:
        console.print(f"  [green]✔ Database migrated to {app_dir / 'ak5.db'}[/green]")
    elif local_db and skipped_reason:
        console.print(f"  [yellow]! Database not migrated:[/yellow] {skipped_reason}")
    elif local_db:
        console.print("  [dim]Database unchanged.[/dim]")

    if copied_attachments > 0:
        console.print(
            f"  [green]✔ Copied {copied_attachments} attachments to {app_dir / 'attachments'}[/green]"
        )
    elif has_attachments:
        console.print(
            "  [dim]No new attachments copied (target already had matching files).[/dim]"
        )

    if not db_migrated and local_db and skipped_reason and "already exists" in str(skipped_reason):
        console.print(
            "[bold yellow]Migration incomplete.[/bold yellow] "
            "Re-run with [cyan]ak5 migrate-legacy --force[/cyan] to overwrite the global DB."
        )
        raise SystemExit(2)

    if db_migrated or copied_attachments > 0:
        console.print("[bold green]Migration complete.[/bold green]")
    else:
        console.print("[bold]Nothing to migrate.[/bold]")
