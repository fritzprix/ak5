"""Shared helpers for pipeline_* scripts (#38)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


def gh_json(args: list[str], *, allow_fail: bool = False) -> Any:
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    if proc.returncode != 0:
        if allow_fail:
            return None
        err = (proc.stderr or "").strip()
        raise SystemExit(err or f"gh failed: {' '.join(args)}")
    out = proc.stdout or ""
    return json.loads(out) if out.strip() else None


def gh_run(args: list[str], *, allow_fail: bool = False) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    if proc.returncode != 0 and not allow_fail:
        err = (proc.stderr or "").strip()
        raise SystemExit(err or f"gh failed: {' '.join(args)}")
    return proc


def load_sibling(name: str):
    import importlib.util

    path = Path(__file__).resolve().parent / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ownership_roots(path: str) -> set[str]:
    """Map a changed file to coarse ownership roots for conflict detection."""
    normalized = path.replace("\\", "/").lstrip("./")
    if not normalized or normalized.endswith("/"):
        return set()
    parts = normalized.split("/")
    if parts[0] in {"backend", "frontend", "packages", "docs", "scripts", "api", ".github", ".agents"}:
        if parts[0] == "packages" and len(parts) > 1:
            return {f"packages/{parts[1]}"}
        if parts[0] == "docs" and len(parts) > 1:
            # docs/pipeline.md → docs; docs/ops/x.md → docs/ops
            return {"docs"} if len(parts) == 2 else {f"docs/{parts[1]}"}
        return {parts[0]}
    if parts[0] == "docker-compose.postgres.yml" or parts[0].startswith("docker-compose"):
        return {"docker"}
    return {parts[0]}
