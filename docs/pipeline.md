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
- **Reviewer** — a separate agent from the worker. Reviews open PRs on a 30-minute loop and **only comments**: no push, no merge, no branch edits.
- **Merger** — the user (or the worker) merges after a ✅ review.

## Worker conventions

- Branch `v2/<issue#>-<slug>`; PR base `feat/workflow-v2`; PR body contains `Closes #<issue>`.
- When the PR is opened, move the issue label `pipeline:implementing` → `pipeline:review`.
- Address every 🔁 item in the review comment by pushing to the same PR. The next reviewer tick re-reviews (de-duplicated by head SHA).

## Reviewer conventions

- Comment marker: `<!-- ak5-review sha=<head sha> -->`. Skip a PR whose head SHA already has a review.
- Verdict: ✅ Ready to merge / 🔁 Changes requested / ⚠️ Needs decision.
- Issue labels follow the verdict:
  - ✅ → `pipeline:integrating`
  - 🔁 → `pipeline:rework`; the 3rd 🔁 on the same PR → `pipeline:escalated`
  - ⚠️ → `pipeline:escalated` + `needs-decision`

## Review checklist

Run against the tree produced by merging the PR into its base:

1. `pytest` and `ruff` pass (backend); no weakened or deleted tests.
2. No `.env` / secret leakage in the diff.
3. Changed paths decide extra gates: frontend build/lint/tests, SDK tests.
4. Server starts on a **copy of a real v1 DB**, twice in a row (migration is idempotent).
5. Integration E2E passes.
6. Live-server CLI and SDK contract tests pass (the CI "Live Server Contracts" job).
7. Change stays within the issue's scope; out-of-scope edits are flagged.
8. Spec changes (`docs/spec/*`) match the code, and vice versa.

## Open items

- Runner automation (readiness from labels and `## 의존`, worktree + agent launch, label transitions, concurrency limit, file-ownership conflict detection) is not implemented; workers and reviewer run as manual loops (#38).
