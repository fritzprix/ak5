#!/usr/bin/env python3
"""Recovery rehearsal: export → empty DB import → /readyz (+ optional smoke).

operations.md §5 / #119. Exit 0 only when readyz reports ready after restore.

Usage (from repo root, with server extras):

  uv run --directory backend --extra server python ../scripts/recovery_rehearsal.py

Environment:
  REHEARSAL_BASE_URL   default http://127.0.0.1:8765 (spawned uvicorn)
  REHEARSAL_KEEP       if 1, leave temp dir and do not stop the server
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"


def _ak5_cmd() -> list[str]:
    """Prefer the installed ``ak5`` console script beside this interpreter."""
    scripts = Path(sys.executable).resolve().parent
    for name in ("ak5.exe", "ak5"):
        candidate = scripts / name
        if candidate.is_file():
            return [str(candidate)]
    raise SystemExit("ak5 console script not found next to sys.executable; use uv run --extra server")


def _http_json(url: str, timeout: float = 5.0) -> tuple[int, dict | list | str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            code = resp.getcode() or 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        code = exc.code
    try:
        return code, json.loads(body)
    except json.JSONDecodeError:
        return code, body


def _wait_ready(base: str, proc: subprocess.Popen[str], attempts: int = 40, delay: float = 0.25) -> dict:
    last: dict | str = {}
    for _ in range(attempts):
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise SystemExit(f"server exited early ({proc.returncode}):\n{out[-4000:]}")
        try:
            code, payload = _http_json(f"{base.rstrip('/')}/readyz")
            if code == 200 and isinstance(payload, dict):
                return payload
            last = payload if isinstance(payload, dict) else {"raw": payload, "status": code}
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(delay)
    raise SystemExit(f"readyz not ready: {last!r}")


def _plant_ticket_and_attachment(base: str, server_env: dict[str, str]) -> tuple[str, str]:
    """Create one ticket + attachment on the live source instance; return ids."""
    plant = r"""
import io, json, os
import httpx
from ak5.server.routers.auth import create_access_token

base = os.environ["REHEARSAL_BASE"].rstrip("/")
token = create_access_token("user_pm", "human")
headers = {"Authorization": f"Bearer {token}"}
with httpx.Client(base_url=base, timeout=60.0) as client:
    boards = client.get("/api/v1/boards", headers=headers)
    boards.raise_for_status()
    board_id = next(b["board_id"] for b in boards.json() if b.get("kind") != "inbox")
    detail = client.get(f"/api/v1/boards/{board_id}", headers=headers)
    detail.raise_for_status()
    column_id = detail.json()["columns"][0]["column_id"]
    t = client.post(
        "/api/v1/tickets",
        headers=headers,
        json={
            "title": "rehearsal restore fixture",
            "board_id": board_id,
            "column_id": column_id,
            "acceptance_criteria": ["restored"],
            "spec": "ops#119",
        },
    )
    t.raise_for_status()
    ticket_id = t.json()["ticket_id"]
    up = client.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        headers=headers,
        files={"file": ("rehearsal.txt", io.BytesIO(b"rehearsal-blob"), "text/plain")},
    )
    up.raise_for_status()
    attachment_id = up.json()["attachment_id"]
print(json.dumps({"ticket_id": ticket_id, "attachment_id": attachment_id}))
"""
    env = {**server_env, "REHEARSAL_BASE": base}
    proc = subprocess.run(
        [sys.executable, "-c", plant],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"plant fixture failed:\n{proc.stdout}\n{proc.stderr}")
    line = proc.stdout.strip().splitlines()[-1]
    data = json.loads(line)
    return str(data["ticket_id"]), str(data["attachment_id"])


def _verify_restored_skew(
    base: str,
    ticket_id: str,
    attachment_id: str,
    attachments_dir: Path,
    server_env: dict[str, str],
) -> None:
    """After import without blobs: list ticket OK, download → ATTACHMENT_MISSING."""
    for path in attachments_dir.rglob("*"):
        if path.is_file():
            path.unlink()
    verify = r"""
import os
import httpx
from ak5.server.routers.auth import create_access_token

base = os.environ["REHEARSAL_BASE"].rstrip("/")
ticket_id = os.environ["REHEARSAL_TICKET_ID"]
attachment_id = os.environ["REHEARSAL_ATTACHMENT_ID"]
token = create_access_token("user_pm", "human")
headers = {"Authorization": f"Bearer {token}"}
with httpx.Client(base_url=base, timeout=60.0) as client:
    boards = client.get("/api/v1/boards", headers=headers)
    boards.raise_for_status()
    assert any(b.get("board_id") for b in boards.json()), "no boards after restore"
    detail = client.get(f"/api/v1/tickets/{ticket_id}", headers=headers)
    detail.raise_for_status()
    atts = detail.json().get("attachments") or []
    assert any(a.get("attachment_id") == attachment_id for a in atts), atts
    dl = client.get(f"/api/v1/tickets/{ticket_id}/attachments/{attachment_id}", headers=headers)
    assert dl.status_code == 404, dl.text
    body = dl.json()
    assert body["error"]["code"] == "ATTACHMENT_MISSING", body
    assert "storage_path" not in body["error"].get("details", {}), body
