#!/usr/bin/env python3
"""AK5 Automated Release Helper.

Usage:
    python scripts/release.py patch [--push]
    python scripts/release.py minor [--push]
    python scripts/release.py major [--push]
    python scripts/release.py 1.1.0 [--push]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = ROOT / "backend" / "pyproject.toml"
FRONTEND_PKG_PATH = ROOT / "frontend" / "package.json"
SDK_PKG_PATH = ROOT / "packages" / "sdk" / "package.json"
LOCK_PATH = ROOT / "uv.lock"
BUILD_SCRIPT = ROOT / "scripts" / "build_web_ui.sh"
SMOKE_SCRIPT = ROOT / "scripts" / "smoke_uvx_wheel.sh"


def run_cmd(cmd: list[str] | str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    shell = isinstance(cmd, str)
    cwd_path = cwd or ROOT
    print(f"\033[0;36m▶ Running:\033[0m {cmd if shell else ' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=cwd_path, shell=shell, text=True)
    if check and res.returncode != 0:
        print(f"\033[0;31m✗ Command failed with code {res.returncode}\033[0m", file=sys.stderr)
        sys.exit(res.returncode)
    return res


def latest_wheel() -> Path:
    wheels = sorted((ROOT / "dist").glob("ak5-*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not wheels:
        raise FileNotFoundError("No dist/ak5-*.whl found after build")
    return wheels[0]


def smoke_test_wheel() -> None:
    """Hard gate: wheel must boot in an isolated env like uvx (declared deps only)."""
    if not SMOKE_SCRIPT.is_file():
        print("\033[0;31m✗ scripts/smoke_uvx_wheel.sh missing — refusing to release\033[0m", file=sys.stderr)
        sys.exit(1)
    wheel = latest_wheel()
    run_cmd(["bash", str(SMOKE_SCRIPT), str(wheel)])


def get_current_version() -> str:
    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    m = re.search(r'version\s*=\s*"([^"]+)"', content)
    if not m:
        raise ValueError("Could not find version in backend/pyproject.toml")
    return m.group(1)


def calculate_next_version(current: str, bump_type: str) -> str:
    if bump_type in {"patch", "minor", "major"}:
        parts = current.split(".")
        if len(parts) != 3:
            raise ValueError(f"Current version '{current}' is not valid semver (X.Y.Z)")
        major, minor, patch = map(int, parts)
        if bump_type == "major":
            return f"{major + 1}.0.0"
        elif bump_type == "minor":
            return f"{major}.{minor + 1}.0"
        elif bump_type == "patch":
            return f"{major}.{minor}.{patch + 1}"
    # Explicit version passed (e.g. '1.1.0')
    if re.match(r"^\d+\.\d+\.\d+.*$", bump_type):
        return bump_type
    raise ValueError(f"Invalid bump type or version: {bump_type}. Choose patch, minor, major, or explicit X.Y.Z")


def update_file_version(path: Path, pattern: str, replacement: str) -> None:
    content = path.read_text(encoding="utf-8")
    new_content, count = re.subn(pattern, replacement, content, count=1)
    if count == 0:
        raise ValueError(f"Pattern '{pattern}' not matched in {path}")
    path.write_text(new_content, encoding="utf-8")
    print(f"\033[0;32m✓ Updated:\033[0m {path.relative_to(ROOT)}")


def update_json_version(path: Path, new_version: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = new_version
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"\033[0;32m✓ Updated:\033[0m {path.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="AK5 Release Automation Script")
    parser.add_argument("bump", choices=["patch", "minor", "major"], help="Bump type (patch, minor, major)")
    parser.add_argument("--push", action="store_true", help="Automatically commit, tag, and push to origin")
    parser.add_argument("--skip-tests", action="store_true", help="Skip running ruff and pytest")
    parser.add_argument("--dry-run", action="store_true", help="Show calculated versions without modifying files")
    args = parser.parse_args()

    current_ver = get_current_version()
    next_ver = calculate_next_version(current_ver, args.bump)

    print(f"\n\033[1mAK5 Release Automation\033[0m")
    print(f"Current version: \033[1;33m{current_ver}\033[0m")
    print(f"Target version:  \033[1;32m{next_ver}\033[0m (bump: {args.bump})\n")

    if args.dry_run:
        print("[Dry run] No changes applied.")
        return

    # 1. Pre-flight tests & linting
    if not args.skip_tests:
        print("\033[1m[1/7] Running pre-flight linting and tests...\033[0m")
        run_cmd(["uv", "run", "ruff", "check", "backend"])
        run_cmd(["uv", "run", "pytest", "backend/tests"])
    else:
        print("\033[1m[1/7] Skipping pre-flight tests (--skip-tests)\033[0m")

    # 2. Build embedded Web UI
    print("\n\033[1m[2/7] Building embedded Web UI assets...\033[0m")
    if BUILD_SCRIPT.is_file():
        run_cmd(["bash", str(BUILD_SCRIPT)])
    else:
        print("\033[0;33m⚠️ build_web_ui.sh not found, skipping web UI compilation\033[0m")

    # 3. Update version numbers across files
    print("\n\033[1m[3/7] Updating versions across packages...\033[0m")
    update_file_version(
        PYPROJECT_PATH,
        r'(version\s*=\s*)"[^"]+"',
        rf'\g<1>"{next_ver}"',
    )
    if FRONTEND_PKG_PATH.is_file():
        update_json_version(FRONTEND_PKG_PATH, next_ver)
    if SDK_PKG_PATH.is_file():
        update_json_version(SDK_PKG_PATH, next_ver)

    # 4. Sync lockfile
    print("\n\033[1m[4/7] Updating uv.lock...\033[0m")
    run_cmd(["uv", "lock"])

    # 5. Build distribution package
    print("\n\033[1m[5/7] Building distribution packages (wheel + sdist)...\033[0m")
    run_cmd(["rm", "-rf", "dist"])
    run_cmd(["uv", "build", "backend", "--out-dir", "dist"])

    # 6. Isolated wheel smoke (uvx contract) — MUST pass before tag/push
    print("\n\033[1m[6/7] Isolated wheel smoke test (uvx-equivalent)...\033[0m")
    smoke_test_wheel()

    # 7. Commit, tag & push
    tag_name = f"v{next_ver}"
    if args.push:
        print(f"\n\033[1m[7/7] Committing, tagging ({tag_name}), and pushing to origin...\033[0m")
        run_cmd(["git", "add", "backend/pyproject.toml", "frontend/package.json", "packages/sdk/package.json", "uv.lock"])
        run_cmd(["git", "commit", "-m", f"release: {tag_name} — bump version to {next_ver}"])
        run_cmd(["git", "tag", "-a", tag_name, "-m", f"{tag_name} release"])
        run_cmd(["git", "push", "origin", "master"])
        run_cmd(["git", "push", "origin", tag_name])
        print(f"\n\033[1;32m🎉 Successfully pushed {tag_name} to origin!\033[0m")
        print(f"Check GitHub Actions release workflow: \033[0;36mgh run list --workflow=release.yml\033[0m")
    else:
        print(f"\n\033[1;33m[7/7] Changes applied locally. Not pushed (--push not set).\033[0m")
        print(f"To finish release manually:")
        print(f"  git add backend/pyproject.toml frontend/package.json packages/sdk/package.json uv.lock")
        print(f"  git commit -m 'release: {tag_name} — bump version to {next_ver}'")
        print(f"  git tag -a {tag_name} -m '{tag_name} release'")
        print(f"  git push origin master && git push origin {tag_name}")


if __name__ == "__main__":
    main()
