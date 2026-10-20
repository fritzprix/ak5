#!/usr/bin/env python3
"""One pipeline runner tick (#38 / docs/pipeline.md).

Default is dry-run: status summary, ownership conflicts, next pick, suggested
commands. Opt-in ``--apply-claim`` moves the picked issue to
``pipeline:implementing`` and posts an issue comment. Does **not** start an
agent or create a worktree unless ``--checkout`` is passed (local branch only).

Usage:

  python scripts/pipeline_tick.py
  python scripts/pipeline_tick.py --json
  python scripts/pipeline_tick.py --apply-claim
  python scripts/pipeline_tick.py --apply-claim --checkout
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
_REPO = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from pipeline_lib import gh_run, load_sibling  # noqa: E402
from pipeline_ownership import collect_ownership  # noqa: E402


def _comment_body(pick: dict[str, Any], *, applied: bool) -> str:
    branch = pick["branch"]
    num = pick["number"]
    state = "claimed" if applied else "suggested"
    return (
        f"## pipeline tick ({state})\n\n"
        f"- branch: `{branch}`\n"
        f"- base: `feat/workflow-v2`\n"
        f"- next: implement → open PR with `Closes #{num}` → label "
        f"`pipeline:implementing` → `pipeline:review`\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--apply-claim",
        action="store_true",
        help="Set pipeline:implementing on the pick and comment on the issue",
    )
    parser.add_argument(
        "--checkout",
        action="store_true",
        help="Create/switch local branch from origin/feat/workflow-v2 (requires --apply-claim)",
    )
    args = parser.parse_args()
    if args.checkout and not args.apply_claim:
        raise SystemExit("--checkout requires --apply-claim")

    status = load_sibling("pipeline_status.py")
    pick_mod = load_sibling("pipeline_pick.py")
    rows = status.collect()
    ownership = collect_ownership()
    chosen = pick_mod.pick(rows)
    open_prs = pick_mod._open_pr_count()

    pick_payload: dict[str, Any] | None = None
    applied = False
    if chosen is not None:
        branch = pick_mod._slug(str(chosen["title"]), int(chosen["number"]))
        pick_payload = {
            "number": chosen["number"],
            "title": chosen["title"],
            "stage": chosen.get("stage"),
            "branch": branch,
        }
        if args.apply_claim:
            num = int(chosen["number"])
            gh_run(
                [
                    "issue",
                    "edit",
                    str(num),
                    "--add-label",
                    "pipeline:implementing",
                    "--remove-label",
                    "pipeline:ready",
                ],
                allow_fail=True,
            )
            # blocked → implementing also fine
            gh_run(
                [
                    "issue",
                    "edit",
                    str(num),
                    "--remove-label",
                    "pipeline:blocked",
                ],
                allow_fail=True,
            )
            body = _comment_body(pick_payload, applied=True)
            proc = gh_run(
                ["issue", "comment", str(num), "--body", body],
                allow_fail=True,
            )
            if proc.returncode != 0:
                raise SystemExit(f"failed to comment on #{num}: {proc.stderr}")
            applied = True
            if args.checkout:
                subprocess.run(
                    ["git", "fetch", "origin", "feat/workflow-v2"],
                    cwd=_REPO,
                    check=False,
                )
                subprocess.run(
                    ["git", "checkout", "-B", branch, "origin/feat/workflow-v2"],
                    cwd=_REPO,
                    check=True,
                )

    payload = {
        "open_prs": open_prs,
        "ownership": ownership,
        "pick": pick_payload,
        "applied_claim": applied,
        "implementing": [
            r["number"]
            for r in rows
            if r.get("stage") == "pipeline:implementing"
        ],
    }

    if args.json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(f"open_prs={open_prs}  implementing={payload['implementing']}")
        if ownership["conflicts"]:
            print("OWNERSHIP CONFLICTS:")
            for c in ownership["conflicts"]:
                print(f"  #{c['a']} ∩ #{c['b']}: {', '.join(c['roots'])}")
        else:
            print("ownership: no open-PR root overlaps")
        if pick_payload is None:
            print("pick: (none)")
        else:
            flag = "CLAIMED" if applied else "SUGGEST"
            print(f"pick [{flag}]: #{pick_payload['number']} {pick_payload['title']}")
            print(f"  branch: {pick_payload['branch']}")
            if not applied:
                print("  (pass --apply-claim to label + comment)")

    if ownership["conflicts"] and not args.apply_claim:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
