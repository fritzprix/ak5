from pathlib import Path

import pytest
from ak5.server.auth.sessions import CSRF_HEADER, SESSION_COOKIE
from ak5.web_auth import reset_rate_limits_for_tests
from httpx import AsyncClient


@pytest.fixture(autouse=True)
def _clear_web_auth_env(monkeypatch):
    for key in (
        "AK5_AUTH_PASSWORD",
        "AK5_AUTH_USERNAME",
        "AK5_WEB_PASSWORD",
        "AK5_WEB_USER",
    ):
        monkeypatch.delenv(key, raising=False)
    reset_rate_limits_for_tests()
    yield
    reset_rate_limits_for_tests()


@pytest.mark.asyncio
async def test_web_auth_disabled_status(client: AsyncClient):
    resp = await client.get("/api/auth/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authEnabled"] is False
    assert body["authenticated"] is True


@pytest.mark.asyncio
async def test_web_auth_login_and_gate(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("AK5_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "secret-pass")
    reset_rate_limits_for_tests()

    denied = await client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert denied.status_code in {303, 307}
    assert "/login" in denied.headers.get("location", "")

    bad = await client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert bad.status_code == 401

    ok = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "secret-pass"}
    )
    assert ok.status_code == 200
    assert ok.json().get("success") is True
    assert SESSION_COOKIE in ok.cookies
    cookie_val = ok.cookies.get(SESSION_COOKIE)
    assert cookie_val
    assert len(cookie_val) >= 32
    assert cookie_val != "secret-pass"

    status = await client.get("/api/auth/status")
    assert status.json()["authEnabled"] is True
    assert status.json()["authenticated"] is True
    assert status.json()["username"] == "admin"


@pytest.mark.asyncio
async def test_web_auth_brute_force_lockout(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "correct")
    reset_rate_limits_for_tests()

    for _ in range(5):
        resp = await client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
        assert resp.status_code == 401

    locked = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "correct"}
    )
    assert locked.status_code == 429
    assert "Retry-After" in locked.headers


@pytest.mark.asyncio
async def test_web_auth_login_rejects_wrong_length_without_500(
    client: AsyncClient, monkeypatch
):
    """Unequal credential lengths must be 401, never ValueError → 500."""
    monkeypatch.setenv("AK5_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "secret-pass")
    reset_rate_limits_for_tests()

    resp = await client.post(
        "/api/auth/login",
        json={"username": "a", "password": "x"},
    )
    assert resp.status_code == 401
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_web_auth_logout_revokes_session(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("AK5_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "secret-pass")
    reset_rate_limits_for_tests()

    ok = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secret-pass"},
    )
    assert ok.status_code == 200
    assert client.cookies.get(SESSION_COOKIE)
    csrf = ok.json()["csrf"]

    status_ok = await client.get("/api/auth/status")
    assert status_ok.json()["authenticated"] is True

    # Cookie-only CSRF must fail and keep the session (#56).
    cookie_only = await client.post("/api/auth/logout")
    assert cookie_only.status_code == 403
    still = await client.get("/api/auth/status")
    assert still.json()["authenticated"] is True

    logout = await client.post(
        "/api/auth/logout", headers={CSRF_HEADER: csrf}
    )
    assert logout.status_code == 200

    status_after = await client.get("/api/auth/status")
    assert status_after.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_oidc_logout_requires_csrf_header(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "local-secret")
    login = await client.post("/auth/local/login", json={"password": "local-secret"})
    assert login.status_code == 200
    csrf = login.json()["csrf"]

    denied = await client.post("/auth/logout")
    assert denied.status_code == 403
    me = await client.get("/auth/me")
    assert me.json()["authenticated"] is True

    ok = await client.post("/auth/logout", headers={CSRF_HEADER: csrf})
    assert ok.status_code == 200
    me2 = await client.get("/auth/me")
    assert me2.json()["authenticated"] is False


def test_no_csrf_cookie_fallback_in_server_source():
    """Regression lock: never treat CSRF cookie as the submitted token."""
    root = Path(__file__).resolve().parents[1] / "src" / "ak5" / "server"
    offenders: list[str] = []
    needle = "or request.cookies.get(CSRF_COOKIE)"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if needle in text:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_detect_tailscale_without_cli(monkeypatch):
    from ak5.cli.tailscale import detect_tailscale

    monkeypatch.setattr("ak5.cli.tailscale.shutil.which", lambda _: None)
    info = detect_tailscale()
    assert info.ipv4 is None
    assert info.dns_name is None


def test_detect_tailscale_parses_cli(monkeypatch):
    from ak5.cli.tailscale import detect_tailscale

    monkeypatch.setattr("ak5.cli.tailscale.shutil.which", lambda _: "/usr/bin/tailscale")

    def fake_run(args, **_kwargs):
        class Result:
            returncode = 0
            stdout = ""

        if args[:3] == ["tailscale", "ip", "-4"]:
            Result.stdout = "100.119.228.9\n"
            return Result()
        if args[:3] == ["tailscale", "status", "--json"]:
            Result.stdout = '{"Self":{"DNSName":"node.tailfd161b.ts.net."}}'
            return Result()
        return Result()

    monkeypatch.setattr("ak5.cli.tailscale.subprocess.run", fake_run)
    info = detect_tailscale()
    assert info.ipv4 == "100.119.228.9"
    assert info.dns_name == "node.tailfd161b.ts.net"
