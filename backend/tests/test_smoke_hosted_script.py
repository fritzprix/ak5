"""``scripts/smoke_hosted.py``: hosted smoke checklist (#179 section C)."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from smoke_hosted import first_chunk_path, main, run_checks  # noqa: E402

BASE = "https://ak5.example"
DASHBOARD = (
    '<!DOCTYPE html><html><head><script src="/_next/static/chunks/webpack-abc123.js" async=""></script>'
    "</head><body></body></html>"
)
HTML = {"content-type": "text/html; charset=utf-8"}
JS = {"content-type": "text/javascript; charset=utf-8"}


def _healthy_routes() -> dict[str, httpx.Response]:
    return {
        "/readyz": httpx.Response(200, json={"status": "ready"}),
        "/meta": httpx.Response(200, json={"api_version": "2", "min_client": "2.0.0"}),
        "/": httpx.Response(200, text=DASHBOARD, headers=HTML),
        "/_next/static/chunks/webpack-abc123.js": httpx.Response(200, text="self.x=1", headers=JS),
    }


def _transport(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(request.url.path, httpx.Response(404, text="Not Found"))

    return httpx.MockTransport(handler)


def _by_name(routes: dict[str, httpx.Response]) -> dict[str, bool]:
    return {r.name: r.ok for r in run_checks(BASE, transport=_transport(routes))}


def test_first_chunk_path() -> None:
    assert first_chunk_path(DASHBOARD) == "/_next/static/chunks/webpack-abc123.js"
    assert first_chunk_path("<html><body>index only</body></html>") is None


def test_healthy_deployment_passes_every_check() -> None:
    assert _by_name(_healthy_routes()) == {"readyz": True, "meta": True, "dashboard": True, "next_chunk": True}


def _drop(path: str) -> Callable[[dict[str, httpx.Response]], None]:
    return lambda routes: routes.pop(path)


def _set(path: str, resp: httpx.Response) -> Callable[[dict[str, httpx.Response]], None]:
    return lambda routes: routes.__setitem__(path, resp)


@pytest.mark.parametrize(
    ("mutate", "failing"),
    [
        pytest.param(_drop("/_next/static/chunks/webpack-abc123.js"), {"next_chunk"}, id="chunk-404"),
        pytest.param(
            _set("/_next/static/chunks/webpack-abc123.js", httpx.Response(200, text=DASHBOARD, headers=HTML)),
            {"next_chunk"},
            id="chunk-spa-fallback-html",
        ),
        pytest.param(
            _set("/", httpx.Response(200, text="<html>index only</html>", headers=HTML)),
            {"next_chunk"},
            id="no-chunk-in-html",
        ),
        pytest.param(
            _set("/readyz", httpx.Response(503, json={"status": "not_ready", "failed": ["blob"]})),
            {"readyz"},
            id="blob-not-ready",
        ),
        pytest.param(_set("/readyz", httpx.Response(200, json=[])), {"readyz"}, id="readyz-not-object"),
        pytest.param(_set("/meta", httpx.Response(200, json={"api_version": "1"})), {"meta"}, id="wrong-api"),
        pytest.param(
            _set("/", httpx.Response(200, json={"detail": "api only"})),
            {"dashboard", "next_chunk"},
            id="no-dashboard",
        ),
    ],
)
def test_broken_deployment_fails_the_matching_checks(
    mutate: Callable[[dict[str, httpx.Response]], None], failing: set[str]
) -> None:
    routes = _healthy_routes()
    mutate(routes)
    results = _by_name(routes)
    assert {name for name, ok in results.items() if not ok} == failing


def test_connection_error_is_reported_not_raised(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["http://127.0.0.1:9"])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert out.count("FAIL") >= 3
