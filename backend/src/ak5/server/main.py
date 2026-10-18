import logging
import os
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.contracts.version import API_VERSION, MIN_CLIENT_VERSION
from ak5.server.auth.oidc_config import oidc_providers_configured
from ak5.server.auth.sessions import SESSION_COOKIE, resolve_web_session
from ak5.server.auth.users import local_owner_configured
from ak5.server.config import settings
from ak5.server.database import AsyncSessionLocal, engine, run_sqlite_schema_migrations
from ak5.server.models.base import Base
from ak5.server.routers.actors import router as actors_router
from ak5.server.routers.auth import router as auth_router
from ak5.server.routers.boards import router as boards_router
from ak5.server.routers.device_auth import router as device_auth_router
from ak5.server.routers.events import router as events_router
from ak5.server.routers.oauth import router as oauth_router
from ak5.server.routers.oidc_auth import router as oidc_auth_router
from ak5.server.routers.subscriptions import router as subscriptions_router
from ak5.server.routers.templates import router as templates_router
from ak5.server.routers.tickets import router as tickets_router
from ak5.server.routers.web_auth import router as web_auth_router
from ak5.server.routers.workspaces import router as workspaces_router
from ak5.server.seed import seed_initial_data
from ak5.server.services.workflow import WorkflowError
from ak5.server.web_auth import get_web_auth_config
from ak5.server.web_ui_static import mount_web_ui, web_ui_available

logger = logging.getLogger(__name__)

# CORS: local + Tailscale CGNAT / MagicDNS only (not open wildcard)
TAILSCALE_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1|100\.\d{1,3}\.\d{1,3}\.\d{1,3}|.*\.ts\.net)(:\d+)?"

_AUTH_PUBLIC_PREFIXES = (
    "/api/auth",
    "/auth",
    "/oauth",
    "/device",
    "/api/v1",
    "/health",
    "/meta",
    "/mcp",
    "/_next",
    "/login",
)

