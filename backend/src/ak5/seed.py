import json
import shutil
from contextlib import suppress
from typing import Any

from sqlalchemy import delete, text, update

from ak5.cli.config import clear_session, find_project_root
from ak5.config import settings
from ak5.database import AsyncSessionLocal
from ak5.models.actor import Actor
from ak5.models.audit import AuditLog
from ak5.models.board import Board
from ak5.models.column import Column
from ak5.models.subscription import Subscription
from ak5.models.ticket import Ticket, TicketAttachment, TicketComment
from ak5.paths import get_app_data_dir

DEFAULT_SYSTEM_ACTORS = {"user_pm", "agent_image_worker", "agent_code_reviewer"}


async def seed_initial_data() -> None:
    """Seed initial actors and default board if not present."""
    async with AsyncSessionLocal() as db:
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

        # Check if default board exists
        board = await db.get(Board, settings.DEFAULT_BOARD_ID)
        if not board:
            board = Board(
                board_id=settings.DEFAULT_BOARD_ID,
                name=settings.DEFAULT_BOARD_NAME,
                description="Default Kanban board for autonomous agent orchestration",
                created_by="user_pm",
            )
            db.add(board)

            # Default columns
            default_cols = [
                {"id": f"{settings.DEFAULT_BOARD_ID}_todo", "name": "To Do", "stage": "open", "pos": 1, "wip": 0},
                {"id": f"{settings.DEFAULT_BOARD_ID}_in_progress", "name": "In Progress", "stage": "in_progress", "pos": 2, "wip": 3},
                {"id": f"{settings.DEFAULT_BOARD_ID}_review", "name": "Review", "stage": "review", "pos": 3, "wip": 3},
                {"id": f"{settings.DEFAULT_BOARD_ID}_done", "name": "Done", "stage": "done", "pos": 4, "wip": 0},
            ]
            for c in default_cols:
                col = Column(
                    column_id=c["id"],
                    board_id=settings.DEFAULT_BOARD_ID,
                    name=c["name"],
                    stage=c["stage"],
                    position=c["pos"],
                    wip_limit=c["wip"],
                )
                db.add(col)

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

    async with AsyncSessionLocal() as db:
        # Temporarily disable foreign keys during bulk purge
        await db.execute(text("PRAGMA foreign_keys = OFF;"))

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

        # 6. Delete boards and columns if not keep_boards
        if not keep_boards:
            await db.execute(delete(Column))
            res_bd = await db.execute(delete(Board))
            counts["boards"] = res_bd.rowcount or 0
        else:
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

    # Re-seed default PM, default agents, and default board
    await seed_initial_data()

    # 8. Clean attachment files from disk
    att_dir = get_app_data_dir() / "attachments"
    if att_dir.is_dir():
        for f in att_dir.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass

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
