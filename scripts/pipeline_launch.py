#!/usr/bin/env python3
"""Opt-in launch helper: pick → worktree → Cursor Agent CLI (#171).

Default is dry-run (print commands only). Never auto-merges.
Worker agents must not merge PRs — humans / gatekeepers only.

Usage:

  python scripts/pipeline_launch.py
  python scripts/pipeline_launch.py --json
  python scripts/pipeline_launch.py --worktree          # git worktree add (+ claim)
  python scripts/pipeline_launch.py --worktree --launch-agent
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
_REPO = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from pipeline_lib import gh_json, gh_run, load_sibling  # noqa: E402

_SKIP_NUMBERS = frozenset({22, 36})
_PROMPT_FILE_NAME = ".ak5-pipeline-agent-prompt.txt"


def _find_agent() -> str | None:
    for name in ("agent", "cursor-agent", "cursor"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _agent_identity(agent_bin: str) -> str:
    """Return logical tool name (stem), ignoring Windows .CMD/.BAT suffixes."""
    return Path(agent_bin).stem.lower()


def _agent_argv(agent_bin: str, prompt_file: Path, worktree: Path) -> list[str]:
    """Build argv.

    Prompt body lives in ``prompt_file`` only. ``-p`` gets a fixed short string so
    Windows ``.cmd``/``.bat`` wrappers never see issue-title metacharacters.
    """
    identity = _agent_identity(agent_bin)
    short = f"Read {_PROMPT_FILE_NAME} in the workspace and implement that issue."
    if identity == "cursor":
        return [agent_bin, "agent", "-p", short, "--workspace", str(worktree)]
    return [agent_bin, "-p", short, "--workspace", str(worktree)]


def _build_prompt(number: int, title: str) -> str:
    return (
        f"Implement GitHub issue #{number}: {title}. "
        f"Base branch: feat/workflow-v2. Open a PR with Closes #{number}. "
        "Follow docs/pipeline.md worker conventions. Do not merge."
    )


def _fmt_cmd(argv: list[str]) -> str:
    return shlex.join(argv)


def _post_issue_comment(number: int, body: str) -> bool:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=".md",
        delete=False,
    ) as handle:
        handle.write(body)
        path = handle.name
    try:
        proc = gh_run(
            ["issue", "comment", str(number), "--body-file", path],
            allow_fail=True,
        )
        if proc.returncode != 0:
            print(
                f"warning: failed to comment on #{number}: {(proc.stderr or '').strip()}",
                file=sys.stderr,
            )
            return False
        return True
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _result_comment(plan: dict[str, Any], *, mode: str) -> str:
    agent_exit = plan.get("agent_exit")
    agent_line = _fmt_cmd([str(x) for x in plan["agent"]])
    status = "ok"
    if plan.get("error"):
        status = f"error: {plan['error']}"
    elif mode == "worktree+agent" and agent_exit not in (0, None):
        status = f"agent_exit={agent_exit}"
    return (
        f"## pipeline_launch ({mode})\n\n"
        f"- status: `{status}`\n"
        f"- branch: `{plan['branch']}`\n"
        f"- worktree: `{plan['worktree']}`\n"
        f"- claimed: `{plan.get('claimed', False)}`\n"
        f"- agent: `{agent_line}`\n"
        f"- agent_found: `{plan['agent_found']}`\n"
        f"- prompt_file: `{plan.get('prompt_file', '')}`\n"
    )


def _resolve_issue(number: int, *, force: bool) -> dict[str, Any]:
    data = gh_json(
        ["issue", "view", str(number), "--json", "number,title,state,labels"],
        allow_fail=True,
    )
    if data is None:
        raise SystemExit(f"issue #{number} not found or unreadable")
    state = str(data.get("state", "")).upper()
    if state == "CLOSED":
        raise SystemExit(f"issue #{number} is CLOSED")
    labels = [lab["name"] for lab in data.get("labels") or []]
    title = str(data.get("title") or f"issue-{number}")
    if number in _SKIP_NUMBERS and not force:
        raise SystemExit(f"issue #{number} is skip-listed (epic/release); pass --force")
    if "pipeline:implementing" in labels and not force:
        print(
            f"warning: #{number} is already pipeline:implementing "
            "(continuing; pass --force to silence)",
            file=sys.stderr,
        )
    return {"number": number, "title": title, "stage": None, "labels": labels}


def _local_branch_exists(branch: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _worktree_add_cmd(branch: str, wt: Path) -> list[str]:
    if _local_branch_exists(branch):
        return ["git", "worktree", "add", str(wt), branch]
    return [
        "git",
        "worktree",
        "add",
        "-b",
        branch,
        str(wt),
        "origin/feat/workflow-v2",
    ]


def _apply_claim(number: int) -> bool:
    """Match pipeline_tick --apply-claim label transitions."""
    ok = True
    for labels in (
        ["--add-label", "pipeline:implementing", "--remove-label", "pipeline:ready"],
        ["--remove-label", "pipeline:blocked"],
        ["--remove-label", "pipeline:rework"],
    ):
        proc = gh_run(["issue", "edit", str(number), *labels], allow_fail=True)
        if proc.returncode != 0:
            ok = False
    return ok


def _cleanup_failed_worktree(wt: Path, branch: str, *, created_branch: bool) -> None:
    if wt.exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt)],
            cwd=_REPO,
            check=False,
            capture_output=True,
        )
    if created_branch and _local_branch_exists(branch):
        # Only delete branch if it is not checked out elsewhere.
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=_REPO,
            check=False,
            capture_output=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--worktree",
        action="store_true",
        help="Create ../ak5-wt-<issue> worktree (claims issue like pipeline_tick)",
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow skip-listed or already-implementing issues",
    )
    parser.add_argument(
        "--no-claim",
        action="store_true",
        help="With --worktree, do not change pipeline labels",
    )
    parser.add_argument(
        "--no-comment",
        action="store_true",
        help="Skip posting an issue comment after --worktree / --launch-agent",
    )
    args = parser.parse_args()
    if args.launch_agent and not args.worktree:
        raise SystemExit("--launch-agent requires --worktree")

    status = load_sibling("pipeline_status.py")
    pick_mod = load_sibling("pipeline_pick.py")
    rows = status.collect()

    if args.issue is not None:
        chosen = _resolve_issue(args.issue, force=args.force)
    else:
        chosen = pick_mod.pick(rows)
        if chosen is None:
            # Allow continuing an already-claimed issue when pick is empty:
            implementing = [
                r for r in rows if r.get("stage") == "pipeline:implementing"
            ]
            if len(implementing) == 1:
                chosen = implementing[0]
                print(
                    f"note: pick empty; using sole implementing #{chosen['number']}",
                    file=sys.stderr,
                )
            else:
                payload = {"pick": None, "reason": "no ready candidate"}
                if args.json:
                    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
                    print()
                else:
                    print("No pick.")
                return 1

    number = int(chosen["number"])
    title = str(chosen["title"])
    if number in _SKIP_NUMBERS and not args.force:
        raise SystemExit(f"issue #{number} is skip-listed (epic/release); pass --force")

    branch = pick_mod._slug(title, number)
    wt = (_REPO.parent / f"ak5-wt-{number}").resolve()
    prompt = _build_prompt(number, title)
    prompt_file = wt / _PROMPT_FILE_NAME

    # Resolve agent before mutating git state.
    agent_bin = _find_agent()
    if args.launch_agent and not agent_bin:
        raise SystemExit("neither `agent`, `cursor-agent`, nor `cursor` found on PATH")

    # Placeholder argv for dry-run (prompt file path may not exist yet).
    dry_prompt_path = Path(_PROMPT_FILE_NAME)
    agent_cmd = (
        _agent_argv(agent_bin, dry_prompt_path if not wt.exists() else prompt_file, wt)
        if agent_bin
        else ["<agent-not-found>", "-p", str(dry_prompt_path)]
    )

    branch_cmd = _worktree_add_cmd(branch, wt)
    created_new_branch = "-b" in branch_cmd
    plan: dict[str, Any] = {
        "number": number,
        "title": title,
        "branch": branch,
        "worktree": str(wt),
        "prompt": prompt,
        "prompt_file": str(prompt_file),
        "fetch": ["git", "fetch", "origin", "feat/workflow-v2"],
        "branch_cmd": branch_cmd,
        "agent": agent_cmd,
        "agent_found": agent_bin is not None,
        "mode": "dry-run",
        "claimed": False,
    }

    exit_code = 0
    if args.worktree:
        if wt.exists():
            raise SystemExit(f"worktree path already exists: {wt}")
        try:
            if not args.no_claim:
                plan["claimed"] = _apply_claim(number)
            subprocess.run(
                ["git", "fetch", "origin", "feat/workflow-v2"],
                cwd=_REPO,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                branch_cmd,
                cwd=_REPO,
                check=True,
                capture_output=True,
                text=True,
            )
            prompt_file.write_text(prompt + "\n", encoding="utf-8")
            plan["worktree_created"] = True
            plan["mode"] = "worktree"
            if agent_bin:
                agent_cmd = _agent_argv(agent_bin, prompt_file, wt)
                plan["agent"] = agent_cmd
            if args.launch_agent:
                assert agent_bin is not None
                env = {**os.environ, "AK5_PIPELINE_ISSUE": str(number)}
                proc = subprocess.run(
                    agent_cmd,
                    cwd=wt,
                    env=env,
                    check=False,
                    stdout=sys.stderr if args.json else None,
                    stderr=None,
                )
                plan["agent_exit"] = proc.returncode
                plan["mode"] = "worktree+agent"
                if proc.returncode != 0:
                    exit_code = int(proc.returncode)
        except (OSError, subprocess.CalledProcessError) as exc:
            plan["error"] = str(exc)
            plan["mode"] = "failed"
            _cleanup_failed_worktree(wt, branch, created_branch=created_new_branch)
            exit_code = 1
            if not args.no_comment:
                plan["commented"] = _post_issue_comment(
                    number, _result_comment(plan, mode="failed")
                )
            if args.json:
                json.dump(plan, sys.stdout, ensure_ascii=False, indent=2)
                print()
            else:
                print(f"error: {exc}", file=sys.stderr)
            return exit_code

        if not args.no_comment:
            plan["commented"] = _post_issue_comment(
                number, _result_comment(plan, mode=str(plan["mode"]))
            )

    if args.json:
        json.dump(plan, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(f"PICK #{number}  {title}")
        print(f"branch={branch}")
        print(f"worktree={wt}")
        print(f"prompt_file={prompt_file}")
        print(f"prompt={prompt!r}")
        print("commands:")
        print(f"  $ {_fmt_cmd(plan['fetch'])}")
        print(f"  $ {_fmt_cmd(plan['branch_cmd'])}")
        print(f"  $ {_fmt_cmd([str(x) for x in plan['agent']])}")
        if not args.worktree:
            print("(dry-run; pass --worktree / --launch-agent to execute)")
        else:
            if args.launch_agent:
                print(f"agent_exit={plan.get('agent_exit')}")
            if plan.get("commented"):
                print(f"commented on #{number}")
            if plan.get("claimed"):
                print(f"claimed #{number} → pipeline:implementing")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