# OpenAPI / Swagger — public only when ``AK5_PUBLIC_DOCS`` is set (#64).
_DOCS_PUBLIC_PREFIXES = (
    "/docs",
    "/redoc",
    "/openapi.json",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One source of truth before create_all/seed — covers raw `uvicorn ak5.server.app:app`
    # (Docker/harness) which never passes through `ak5 serve` / `ak5 web` CLI gates.
    # Daemon is never started here (events.md §4.5 / cli.md §4.1): use `ak5 serve --daemon`.
    try:
        from ak5.server.services.db_safety import prepare_database_for_use, snapshot_live_database

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
    # Server no longer starts a background exec listener (events.md §0 / #11).
    yield
    # Shutdown engine
    await engine.dispose()


def _web_gate_enabled() -> bool:
    """Dashboard gate on when local password, OIDC, or legacy web auth is configured."""
    return (
        get_web_auth_config().enabled
        or local_owner_configured()
        or oidc_providers_configured()
    )


def _public_docs_enabled() -> bool:
    """When true, ``/docs`` · ``/redoc`` · ``/openapi.json`` stay unauthenticated (#64)."""
    return os.environ.get("AK5_PUBLIC_DOCS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _is_public_path(path: str) -> bool:
    if path == "/" and not _web_gate_enabled():
        return True
    for prefix in _AUTH_PUBLIC_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    if _public_docs_enabled():
        for prefix in _DOCS_PUBLIC_PREFIXES:
            if path == prefix or path.startswith(prefix + "/"):
                return True
    # Static asset extensions always public (JS/CSS needed on login page)
    lowered = path.lower()
    for ext in (".js", ".css", ".map", ".ico", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".woff", ".woff2", ".ttf"):
        if lowered.endswith(ext):
            return True
    return False


async def _request_has_web_session(request: Request) -> bool:
    """Accept DB-backed ``ak5_session`` cookies (identity.md §3.2)."""
    session_cookie = request.cookies.get(SESSION_COOKIE)
    if not session_cookie:
        return False
    # Tests set ``app.state.session_factory`` to the in-memory test engine.
    factory = getattr(request.app.state, "session_factory", None) or AsyncSessionLocal
    async with factory() as db:
        return await resolve_web_session(db, session_cookie) is not None


def create_app() -> FastAPI:
    """ASGI application factory (cli.md §1). Prefer this for remote/deploy entrypoints."""
    application = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description="AK5: Agent-Orchestrated Kanban System Gateway",
        lifespan=lifespan,
    )

    @application.exception_handler(WorkflowError)
    async def workflow_error_handler(_request: Request, exc: WorkflowError) -> JSONResponse:
        """Map workflow state-machine errors to the contracts error envelope."""
        return JSONResponse(
            status_code=HTTP_STATUS[exc.code],
            content=error_body(exc.code, exc.message, details=exc.details),
        )

    @application.exception_handler(HTTPException)
    async def http_exception_envelope_handler(
        _request: Request, exc: HTTPException
    ) -> JSONResponse:
        """Pass through contracts error envelopes raised as ``HTTPException.detail``."""
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.detail,
                headers=dict(exc.headers) if exc.headers else None,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=dict(exc.headers) if exc.headers else None,
        )

    @application.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Map FastAPI/Pydantic 422 into the contracts error envelope (cli.md §3)."""
        # jsonable_encoder: pydantic validators may put Exception in ctx.error
        errors: list[Any] = jsonable_encoder(list(exc.errors()))
        return JSONResponse(
            status_code=HTTP_STATUS[ErrorCode.PAYLOAD_INVALID],
            content=error_body(
                ErrorCode.PAYLOAD_INVALID,
                "Request validation failed",
                details={"errors": errors},
            ),
        )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_origin_regex=TAILSCALE_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API Routers
    application.include_router(auth_router, prefix=settings.API_V1_STR)
    application.include_router(device_auth_router, prefix=settings.API_V1_STR)
    application.include_router(device_auth_router)  # Direct access without /api/v1 prefix
    application.include_router(actors_router, prefix=settings.API_V1_STR)
    application.include_router(boards_router, prefix=settings.API_V1_STR)
    application.include_router(templates_router, prefix=settings.API_V1_STR)
    application.include_router(tickets_router, prefix=settings.API_V1_STR)
    application.include_router(events_router, prefix=settings.API_V1_STR)
    application.include_router(subscriptions_router, prefix=settings.API_V1_STR)
    application.include_router(workspaces_router, prefix=settings.API_V1_STR)
    application.include_router(web_auth_router)
    application.include_router(oidc_auth_router)
    application.include_router(oauth_router)

    # Optional MCP bridge: requires ak5[mcp] + AK5_ENABLE_MCP=1 (client.md §7)
    if os.environ.get("AK5_ENABLE_MCP", "").strip().lower() in ("1", "true", "yes", "on"):
        try:
            from ak5.mcp.tools import server as mcp_server

            application.mount("/mcp", mcp_server.sse_app())
            logger.info("Mounted /mcp (AK5_ENABLE_MCP=1)")
        except ImportError:
            logger.warning(
                "AK5_ENABLE_MCP=1 but mcp extra is not installed; skip /mcp mount. "
                "Install with: pip install 'ak5[mcp]'"
            )

    @application.get("/health", tags=["system"])
    async def health_check():
        return {"status": "ok", "project": settings.PROJECT_NAME, "version": settings.VERSION}

    @application.get("/meta", tags=["system"])
    async def meta():
        """API version negotiation (cli.md §2). No authentication required."""
        return {
            "api_version": API_VERSION,
            "min_client": MIN_CLIENT_VERSION,
            "capabilities": ["long_poll"],
        }

    @application.middleware("http")
    async def web_dashboard_auth_gate(request: Request, call_next):
        if not _web_gate_enabled():
            return await call_next(request)

        path = request.url.path
        if _is_public_path(path):
            return await call_next(request)

        if await _request_has_web_session(request):
            return await call_next(request)

        accept = request.headers.get("accept", "")
        wants_html = "text/html" in accept or path == "/" or path.startswith("/board")
        if wants_html and request.method in {"GET", "HEAD"}:
            dest = path
            if request.url.query:
                dest = f"{dest}?{request.url.query}"
            return RedirectResponse(url=f"/login?from={quote(dest, safe='')}", status_code=303)

        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    has_web_ui = mount_web_ui(application)

    if not has_web_ui:

        @application.get("/", tags=["system"])
        async def root():
            return {
                "name": "AK5 Gateway API",
                "docs": "/docs",
                "version": settings.VERSION,
                "web_ui": False,
                "hint": "Run `scripts/build_web_ui.sh` then reinstall, or use `ak5 serve --ui`.",
            }
    else:

        @application.get("/api/system/web-ui", tags=["system"], include_in_schema=False)
        async def web_ui_status():
            return {"web_ui": True, "available": web_ui_available()}

    return application


# Module-level ASGI app for `uvicorn ak5.server.app:app` (shim: `ak5.main:app`).
app = create_app()