print("skew_ok")
"""
    env = {
        **server_env,
        "REHEARSAL_BASE": base,
        "REHEARSAL_TICKET_ID": ticket_id,
        "REHEARSAL_ATTACHMENT_ID": attachment_id,
    }
    proc = subprocess.run(
        [sys.executable, "-c", verify],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or "skew_ok" not in proc.stdout:
        raise SystemExit(f"restore skew verify failed:\n{proc.stdout}\n{proc.stderr}")
def _stop(proc: subprocess.Popen[str]) -> str:
    if proc.poll() is not None:
        return proc.stdout.read() if proc.stdout else ""
    if sys.platform == "win32":
        proc.terminate()
    else:
        proc.send_signal(signal.SIGTERM)
    try:
        out, _ = proc.communicate(timeout=15)
        return out or ""
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate(timeout=5)
        return out or ""


def _serve(env: dict[str, str], port: int) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "ak5.server.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def main() -> int:
    keep = os.environ.get("REHEARSAL_KEEP", "").strip() in ("1", "true", "yes")
    base = os.environ.get("REHEARSAL_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
    port = int(base.rsplit(":", 1)[-1])

    work = Path(tempfile.mkdtemp(prefix="ak5-rehearsal-"))
    data_src = work / "data-src"
    data_restore = work / "data-restore"
    data_src.mkdir()
    data_restore.mkdir()
    src_db = data_src / "ak5.db"
    restore_db = data_restore / "ak5.db"
    dump = work / "dump.jsonl"
    attachments_src = data_src / "attachments"
    attachments_restore = data_restore / "attachments"
    attachments_src.mkdir()
    attachments_restore.mkdir()

    env = os.environ.copy()
    env["AK5_SKIP_DOTENV"] = "1"
    env["AK5_DEPLOY_PROFILE"] = "local"
    env["AK5_AUTO_MIGRATE"] = "1"
    env["AK5_BLOB_BACKEND"] = "fs"
    env["AK5_DATA_DIR"] = str(data_src)
    env["ATTACHMENTS_DIR"] = str(attachments_src)
    # Must match canonical store under AK5_DATA_DIR.
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{src_db.as_posix()}"
    # Stable secret so plant/verify subprocess JWTs match the servers.
    env["AK5_JWT_SECRET"] = "rehearsal-jwt-secret-not-for-production"

    print(f"work dir: {work}")

    ticket_id = ""
    attachment_id = ""
    ak5 = _ak5_cmd()
    upgrade = subprocess.run(
        [*ak5, "db", "upgrade", "--database-url", env["DATABASE_URL"]],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if upgrade.returncode != 0:
        print(upgrade.stdout, upgrade.stderr, file=sys.stderr)
        return upgrade.returncode or 1

    proc = _serve(env, port)
    try:
        _wait_ready(base, proc)
        code, meta = _http_json(f"{base}/meta")
        if code != 200:
            raise SystemExit(f"/meta failed: {code} {meta!r}")
        ticket_id, attachment_id = _plant_ticket_and_attachment(base, env)
        print(f"planted ticket={ticket_id} attachment={attachment_id}")
        print("source instance ready; exporting…")

        export = subprocess.run(
            [*ak5, "db", "export", str(dump), "--database-url", env["DATABASE_URL"]],
            cwd=BACKEND,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if export.returncode != 0:
            print(export.stdout, export.stderr, file=sys.stderr)
            return export.returncode or 1
        print(export.stdout.strip() or f"exported {dump}")
    finally:
        _stop(proc)

    restore_url = f"sqlite+aiosqlite:///{restore_db.as_posix()}"
    restore_env = env.copy()
    restore_env["AK5_DATA_DIR"] = str(data_restore)
    restore_env["ATTACHMENTS_DIR"] = str(attachments_restore)
    restore_env["DATABASE_URL"] = restore_url
    imp = subprocess.run(
        [*ak5, "db", "import", str(dump), "--to", restore_url],
        cwd=BACKEND,
        env=restore_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if imp.returncode != 0:
        print(imp.stdout, imp.stderr, file=sys.stderr)
        return imp.returncode or 1
    print(imp.stdout.strip() or "import ok")

    # Intentionally do not copy source blobs — restore skew must surface ATTACHMENT_MISSING.
    proc2 = _serve(restore_env, port)
    ok = False
    try:
        ready = _wait_ready(base, proc2)
        print("readyz:", json.dumps(ready, ensure_ascii=False))
        code, meta = _http_json(f"{base}/meta")
        if code != 200:
            raise SystemExit(f"post-restore /meta failed: {code} {meta!r}")
        print("smoke /meta ok")
        _verify_restored_skew(base, ticket_id, attachment_id, attachments_restore, restore_env)
        print("ATTACHMENT_MISSING skew verified")
        print("RECOVERY_REHEARSAL_OK")
        ok = True
        return 0
    finally:
        if not keep:
            log = _stop(proc2)
            if not ok and log:
                print(log[-4000:], file=sys.stderr)
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"kept work dir / server on {base}: {work}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
