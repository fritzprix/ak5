import json
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
from ak5.server.services.ticket_status import derive_status

DEFAULT_SYSTEM_ACTORS = {"user_pm", "agent_image_worker", "agent_code_reviewer", "system"}
INBOX_BOARD_ID = "inbox"
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
    leave an audit row + comment, and recompute ``status`` via derive_status.
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
        # Refresh stage for derive_status without another join.
        ticket.column = backlog
        ticket.status = derive_status(ticket)

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
        await repair_claimless_in_progress_tickets(db)
        # Recompute derived status for migrated tickets (orchestrator note on #10).
        from ak5.server.services.workflow import (
            clear_assignees_without_claim,
            recompute_all_ticket_statuses,
        )

        await recompute_all_ticket_statuses(db)
        await clear_assignees_without_claim(db)

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
