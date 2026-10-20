#!/usr/bin/env python3
"""Suggest the next pipeline:ready issue for a worker (#38 / docs/pipeline.md).

Read-only: prints one pick (or none). Does not change labels or start agents.

Skips epic/release meta issues (#22 / #36 by default) and anything still
blocked by open ``## 의존``. Prefers explicit ``pipeline:ready``, then
inferred-ready tickets whose label is stale.

Usage (repo root):

  python scripts/pipeline_pick.py
  python scripts/pipeline_pick.py --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_SKIP_NUMBERS = frozenset({22, 36})  # epic / human release
_SKIP_TITLE = re.compile(r"\[v2/(?:release|Epic)\]|needs-decision", re.IGNORECASE)


def _load_status_mod():
    path = Path(__file__).resolve().parent / "pipeline_status.py"
    spec = importlib.util.spec_from_file_location("pipeline_status", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _open_pr_count() -> int:
    proc = subprocess.run(
        ["gh", "pr", "list", "--state", "open", "--json", "number"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        return -1
    try:
        return len(json.loads(proc.stdout or "[]"))
    except json.JSONDecodeError:
        return -1


def _slug(title: str, number: int) -> str:
    raw = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
    raw = re.sub(r"-+", "-", raw)[:40].strip("-")
    return f"v2/{number}-{raw or 'work'}"


def pick(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        num = int(row["number"])
        if num in _SKIP_NUMBERS:
            continue
        if _SKIP_TITLE.search(str(row.get("title") or "")):
            continue
        if row.get("inferred_readiness") != "ready":
            continue
        stage = row.get("stage")
        if stage in ("pipeline:implementing", "pipeline:review", "pipeline:integrating", "pipeline:rework"):
            continue
        if stage not in (None, "pipeline:ready", "pipeline:blocked"):
            continue
        # Prefer explicit ready over inferred-only / stale blocked.
        rank = 0 if stage == "pipeline:ready" else 1
        candidates.append({**row, "_rank": rank})
    if not candidates:
        return None
    candidates.sort(key=lambda r: (r["_rank"], r["number"]))
    chosen = candidates[0]
    chosen.pop("_rank", None)
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    status = _load_status_mod()
    rows = status.collect()
    chosen = pick(rows)
    prs = _open_pr_count()

    if chosen is None:
        payload = {"pick": None, "open_prs": prs, "reason": "no ready candidate"}
        if args.json:
            json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            print("No pick: no inferred-ready issue outside implementing/review/release/epic.")
            if prs == 0:
                print("warning: open PR queue is empty", file=sys.stderr)
        return 0

    branch = _slug(str(chosen["title"]), int(chosen["number"]))
    payload = {
        "pick": {
            "number": chosen["number"],
            "title": chosen["title"],
            "stage": chosen.get("stage"),
            "branch": branch,
            "suggested": [
                f"gh issue edit {chosen['number']} --add-label pipeline:implementing --remove-label pipeline:ready",
                f"git fetch origin feat/workflow-v2 && git checkout -B {branch} origin/feat/workflow-v2",
            ],
        },
        "open_prs": prs,
    }
    if args.json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        p = payload["pick"]
        print(f"PICK #{p['number']}  {p['title']}")
        print(f"stage={p['stage'] or '(none)'}  branch={p['branch']}  open_prs={prs}")
        for cmd in p["suggested"]:
            print(f"  $ {cmd}")
        if prs == 0:
            print("note: open PR queue is empty — start this pick to refill.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
