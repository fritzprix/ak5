#!/usr/bin/env python3
"""Opt-in launch helper: pick → worktree → Cursor Agent CLI (#171).

Default is dry-run (print commands only). Never auto-merges.

Usage:

  python scripts/pipeline_launch.py
  python scripts/pipeline_launch.py --json
  python scripts/pipeline_launch.py --worktree          # git worktree add
  python scripts/pipeline_launch.py --worktree --launch-agent
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
_REPO = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from pipeline_lib import load_sibling  # noqa: E402


def _find_agent() -> str | None:
    for name in ("agent", "cursor"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _agent_argv(agent_bin: str, prompt: str, worktree: Path) -> list[str]:
    base = Path(agent_bin).name.lower()
    if base in {"cursor", "cursor.exe"}:
        return [agent_bin, "agent", "-p", prompt, "--workspace", str(worktree)]
    # `agent` CLI
    return [agent_bin, "-p", prompt, "--workspace", str(worktree)]


def _build_prompt(number: int, title: str) -> str:
    return (
        f"Implement GitHub issue #{number}: {title}\n"
        f"Base branch: feat/workflow-v2. Open a PR with Closes #{number}.\n"
        "Follow docs/pipeline.md worker conventions. Do not merge."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--worktree",
        action="store_true",
        help="Create ../ak5-wt-<issue> worktree on the pick branch",
    )
    parser.add_argument(
        "--launch-agent",
        action="store_true",
        help="Run Cursor Agent CLI in the worktree (requires --worktree)",
    )
    parser.add_argument(
        "--issue",
        type=int,
        default=None,
        help="Force issue number instead of pipeline_pick",
    )
    args = parser.parse_args()
    if args.launch_agent and not args.worktree:
        raise SystemExit("--launch-agent requires --worktree")

    status = load_sibling("pipeline_status.py")
    pick_mod = load_sibling("pipeline_pick.py")
    rows = status.collect()
    if args.issue is not None:
        chosen = next((r for r in rows if int(r["number"]) == args.issue), None)
        if chosen is None:
            # Allow forced issue even if not in status list
            chosen = {
                "number": args.issue,
                "title": f"issue-{args.issue}",
                "stage": None,
            }
    else:
        chosen = pick_mod.pick(rows)

    if chosen is None:
        payload = {"pick": None, "reason": "no ready candidate"}
        if args.json:
            json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            print("No pick.")
        return 1

    number = int(chosen["number"])
    title = str(chosen["title"])
    branch = pick_mod._slug(title, number)
    wt = (_REPO.parent / f"ak5-wt-{number}").resolve()
    prompt = _build_prompt(number, title)
    agent_bin = _find_agent()
    agent_cmd = (
        _agent_argv(agent_bin, prompt, wt) if agent_bin else ["<agent-not-found>", "-p", prompt]
    )

    plan: dict[str, Any] = {
        "number": number,
        "title": title,
        "branch": branch,
        "worktree": str(wt),
        "fetch": ["git", "fetch", "origin", "feat/workflow-v2"],
        "branch_cmd": [
            "git",
            "worktree",
            "add",
            "-b",
            branch,
            str(wt),
            "origin/feat/workflow-v2",
        ],
        "agent": agent_cmd,
        "agent_found": agent_bin is not None,
    }

    if args.worktree:
        if wt.exists():
            raise SystemExit(f"worktree path already exists: {wt}")
        subprocess.run(["git", "fetch", "origin", "feat/workflow-v2"], cwd=_REPO, check=True)
        subprocess.run(plan["branch_cmd"], cwd=_REPO, check=True)
        plan["worktree_created"] = True
        if args.launch_agent:
            if not agent_bin:
                raise SystemExit("neither `agent` nor `cursor` found on PATH")
            env = {**os.environ, "AK5_PIPELINE_ISSUE": str(number)}
            proc = subprocess.run(agent_cmd, cwd=wt, env=env, check=False)
            plan["agent_exit"] = proc.returncode

    if args.json:
        json.dump(plan, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(f"PICK #{number}  {title}")
        print(f"branch={branch}")
        print(f"worktree={wt}")
        print("commands:")
        print(f"  $ {' '.join(plan['fetch'])}")
        print(f"  $ {' '.join(plan['branch_cmd'])}")
        print(f"  $ {' '.join(agent_cmd)}")
        if not args.worktree:
            print("(dry-run; pass --worktree / --launch-agent to execute)")
        elif args.launch_agent:
            print(f"agent_exit={plan.get('agent_exit')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
