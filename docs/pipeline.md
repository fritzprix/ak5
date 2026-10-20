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

## Status helper (read-only runner fragment)

```bash
python scripts/pipeline_status.py
python scripts/pipeline_status.py --json
python scripts/pipeline_pick.py          # next ready candidate + suggested commands
python scripts/pipeline_pick.py --json
```

Reads open issues + `pipeline:*` / `v2` labels, parses `## 의존`, and prints inferred readiness / one pick. Does **not** change labels or start agents. Skips epic (#22) and release (#36).

## Concurrency & ownership (manual rules until automation)

- At most **one** `pipeline:implementing` issue per worker agent.
- Prefer non-overlapping paths; if two PRs touch the same package root, serialize or escalate.
- Open-PR queue must not stay empty while `pipeline:ready` (or inferred-ready) issues exist.

## Open decisions (#38)

- [ ] Review owner: orchestrator vs dedicated reviewer agent (must stay ≠ implementer)
- [ ] `pipeline:integrating`: auto-merge after regression gate (#29) vs human confirm
- [ ] Runner cadence: manual tick vs scheduled
- [ ] Rework cap before `pipeline:escalated` (default in conventions: 3× 🔁)

## Remaining automation (#38)

- Full runner: `pipeline_pick` → worktree + agent → label transitions + issue comments
- File-ownership conflict detection beyond the manual rules above
