#!/usr/bin/env python3
"""Detect overlapping ownership roots across open PRs (#38).

Read-only. Groups changed files into coarse roots (backend/, frontend/,
packages/<name>, docs/<dir>, …) and reports pairwise overlaps.

Usage:

  python scripts/pipeline_ownership.py
  python scripts/pipeline_ownership.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from pipeline_lib import gh_json, gh_run, ownership_roots  # noqa: E402


def _pr_files(number: int) -> list[str]:
    proc = gh_run(["pr", "diff", str(number), "--name-only"], allow_fail=True)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def collect_ownership() -> dict[str, Any]:
    prs = gh_json(
        ["pr", "list", "--state", "open", "--limit", "50", "--json", "number,title,headRefName"]
    )
    entries: list[dict[str, Any]] = []
    for pr in prs or []:
        files = _pr_files(int(pr["number"]))
        roots: set[str] = set()
        for path in files:
            roots |= ownership_roots(path)
        entries.append(
            {
                "number": pr["number"],
                "title": pr["title"],
                "head": pr.get("headRefName"),
                "roots": sorted(roots),
                "file_count": len(files),
            }
        )

    conflicts: list[dict[str, Any]] = []
    for i, a in enumerate(entries):
        for b in entries[i + 1 :]:
            overlap = sorted(set(a["roots"]) & set(b["roots"]))
            if overlap:
                conflicts.append({"a": a["number"], "b": b["number"], "roots": overlap})
    return {"prs": entries, "conflicts": conflicts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    data = collect_ownership()
    if args.json:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    if not data["prs"]:
        print("No open PRs.")
        return 0
    print(f"{'PR':<8} {'ROOTS':<40} TITLE")
    print("-" * 90)
    for pr in data["prs"]:
        roots = ",".join(pr["roots"]) or "-"
        print(f"#{pr['number']:<7} {roots:<40} {pr['title'][:40]}")
    print()
    if data["conflicts"]:
        print("CONFLICTS:")
        for c in data["conflicts"]:
            print(f"  #{c['a']} ∩ #{c['b']}: {', '.join(c['roots'])}")
        return 2
    print("No ownership-root overlaps among open PRs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
