"""Issue #28: passkey registration + user-presence enforcement on T15 (identity.md §6)."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import cbor2
import jwt
import pytest
from ak5.contracts.errors import ErrorCode
from ak5.contracts.stages import Role
from ak5.server.auth.passkeys import PRESENCE_WINDOW
from ak5.server.auth.principal import Principal
from ak5.server.auth.sessions import CSRF_HEADER
from ak5.server.auth.tokens import issue_access_token, verify_access_token
from ak5.server.authz import attach_principal
from ak5.server.models.actor import Actor
from ak5.server.models.passkey import WebauthnChallenge
from ak5.server.models.workspace import LOCAL_WORKSPACE_ID, Workspace
from ak5.server.seed import LOCAL_USER_ID, ensure_local_tenancy
from ak5.server.services.workflow import WorkflowError, _require_user_presence_for_approval
from ak5.server.services.workspaces import merge_policy
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

RP_ID = "test"
ORIGIN = "http://test"


class VirtualAuthenticator:
    """Minimal software authenticator (packed-less ``none`` attestation, ES256)."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.sign_count = 0

    def _cose_key(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: nums.x.to_bytes(32, "big"),
                -3: nums.y.to_bytes(32, "big"),
            }
        )

    def _client_data(self, type_: str, challenge_b64: str, origin: str) -> bytes:
        return json.dumps(
            {"type": type_, "challenge": challenge_b64, "origin": origin, "crossOrigin": False}
        ).encode()

    def register(self, challenge_b64: str, *, origin: str = ORIGIN) -> dict:
        client_data = self._client_data("webauthn.create", challenge_b64, origin)
        auth_data = (
            hashlib.sha256(RP_ID.encode()).digest()
            + bytes([0x45])  # UP | UV | AT
            + self.sign_count.to_bytes(4, "big")
            + bytes(16)
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + self._cose_key()
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        cid = bytes_to_base64url(self.credential_id)
        return {
            "id": cid,
            "rawId": cid,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "attestationObject": bytes_to_base64url(attestation),
            },
        }

    def assert_(self, challenge_b64: str, *, origin: str = ORIGIN, uv: bool = True) -> dict:
        self.sign_count += 1
        client_data = self._client_data("webauthn.get", challenge_b64, origin)
        auth_data = (
            hashlib.sha256(RP_ID.encode()).digest()
            + bytes([0x05 if uv else 0x01])
            + self.sign_count.to_bytes(4, "big")
        )
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        cid = bytes_to_base64url(self.credential_id)
        return {
            "id": cid,
            "rawId": cid,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
            },
        }


