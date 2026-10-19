"""#113: request ids, structured logs, never-log list, healthz/readyz (operations.md §4)."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest
from ak5.client.errors import ClientError
from ak5.client.output import print_error
from ak5.config import settings
from ak5.server import migrations as mig
from ak5.server import observability as obs
from ak5.server.models.audit import AuditLog
from helpers_workflow import board_ticket
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def clean_logging():
    """Remove the handler configure_logging() installs, whatever the test did."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_ak5_handler", False):
            root.removeHandler(handler)
    root.setLevel(logging.WARNING)


# --- request id -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_always_carries_a_request_id(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.headers["x-request-id"].startswith("req_")
    other = await client.get("/health")
    assert other.headers["x-request-id"] != resp.headers["x-request-id"]


@pytest.mark.asyncio
async def test_safe_caller_request_id_is_kept_and_unsafe_ones_are_replaced(client: AsyncClient):
    kept = await client.get("/health", headers={"X-Request-ID": "trace-abc_123-XYZ"})
    assert kept.headers["x-request-id"] == "trace-abc_123-XYZ"

    for bad in ("short", "x" * 65, "has space in it", "semi;colon;1234", "ünïcode-id-1234"):
        try:
            resp = await client.get("/health", headers={"X-Request-ID": bad})
        except UnicodeEncodeError:  # httpx refuses to even send non-ASCII header values
            continue
        assert resp.headers["x-request-id"].startswith("req_"), bad
        assert resp.headers["x-request-id"] != bad


def test_accept_request_id_unit():
    assert obs.accept_request_id("abcdefgh") == "abcdefgh"
    assert obs.accept_request_id(None).startswith("req_")
    assert obs.accept_request_id("bad id!").startswith("req_")
    assert obs.accept_request_id("a" * 8) == "a" * 8
    assert obs.accept_request_id("a" * 7).startswith("req_")


# --- access log ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_access_log_has_the_fixed_fields_and_templated_path(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
):
    created = await client.post("/api/v1/tickets", json=board_ticket("Logged ticket"))
    tid = created.json()["ticket_id"]
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="ak5.access"):
        resp = await client.get(
            f"/api/v1/tickets/{tid}", params={"note": "do-not-log-me"}, headers={"X-Request-ID": "trace-0001-abcd"}
        )
    assert resp.status_code == 200
    records = [r for r in caplog.records if r.name == "ak5.access"]
    assert len(records) == 1
    rec = records[0]
    assert rec.method == "GET"
    assert rec.path == "/api/v1/tickets/{ticket_id}"  # the template, not the raw id
    assert rec.status == 200
    assert rec.duration_ms >= 0
    assert rec.principal_id and rec.workspace_id
    assert rec.instance_id
    text = json.dumps({k: str(v) for k, v in vars(rec).items()})
    assert tid not in text and "do-not-log-me" not in text


@pytest.mark.asyncio
async def test_health_probes_are_not_access_logged(client: AsyncClient, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.INFO, logger="ak5.access"):
        for path in ("/health", "/healthz", "/readyz"):
            await client.get(path)
    assert [r for r in caplog.records if r.name == "ak5.access"] == []


# --- structured logs ------------------------------------------------------------------------


def _json_lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_json_format_emits_one_object_per_line_with_request_id(
    monkeypatch: pytest.MonkeyPatch, clean_logging, capsys
):
    monkeypatch.setenv("AK5_LOG_FORMAT", "json")
    obs.configure_logging()
    token = obs.request_id_var.set("trace-json-0001")
    try:
        logging.getLogger("ak5.test").info("hello %s", "world")
    finally:
        obs.request_id_var.reset(token)
    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert line["msg"] == "hello world"
    assert line["level"] == "INFO"
    assert line["logger"] == "ak5.test"
    assert line["request_id"] == "trace-json-0001"
    assert line["ts"].endswith("+00:00")


def test_default_text_format_leaves_logging_untouched(monkeypatch: pytest.MonkeyPatch, clean_logging):
    monkeypatch.delenv("AK5_LOG_FORMAT", raising=False)
    monkeypatch.delenv("AK5_LOG_LEVEL", raising=False)
    before = list(logging.getLogger().handlers)
    obs.configure_logging()
    assert list(logging.getLogger().handlers) == before


def test_configure_logging_is_idempotent(monkeypatch: pytest.MonkeyPatch, clean_logging):
    monkeypatch.setenv("AK5_LOG_FORMAT", "json")
    obs.configure_logging()
    obs.configure_logging()
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_ak5_handler", False)]
    assert len(ours) == 1


# --- never-log list (operations.md §4.4) -------------------------------------------------------

SECRETS = [
    "ak5_agt_SUPERSECRETTOKEN123",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c3JfMSJ9.c2lnbmF0dXJlMTIz",
    "claim-secret-value-42",
    "session-cookie-value-99",
    "oidc-code-verifier-7",
]


def test_scrub_masks_token_shaped_strings():
    text = (
        f"Authorization: Bearer {SECRETS[1]} agent={SECRETS[0]} claim_token={SECRETS[2]} "
        f"Cookie: ak5_session={SECRETS[3]}; code_verifier={SECRETS[4]}"
    )
    cleaned = obs.scrub(text)
    for secret in SECRETS:
        assert secret not in cleaned, secret
    assert "[redacted]" in cleaned


