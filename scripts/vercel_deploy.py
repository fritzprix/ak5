"""Deploy AK5 to Vercel from a clean, Python-only staging directory (#179 section B).

Steps:

1. Require built dashboard assets (``scripts/build_web_ui.sh``) so ``/_next`` ships.
2. Build the ``ak5`` wheel and verify it carries ``web_ui/_next/static/chunks``
   exactly once (no duplicate paths).
3. Stage ``api/index.py`` + the wheel + ``requirements.txt`` + ``vercel.json``.
   The stage has no ``.git`` (avoids the commit-author ``BLOCKED`` state), no
   gitignore (Vercel CLI would drop ``_next``), and no ``package.json`` (no Node
   build in front of ``@vercel/python``).
4. ``vercel deploy`` and run ``scripts/smoke_hosted.py`` checks on the result.

Link the Vercel project once from the repo root (``vercel link``) or export
``VERCEL_ORG_ID`` + ``VERCEL_PROJECT_ID``. Project env vars (Neon, JWT, Blob)
live in Vercel, not in the checkout ``.env``.

Usage::

    uv run python scripts/vercel_deploy.py                      # production
    uv run python scripts/vercel_deploy.py --preview
    uv run python scripts/vercel_deploy.py --stage-only --stage-dir build/vercel
    uv run python scripts/vercel_deploy.py --smoke-url https://ak5-five.vercel.app
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter
from pathlib import Path

from smoke_hosted import CheckResult, run_checks

ROOT = Path(__file__).resolve().parents[1]
WEB_UI_CHUNKS = ROOT / "backend" / "src" / "ak5" / "server" / "web_ui" / "_next" / "static" / "chunks"
WHEEL_CHUNKS_PREFIX = "ak5/server/web_ui/_next/static/chunks/"
WHEEL_EXTRAS = "server,postgres,vercel"

VERCEL_JSON = {
    "builds": [{"src": "api/index.py", "use": "@vercel/python"}],
    "routes": [{"src": "/(.*)", "dest": "api/index.py"}],
}

_URL_RE = re.compile(r"https://[^\s]+\.vercel\.app\b")


class DeployError(RuntimeError):
    pass


def web_ui_built(chunks_dir: Path = WEB_UI_CHUNKS) -> bool:
    return chunks_dir.is_dir() and any(chunks_dir.glob("*.js"))


def verify_wheel(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        raise DeployError(f"{wheel.name} has duplicate paths: {duplicates[:5]}")
    if not any(name.startswith(WHEEL_CHUNKS_PREFIX) and name.endswith(".js") for name in names):
        raise DeployError(f"{wheel.name} has no {WHEEL_CHUNKS_PREFIX}*.js; dashboard would 404")


def build_wheel(out_dir: Path) -> Path:
    subprocess.run(["uv", "build", "backend", "--wheel", "--out-dir", str(out_dir)], cwd=ROOT, check=True)
    wheels = sorted(out_dir.glob("ak5-*.whl"))
    if len(wheels) != 1:
        raise DeployError(f"expected one ak5 wheel in {out_dir}, found {[w.name for w in wheels]}")
    return wheels[0]


def find_project_link(root: Path = ROOT) -> Path | None:
    link = root / ".vercel" / "project.json"
    return link if link.is_file() else None


def stage(stage_dir: Path, wheel: Path, project_link: Path | None) -> None:
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    (stage_dir / "api").mkdir(parents=True)
    shutil.copy2(ROOT / "api" / "index.py", stage_dir / "api" / "index.py")
    shutil.copy2(wheel, stage_dir / wheel.name)
    (stage_dir / "requirements.txt").write_text(f"./{wheel.name}[{WHEEL_EXTRAS}]\n", encoding="utf-8")
    (stage_dir / "vercel.json").write_text(json.dumps(VERCEL_JSON, indent=2) + "\n", encoding="utf-8")
    if project_link is not None:
        (stage_dir / ".vercel").mkdir()
        shutil.copy2(project_link, stage_dir / ".vercel" / "project.json")


def vercel_command() -> list[str]:
    vercel = shutil.which("vercel")
    if vercel:
        return [vercel]
    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", "vercel"]
    raise DeployError("Vercel CLI not found: install it (npm i -g vercel) or put npx on PATH")


def parse_deployment_url(stdout: str) -> str:
    urls = _URL_RE.findall(stdout)
    if not urls:
        raise DeployError(f"no deployment URL in vercel output: {stdout[-500:]!r}")
    return urls[-1]


def deploy(stage_dir: Path, *, prod: bool, token: str | None) -> str:
    cmd = [*vercel_command(), "deploy", "--yes"]
    if prod:
        cmd.append("--prod")
    if token:
        cmd.extend(["--token", token])
    proc = subprocess.run(cmd, cwd=stage_dir, stdout=subprocess.PIPE, text=True, check=False)
    if proc.returncode != 0:
        raise DeployError(f"vercel deploy exited {proc.returncode}")
    return parse_deployment_url(proc.stdout)


def smoke_with_retries(url: str, *, attempts: int, delay_seconds: float) -> list[CheckResult]:
    results: list[CheckResult] = []
    for attempt in range(1, attempts + 1):
        results = run_checks(url)
        if all(r.ok for r in results) or attempt == attempts:
            return results
        time.sleep(delay_seconds)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy AK5 to Vercel from a Python-only stage.")
    parser.add_argument("--preview", action="store_true", help="Preview deployment instead of production.")
    parser.add_argument("--stage-dir", type=Path, help="Keep the stage here (default: temp dir).")
    parser.add_argument("--stage-only", action="store_true", help="Build and stage, do not deploy.")
    parser.add_argument("--smoke-url", help="Origin to smoke-check (default: the deployment URL).")
    parser.add_argument("--no-smoke", action="store_true", help="Skip the post-deploy smoke check.")
    args = parser.parse_args(argv)

    if not web_ui_built():
        print(f"error: no dashboard chunks in {WEB_UI_CHUNKS}; run scripts/build_web_ui.sh first", file=sys.stderr)
        return 1

    project_link = find_project_link()
    if (
        not args.stage_only
        and project_link is None
        and not (os.environ.get("VERCEL_ORG_ID") and os.environ.get("VERCEL_PROJECT_ID"))
    ):
        print(
            "error: no Vercel project link: run `vercel link` in the repo root "
            "or set VERCEL_ORG_ID and VERCEL_PROJECT_ID",
            file=sys.stderr,
        )
        return 1

    with tempfile.TemporaryDirectory(prefix="ak5-vercel-") as tmp:
        tmp_path = Path(tmp)
        stage_dir = args.stage_dir.resolve() if args.stage_dir else tmp_path / "stage"
        try:
            wheel = build_wheel(tmp_path / "dist")
            verify_wheel(wheel)
            stage(stage_dir, wheel, project_link)
            print(f"==> Staged {wheel.name} in {stage_dir}")
            if args.stage_only:
                return 0
            url = deploy(stage_dir, prod=not args.preview, token=os.environ.get("VERCEL_TOKEN"))
        except (DeployError, subprocess.CalledProcessError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    print(f"==> Deployed {url}")
    if args.no_smoke:
        return 0
    smoke_url = args.smoke_url or url
    results = smoke_with_retries(smoke_url, attempts=3, delay_seconds=5.0)
    for r in results:
        print(f"{'PASS' if r.ok else 'FAIL'}  {r.name:<10} {r.detail}")
    if all(r.ok for r in results):
        return 0
    if any("HTTP 401" in r.detail for r in results):
        print(
            "hint: deployment URLs may sit behind Vercel Deployment Protection; "
            "pass --smoke-url with the public production alias",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