@pytest.fixture
async def web(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Log in as local owner; return CSRF headers (session cookie lives on the client)."""
    monkeypatch.setenv("AK5_AUTH_PASSWORD", "local-secret")
    login = await client.post("/auth/local/login", json={"password": "local-secret"})
    assert login.status_code == 200, login.text
    return {CSRF_HEADER: login.json()["csrf"]}


async def _register(client: AsyncClient, web: dict[str, str], auth: VirtualAuthenticator) -> dict:
    opts = await client.post("/auth/passkeys/register/options", headers=web)
    assert opts.status_code == 200, opts.text
    body = opts.json()
    credential = auth.register(body["options"]["challenge"])
    verify = await client.post(
        "/auth/passkeys/register/verify",
        headers=web,
        json={"challenge_id": body["challenge_id"], "credential": credential, "label": "Test key"},
    )
    assert verify.status_code == 201, verify.text
    return verify.json()


async def _assert(
    client: AsyncClient, web: dict[str, str], auth: VirtualAuthenticator, **kw
) -> dict:
    opts = await client.post("/auth/passkeys/assert/options", headers=web)
    assert opts.status_code == 200, opts.text
    body = opts.json()
    credential = auth.assert_(body["options"]["challenge"], **kw)
    resp = await client.post(
        "/auth/passkeys/assert/verify",
        headers=web,
        json={
            "challenge_id": body["challenge_id"],
            "credential": credential,
            "workspace_id": LOCAL_WORKSPACE_ID,
        },
    )
    return {"status": resp.status_code, "json": resp.json()}


@pytest.mark.asyncio
async def test_register_list_and_delete_passkey(client: AsyncClient, web):
    auth = VirtualAuthenticator()
    created = await _register(client, web, auth)
    assert created["label"] == "Test key"

    listed = await client.get("/auth/passkeys")
    assert [p["passkey_id"] for p in listed.json()["passkeys"]] == [created["passkey_id"]]

    missing_csrf = await client.delete(f"/auth/passkeys/{created['passkey_id']}")
    assert missing_csrf.status_code == 403
    deleted = await client.delete(f"/auth/passkeys/{created['passkey_id']}", headers=web)
    assert deleted.status_code == 200
    assert (await client.get("/auth/passkeys")).json()["passkeys"] == []
    assert (
        await client.delete(f"/auth/passkeys/{created['passkey_id']}", headers=web)
    ).status_code == 404


@pytest.mark.asyncio
async def test_register_requires_session_and_csrf(client: AsyncClient, anon_client: AsyncClient, web):
    assert (await anon_client.post("/auth/passkeys/register/options")).status_code == 401
    assert (await client.post("/auth/passkeys/register/options")).status_code == 403


@pytest.mark.asyncio
async def test_register_rejects_wrong_origin_and_challenge_is_single_use(
    client: AsyncClient, web, test_db: AsyncSession
):
    auth = VirtualAuthenticator()
    opts = (await client.post("/auth/passkeys/register/options", headers=web)).json()
    bad = await client.post(
        "/auth/passkeys/register/verify",
        headers=web,
        json={
            "challenge_id": opts["challenge_id"],
            "credential": auth.register(opts["options"]["challenge"], origin="http://evil.test"),
        },
    )
    assert bad.status_code == 400
    # the failed attempt burned the challenge
    retry = await client.post(
        "/auth/passkeys/register/verify",
        headers=web,
        json={
            "challenge_id": opts["challenge_id"],
            "credential": auth.register(opts["options"]["challenge"]),
        },
    )
    assert retry.status_code == 400
    assert (await client.get("/auth/passkeys")).json()["passkeys"] == []


@pytest.mark.asyncio
async def test_assert_requires_registered_passkey(client: AsyncClient, web):
    resp = await client.post("/auth/passkeys/assert/options", headers=web)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_assertion_mints_presence_token(client: AsyncClient, web):
    auth = VirtualAuthenticator()
    await _register(client, web, auth)

    result = await _assert(client, web, auth)
    assert result["status"] == 200, result
    token = result["json"]["access_token"]
    assert result["json"]["expires_in"] == int(PRESENCE_WINDOW.total_seconds())

    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["sub"] == LOCAL_USER_ID
    assert abs(claims["pres"] - datetime.now(UTC).timestamp()) < 10


@pytest.mark.asyncio
async def test_assertion_rejects_missing_user_verification_and_replay(client: AsyncClient, web):
    auth = VirtualAuthenticator()
    await _register(client, web, auth)

    no_uv = await _assert(client, web, auth, uv=False)
    assert no_uv["status"] == 401

    opts = (await client.post("/auth/passkeys/assert/options", headers=web)).json()
    credential = auth.assert_(opts["options"]["challenge"])
    payload = {
        "challenge_id": opts["challenge_id"],
        "credential": credential,
        "workspace_id": LOCAL_WORKSPACE_ID,
    }
    assert (
        await client.post("/auth/passkeys/assert/verify", headers=web, json=payload)
    ).status_code == 200
    replay = await client.post("/auth/passkeys/assert/verify", headers=web, json=payload)
    assert replay.status_code == 400


@pytest.mark.asyncio
async def test_expired_challenge_rejected(client: AsyncClient, web, test_db: AsyncSession):
    auth = VirtualAuthenticator()
    await _register(client, web, auth)
    opts = (await client.post("/auth/passkeys/assert/options", headers=web)).json()
    row = (
        await test_db.execute(
            select(WebauthnChallenge).where(WebauthnChallenge.challenge_id == opts["challenge_id"])
        )
    ).scalar_one()
    row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    await test_db.commit()
    resp = await client.post(
        "/auth/passkeys/assert/verify",
        headers=web,
        json={
            "challenge_id": opts["challenge_id"],
            "credential": auth.assert_(opts["options"]["challenge"]),
            "workspace_id": LOCAL_WORKSPACE_ID,
        },
    )
    assert resp.status_code == 400


def _human_actor(presence_at: datetime | None) -> Actor:
    actor = Actor(actor_id=LOCAL_USER_ID, actor_type="human", role="human")
    attach_principal(
        actor,
        Principal(
            principal_id=LOCAL_USER_ID,
            principal_type="user",
            workspace_id=LOCAL_WORKSPACE_ID,
            workflow_role=Role.HUMAN,
            membership_role="owner",
            presence_at=presence_at,
        ),
    )
    return actor


async def _set_policy(db: AsyncSession, enabled: bool) -> None:
    await ensure_local_tenancy(db)
    await db.commit()
    ws = await db.get(Workspace, LOCAL_WORKSPACE_ID)
    ws.policy = merge_policy(ws.policy, {"require_user_presence_for_approval": enabled})
    await db.commit()


@pytest.mark.asyncio
async def test_t15_presence_enforced_only_when_policy_enabled(test_db: AsyncSession):
    ticket = SimpleNamespace(workspace_id=LOCAL_WORKSPACE_ID)
    now = datetime.now(UTC)

    # policy off (default): no presence needed
    await _require_user_presence_for_approval(test_db, _human_actor(None), ticket)

    await _set_policy(test_db, True)
    for presence in (None, now - PRESENCE_WINDOW - timedelta(seconds=5)):
        with pytest.raises(WorkflowError) as exc:
            await _require_user_presence_for_approval(test_db, _human_actor(presence), ticket)
        assert exc.value.code == ErrorCode.USER_PRESENCE_REQUIRED

    await _require_user_presence_for_approval(test_db, _human_actor(now), ticket)

    # no principal at all fails closed
    bare = Actor(actor_id="user_pm", actor_type="human", role="human")
    with pytest.raises(WorkflowError):
        await _require_user_presence_for_approval(test_db, bare, ticket)


@pytest.mark.asyncio
async def test_presence_claim_round_trips_through_verify(test_db: AsyncSession):
    await ensure_local_tenancy(test_db)
    await test_db.commit()
    now = datetime.now(UTC)
    principal = Principal(
        principal_id=LOCAL_USER_ID,
        principal_type="user",
        workspace_id=LOCAL_WORKSPACE_ID,
        workflow_role=Role.HUMAN,
        membership_role="owner",
    )
    plain = await verify_access_token(issue_access_token(principal), test_db)
    assert plain.presence_at is None
    fresh = await verify_access_token(issue_access_token(principal, presence_at=now), test_db)
    assert fresh.presence_at is not None
    assert abs((fresh.presence_at - now).total_seconds()) < 2


def test_base64url_helpers_available():
    assert base64url_to_bytes(bytes_to_base64url(b"ak5")) == b"ak5"
