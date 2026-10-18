import asyncio
from typing import Any

import httpx
import pytest
from ak5.cli.commands import board as board_cmd
from ak5.cli.main import cli
from ak5.client.errors import EXIT_AUTH, ClientError
from ak5.contracts.errors import ErrorCode
from click.testing import CliRunner


def _sample_board(board_id: str = "proj-watch") -> dict[str, Any]:
    return {
        "board_id": board_id,
        "name": "Watch Test Board",
        "columns": [
            {
                "name": "Todo",
                "stage": "open",
                "tickets": [],
                "total_ticket_count": 0,
                "wip_limit": 0,
            }
        ],
    }


def test_board_watch_unauthenticated_exits_8(monkeypatch: pytest.MonkeyPatch):
    """When no credentials exist, ak5 board <id> --watch must exit with 8 (cli.md §3.1)."""
    monkeypatch.setattr(board_cmd, "get_auth_headers", lambda _url: {})

    runner = CliRunner()
    result = runner.invoke(cli, ["board", "proj-watch", "--watch"])
    assert result.exit_code == EXIT_AUTH


def test_board_watch_server_401_raises_exit_8(monkeypatch: pytest.MonkeyPatch):
    """When server returns 401 on /events/stream, exit 8."""
    monkeypatch.setattr(board_cmd, "get_auth_headers", lambda _url: {"Authorization": "Bearer bad-token"})
    monkeypatch.setattr(board_cmd, "fetch_board", lambda *a, **kw: _sample_board())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Unauthorized"})

    transport = httpx.MockTransport(handler)

    with pytest.raises(ClientError) as exc_info:
        asyncio.run(
            board_cmd.watch_board_live(
                "http://testserver",
                "proj-watch",
                transport=transport,
            )
        )

    assert exc_info.value.exit_code == EXIT_AUTH
    assert exc_info.value.code == ErrorCode.UNAUTHENTICATED


def test_board_watch_server_403_and_404(monkeypatch: pytest.MonkeyPatch):
    """When server returns 403 or 404, exit with 4."""
    monkeypatch.setattr(board_cmd, "get_auth_headers", lambda _url: {"Authorization": "Bearer tok"})
    monkeypatch.setattr(board_cmd, "fetch_board", lambda *a, **kw: _sample_board())

    transport_403 = httpx.MockTransport(lambda req: httpx.Response(403))
    with pytest.raises(ClientError) as exc_403:
        asyncio.run(
            board_cmd.watch_board_live("http://testserver", "proj-watch", transport=transport_403)
        )
    assert exc_403.value.exit_code == 4

    transport_404 = httpx.MockTransport(lambda req: httpx.Response(404))
    with pytest.raises(ClientError) as exc_404:
        asyncio.run(
            board_cmd.watch_board_live("http://testserver", "proj-watch", transport=transport_404)
        )
    assert exc_404.value.exit_code == 4


def test_board_watch_sends_bearer_and_board_id_param(monkeypatch: pytest.MonkeyPatch):
    """Verify stream is opened with Bearer token and ?board_id=<id>."""
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        # return empty SSE stream
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b": ping\n\n",
        )

    monkeypatch.setattr(board_cmd, "get_auth_headers", lambda _url: {"Authorization": "Bearer test-jwt-123"})
    monkeypatch.setattr(board_cmd, "fetch_board", lambda *a, **kw: _sample_board("proj-alpha"))

    transport = httpx.MockTransport(handler)
    asyncio.run(
        board_cmd.watch_board_live(
            "http://testserver",
            "proj-alpha",
            transport=transport,
        )
    )

    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.url.path == "/events/stream"
    assert req.url.query.decode() == "board_id=proj-alpha"
    assert req.headers["authorization"] == "Bearer test-jwt-123"


def test_board_watch_refetches_on_ticket_moved(monkeypatch: pytest.MonkeyPatch):
    """Verify that receiving a TICKET_MOVED event triggers fetch_board."""
    fetch_calls: list[str] = []

    def mock_fetch(api_url: str, board_id: str, **kwargs):
        fetch_calls.append(board_id)
        return _sample_board(board_id)

    monkeypatch.setattr(board_cmd, "get_auth_headers", lambda _url: {"Authorization": "Bearer test-token"})
    monkeypatch.setattr(board_cmd, "fetch_board", mock_fetch)

    sse_payload = (
        b"event: CONNECTED\r\ndata: {}\r\n\r\n"
        b"event: TICKET_MOVED\r\ndata: {\"ticket_id\": \"TK-1\"}\r\n\r\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse_payload,
        )

    transport = httpx.MockTransport(handler)
    asyncio.run(
        board_cmd.watch_board_live(
            "http://testserver",
            "proj-target",
            transport=transport,
        )
    )

    # First fetch before opening stream, second fetch on TICKET_MOVED
    assert len(fetch_calls) == 2
    assert fetch_calls == ["proj-target", "proj-target"]
