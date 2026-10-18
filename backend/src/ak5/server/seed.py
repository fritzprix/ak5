import json
import logging
import shutil
import uuid
from contextlib import suppress
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ak5.cli.config import clear_session, find_project_root
from ak5.contracts.stages import STANDARD_STAGES
from ak5.paths import get_app_data_dir
from ak5.server.config import settings
from ak5.server.database import AsyncSessionLocal
from ak5.server.inbox import INBOX_BOARD_ID, is_inbox_board_id
from ak5.server.models.actor import Actor
from ak5.server.models.audit import AuditLog
from ak5.server.models.board import Board
from ak5.server.models.board_member import BoardMember
from ak5.server.models.column import Column
from ak5.server.models.device_code import DeviceCode
from ak5.server.models.event import Event
from ak5.server.models.idempotency import IdempotencyKey
from ak5.server.models.subscription import Subscription
from ak5.server.models.ticket import Ticket, TicketAttachment, TicketComment
from ak5.server.models.ticket_postmortem import TicketPostmortem
from ak5.server.models.ticket_review import TicketReview
from ak5.server.models.ticket_revision import TicketRevision
from ak5.server.models.workspace import (
    LOCAL_WORKSPACE_ID,
    Agent,
    Membership,
    User,
    Workspace,
)
from ak5.server.services.templates import DEFAULT_TEMPLATE, INBOX_TEMPLATE, get_template

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_ACTORS = {"user_pm", "agent_image_worker", "agent_code_reviewer", "system"}
SYSTEM_ACTOR_ID = "system"
LOCAL_USER_ID = "usr_local_pm"
LOCAL_AGENT_WORKER_ID = "agt_image_worker"
LOCAL_AGENT_REVIEWER_ID = "agt_code_reviewer"

_CLAIMLESS_IN_PROGRESS_REASON = (
    "v2 migration: ticket was in in_progress without a valid claim; "
    "moved to backlog to restore invariant 2 (in_progress ⇔ valid claim)."
)


async def backfill_standard_board_stages(db: AsyncSession) -> None:
    """Ensure every standard board has at least one column per STANDARD_STAGE.

    Existing v1 boards only have 4 columns; workflow.md §2.2 requires each
    stage to have ≥1 column. Missing stages are created from the default
    template (name/position/wip_limit); existing columns are re-positioned
    so template stages keep their template positions and extras trail after.
    """
    tmpl = DEFAULT_TEMPLATE
    tmpl_by_stage = {c.stage: c for c in tmpl.columns}
    required = {s.value for s in STANDARD_STAGES}

    boards = (
        await db.execute(select(Board).where(Board.board_id != INBOX_BOARD_ID))
    ).scalars().all()

    for board in boards:
        if board.kind == "inbox":
            continue
        # Legacy / null kind is treated as standard.
        if board.kind not in ("standard", None, ""):
            continue
        if not board.template:
            board.template = tmpl.name

        cols = (
            await db.execute(select(Column).where(Column.board_id == board.board_id))
        ).scalars().all()
        by_stage = {c.stage: c for c in cols}
        present = set(by_stage)

        for stage in required - present:
            spec = tmpl_by_stage[stage]
            col_id = f"{board.board_id}_{stage}"
            # Avoid colliding with a pre-existing id that somehow has another stage.
            existing = await db.get(Column, col_id)
            if existing is not None:
                col_id = f"{board.board_id}_{stage}_{uuid.uuid4().hex[:6]}"
            db.add(
                Column(
                    column_id=col_id,
                    board_id=board.board_id,
                    name=spec.name,
                    stage=spec.stage,
                    position=spec.position,
                    wip_limit=spec.wip_limit,
                )
            )

        await db.flush()

        # Re-read and normalize positions: template stages first, extras after.
        cols = (
            await db.execute(select(Column).where(Column.board_id == board.board_id))
        ).scalars().all()
        max_tmpl_pos = max(c.position for c in tmpl.columns)
        extras = [c for c in cols if c.stage not in tmpl_by_stage]
        extras.sort(key=lambda c: (c.position, c.column_id))
        for col in cols:
            spec = tmpl_by_stage.get(col.stage)
            if spec is not None:
                col.position = spec.position
        for offset, col in enumerate(extras, start=1):
            col.position = max_tmpl_pos + offset


