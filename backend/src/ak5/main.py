import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from ak5.config import settings
from ak5.database import engine, run_sqlite_schema_migrations
from ak5.mcp.tools import server as mcp_server
from ak5.models.base import Base
from ak5.routers.actors import router as actors_router
from ak5.routers.auth import router as auth_router
from ak5.routers.boards import router as boards_router
from ak5.routers.device_auth import router as device_auth_router
from ak5.routers.events import router as events_router
from ak5.routers.subscriptions import router as subscriptions_router
from ak5.routers.tickets import router as tickets_router
from ak5.routers.web_auth import router as web_auth_router
from ak5.seed import seed_initial_data
from ak5.services.subscription_service import subscription_service
from ak5.web_auth import COOKIE_NAME, get_web_auth_config, verify_session_cookie
from ak5.web_ui_static import mount_web_ui, web_ui_available

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One source of truth before create_all/seed — covers raw `uvicorn ak5.main:app`
    # (Docker/harness) which never passes through `ak5 serve` / `ak5 web` CLI gates.
    try:
        from ak5.services.db_safety import prepare_database_for_use, snapshot_live_database

        prepare_database_for_use(settings.DATABASE_URL)
    except Exception:
        logger.exception("Database safety preflight failed")
        raise

    # Initialize DB tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await run_sqlite_schema_migrations()
    # Seed initial entities (never deletes existing boards/tickets)
    await seed_initial_data()
    # Keep a rolling backup whenever the live DB has real ticket data.
    try:
        snapshot_live_database(reason="startup")
    except Exception:
        logger.exception("Failed to snapshot live database on startup")
    # Start background event subscriber service
    await subscription_service.start()
    yield
    # Stop background event subscriber service
    await subscription_service.stop()
    # Shutdown engine
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="AK5: Agent-Orchestrated Kanban System Gateway",
    lifespan=lifespan,
)

# CORS: local + Tailscale CGNAT / MagicDNS only (not open wildcard)
TAILSCALE_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1|100\.\d{1,3}\.\d{1,3}\.\d{1,3}|.*\.ts\.net)(:\d+)?"

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=TAILSCALE_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API Routers
app.include_router(auth_router, prefix=settings.API_V1_STR)
app.include_router(device_auth_router, prefix=settings.API_V1_STR)
app.include_router(device_auth_router)  # Direct access without /api/v1 prefix
app.include_router(actors_router, prefix=settings.API_V1_STR)
app.include_router(boards_router, prefix=settings.API_V1_STR)
app.include_router(tickets_router, prefix=settings.API_V1_STR)
app.include_router(events_router, prefix=settings.API_V1_STR)
app.include_router(subscriptions_router, prefix=settings.API_V1_STR)
app.include_router(web_auth_router)


# Mount Embedded MCP Bridge Endpoint (/mcp/sse, /mcp/messages)
app.mount("/mcp", mcp_server.sse_app())


@app.get("/health", tags=["system"])
async def health_check():
    return {"status": "ok", "project": settings.PROJECT_NAME, "version": settings.VERSION}


_AUTH_PUBLIC_PREFIXES = (
    "/api/auth",
    "/api/v1",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
    "/mcp",
    "/_next",
    "/login",
)


def _is_public_path(path: str) -> bool:
    if path == "/" and not get_web_auth_config().enabled:
        return True
    for prefix in _AUTH_PUBLIC_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    # Static asset extensions always public (JS/CSS needed on login page)
    lowered = path.lower()
    for ext in (".js", ".css", ".map", ".ico", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".woff", ".woff2", ".ttf"):
        if lowered.endswith(ext):
            return True
    return False


@app.middleware("http")
async def web_dashboard_auth_gate(request: Request, call_next):
    cfg = get_web_auth_config()
    if not cfg.enabled:
        return await call_next(request)

    path = request.url.path
    if _is_public_path(path):
        return await call_next(request)

    cookie = request.cookies.get(COOKIE_NAME)
    if verify_session_cookie(cookie):
        return await call_next(request)

    accept = request.headers.get("accept", "")
    wants_html = "text/html" in accept or path == "/" or path.startswith("/board")
    if wants_html and request.method in {"GET", "HEAD"}:
        dest = path
        if request.url.query:
            dest = f"{dest}?{request.url.query}"
        return RedirectResponse(url=f"/login?from={quote(dest, safe='')}", status_code=303)

    return JSONResponse({"error": "Unauthorized"}, status_code=401)


_HAS_WEB_UI = mount_web_ui(app)

if not _HAS_WEB_UI:

    @app.get("/", tags=["system"])
    async def root():
        return {
            "name": "AK5 Gateway API",
            "docs": "/docs",
            "version": settings.VERSION,
            "web_ui": False,
            "hint": "Run `scripts/build_web_ui.sh` then reinstall, or use `ak5 web` after a release build.",
        }
else:

    @app.get("/api/system/web-ui", tags=["system"], include_in_schema=False)
    async def web_ui_status():
        return {"web_ui": True, "available": web_ui_available()}
