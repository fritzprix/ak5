"""Locate and serve the packaged Next.js static dashboard (`ak5/web_ui`)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


def web_ui_root() -> Path:
    return Path(__file__).resolve().parent / "web_ui"


def web_ui_available() -> bool:
    return (web_ui_root() / "index.html").is_file()


def _safe_join(root: Path, relative: str) -> Path | None:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def mount_web_ui(app: FastAPI) -> bool:
    """Serve exported Next.js assets and SPA fallbacks. Returns True if mounted."""
    root = web_ui_root()
    if not (root / "index.html").is_file():
        return False

    next_dir = root / "_next"
    if next_dir.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/_next", StaticFiles(directory=str(next_dir)), name="next_static")

    @app.get("/", include_in_schema=False)
    async def web_index() -> FileResponse:
        return FileResponse(root / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_or_static(full_path: str) -> FileResponse:
        # API / docs / health / mcp are registered earlier and win the match.
        target = _safe_join(root, full_path)
        if target is not None and target.is_file():
            return FileResponse(target)

        if target is not None:
            as_index = target / "index.html"
            if as_index.is_file():
                return FileResponse(as_index)

        if full_path.startswith("board/") or full_path == "board":
            for candidate in (
                root / "board" / "_" / "index.html",
                root / "board" / "proj-core-engine" / "index.html",
            ):
                if candidate.is_file():
                    return FileResponse(candidate)

        if full_path.startswith("login"):
            login_index = root / "login" / "index.html"
            if login_index.is_file():
                return FileResponse(login_index)

        index = root / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(status_code=404, detail="Web UI asset not found")

    return True
