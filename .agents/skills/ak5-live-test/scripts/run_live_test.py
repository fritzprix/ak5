#!/usr/bin/env python3
"""AK5 Live Server Integration & E2E Functional Test Runner.

Spins up an isolated AK5 server instance in a temporary sandbox to verify:
  1. Runtime security gates (AK5_IDENTIFY_SECRET, JWT secret persistence & 0600 permissions, web auth bypass)
  2. Core REST API functionality (actors, boards, tickets, status transitions)
  3. CLI multi-actor authentication and session binding
  4. TypeScript SDK compatibility (when built)

Ensures zero side-effects on the host workspace by sandboxing databases and session files.
"""

from __future__ import annotations

import argparse
import os
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


def find_free_port() -> int:
    """Find an available port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_until_ready(api_url: str, timeout: float = 12.0) -> bool:
    """Poll the API until the server is responsive."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = httpx.get(f"{api_url}/boards", timeout=1.0)
            if r.status_code in (200, 401, 403):
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
        self.sandbox_dir = Path(tempfile.mkdtemp(prefix="ak5_live_test_"))
        self.db_path = self.sandbox_dir / "ak5_test.db"
        self.jwt_secret_path = self.sandbox_dir / "jwt_secret"
        self.shared_secret = "test-identify-secret-live-2026"
        self.web_user = "admin"
        self.web_pass = "ak5livepass123"

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

    def cleanup(self) -> None:
        if self.keep_artifacts:
            self.log(f"Sandbox preserved at {self.sandbox_dir}", level="INFO")
            return
        if self.sandbox_dir.exists():
            shutil.rmtree(self.sandbox_dir, ignore_errors=True)

    def spawn_server(self, extra_env: dict[str, str], use_web: bool = True) -> subprocess.Popen[str]:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.db_path}"
        env["AK5_JWT_SECRET_FILE"] = str(self.jwt_secret_path)
        env["HOME"] = str(self.sandbox_dir)  # Sandbox user home for CLI sessions
        env.update(extra_env)

        cmd = [
            sys.executable,
            "-m",
            "ak5.cli.main",
            "web" if use_web else "serve",
            "--port",
            str(self.port),
        ]
        if use_web:
            cmd.append("--no-browser")

        proc = subprocess.Popen(
            cmd,
            env=env,
            cwd=str(self.sandbox_dir),
            stdout=subprocess.PIPE if not self.verbose else None,
            stderr=subprocess.STDOUT if not self.verbose else None,
            text=True,
        )
        return proc

    def stop_server(self, proc: subprocess.Popen[str]) -> None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    # -------------------------------------------------------------------------
    # Test Suite 1: Security & Identity Gates
    # -------------------------------------------------------------------------
    def test_security_gates(self) -> None:
        print("\n\033[1;34m[Suite 1/3] Security, Identify Gate & JWT Hardening\033[0m")
        extra_env = {
            "AK5_IDENTIFY_SECRET": self.shared_secret,
            "AK5_AUTH_USERNAME": self.web_user,
            "AK5_AUTH_PASSWORD": self.web_pass,
        }
        proc = self.spawn_server(extra_env, use_web=True)
        try:
            if not wait_until_ready(self.api_url):
                self.report_fail("Server Startup", "Server did not respond within timeout")
                return
            self.report_pass("Server Startup (Protected Mode)")

            # 1. POST /auth/identify without secret -> 401
            r = httpx.post(
                f"{self.api_url}/auth/identify",
                json={"actor_id": "intruder", "actor_type": "agent", "name": "Intruder", "role": "Hacker"},
            )
            if r.status_code == 401:
                self.report_pass("Identify without secret rejected (401)")
            else:
                self.report_fail("Identify without secret rejected", f"Expected 401, got {r.status_code}")

            # 2. POST /auth/identify with wrong secret -> 401
            r = httpx.post(
                f"{self.api_url}/auth/identify",
                headers={"X-AK5-Identify-Secret": "invalid-secret"},
                json={"actor_id": "intruder", "actor_type": "agent", "name": "Intruder", "role": "Hacker"},
            )
            if r.status_code == 401:
                self.report_pass("Identify with wrong secret rejected (401)")
            else:
                self.report_fail("Identify with wrong secret rejected", f"Expected 401, got {r.status_code}")

            # 3. POST /auth/identify with header secret -> 200 & JWT
            r = httpx.post(
                f"{self.api_url}/auth/identify",
                headers={"X-AK5-Identify-Secret": self.shared_secret},
                json={"actor_id": "agent_alpha", "actor_type": "agent", "name": "Agent Alpha", "role": "Worker"},
            )
            if r.status_code == 200 and "access_token" in r.json():
                self.report_pass("Identify with X-AK5-Identify-Secret header accepted (200 + JWT)")
            else:
                self.report_fail("Identify with header secret", f"Got status {r.status_code}")

            # 4. Verify JWT Secret File Created with 0600 Permissions
            if self.jwt_secret_path.is_file():
                mode = stat.S_IMODE(os.stat(self.jwt_secret_path).st_mode)
                if mode == 0o600:
                    self.report_pass(f"JWT Secret file auto-persisted with 0600 permissions ({oct(mode)})")
                else:
                    self.report_fail("JWT Secret permissions", f"Expected 0o600, got {oct(mode)}")
            else:
                self.report_fail("JWT Secret persistence", f"File {self.jwt_secret_path} was not created")

            # 5. POST /auth/identify with body identify_secret -> 200
            r = httpx.post(
                f"{self.api_url}/auth/identify",
                json={
                    "actor_id": "agent_beta",
                    "actor_type": "agent",
                    "name": "Agent Beta",
                    "role": "Worker",
                    "identify_secret": self.shared_secret,
                },
            )
            if r.status_code == 200:
                self.report_pass("Identify with body identify_secret accepted (200)")
            else:
                self.report_fail("Identify with body secret", f"Got status {r.status_code}")

            # 6. Web Dashboard Auth Cookie Bypass
            client = httpx.Client()
            login_resp = client.post(
                f"{self.base_url}/api/auth/login",
                json={"username": self.web_user, "password": self.web_pass},
            )
            if login_resp.status_code == 200 and "ak5_auth" in client.cookies:
                self.report_pass("Web dashboard login succeeded and set ak5_auth cookie")
            else:
                self.report_fail("Web dashboard login", f"Status {login_resp.status_code}")

            cookie_id_resp = client.post(
                f"{self.api_url}/auth/identify",
                json={"actor_id": "user_pm", "actor_type": "human", "name": "PM User", "role": "PM"},
            )
            if cookie_id_resp.status_code == 200:
                self.report_pass("Web auth session cookie authorized identify without shared secret (200)")
            else:
                self.report_fail("Web cookie identify bypass", f"Status {cookie_id_resp.status_code}")

            # 7. CLI login auto-propagating AK5_IDENTIFY_SECRET
            cli_env = os.environ.copy()
            cli_env["AK5_IDENTIFY_SECRET"] = self.shared_secret
            cli_env["HOME"] = str(self.sandbox_dir)
            cli_run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ak5.cli.main",
                    "login",
                    "--url",
                    self.api_url,
                    "--id",
                    "cli_tester",
                    "--role",
                    "QA",
                    "--caps",
                    "e2e,smoke",
                    "--type",
                    "agent",
                ],
                env=cli_env,
                cwd=str(self.sandbox_dir),
                capture_output=True,
                text=True,
            )
            if cli_run.returncode == 0 and "Authenticated as cli_tester" in cli_run.stdout:
                self.report_pass("CLI 'ak5 login' auto-sent AK5_IDENTIFY_SECRET and authenticated")
            else:
                self.report_fail("CLI login with secret", f"Code {cli_run.returncode}, stdout: {cli_run.stdout}")

        finally:
            self.stop_server(proc)

    # -------------------------------------------------------------------------
    # Test Suite 2: Core REST Operations (Open Mode)
    # -------------------------------------------------------------------------
    def test_core_operations(self) -> None:
        print("\n\033[1;34m[Suite 2/3] Core REST API & Workflow Operations\033[0m")
        proc = self.spawn_server({}, use_web=False)
        try:
            if not wait_until_ready(self.api_url):
                self.report_fail("Server Startup", "Open server did not respond")
                return
            self.report_pass("Server Startup (Open Mode)")

            # 1. Open Identify
            r = httpx.post(
                f"{self.api_url}/auth/identify",
                json={"actor_id": "test_leader", "actor_type": "agent", "name": "Test Leader", "role": "Lead"},
            )
            if r.status_code != 200:
                self.report_fail("Open identify", f"Got status {r.status_code}")
                return
            token = r.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            self.report_pass("Open Mode accepted identify without secret (200)")

            # 2. Boards API: List & Create
            r = httpx.get(f"{self.api_url}/boards", headers=headers)
            if r.status_code == 200:
                self.report_pass("GET /boards fetched default boards")
            else:
                self.report_fail("GET /boards", f"Got status {r.status_code}")

            board_id = f"test-board-{int(time.time())}"
            r = httpx.post(
                f"{self.api_url}/boards",
                json={"board_id": board_id, "name": "E2E Live Board", "description": "Board for test"},
                headers=headers,
            )
            if r.status_code in (200, 201):
                self.report_pass(f"POST /boards created board '{board_id}'")
            else:
                self.report_fail("POST /boards", f"Got status {r.status_code}")
                return

            # Get board details to retrieve initialized columns
            r = httpx.get(f"{self.api_url}/boards/{board_id}", headers=headers)
            if r.status_code != 200 or not r.json().get("columns"):
                self.report_fail("GET /boards/{board_id}", f"Could not fetch columns: {r.status_code}")
                return
            columns = r.json()["columns"]
            first_col_id = columns[0]["column_id"]
            second_col_id = columns[1]["column_id"] if len(columns) > 1 else first_col_id

            # 3. Tickets API: Create ticket
            r = httpx.post(
                f"{self.api_url}/tickets",
                json={
                    "board_id": board_id,
                    "column_id": first_col_id,
                    "title": "E2E Live Feature Ticket",
                    "description": "Verification of ticket creation and lifecycle",
                    "priority": "high",
                },
                headers=headers,
            )
            if r.status_code == 201:
                ticket = r.json()
                ticket_id = ticket["ticket_id"]
                self.report_pass(f"POST /tickets created ticket #{ticket_id}")
            else:
                self.report_fail("POST /tickets", f"Got status {r.status_code}: {r.text}")
                return

            # 4. Tickets API: Transition status / Move
            r = httpx.patch(
                f"{self.api_url}/tickets/{ticket_id}/move",
                json={"target_column_id": second_col_id},
                headers=headers,
            )
            if r.status_code == 200:
                self.report_pass(f"PATCH /tickets/{ticket_id}/move moved ticket to next column")
            else:
                self.report_fail("Ticket move", f"Got status {r.status_code}: {r.text}")

            # 5. Tickets API: Add Comment
            r = httpx.post(
                f"{self.api_url}/tickets/{ticket_id}/comments",
                json={"content": "E2E automated verification comment"},
                headers=headers,
            )
            if r.status_code == 201:
                self.report_pass(f"POST /tickets/{ticket_id}/comments added comment")
            else:
                self.report_fail("POST ticket comment", f"Got status {r.status_code}")

        finally:
            self.stop_server(proc)

    # -------------------------------------------------------------------------
    # Test Suite 3: TypeScript SDK Compatibility
    # -------------------------------------------------------------------------
    def test_typescript_sdk(self) -> None:
        print("\n\033[1;34m[Suite 3/3] TypeScript SDK Integration (@ak5/sdk)\033[0m")
        sdk_dist = Path(__file__).resolve().parents[4] / "packages" / "sdk" / "dist" / "index.mjs"
        if not sdk_dist.is_file():
            self.log(f"SDK dist not found at {sdk_dist}. Run 'npm run build' in packages/sdk.", level="WARN")
            return

        extra_env = {"AK5_IDENTIFY_SECRET": self.shared_secret}
        proc = self.spawn_server(extra_env, use_web=False)
        try:
            if not wait_until_ready(self.api_url):
                self.report_fail("Server Startup (SDK test)", "Server failed to start")
                return

            node_script = f"""
            import {{ AK5Client }} from "{sdk_dist.as_posix()}";

            async function run() {{
                const baseUrl = "{self.api_url}";
                const secret = "{self.shared_secret}";

                // 1. With explicit option
                const c1 = new AK5Client({{ baseUrl, actorId: "sdk_1", actorType: "agent", identifySecret: secret }});
                const r1 = await c1.identify({{ actor_id: "sdk_1", actor_type: "agent", name: "SDK 1", role: "Dev" }});
                if (!r1.access_token) throw new Error("No token returned");

                // 2. With process.env
                process.env.AK5_IDENTIFY_SECRET = secret;
                const c2 = new AK5Client({{ baseUrl, actorId: "sdk_2", actorType: "agent" }});
                const r2 = await c2.identify({{ actor_id: "sdk_2", actor_type: "agent", name: "SDK 2", role: "Dev" }});
                if (!r2.access_token) throw new Error("No token returned from env identify");

                // 3. Without secret -> should fail
                delete process.env.AK5_IDENTIFY_SECRET;
                const c3 = new AK5Client({{ baseUrl, actorId: "sdk_3", actorType: "agent" }});
                let threw = false;
                try {{
                    await c3.identify({{ actor_id: "sdk_3", actor_type: "agent", name: "SDK 3", role: "Dev" }});
                }} catch (e) {{
                    threw = true;
                }}
                if (!threw) throw new Error("Expected identify without secret to fail");
            }}
            run().catch(e => {{ console.error(e); process.exit(1); }});
            """

            script_file = self.sandbox_dir / "sdk_smoke.mjs"
            script_file.write_text(node_script, encoding="utf-8")

            res = subprocess.run(["node", str(script_file)], capture_output=True, text=True)
            if res.returncode == 0:
                self.report_pass("SDK explicit & env secret identification and rejection")
            else:
                self.report_fail("SDK integration", f"Stderr: {res.stderr}\nStdout: {res.stdout}")

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
            if mode in ("all", "security"):
                self.test_security_gates()
            if mode in ("all", "core"):
                self.test_core_operations()
            if mode in ("all", "sdk"):
                self.test_typescript_sdk()
        finally:
            self.cleanup()

        elapsed = time.time() - start_time
        print("\n\033[1;36m============================================================\033[0m")
        print(f"Results: \033[0;32m{self.passed_tests} passed\033[0m, \033[0;31m{self.failed_tests} failed\033[0m in {elapsed:.2f}s")
        print("\033[1;36m============================================================\033[0m")
        return 0 if self.failed_tests == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AK5 Live Server Integration Tests")
    parser.add_argument("--port", type=int, default=None, help="Port to bind server (default: random free port)")
    parser.add_argument(
        "--mode",
        choices=["all", "security", "core", "sdk"],
        default="all",
        help="Test mode to execute",
    )
    parser.add_argument("--verbose", action="store_true", help="Print server stdout/stderr")
    parser.add_argument("--keep-artifacts", action="store_true", help="Keep sandbox directory after tests")
    args = parser.parse_args()

    runner = LiveTestRunner(port=args.port, verbose=args.verbose, keep_artifacts=args.keep_artifacts)
    sys.exit(runner.run_all(mode=args.mode))


if __name__ == "__main__":
    main()
