import pytest
from ak5.main import app
from ak5.web_auth import (
    COOKIE_NAME,
    reset_rate_limits_for_tests,
)
from httpx import ASGITransport, AsyncClient


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
async def test_web_auth_disabled_status():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/auth/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authEnabled"] is False
    assert body["authenticated"] is True


@pytest.mark.asyncio
async def test_web_auth_login_and_gate(monkeypatch):
    monkeypatch.setenv("AK5_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "secret-pass")
    reset_rate_limits_for_tests()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        denied = await client.get("/", headers={"Accept": "text/html"})
        assert denied.status_code in {303, 307}
        assert "/login" in denied.headers.get("location", "")

        bad = await client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert bad.status_code == 401

        ok = await client.post("/api/auth/login", json={"username": "admin", "password": "secret-pass"})
        assert ok.status_code == 200
        assert ok.json().get("success") is True
        assert COOKIE_NAME in ok.cookies
        cookie_val = ok.cookies.get(COOKIE_NAME)
        assert cookie_val
        assert len(cookie_val) >= 32
        # Must NOT be a deterministic password digest
        assert cookie_val != "secret-pass"

        status = await client.get("/api/auth/status")
        assert status.json()["authEnabled"] is True
        assert status.json()["authenticated"] is True
        assert status.json()["username"] == "admin"


@pytest.mark.asyncio
async def test_web_auth_brute_force_lockout(monkeypatch):
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "correct")
    reset_rate_limits_for_tests()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(5):
            resp = await client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
            assert resp.status_code == 401

        locked = await client.post("/api/auth/login", json={"username": "admin", "password": "correct"})
        assert locked.status_code == 429
        assert "Retry-After" in locked.headers


@pytest.mark.asyncio
async def test_web_auth_login_rejects_wrong_length_without_500(monkeypatch):
    """Unequal credential lengths must be 401, never ValueError → 500."""
    monkeypatch.setenv("AK5_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "secret-pass")
    reset_rate_limits_for_tests()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login",
            json={"username": "a", "password": "x"},
        )
        assert resp.status_code == 401
        assert "error" in resp.json()


@pytest.mark.asyncio
async def test_web_auth_logout_revokes_session(monkeypatch):
    monkeypatch.setenv("AK5_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "secret-pass")
    reset_rate_limits_for_tests()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "secret-pass"},
        )
        assert ok.status_code == 200
        assert client.cookies.get(COOKIE_NAME)

        status_ok = await client.get("/api/auth/status")
        assert status_ok.json()["authenticated"] is True

        logout = await client.post("/api/auth/logout")
        assert logout.status_code == 200

        status_after = await client.get("/api/auth/status")
        # Cookie may still be sent empty/cleared; session store must reject it.
        assert status_after.json()["authenticated"] is False


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