async def repair_claimless_in_progress_tickets(db: AsyncSession) -> None:
    """Move claimless in_progress tickets to backlog (invariant 2).

    v1 had no claims; tickets sitting in in_progress violate
    ``in_progress ⇔ valid claim``. Relocate to the board's backlog column,
    leave an audit row + comment (``status`` is derived from the new stage).
    """
    tickets = (
        await db.execute(
            select(Ticket)
            .options(selectinload(Ticket.column))
            .join(Column, Ticket.column_id == Column.column_id)
            .where(
                Column.stage == "in_progress",
                Ticket.claim_token.is_(None),
            )
        )
    ).scalars().all()
    if not tickets:
        return

    backlog_by_board: dict[str, Column] = {}
    for ticket in tickets:
        backlog = backlog_by_board.get(ticket.board_id)
        if backlog is None:
            backlog = (
                await db.execute(
                    select(Column)
                    .where(
                        Column.board_id == ticket.board_id,
                        Column.stage == "backlog",
                    )
                    .order_by(Column.position.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if backlog is None:
                continue
            backlog_by_board[ticket.board_id] = backlog

        old_column_id = ticket.column_id
        ticket.column_id = backlog.column_id
        ticket.column = backlog

        db.add(
            AuditLog(
                actor_id="user_pm",
                action="MOVED",
                target_type="ticket",
                target_id=ticket.ticket_id,
                payload=json.dumps(
                    {
                        "reason": "v2_claimless_in_progress_repair",
                        "from_column_id": old_column_id,
                        "to_column_id": backlog.column_id,
                        "message": _CLAIMLESS_IN_PROGRESS_REASON,
                    }
                ),
            )
        )
        db.add(
            TicketComment(
                comment_id=f"cmt_{uuid.uuid4().hex[:12]}",
                ticket_id=ticket.ticket_id,
                actor_id="user_pm",
                content=_CLAIMLESS_IN_PROGRESS_REASON,
                is_internal=True,
            )
        )


_AGENT_WORKFLOW_ROLES = frozenset({"triage", "worker", "gatekeeper"})


def _agent_workflow_role(actor: Actor) -> str:
    raw = (actor.role or "").strip().lower()
    if raw in _AGENT_WORKFLOW_ROLES:
        return raw
    if "review" in raw or "gate" in raw:
        return "gatekeeper"
    if "triage" in raw:
        return "triage"
    return "worker"


async def migrate_v1_actors_to_principals(db: AsyncSession) -> None:
    """Map every v1 Actor into users/agents + bridge (identity.md §9)."""
    from ak5.server.auth.bridge import principal_id_for_v1_actor, register_bridge

    actors = list((await db.execute(select(Actor))).scalars().all())
    for actor in actors:
        if actor.actor_id in {"system", SYSTEM_ACTOR_ID}:
            continue
        # Skip principal-id alias rows (identity.md §9) so re-running migrate
        # does not overwrite Principal→legacy bridge mappings.
        if actor.actor_id.startswith(("usr_", "agt_")):
            continue
        if actor.actor_type == "human":
            pid = principal_id_for_v1_actor(actor_id=actor.actor_id, actor_type="human")
            register_bridge(pid, actor.actor_id)
            if await db.get(User, pid) is None:
                db.add(
                    User(
                        user_id=pid,
                        email=None,
                        display_name=actor.name or actor.actor_id,
                    )
                )
            mem_id = f"mem_{LOCAL_WORKSPACE_ID}_{pid}"
            if await db.get(Membership, mem_id) is None:
                role = "owner" if pid == LOCAL_USER_ID else "member"
                db.add(
                    Membership(
                        membership_id=mem_id,
                        workspace_id=LOCAL_WORKSPACE_ID,
                        user_id=pid,
                        role=role,
                    )
                )
        elif actor.actor_type == "agent":
            pid = principal_id_for_v1_actor(actor_id=actor.actor_id, actor_type="agent")
            register_bridge(pid, actor.actor_id)
            if await db.get(Agent, pid) is None:
                db.add(
                    Agent(
                        agent_id=pid,
                        workspace_id=LOCAL_WORKSPACE_ID,
                        name=actor.name or actor.actor_id,
                        workflow_role=_agent_workflow_role(actor),
                        capabilities=actor.capabilities or "[]",
                        board_scope="[]",
                        created_by=LOCAL_USER_ID,
                    )
                )
    await db.flush()
    await ensure_principal_actor_aliases(db)
    await rewrite_actor_refs_to_principals(db)


async def ensure_principal_actor_aliases(db: AsyncSession) -> None:
    """Create Actor rows keyed by principal_id so FKs can store usr_/agt_ ids."""
    from ak5.server.auth.bridge import PRINCIPAL_TO_ACTOR

    for principal_id, actor_id in list(PRINCIPAL_TO_ACTOR.items()):
        if principal_id == actor_id:
            continue
        if await db.get(Actor, principal_id) is not None:
            continue
        src = await db.get(Actor, actor_id)
        if src is not None:
            db.add(
                Actor(
                    actor_id=principal_id,
                    actor_type=src.actor_type,
                    name=src.name,
                    role=src.role,
                    description=src.description,
                    capabilities=src.capabilities or "[]",
                    status=src.status,
                    avatar_url=src.avatar_url,
                )
            )
            continue
        user = await db.get(User, principal_id)
        if user is not None:
            db.add(
                Actor(
                    actor_id=principal_id,
                    actor_type="human",
                    name=user.display_name or principal_id,
                    role="human",
                    capabilities="[]",
                    status="idle",
                )
            )
            continue
        agent = await db.get(Agent, principal_id)
        if agent is not None:
            db.add(
                Actor(
                    actor_id=principal_id,
                    actor_type="agent",
                    name=agent.name or principal_id,
                    role=agent.workflow_role,
                    capabilities=agent.capabilities or "[]",
                    status="idle",
                )
            )
    await db.flush()


async def rewrite_actor_refs_to_principals(db: AsyncSession) -> None:
    """Rewrite ticket/audit/board actor FK columns to principal_id (identity.md §9)."""
    from ak5.server.auth.bridge import ACTOR_TO_PRINCIPAL

    mapping = dict(ACTOR_TO_PRINCIPAL)
    if not mapping:
        return

    # board_members: unique(board_id, actor_id) — drop legacy row if alias already present
    members = list((await db.execute(select(BoardMember))).scalars().all())
    existing = {(m.board_id, m.actor_id) for m in members}
    for member in members:
        new_id = mapping.get(member.actor_id)
        if not new_id or new_id == member.actor_id:
            continue
        if (member.board_id, new_id) in existing:
            await db.delete(member)
            continue
        existing.discard((member.board_id, member.actor_id))
        member.actor_id = new_id
        existing.add((member.board_id, new_id))

    orm_updates: list[tuple[type, tuple[str, ...]]] = [
        (Ticket, ("created_by", "assigned_to", "claimed_by", "submitted_by", "blocked_actor")),
        (Board, ("created_by",)),
        (AuditLog, ("actor_id",)),
        (TicketComment, ("actor_id",)),
        (TicketAttachment, ("actor_id",)),
        (DeviceCode, ("actor_id",)),
        (Subscription, ("created_by",)),
        (Event, ("actor_id",)),
        (TicketRevision, ("author",)),
        (TicketReview, ("reviewer",)),
        (TicketPostmortem, ("author",)),
    ]
    # Core UPDATE per (column, old id): no row loads, and columns with ``onupdate``
    # (``updated_at``) are pinned to their current value so a reference rewrite
    # does not look like a user edit (#79).
    for model, columns in orm_updates:
        table = model.__table__
        preserve = {c.name: c for c in table.columns if c.onupdate is not None}
        for col in columns:
            for old_id, new_id in mapping.items():
                if old_id == new_id:
                    continue
                await db.execute(
                    update(table).where(table.c[col] == old_id).values({col: new_id, **preserve})
                )

    await db.flush()
    # Core UPDATEs bypass the identity map; drop cached rows so later seed steps reload.
    db.expire_all()


async def ensure_local_tenancy(db: AsyncSession) -> None:
    """Create ``ws_local`` workspace + mapped user/agents (identity.md §9 / #23)."""
    ws = await db.get(Workspace, LOCAL_WORKSPACE_ID)
    if ws is None:
        db.add(Workspace(workspace_id=LOCAL_WORKSPACE_ID, name="Local", policy="{}"))

    user = await db.get(User, LOCAL_USER_ID)
    if user is None:
        db.add(
            User(
                user_id=LOCAL_USER_ID,
                email="local-pm@localhost",
                display_name="Project Manager (David)",
            )
        )
    membership_id = f"mem_{LOCAL_WORKSPACE_ID}_{LOCAL_USER_ID}"
    if await db.get(Membership, membership_id) is None:
        db.add(
            Membership(
                membership_id=membership_id,
                workspace_id=LOCAL_WORKSPACE_ID,
                user_id=LOCAL_USER_ID,
                role="owner",
            )
        )

    for agent_id, name, workflow_role, caps in (
        (
            LOCAL_AGENT_WORKER_ID,
            "Image Worker Agent",
            "worker",
            ["image-resize", "webp", "ocr", "thumbnail"],
        ),
        (
            LOCAL_AGENT_REVIEWER_ID,
            "Code Reviewer Agent",
            "gatekeeper",
            ["code-review", "python", "rust", "security", "testing"],
        ),
    ):
        if await db.get(Agent, agent_id) is None:
            db.add(
                Agent(
                    agent_id=agent_id,
                    workspace_id=LOCAL_WORKSPACE_ID,
                    name=name,
                    workflow_role=workflow_role,
                    capabilities=json.dumps(caps),
                    board_scope="[]",
                    created_by=LOCAL_USER_ID,
                )
            )
    await db.flush()
    # Register seed bridges then migrate any remaining v1 actors.
    from ak5.server.auth.bridge import register_bridge

    register_bridge(LOCAL_USER_ID, "user_pm")
    register_bridge(LOCAL_AGENT_WORKER_ID, "agent_image_worker")
    register_bridge(LOCAL_AGENT_REVIEWER_ID, "agent_code_reviewer")
    await migrate_v1_actors_to_principals(db)


async def warn_inbox_like_standard_boards(db: AsyncSession) -> list[str]:
    """Log non-inbox boards whose id looks like an inbox (``*__inbox``, #82 review).

    ``is_inbox_board_id`` treats that suffix as an inbox; creation now rejects
    it, but boards made before #82 could carry it and would be misclassified.
    """
    rows = (await db.execute(select(Board.board_id, Board.kind))).all()
    suspicious = [bid for bid, kind in rows if kind != "inbox" and is_inbox_board_id(bid)]
    for bid in suspicious:
        logger.warning(
            "Board %r is not an inbox but its id ends with '__inbox'; workflow code will "
            "treat it as an inbox. Rename it (identity.md §7).",
            bid,
        )
    return suspicious


async def seed_initial_data() -> None:
    """Seed initial actors, global inbox, and default board if not present."""
    async with AsyncSessionLocal() as db:
        await ensure_local_tenancy(db)

        # Check if default PM actor exists
        pm_actor = await db.get(Actor, "user_pm")
        if not pm_actor:
            pm_actor = Actor(
                actor_id="user_pm",
                actor_type="human",
                name="Project Manager (David)",
                role="PM",
                description="Lead Project Manager and System Orchestrator",
                capabilities=json.dumps(["project-management", "planning", "review"]),
                status="idle",
            )
            db.add(pm_actor)

        # Check if sample image worker agent exists
        img_agent = await db.get(Actor, "agent_image_worker")
        if not img_agent:
            img_agent = Actor(
                actor_id="agent_image_worker",
                actor_type="agent",
                name="Image Worker Agent",
                role="Media Specialist",
                description="Specialized in image processing, resizing, format conversion (WebP, PNG), and OCR extraction",
                capabilities=json.dumps(["image-resize", "webp", "ocr", "thumbnail"]),
                status="idle",
            )
            db.add(img_agent)

        # Check if sample code reviewer agent exists
        reviewer_agent = await db.get(Actor, "agent_code_reviewer")
        if not reviewer_agent:
            reviewer_agent = Actor(
                actor_id="agent_code_reviewer",
                actor_type="agent",
                name="Code Review Agent",
                role="Senior Reviewer",
                description="Automated code review, security audits, test verification, and lint enforcement",
                capabilities=json.dumps(["code-review", "python", "rust", "security", "testing"]),
                status="idle",
            )
            db.add(reviewer_agent)

        # System actor for lease reclaim / automated audit (not a workflow role).
        # Seed bypasses reserved-id API guards; actor_id "system" is intentional.
        system_actor = await db.get(Actor, SYSTEM_ACTOR_ID)
        if not system_actor:
            system_actor = Actor(
                actor_id=SYSTEM_ACTOR_ID,
                actor_type="agent",
                name="System",
                role="system",
                description="Automated system actions (lease reclaim, repair)",
                capabilities=json.dumps([]),
                status="idle",
            )
            db.add(system_actor)

        # Ensure actors are flushed so FK from boards.created_by succeeds
        await db.flush()

        # Global inbox board (workflow.md §2.1) — system board, immutable
        inbox = await db.get(Board, INBOX_BOARD_ID)
        if not inbox:
            inbox = Board(
                board_id=INBOX_BOARD_ID,
                workspace_id=LOCAL_WORKSPACE_ID,
                name="Global Inbox",
                description="System inbox for unclassified work (triage)",
                created_by="user_pm",
                kind="inbox",
                template=INBOX_TEMPLATE.name,
                auto_created=False,
            )
            db.add(inbox)
            for col in INBOX_TEMPLATE.columns:
                db.add(
                    Column(
                        column_id=f"{INBOX_BOARD_ID}_{col.stage}",
                        board_id=INBOX_BOARD_ID,
                        name=col.name,
                        stage=col.stage,
                        position=col.position,
                        wip_limit=col.wip_limit,
                    )
                )
            for actor_id in DEFAULT_SYSTEM_ACTORS:
                db.add(
                    BoardMember(
                        board_id=INBOX_BOARD_ID,
                        actor_id=actor_id,
                        role="admin" if actor_id == "user_pm" else "agent",
                    )
                )
        elif inbox.kind != "inbox":
            inbox.kind = "inbox"
            inbox.template = inbox.template or INBOX_TEMPLATE.name

        # Default project board with default template columns
        board = await db.get(Board, settings.DEFAULT_BOARD_ID)
        if not board:
            tmpl = DEFAULT_TEMPLATE
            board = Board(
                board_id=settings.DEFAULT_BOARD_ID,
                workspace_id=LOCAL_WORKSPACE_ID,
                name=settings.DEFAULT_BOARD_NAME,
                description="Default Kanban board for autonomous agent orchestration",
                created_by="user_pm",
                kind="standard",
                template=tmpl.name,
                auto_created=False,
            )
            db.add(board)

            for col in tmpl.columns:
                db.add(
                    Column(
                        column_id=f"{settings.DEFAULT_BOARD_ID}_{col.stage}",
                        board_id=settings.DEFAULT_BOARD_ID,
                        name=col.name,
                        stage=col.stage,
                        position=col.position,
                        wip_limit=col.wip_limit,
                    )
                )

        # Seed default board members
        for actor_id in DEFAULT_SYSTEM_ACTORS:
            stmt = select(BoardMember).where(
                BoardMember.board_id == settings.DEFAULT_BOARD_ID,
                BoardMember.actor_id == actor_id,
            )
            res = await db.execute(stmt)
            if not res.scalar_one_or_none():
                db.add(
                    BoardMember(
                        board_id=settings.DEFAULT_BOARD_ID,
                        actor_id=actor_id,
                        role="admin" if actor_id == "user_pm" else "agent",
                    )
                )

        # v1 → v2 data repairs (idempotent): missing stages + claimless tickets
        await backfill_standard_board_stages(db)
        await warn_inbox_like_standard_boards(db)
        await repair_claimless_in_progress_tickets(db)
        from ak5.server.services.workflow import clear_assignees_without_claim

        await clear_assignees_without_claim(db)

        # §9: principal-id Actor aliases + rewrite FK refs (idempotent).
        await ensure_principal_actor_aliases(db)
        await rewrite_actor_refs_to_principals(db)

        await db.commit()


async def reset_kanban_data(keep_boards: bool = False, clear_sessions: bool = True) -> dict[str, Any]:
    """Purge tickets, custom identities, and optionally boards, restoring default seed state."""
    counts: dict[str, int] = {
        "tickets": 0,
        "comments": 0,
        "attachments": 0,
        "subscriptions": 0,
        "custom_actors": 0,
        "boards": 0,
    }

    from ak5.server.database import engine
    from ak5.server.models.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        # Temporarily disable foreign keys during bulk purge
        await db.execute(text("PRAGMA foreign_keys = OFF;"))

        # Workflow history tables
        await db.execute(delete(TicketPostmortem))
        await db.execute(delete(TicketReview))
        await db.execute(delete(TicketRevision))
        await db.execute(delete(Event))
        await db.execute(delete(IdempotencyKey))

        # 1. Delete attachments records
        res_att = await db.execute(delete(TicketAttachment))
        counts["attachments"] = res_att.rowcount or 0

        # 2. Delete comments
        res_cmt = await db.execute(delete(TicketComment))
        counts["comments"] = res_cmt.rowcount or 0

        # 3. Delete tickets
        res_tk = await db.execute(delete(Ticket))
        counts["tickets"] = res_tk.rowcount or 0

        # 4. Delete subscriptions
        res_sub = await db.execute(delete(Subscription))
        counts["subscriptions"] = res_sub.rowcount or 0

        # 5. Delete audit logs
        await db.execute(delete(AuditLog))

        # 6. Delete boards, board_members, device_codes, and columns
        await db.execute(delete(DeviceCode))
        if not keep_boards:
            await db.execute(delete(BoardMember))
            await db.execute(delete(Column))
            res_bd = await db.execute(delete(Board))
            counts["boards"] = res_bd.rowcount or 0
        else:
            # Delete members of custom actors
            await db.execute(
                delete(BoardMember).where(BoardMember.actor_id.not_in(DEFAULT_SYSTEM_ACTORS))
            )
            # Reassign any boards created by custom actors to default user_pm
            await db.execute(
                update(Board)
                .where(Board.created_by.not_in(DEFAULT_SYSTEM_ACTORS))
                .values(created_by="user_pm")
            )

        # 7. Delete custom actors (keep user_pm, agent_image_worker, agent_code_reviewer)
        res_act = await db.execute(delete(Actor).where(Actor.actor_id.not_in(DEFAULT_SYSTEM_ACTORS)))
        counts["custom_actors"] = res_act.rowcount or 0

        await db.execute(text("PRAGMA foreign_keys = ON;"))
        await db.commit()

    # Re-seed default PM, default agents, inbox, and default board
    await seed_initial_data()

    # 8. Clean attachment files from disk
    att_dir = get_app_data_dir() / "attachments"
    if att_dir.is_dir():
        for f in att_dir.iterdir():
            if f.is_file():
                with suppress(OSError):
                    f.unlink()

    # 9. Clean local project sessions and identity claims if requested
    if clear_sessions:
        try:
            project_root = find_project_root()
            clear_session(all_actors=True, project_root=project_root)
            ak5_dir = project_root / ".ak5"
            if ak5_dir.is_dir():
                for sub in ("sessions", "identity"):
                    sdir = ak5_dir / sub
                    if sdir.is_dir():
                        shutil.rmtree(sdir, ignore_errors=True)
                for p in ak5_dir.glob("*.json"):
                    with suppress(OSError):
                        p.unlink()
        except Exception:
            pass

    return counts


# Re-export for boards router / tests
__all__ = [
    "DEFAULT_SYSTEM_ACTORS",
    "INBOX_BOARD_ID",
    "SYSTEM_ACTOR_ID",
    "get_template",
    "reset_kanban_data",
    "seed_initial_data",
]
