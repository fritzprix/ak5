#!/usr/bin/env python3
"""Read-only GitHub pipeline status for ak5 v2 issues (#38 / docs/pipeline.md).

Lists open issues with ``pipeline:*`` labels, parses ``## 의존`` for blocker
issue numbers, and prints which tickets look ready vs blocked.

Requires ``gh`` authenticated to the repo. Does not mutate labels.

Usage (repo root):

  python scripts/pipeline_status.py
  python scripts/pipeline_status.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any

PIPELINE_LABELS = (
    "pipeline:blocked",
    "pipeline:ready",
    "pipeline:implementing",
    "pipeline:review",
    "pipeline:rework",
    "pipeline:integrating",
    "pipeline:escalated",
)

_LIST_LIMIT = 100

_DEP_SECTION = re.compile(
    r"##\s*의존\s*\n(.*?)(?=\n##\s|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_ISSUE_REF = re.compile(r"#(\d+)\b")

# Module-level cache: issue number → state ("OPEN" / "CLOSED" / "MISSING")
_issue_state_cache: dict[int, str] = {}


def _gh_json(args: list[str], *, allow_fail: bool = False) -> Any:
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
        err = (proc.stderr or "").strip()
        if allow_fail:
            return None
        raise SystemExit(err or f"gh failed: {' '.join(args)}")
    out = proc.stdout or ""
    return json.loads(out) if out.strip() else None


def _pipeline_label(labels: list[str]) -> str | None:
    for name in PIPELINE_LABELS:
        if name in labels:
            return name
    return None


def _dep_numbers(body: str) -> list[int]:
    match = _DEP_SECTION.search(body or "")
    if not match:
        return []
    return sorted({int(n) for n in _ISSUE_REF.findall(match.group(1))})


def _issue_states(numbers: list[int]) -> dict[int, str]:
    """Resolve states with a process-wide cache; skip missing numbers with a warning."""
    out: dict[int, str] = {}
    for num in numbers:
        if num in _issue_state_cache:
            out[num] = _issue_state_cache[num]
            continue
        data = _gh_json(["issue", "view", str(num), "--json", "state"], allow_fail=True)
        if data is None or "state" not in data:
            print(f"warning: skipping dependency #{num} (missing or unreadable)", file=sys.stderr)
            _issue_state_cache[num] = "MISSING"
            out[num] = "MISSING"
            continue
        state = str(data["state"]).upper()
        _issue_state_cache[num] = state
        out[num] = state
    return out


def collect() -> list[dict[str, Any]]:
    issues = _gh_json(
        [
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            str(_LIST_LIMIT),
            "--json",
            "number,title,labels,body",
        ]
    )
    if isinstance(issues, list) and len(issues) >= _LIST_LIMIT:
        print(
            f"warning: open issue list hit --limit {_LIST_LIMIT}; "
            "results may be truncated",
            file=sys.stderr,
        )
    rows: list[dict[str, Any]] = []
    for issue in issues or []:
        labels = [lab["name"] for lab in issue.get("labels") or []]
        stage = _pipeline_label(labels)
        if stage is None and "v2" not in labels:
            continue
        deps = _dep_numbers(issue.get("body") or "")
        dep_states = _issue_states(deps)
        open_deps = [n for n, st in dep_states.items() if st == "OPEN"]
        inferred = "blocked" if open_deps else "ready"
        rows.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                "stage": stage,
                "labels": labels,
                "deps": deps,
                "open_deps": open_deps,
                "inferred_readiness": inferred,
            }
        )
    rows.sort(key=lambda r: (r["stage"] or "zz", r["number"]))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    args = parser.parse_args()
    rows = collect()
    if args.json:
        json.dump(rows, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    if not rows:
        print("No open v2 / pipeline-labeled issues.")
        return 0

    print(f"{'ISSUE':<8} {'STAGE':<22} {'INFER':<8} {'OPEN_DEPS':<16} TITLE")
    print("-" * 100)
    for row in rows:
        deps = ",".join(f"#{n}" for n in row["open_deps"]) or "-"
        stage = row["stage"] or "(none)"
        print(
            f"#{row['number']:<7} {stage:<22} {row['inferred_readiness']:<8} {deps:<16} {row['title'][:60]}"
        )
    print()
    print("INFER=ready means ## 의존 blockers are closed (label may still say blocked).")
    print("This tool is read-only; label transitions stay with the worker/reviewer loops.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
