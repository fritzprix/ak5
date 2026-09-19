from contextlib import asynccontextmanager
import json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from ak5.config import settings
from ak5.database import AsyncSessionLocal, engine
from ak5.models.actor import Actor
from ak5.models.base import Base
from ak5.models.board import Board
from ak5.models.column import Column
from ak5.routers.actors import router as actors_router
from ak5.routers.auth import router as auth_router
from ak5.routers.boards import router as boards_router
from ak5.routers.events import router as events_router
from ak5.routers.tickets import router as tickets_router
from ak5.mcp.tools import server as mcp_server


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Seed initial entities
    await seed_initial_data()
    yield
    # Shutdown engine
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="AK5: Agent-Orchestrated Kanban System Gateway",
    lifespan=lifespan,
)

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API Routers
app.include_router(auth_router, prefix=settings.API_V1_STR)
app.include_router(actors_router, prefix=settings.API_V1_STR)
app.include_router(boards_router, prefix=settings.API_V1_STR)
app.include_router(tickets_router, prefix=settings.API_V1_STR)
app.include_router(events_router, prefix=settings.API_V1_STR)

# Mount Embedded MCP Bridge Endpoint (/mcp/sse, /mcp/messages)
app.mount("/mcp", mcp_server.sse_app())


@app.get("/health", tags=["system"])
async def health_check():
    return {"status": "ok", "project": settings.PROJECT_NAME, "version": settings.VERSION}


@app.get("/", tags=["system"])
async def root():
    return {
        "name": "AK5 Gateway API",
        "docs": "/docs",
        "version": settings.VERSION,
    }