def test_logged_messages_and_tracebacks_never_contain_secrets(
    monkeypatch: pytest.MonkeyPatch, clean_logging, capsys
):
    monkeypatch.setenv("AK5_LOG_FORMAT", "json")
    obs.configure_logging()
    log = logging.getLogger("ak5.leaky")
    log.info("calling with Bearer %s", SECRETS[1])
    log.warning("agent token %s", SECRETS[0])
    try:
        raise RuntimeError(f"upstream said claim_token={SECRETS[2]} and Bearer {SECRETS[1]}")
    except RuntimeError:
        log.exception("request failed")
    out = capsys.readouterr().err
    for secret in SECRETS[:3]:
        assert secret not in out, secret
    assert "request failed" in out


@pytest.mark.asyncio
async def test_access_log_never_contains_authorization_or_cookie(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.DEBUG):
        await client.get(
            "/api/v1/boards",
            headers={"Cookie": f"ak5_session={SECRETS[3]}", "X-CSRF-Token": SECRETS[2]},
        )
    everything = "\n".join(f"{r.name} {r.getMessage()} {vars(r)}" for r in caplog.records)
    assert SECRETS[3] not in everything and SECRETS[2] not in everything
    token = client.headers["authorization"].split(" ", 1)[1]
    assert token not in everything


# --- audit ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_payload_carries_the_request_id(
    client: AsyncClient, test_db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "ATTACHMENTS_DIR", str(tmp_path))
    tid = (await client.post("/api/v1/tickets", json=board_ticket("Audited"))).json()["ticket_id"]
    resp = await client.post(
        f"/api/v1/tickets/{tid}/attachments",
        files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
        headers={"X-Request-ID": "trace-audit-0001"},
    )
    assert resp.status_code == 201
    rows = (
        await test_db.execute(select(AuditLog).where(AuditLog.action == "ATTACHMENT_UPLOADED"))
    ).scalars().all()
    payload = json.loads(rows[-1].payload)
    assert payload["request_id"] == "trace-audit-0001"
    assert payload["filename"] == "a.txt"  # the original payload is preserved


# --- health --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoints_are_public_and_keep_the_old_shape(anon_client: AsyncClient):
    old = await anon_client.get("/health")
    assert old.status_code == 200 and old.json()["status"] == "ok"
    live = await anon_client.get("/healthz")
    assert live.status_code == 200 and live.json() == {"status": "ok"}
    assert (await anon_client.get("/readyz")).status_code in (200, 503)  # reachable without auth


@pytest.mark.asyncio
async def test_readyz_reports_schema_until_the_database_is_at_head(
    anon_client: AsyncClient, test_db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "ATTACHMENTS_DIR", str(tmp_path))
    not_ready = await anon_client.get("/readyz")  # tables exist, but no alembic_version
    assert not_ready.status_code == 503
    assert not_ready.json() == {"status": "not_ready", "failed": ["schema"]}

    conn = await test_db.connection()
    await conn.run_sync(mig.stamp_sync)
    await test_db.commit()
    ready = await anon_client.get("/readyz")
    assert ready.status_code == 200, ready.text
    assert ready.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_readyz_names_a_broken_blob_store_without_leaking_paths(
    anon_client: AsyncClient, test_db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    conn = await test_db.connection()
    await conn.run_sync(mig.stamp_sync)
    await test_db.commit()
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory")
    monkeypatch.setattr(settings, "ATTACHMENTS_DIR", str(blocker / "attachments"))

    resp = await anon_client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "not_ready", "failed": ["blob"]}
    assert str(tmp_path) not in resp.text


@pytest.mark.asyncio
async def test_readiness_failures_hides_database_errors():
    class BrokenDb:
        async def execute(self, *_a, **_k):
            raise RuntimeError("postgresql://user:hunter2@db.internal/ak5 refused")

    assert await obs.readiness_failures(BrokenDb()) == ["database"]


# --- client side ----------------------------------------------------------------------------------


def test_client_error_envelope_includes_request_id():
    err = ClientError("boom", code="SOME_CODE", request_id="req_1234abcd5678")
    assert err.envelope()["error"]["request_id"] == "req_1234abcd5678"
    assert "request_id" not in ClientError("boom", code="SOME_CODE").envelope()["error"]


def test_cli_prints_the_request_id(capsys: pytest.CaptureFixture[str]):
    err = ClientError("boom", code="SOME_CODE", request_id="req_1234abcd5678")
    with pytest.raises(SystemExit):
        print_error(err, json_mode=False)
    assert "request id: req_1234abcd5678" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        print_error(err, json_mode=True)
    assert json.loads(capsys.readouterr().out)["error"]["request_id"] == "req_1234abcd5678"


class _Route:
    path = "/relative"


def test_route_template_masks_parameter_values_only():
    scope = {
        "path": "/api/v1/tickets/TK-0001/attachments/att_abc",
        "route": _Route(),
        "path_params": {"ticket_id": "TK-0001", "attachment_id": "att_abc"},
    }
    assert obs._route_template(scope) == "/api/v1/tickets/{ticket_id}/attachments/{attachment_id}"

    # a parameter whose value equals a static segment: only the parameter position is masked
    scope = {"path": "/api/v1/boards/boards", "route": _Route(), "path_params": {"board_id": "boards"}}
    assert obs._route_template(scope) == "/api/v1/boards/{board_id}"

    # static route: nothing to mask
    assert obs._route_template({"path": "/api/v1/boards", "route": _Route(), "path_params": {}}) == "/api/v1/boards"


def test_route_template_for_unmatched_requests():
    assert obs._route_template({"path": "/definitely/not/here/12345"}) == "<unmatched>"
    assert obs._route_template({"path": "/healthz"}) == "/healthz"
