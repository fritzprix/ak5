# Pipeline: issue-driven development (v2)

Development of ak5 v2 follows the same shape as the product workflow: a ticket (GitHub issue) flows through stages, a separate reviewer gates it, and a human has the last word. The `pipeline:*` labels on an issue are the source of truth for progress (tracked in #22; decisions in #38).

## Stages (labels)

`pipeline:blocked` → `pipeline:ready` → `pipeline:implementing` → `pipeline:review` → (`pipeline:rework` → `pipeline:implementing`) → `pipeline:integrating` → closed / `pipeline:escalated`

| Label | Meaning |
|---|---|
| `pipeline:blocked` | `## 의존` issues are not finished yet |
| `pipeline:ready` | dependencies done, can be picked up |
| `pipeline:implementing` | a worker is on it |
| `pipeline:review` | PR is open, waiting for / in review |
| `pipeline:rework` | reviewer requested changes |
| `pipeline:integrating` | review passed, waiting for merge |
| `pipeline:escalated` | needs a human (also gets `needs-decision`) |

## Roles

- **Worker** — implements `pipeline:ready` tickets, one at a time.
- **Reviewer** — a separate agent from the worker. Reviews open PRs on a loop and **only comments**: no push, no merge, no branch edits.
- **Merger** — human (or worker after explicit ✅ / APPROVE gatekeeper verdict). **No auto-merge.**

## Decisions (#38) — locked defaults

| Topic | Decision |
|---|---|
| Review owner | Dedicated reviewer / gatekeeper agent ≠ implementer |
| Integrating / merge | Human or post-APPROVE merge; not CI-auto |
| Runner cadence | Manual tick (`pipeline_tick.py`); optional outer scheduler |
| Rework cap | 3× 🔁 on the same PR → `pipeline:escalated` |

## Worker conventions

- Branch `v2/<issue#>-<slug>`; PR base `feat/workflow-v2`; PR body contains `Closes #<issue>` (or `Refs` for partial).
- When the PR is opened, move the issue label `pipeline:implementing` → `pipeline:review`.
- Address every REQUEST CHANGES item by pushing to the same PR.

## Reviewer conventions

- Comment marker: `<!-- ak5-review sha=<head sha> -->`. Skip a PR whose head SHA already has a review.
- Verdict: APPROVE / REQUEST CHANGES / HOLD / needs-decision.
- Issue labels follow the verdict:
  - APPROVE → `pipeline:integrating`
  - REQUEST CHANGES → `pipeline:rework`; the 3rd on the same PR → `pipeline:escalated`
  - needs-decision → `pipeline:escalated` + `needs-decision`

## Review checklist

Run against the tree produced by merging the PR into its base (gatekeeper / reviewer agent):

1. `pytest` and `ruff` pass (backend); no weakened or deleted tests to make CI green.
2. No `.env` / secret leakage in the diff (tokens, private keys, connection strings).
3. Changed paths decide extra gates: frontend build/lint/tests, SDK tests / typecheck.
4. Server starts on a **copy of a real v1 DB**, twice in a row (migration is idempotent).
5. Integration E2E passes when the change touches workflow / auth / events.
6. Live-server CLI and SDK contract tests pass (CI job **Live Server Contracts**).
7. Change stays within the issue's scope; out-of-scope edits are flagged or split.
8. Spec changes (`docs/spec/*`) match the code, and vice versa.
9. Hosted / ops docs (`docs/deploy/*`, `docs/ops/*`) match fail-closed startup gates when touched.

## Runner scripts

```bash
python scripts/pipeline_status.py          # board + ## 의존 readiness
python scripts/pipeline_pick.py            # next candidate + suggested commands
python scripts/pipeline_ownership.py       # open-PR ownership-root overlaps (exit 2 if any)
python scripts/pipeline_tick.py            # one dry-run tick (status + ownership + pick)
python scripts/pipeline_tick.py --apply-claim           # label implementing + issue comment
python scripts/pipeline_tick.py --apply-claim --checkout  # also create local branch
```

All default to read-only except `--apply-claim` / `--checkout`. Skips epic (#22) and release (#36). Agent/worktree launch stays outside these scripts (Cursor / orchestrator).

## Concurrency & ownership

- At most **one** `pipeline:implementing` issue per worker agent.
- Prefer non-overlapping ownership roots; `pipeline_ownership.py` flags overlaps among open PRs.
- Open-PR queue must not stay empty while inferred-ready issues exist.

## Remaining automation (#38)

- Optional: wire `pipeline_tick` → Cursor Agent CLI / git worktree launcher
- Optional: auto label `pipeline:review` when a PR with `Closes #N` opens
