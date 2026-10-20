"""``ak5 up`` — zero-config local launcher for layman users."""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime

import click
from ak5.cli.commands.serve import (
    _load_dotenv_files,
    _prepare_database,
    is_loopback_bind,
    run_serve,
)
from ak5.daemon.credentials import ProfileCredential, load_credential, save_credential
from ak5.daemon.profiles import (
    Profile,
    load_profiles_file,
    set_default_profile,
    upsert_profile,
)
from rich.console import Console
from rich.panel import Panel

console = Console()

DEFAULT_LOCAL_AGENT_SPECS: tuple[tuple[str, str, str, list[str]], ...] = (
    ("triage", "Local Triage", "triage", ["triage", "routing", "planning"]),
    (
        "worker",
        "Local Worker",
        "worker",
        ["python", "typescript", "general", "webp", "image-resize"],
    ),
    (
        "gatekeeper",
        "Local Gatekeeper",
        "gatekeeper",
        ["review", "qa", "security", "code-review"],
    ),
)


async def bootstrap_local_profiles(api_url: str) -> dict[str, str]:
    """Initialize local DB, seed data, and provision ready-to-use CLI profiles.

    Provisions ``me`` (human owner) and ``triage`` / ``worker`` / ``gatekeeper``
    (workspace agents) in ``~/.ak5/profiles.toml`` and ``~/.ak5/credentials/``
    so local CLI commands work out-of-the-box without manual ``ak5 login``.
    """
    from ak5.cli.server_extra import require_server_extra

    require_server_extra()

    from ak5.contracts.stages import Role
    from ak5.server.auth.principal import Principal
    from ak5.server.auth.sessions import create_cli_session_tokens
    from ak5.server.auth.tokens import issue_access_token, session_token_ttl
    from ak5.server.auth.users import ensure_local_owner_user
    from ak5.server.config import settings
    from ak5.server.database import ensure_database_schema, open_admin_session
    from ak5.server.models.actor import Actor
    from ak5.server.models.board import Board
    from ak5.server.models.board_member import BoardMember
    from ak5.server.models.workspace import (
        LOCAL_WORKSPACE_ID,
        Agent,
        AgentCredential,
        UserSession,
    )
    from ak5.server.seed import ensure_local_tenancy, seed_initial_data
    from ak5.server.services.agents import (
        create_agent_with_credential,
        issue_credential,
    )
    from sqlalchemy import select

    await ensure_database_schema()
    await seed_initial_data()

    provisioned: dict[str, str] = {}
    prior_default = load_profiles_file().get("default_profile") or ""

    async with open_admin_session() as db:
        await ensure_local_tenancy(db)
        user = await ensure_local_owner_user(db)

        if await db.get(Actor, user.user_id) is None:
            db.add(
                Actor(
                    actor_id=user.user_id,
                    actor_type="human",
                    name=user.display_name or "Local Owner",
                    role=Role.HUMAN.value,
                    capabilities="[]",
                    status="idle",
                )
            )
            await db.flush()

        # 1. Human owner profile ("me") — reuse valid CLI refresh token if present
        existing_me = load_credential("me")
        refresh_token = ""
        if (
            existing_me is not None
            and existing_me.status == "active"
            and existing_me.refresh_token
        ):
            refresh_hash = hashlib.sha256(
                existing_me.refresh_token.encode("utf-8")
            ).hexdigest()
            now_naive = datetime.now(UTC).replace(tzinfo=None)
            valid_session = (
                await db.execute(
                    select(UserSession).where(
                        UserSession.user_id == user.user_id,
                        UserSession.client == "cli",
                        UserSession.refresh_token_hash == refresh_hash,
                        UserSession.revoked_at.is_(None),
                        UserSession.expires_at > now_naive,
                    )
                )
            ).scalar_one_or_none()
            if valid_session is not None:
                refresh_token = existing_me.refresh_token

        if not refresh_token:
            refresh_token, _session_id = await create_cli_session_tokens(
                db, user.user_id
            )
        ttl = session_token_ttl()
        access_token = issue_access_token(
            Principal(
                principal_id=user.user_id,
                principal_type="user",
                workspace_id=LOCAL_WORKSPACE_ID,
                workflow_role=Role.HUMAN,
                membership_role="owner",
            ),
            ttl=ttl,
        )
        expires_at = (datetime.now(UTC) + ttl).isoformat()
        save_credential(
            "me",
            ProfileCredential(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                actor_id=user.user_id,
                actor_type="user",
                status="active",
            ),
        )
        upsert_profile(
            Profile(
                name="me",
                server=api_url,
                workspace=LOCAL_WORKSPACE_ID,
                principal="user",
                role=Role.HUMAN.value,
                actor_id=user.user_id,
            )
        )
        if not prior_default or prior_default == "me":
            set_default_profile("me")
        provisioned["me"] = user.user_id

        # 2. Default agent profiles ("triage", "worker", "gatekeeper")
        for profile_name, display_name, role, caps in DEFAULT_LOCAL_AGENT_SPECS:
            existing_cred = load_credential(profile_name)
            reused_agent: Agent | None = None
            if (
                existing_cred is not None
                and existing_cred.status == "active"
                and existing_cred.access_token.startswith("ak5_agt_")
            ):
                token_hash = hashlib.sha256(
                    existing_cred.access_token.encode("utf-8")
                ).hexdigest()
                cred_row = (
                    await db.execute(
                        select(AgentCredential).where(
                            AgentCredential.token_hash == token_hash,
                            AgentCredential.revoked_at.is_(None),
                        )
                    )
                ).scalar_one_or_none()
                if cred_row is not None:
                    candidate = await db.get(Agent, cred_row.agent_id)
                    if (
                        candidate is not None
                        and candidate.workspace_id == LOCAL_WORKSPACE_ID
                        and candidate.disabled_at is None
                        and candidate.workflow_role == role
                    ):
                        reused_agent = candidate

            if reused_agent is not None:
                assert existing_cred is not None
                agent = reused_agent
                plaintext = existing_cred.access_token
            else:
                agent = (
                    await db.execute(
                        select(Agent)
                        .where(
                            Agent.workspace_id == LOCAL_WORKSPACE_ID,
                            Agent.name == display_name,
                            Agent.workflow_role == role,
                            Agent.disabled_at.is_(None),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if agent is not None:
                    _cred_row, plaintext = await issue_credential(
                        db,
                        workspace_id=LOCAL_WORKSPACE_ID,
                        agent_id=agent.agent_id,
                    )
                else:
                    agent, _cred_row, plaintext = await create_agent_with_credential(
                        db,
                        workspace_id=LOCAL_WORKSPACE_ID,
                        name=display_name,
                        workflow_role=role,
                        created_by=user.user_id,
                        capabilities=caps,
                    )
                save_credential(
                    profile_name,
                    ProfileCredential(
                        access_token=plaintext,
                        actor_id=agent.agent_id,
                        actor_type="agent",
                        status="active",
                    ),
                )

            upsert_profile(
                Profile(
                    name=profile_name,
                    server=api_url,
                    workspace=LOCAL_WORKSPACE_ID,
                    principal="agent",
                    role=role,
                    actor_id=agent.agent_id,
                )
            )
            provisioned[profile_name] = agent.agent_id

        # 3. Ensure default board membership so agents appear in the board UI header
        default_board = await db.get(Board, settings.DEFAULT_BOARD_ID)
        if default_board is not None:
            for profile_name, actor_id in provisioned.items():
                member_role = "admin" if profile_name == "me" else profile_name
                exists = (
                    await db.execute(
                        select(BoardMember).where(
                            BoardMember.board_id == default_board.board_id,
                            BoardMember.actor_id == actor_id,
                        )
                    )
                ).scalar_one_or_none()
                if exists is None:
                    db.add(
                        BoardMember(
                            board_id=default_board.board_id,
                            workspace_id=default_board.workspace_id,
                            actor_id=actor_id,
                            role=member_role,
                        )
                    )

        await db.commit()

    return provisioned


def _print_quickstart_banner(host: str, port: int, provisioned: dict[str, str]) -> None:
    url = f"http://127.0.0.1:{port}"
    profiles_summary = ", ".join(
        f"[cyan]{name}[/cyan]" + (" (default)" if name == "me" else "")
        for name in ("me", "triage", "worker", "gatekeeper")
        if name in provisioned
    )
    lines = [
        f"[bold green]✓ Local environment ready![/bold green]  Dashboard: [bold cyan]{url}[/bold cyan]",
        f"[bold]CLI Profiles:[/bold]       {profiles_summary}",
        "",
        "[bold]Try these in another terminal:[/bold]",
        "  [cyan]ak5 demo[/cyan]                                    [dim]# Watch live multi-agent workflow on the board[/dim]",
        "  [cyan]ak5 board[/cyan]                                   [dim]# View Kanban board in terminal[/dim]",
        "  [cyan]ak5 ticket create \"Add dark mode\"[/cyan]           [dim]# Drop a request into Inbox[/dim]",
        "  [cyan]ak5 --profile worker next --wait 25[/cyan]         [dim]# Poll for work as Worker agent[/dim]",
    ]
    console.print(
        Panel(
            "\n".join(lines),
            title="[bold cyan]AK5 Local Quick Start (ak5 up)[/bold cyan]",
            border_style="cyan",
        )
    )


@click.command("up")
@click.option("--host", default="127.0.0.1", show_default=True, help="Loopback host to bind")
@click.option("--port", default=8000, show_default=True, type=int, help="Port to bind")
@click.option("--no-browser", is_flag=True, help="Do not open a browser tab on startup")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
def up_command(
    host: str,
    port: int,
    no_browser: bool,
    reload: bool,
) -> None:
    """Start AK5 locally with zero configuration (Web UI + Daemon + Auto-Login).

    Automatically initializes the local database, provisions ready-to-use CLI
    profiles (`me`, `triage`, `worker`, `gatekeeper`), starts the client daemon,
    and opens the Web Dashboard signed in as the local owner.
    """
    from ak5.cli.server_extra import require_server_extra

    require_server_extra()
    if not is_loopback_bind(host):
        console.print(
            f"[bold red]✗ 'ak5 up' is for local loopback only (got {host}).[/bold red]\n"
            "  For network or Tailscale binds, use [cyan]ak5 serve[/cyan]."
        )
        raise SystemExit(1)

    _load_dotenv_files()
    _prepare_database()

    api_url = f"http://127.0.0.1:{port}/api/v1"
    provisioned = asyncio.run(bootstrap_local_profiles(api_url))
    os.environ["AK5_LOCAL_AUTO_LOGIN"] = "1"
    _print_quickstart_banner(host, port, provisioned)

    run_serve(
        host=host,
        port=port,
        ui=True,
        daemon=True,
        tailscale_serve=False,
        ssl_keyfile=None,
        ssl_certfile=None,
        reload=reload,
        insecure=False,
        no_browser=no_browser,
        dev_insecure_identify=True,
    )
