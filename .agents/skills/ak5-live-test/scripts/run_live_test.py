#!/usr/bin/env python3
"""AK5 v2 Live Server Integration & E2E Functional Test Runner.

Spins up an isolated AK5 server instance in a temporary sandbox to verify:
  1. Identity gates: identify disabled, loopback local-owner login, web session →
     user JWT, OAuth device grant (user + agent), JWT secret 0600 (identity.md)
  2. Workflow through the real CLI + client daemon: ``ak5 login`` per profile,
     inbox → triage route → worker next/claim/submit → gatekeeper verdict →
     human verdict → done, plus ``events wait`` / ``next --wait`` (client.md §4.4)
  3. TypeScript SDK with a device-grant agent token (when built)

Ensures zero side-effects on the host workspace by sandboxing HOME, the data
directory, the JWT key, and the daemon socket.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    print("Error: 'httpx' is required. Run via 'uv run python ...'")
    sys.exit(1)

CSRF_HEADER = "X-CSRF-Token"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
USER_CODE_RE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b")


def find_free_port() -> int:
    """Find an available port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_until_ready(base_url: str, timeout: float = 20.0) -> bool:
    """Poll ``/meta`` until the server is responsive."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = httpx.get(f"{base_url}/meta", timeout=1.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


class LiveTestRunner:
    def __init__(self, port: int | None = None, verbose: bool = False, keep_artifacts: bool = False):
        self.port = port or find_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.api_url = f"{self.base_url}/api/v1"
        self.verbose = verbose
        self.keep_artifacts = keep_artifacts
        # Short prefix: the daemon socket lives under this dir (AF_UNIX ~104 bytes).
        self.sandbox_dir = Path(tempfile.mkdtemp(prefix="ak5lt_"))
        # Canonical store under AK5_DATA_DIR — never a divergent DATABASE_URL file.
        self.data_dir = self.sandbox_dir / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "ak5.db"
        self.jwt_secret_path = self.data_dir / "jwt_secret"
        self.home_dir = self.sandbox_dir / "h"
        self.home_dir.mkdir()
        self.runtime_dir = self.sandbox_dir / "r"
        self.runtime_dir.mkdir(mode=0o700)
        self.owner_password = "ak5livepass123"

        self.passed_tests = 0
        self.failed_tests = 0

    def log(self, msg: str, level: str = "INFO") -> None:
        symbol = {"INFO": "ℹ", "PASS": "✔", "FAIL": "✗", "WARN": "⚠"}.get(level, "·")
        color = {"INFO": "\033[0;36m", "PASS": "\033[0;32m", "FAIL": "\033[0;31m", "WARN": "\033[0;33m"}.get(
            level, "\033[0m"
        )
        reset = "\033[0m"
        print(f"{color}{symbol} {msg}{reset}")

    def report_pass(self, name: str) -> None:
        self.passed_tests += 1
        self.log(name, level="PASS")

    def report_fail(self, name: str, reason: str) -> None:
        self.failed_tests += 1
        self.log(f"{name}: {reason}", level="FAIL")

    def check(self, name: str, ok: bool, reason: str = "") -> bool:
        if ok:
            self.report_pass(name)
        else:
            self.report_fail(name, reason or "assertion failed")
        return ok

    def cleanup(self) -> None:
        if self.keep_artifacts:
            self.log(f"Sandbox preserved at {self.sandbox_dir}", level="INFO")
            return
        if self.sandbox_dir.exists():
            shutil.rmtree(self.sandbox_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Process helpers
    # -------------------------------------------------------------------------
    def base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in (
            "AK5_TOKEN",
            "AK5_ACTOR_TOKEN",
            "AK5_ACTOR_ID",
            "AK5_PROFILE",
            "AK5_SESSION_FILE",
            "AK5_DEV_INSECURE_IDENTIFY",
            "AK5_IDENTIFY_SECRET",
            "AK5_OIDC_PROVIDERS",
        ):
            env.pop(key, None)
        env["AK5_SKIP_DOTENV"] = "1"
        env["AK5_SKIP_VERSION_CHECK"] = "1"
        env["AK5_DATA_DIR"] = str(self.data_dir)
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.db_path.as_posix()}"
        env["AK5_JWT_SECRET_FILE"] = str(self.jwt_secret_path)
        env["AK5_AUTH_PASSWORD"] = self.owner_password
        env["HOME"] = str(self.home_dir)
        env["AK5_HOME"] = str(self.home_dir / ".ak5")
        env["XDG_RUNTIME_DIR"] = str(self.runtime_dir)
        env["AK5_API_URL"] = self.api_url
        return env

    def spawn_server(self, extra_env: dict[str, str] | None = None) -> subprocess.Popen[str]:
        env = self.base_env()
        env.update(extra_env or {})
        cmd = [sys.executable, "-m", "ak5.cli.main", "serve", "--port", str(self.port)]
        return subprocess.Popen(
            cmd,
            env=env,
            cwd=str(self.sandbox_dir),
            stdout=subprocess.DEVNULL if not self.verbose else None,
            stderr=subprocess.STDOUT if not self.verbose else None,
            text=True,
        )

    def stop_server(self, proc: subprocess.Popen[str]) -> None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def ak5(
        self, *args: str, profile: str | None = None, timeout: float = 60.0
    ) -> subprocess.CompletedProcess[str]:
        env = self.base_env()
        if profile:
            env["AK5_PROFILE"] = profile
        return subprocess.run(
            [sys.executable, "-m", "ak5.cli.main", *args],
            env=env,
            cwd=str(self.sandbox_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def owner_client(self) -> tuple[httpx.Client, str]:
        """Browser stand-in: loopback local-owner session + CSRF token."""
        client = httpx.Client(base_url=self.base_url, timeout=30.0)
        r = client.post("/auth/local/login", json={"password": self.owner_password})
        r.raise_for_status()
        return client, r.json()["csrf"]

    def approve(self, user_code: str, **extra: object) -> int:
        client, csrf = self.owner_client()
        try:
            body = {"user_code": user_code, "approved": True, **extra}
            r = client.post("/oauth/device/approve", json=body, headers={CSRF_HEADER: csrf})
            return r.status_code
        finally:
            client.close()

    def cli_login(self, *args: str, approve_extra: dict[str, object] | None = None) -> subprocess.CompletedProcess[str]:
        """Run ``ak5 login …`` and approve its user code over HTTP like a human would."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "ak5.cli.main", "login", "--url", self.api_url, *args],
            env=self.base_env(),
            cwd=str(self.sandbox_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert proc.stdout is not None
        output: list[str] = []
        deadline = time.time() + 30
        code = None
        while time.time() < deadline and code is None:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            output.append(line)
            m = USER_CODE_RE.search(line)
            if m:
                code = m.group(0)
        if code is not None:
            self.approve(code, **(approve_extra or {}))
        try:
            rest, _ = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            rest, _ = proc.communicate()
        output.append(rest or "")
        return subprocess.CompletedProcess(proc.args, proc.returncode, "".join(output), "")

    def device_grant_token(self, *, principal: str, **request: object) -> dict:
        """Full device grant over HTTP (no CLI); returns the token response."""
        approve_extra: dict[str, object] = {}
        if principal == "agent":
            approve_extra = {"workflow_role": request.get("workflow_role", "worker"), "board_scope": []}
        with httpx.Client(base_url=self.base_url, timeout=30.0) as client:
            start = client.post(
                "/oauth/device_authorization",
                json={"client_id": "ak5-cli", "principal": principal, **request},
            )
            start.raise_for_status()
            body = start.json()
            status = self.approve(body["user_code"], **approve_extra)
            if status != 200:
                raise RuntimeError(f"approve returned {status}")
            token = client.post(
                "/oauth/token",
                json={"grant_type": DEVICE_GRANT, "device_code": body["device_code"], "client_id": "ak5-cli"},
            )
            token.raise_for_status()
            return token.json()

    # -------------------------------------------------------------------------
    # Suite 1: Identity gates (identity.md §2, §3, §8)
    # -------------------------------------------------------------------------
    def test_identity(self) -> None:
        print("\n\033[1;34m[Suite 1/3] Identity: local owner, session token, device grant\033[0m")
        proc = self.spawn_server()
        try:
            if not self.check("Server startup", wait_until_ready(self.base_url), "server did not become ready"):
                return

            meta = httpx.get(f"{self.base_url}/meta").json()
            self.check("GET /meta api_version=2", str(meta.get("api_version")) == "2", f"meta={meta}")

            r = httpx.post(
                f"{self.api_url}/auth/identify",
                json={"actor_id": "sneaky", "actor_type": "agent", "name": "x", "role": "worker"},
            )
            self.check(
                "Self-identify disabled without AK5_DEV_INSECURE_IDENTIFY",
                r.status_code != 200,
                f"got {r.status_code}",
            )

            r = httpx.post(f"{self.base_url}/auth/local/login", json={"password": "wrong"})
            self.check("Local owner login rejects wrong password", r.status_code == 401, f"got {r.status_code}")

            client, csrf = self.owner_client()
            try:
                self.report_pass("Local owner login (loopback)")
                bad = client.post("/auth/session/token", json={}, headers={CSRF_HEADER: "nope"})
                self.check("Session token exchange enforces CSRF", bad.status_code == 403, f"got {bad.status_code}")
                good = client.post("/auth/session/token", json={}, headers={CSRF_HEADER: csrf})
                ok = good.status_code == 200 and bool(good.json().get("access_token"))
                self.check("Session token exchange → user JWT", ok, f"{good.status_code} {good.text[:200]}")
            finally:
                client.close()

            user = self.device_grant_token(principal="user")
            self.check("Device grant (user) → access token", bool(user.get("access_token")), str(user)[:200])

            agent = self.device_grant_token(
                principal="agent", name="lt-worker", workflow_role="worker", capabilities=["py"]
            )
            opaque = str(agent.get("access_token") or "")
            self.check("Device grant (agent) → opaque ak5_agt_ token", opaque.startswith("ak5_agt_"), opaque[:16])

            r = httpx.get(f"{self.api_url}/boards", headers={"Authorization": f"Bearer {opaque}"})
            self.check("Agent token authorizes API", r.status_code == 200, f"got {r.status_code}")

            if self.jwt_secret_path.exists():
                mode = stat.S_IMODE(self.jwt_secret_path.stat().st_mode)
                self.check("JWT secret file is 0600", mode == 0o600, oct(mode))
            else:
                self.report_fail("JWT secret file is 0600", f"{self.jwt_secret_path} was not created")
        except Exception as exc:  # noqa: BLE001
            self.report_fail("Identity suite", repr(exc))
        finally:
            self.stop_server(proc)

    # -------------------------------------------------------------------------
    # Suite 2: Workflow through CLI + daemon (workflow.md §3, client.md §3–4)
    # -------------------------------------------------------------------------
    def test_workflow(self) -> None:
        print("\n\033[1;34m[Suite 2/3] Workflow: CLI + daemon pull loop to human approval\033[0m")
        proc = self.spawn_server()
        try:
            if not self.check("Server startup", wait_until_ready(self.base_url), "server did not become ready"):
                return

            logins = [
                ("me", ["--profile", "me"], None),
                ("triage-1", ["--agent", "triage-1", "--role", "triage"], "triage"),
                ("worker-1", ["--agent", "worker-1", "--role", "worker", "--caps", "py"], "worker"),
                ("gk-1", ["--agent", "gk-1", "--role", "gatekeeper"], "gatekeeper"),
            ]
            for name, args, role in logins:
                extra = {"workflow_role": role, "board_scope": []} if role else None
                res = self.cli_login(*args, approve_extra=extra)
                if not self.check(f"ak5 login profile {name}", res.returncode == 0, res.stdout[-400:]):
                    return

            res = self.ak5("--json", "whoami", profile="worker-1")
            body = json.loads(res.stdout or "{}") if res.returncode == 0 else {}
            self.check(
                "whoami resolves token via daemon",
                res.returncode == 0 and body.get("source") == "daemon",
                res.stdout[-300:] + res.stderr[-300:],
            )

            res = self.ak5("board", "create", "lt-board", "--template", "default", profile="me")
            if not self.check("board create (human)", res.returncode == 0, res.stdout + res.stderr):
                return

            res = self.ak5("--json", "ticket", "create", "Live test request", profile="me")
            tid = json.loads(res.stdout).get("ticket_id") if res.returncode == 0 else None
            if not self.check("T1 ticket create → inbox", bool(tid), res.stdout + res.stderr):
                return

            res = self.ak5("--json", "next", "--inbox", profile="triage-1")
            self.check("triage next --inbox", res.returncode == 0 and tid in res.stdout, res.stdout[-300:])
            res = self.ak5("ticket", "claim", tid, profile="triage-1")
            self.check("T3 triage claim", res.returncode == 0, res.stdout + res.stderr)

            plan = self.sandbox_dir / "route.json"
            plan.write_text(
                json.dumps(
                    {
                        "refined_title": "Live test request",
                        "refined_spec": "Print hello",
                        "acceptance_criteria": ["prints hello"],
                        "target": {"board_id": "lt-board"},
                        "confidence": 0.9,
                    }
                ),
                encoding="utf-8",
            )
            res = self.ak5("ticket", "route", tid, "--plan", f"@{plan}", profile="triage-1")
            self.check("T4 route → board backlog", res.returncode == 0, res.stdout + res.stderr)

            res = self.ak5(
                "--json", "events", "wait", "--events", "WORK_AVAILABLE", "--timeout", "10", profile="worker-1"
            )
            self.check("events wait sees WORK_AVAILABLE", res.returncode == 0, f"exit={res.returncode} {res.stdout[-300:]}")

            res = self.ak5("--json", "next", "--board", "lt-board", "--caps", "py", profile="worker-1")
            self.check("worker next", res.returncode == 0 and tid in res.stdout, res.stdout[-300:])
            res = self.ak5("ticket", "claim", tid, "--caps", "py", profile="worker-1")
            self.check("T5 worker claim", res.returncode == 0, res.stdout + res.stderr)
            res = self.ak5("ticket", "heartbeat", tid, profile="worker-1")
            self.check("T6 heartbeat (local claim token)", res.returncode == 0, res.stdout + res.stderr)

            deliverable = self.sandbox_dir / "deliverable.json"
            deliverable.write_text(
                json.dumps(
                    {
                        "summary": "hello printed",
                        "artifacts": [{"kind": "text", "ref": "print('hello')"}],
                        "criteria_self_check": [{"criterion": "prints hello", "pass": True, "note": ""}],
                    }
                ),
                encoding="utf-8",
            )
            res = self.ak5("ticket", "submit", tid, "--deliverable", f"@{deliverable}", profile="worker-1")
            self.check("T9 submit → agent_review", res.returncode == 0, res.stdout + res.stderr)

            res = self.ak5("ticket", "claim", tid, profile="gk-1")
            self.check("T11 gatekeeper claim", res.returncode == 0, res.stdout + res.stderr)
            findings = self.sandbox_dir / "findings.json"
            findings.write_text(
                json.dumps([{"criterion": "prints hello", "pass": True, "note": "ok"}]), encoding="utf-8"
            )
            res = self.ak5("ticket", "verdict", tid, "--approve", "--findings", f"@{findings}", profile="gk-1")
            self.check("T12 gatekeeper approve → human_review", res.returncode == 0, res.stdout + res.stderr)

            res = self.ak5("ticket", "verdict", tid, "--approve", "--reason", "ship it", profile="worker-1")
            self.check("T15 rejected for agent (human only)", res.returncode == 4, f"exit={res.returncode}")
            res = self.ak5("ticket", "verdict", tid, "--approve", "--reason", "ship it", profile="me")
            self.check("T15 human approve → done", res.returncode == 0, res.stdout + res.stderr)

            res = self.ak5("--json", "ticket", "view", tid, profile="me")
            stage = json.loads(res.stdout).get("stage") if res.returncode == 0 else None
            self.check("ticket stage is done", stage == "done", f"stage={stage}")

            res = self.ak5("next", "--board", "lt-board", "--wait", "1", profile="worker-1")
            self.check("next --wait with no work → exit 6", res.returncode == 6, f"exit={res.returncode}")
        except Exception as exc:  # noqa: BLE001
            self.report_fail("Workflow suite", repr(exc))
        finally:
            self.ak5("daemon", "stop", timeout=20)
            self.stop_server(proc)

    # -------------------------------------------------------------------------
    # Suite 3: TypeScript SDK (@ak5/sdk) with a device-grant agent token
    # -------------------------------------------------------------------------
    def test_typescript_sdk(self) -> None:
        print("\n\033[1;34m[Suite 3/3] TypeScript SDK Integration (@ak5/sdk)\033[0m")
        sdk_dist = Path(__file__).resolve().parents[4] / "packages" / "sdk" / "dist" / "index.mjs"
        if not sdk_dist.is_file():
            self.log(f"SDK dist not found at {sdk_dist}. Run 'npm run build' in packages/sdk.", level="WARN")
            return

        proc = self.spawn_server()
        try:
            if not self.check("Server startup (SDK)", wait_until_ready(self.base_url), "server did not become ready"):
                return
            agent = self.device_grant_token(
                principal="agent", name="lt-sdk", workflow_role="worker", capabilities=["py"]
            )
            api = json.dumps(self.api_url)
            node_script = f"""
            import {{ AK5Client }} from {json.dumps(sdk_dist.as_posix())};

            const client = new AK5Client({{ baseUrl: {api}, token: {json.dumps(agent["access_token"])} }});
            const meta = await client.ensureCompatible();
            if (String(meta.api_version) !== "2") throw new Error(`api_version=${{meta.api_version}}`);
            const boards = await client.listBoards();
            if (!Array.isArray(boards)) throw new Error("listBoards did not return an array");

            const bad = new AK5Client({{ baseUrl: {api}, token: "ak5_agt_invalid" }});
            let rejected = false;
            try {{ await bad.listBoards(); }} catch (e) {{ rejected = true; }}
            if (!rejected) throw new Error("invalid agent token was accepted");
            """
            script_file = self.sandbox_dir / "sdk_smoke.mjs"
            script_file.write_text(node_script, encoding="utf-8")
            res = subprocess.run(["node", str(script_file)], capture_output=True, text=True, check=False)
            self.check(
                "SDK: agent token, version check, invalid token rejected",
                res.returncode == 0,
                f"stderr={res.stderr[-500:]} stdout={res.stdout[-300:]}",
            )
        except Exception as exc:  # noqa: BLE001
            self.report_fail("SDK suite", repr(exc))
        finally:
            self.stop_server(proc)

    def run_all(self, mode: str = "all") -> int:
        start_time = time.time()
        print("\033[1;36m============================================================\033[0m")
        print("\033[1m              AK5 Live Server Integration Runner             \033[0m")
        print("\033[1;36m============================================================\033[0m")
        print(f"Sandbox: {self.sandbox_dir}")
        print(f"Target Port: {self.port}")

        try:
            if mode in ("all", "identity"):
                self.test_identity()
            if mode in ("all", "workflow"):
                self.test_workflow()
            if mode in ("all", "sdk"):
                self.test_typescript_sdk()
        finally:
            self.cleanup()

        elapsed = time.time() - start_time
        print("\n\033[1;36m============================================================\033[0m")
        print(f"Results: \033[0;32m{self.passed_tests} passed\033[0m, \033[0;31m{self.failed_tests} failed\033[0m in {elapsed:.2f}s")
        print("\033[1;36m============================================================\033[0m")
        return 0 if self.failed_tests == 0 else 1


_MODE_ALIASES = {"security": "identity", "core": "workflow"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AK5 Live Server Integration Tests")
    parser.add_argument("--port", type=int, default=None, help="Port to bind server (default: random free port)")
    parser.add_argument(
        "--mode",
        choices=["all", "identity", "workflow", "sdk", *_MODE_ALIASES],
        default="all",
        help="Suite to execute (security/core are v1 aliases of identity/workflow)",
    )
    parser.add_argument("--verbose", action="store_true", help="Print server stdout/stderr")
    parser.add_argument("--keep-artifacts", action="store_true", help="Keep sandbox directory after tests")
    args = parser.parse_args()

    runner = LiveTestRunner(port=args.port, verbose=args.verbose, keep_artifacts=args.keep_artifacts)
    sys.exit(runner.run_all(mode=_MODE_ALIASES.get(args.mode, args.mode)))


if __name__ == "__main__":
    main()
