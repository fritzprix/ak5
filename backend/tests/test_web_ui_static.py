from __future__ import annotations

from pathlib import Path

import pytest
from ak5.server import web_ui_static
from ak5.server.web_ui_static import (
    STATIC_ASSET_EXTENSIONS,
    mount_web_ui,
    web_ui_assets_built,
    web_ui_available,
)
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _build_test_app(ui_dir: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(web_ui_static, "web_ui_root", lambda: ui_dir)
    app = FastAPI()
    mounted = mount_web_ui(app)
    assert mounted is True
    return app


def test_web_ui_availability_and_assets_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web_ui_static, "web_ui_root", lambda: tmp_path)
    assert web_ui_available() is False
    assert web_ui_assets_built() is False

    # index.html alone (git checkout state before scripts/build_web_ui.sh)
    (tmp_path / "index.html").write_text("<html>root</html>", encoding="utf-8")
    assert web_ui_available() is True
    assert web_ui_assets_built() is False

    # Full static export with _next/ directory
    (tmp_path / "_next").mkdir()
    assert web_ui_available() is True
    assert web_ui_assets_built() is True


def test_mount_web_ui_returns_false_when_index_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web_ui_static, "web_ui_root", lambda: tmp_path)
    app = FastAPI()
    assert mount_web_ui(app) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_path",
    [
        "/_next",
        "/_next/static/chunks/webpack-missing.js",
        "/_next/static/css/missing.css",
        "/_next/data/build-id/index.json",
        "/static/bundle.js",
        "/static/module.mjs",
        "/styles/app.css",
        "/chunk.js.map",
        "/favicon.ico",
        "/logo.svg",
        "/banner.png",
        "/photo.jpg",
        "/photo.jpeg",
        "/hero.webp",
        "/font.woff",
        "/font.woff2",
        "/font.ttf",
        "/board/_/index.txt",
        "/manifest.json",
        "/site.webmanifest",
        "/UPPERCASE/CHUNK.JS",
    ],
)
async def test_missing_static_assets_never_fallback_to_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_path: str
) -> None:
    """Missing JS/CSS/font/image/RSC assets must return 404, never 200 index.html."""
    (tmp_path / "index.html").write_text("<!DOCTYPE html><html>root</html>", encoding="utf-8")
    app = _build_test_app(tmp_path, monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(missing_path)

    assert resp.status_code == 404
    assert "text/html" not in resp.headers.get("content-type", "")
    assert resp.json() == {"detail": "Web UI asset not found"}


@pytest.mark.asyncio
async def test_existing_static_assets_and_spa_fallbacks_served_properly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "index.html").write_text("<!DOCTYPE html><html>root-index</html>", encoding="utf-8")
    (tmp_path / "favicon.ico").write_bytes(b"ico-bytes")

    chunks = tmp_path / "_next" / "static" / "chunks"
    chunks.mkdir(parents=True)
    (chunks / "app.js").write_text("console.log('ok');", encoding="utf-8")

    board_placeholder = tmp_path / "board" / "_"
    board_placeholder.mkdir(parents=True)
    (board_placeholder / "index.html").write_text(
        "<!DOCTYPE html><html>board-shell</html>", encoding="utf-8"
    )

    login_dir = tmp_path / "login"
    login_dir.mkdir(parents=True)
    (login_dir / "index.html").write_text(
        "<!DOCTYPE html><html>login-shell</html>", encoding="utf-8"
    )

    app = _build_test_app(tmp_path, monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Existing mounted /_next chunk returns JS
        js_resp = await ac.get("/_next/static/chunks/app.js")
        assert js_resp.status_code == 200
        assert "javascript" in js_resp.headers.get("content-type", "")
        assert js_resp.text == "console.log('ok');"

        # 2. Missing chunk under mounted /_next returns 404 (not HTML)
        missing_next = await ac.get("/_next/static/chunks/does-not-exist.js")
        assert missing_next.status_code == 404
        assert "<!DOCTYPE html>" not in missing_next.text

        # 3. Existing top-level asset is served directly
        ico_resp = await ac.get("/favicon.ico")
        assert ico_resp.status_code == 200
        assert ico_resp.content == b"ico-bytes"

        # 4. SPA routes fall back to their exported HTML shells
        root_resp = await ac.get("/")
        assert root_resp.status_code == 200
        assert "root-index" in root_resp.text

        board_resp = await ac.get("/board/inbox")
        assert board_resp.status_code == 200
        assert "board-shell" in board_resp.text

        login_resp = await ac.get("/login")
        assert login_resp.status_code == 200
        assert "login-shell" in login_resp.text

        unknown_spa = await ac.get("/custom-client-route")
        assert unknown_spa.status_code == 200
        assert "root-index" in unknown_spa.text


def test_static_asset_extensions_AllLowerAndDotPrefixed() -> None:
    for ext in STATIC_ASSET_EXTENSIONS:
        assert ext.startswith(".")
        assert ext == ext.lower()
