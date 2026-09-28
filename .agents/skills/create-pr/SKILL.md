---
name: create-pr
description: >-
  Create a GitHub pull request from the current feature branch into the latest
  ``dev/MAJOR.MINOR.x`` integration branch. Use when the user asks to open/create
  a PR, ``/create-pr``, or merge feature work into ``dev/z.y.x``.
---

# Create PR → latest `dev/z.y.x`

Open a GitHub PR whose **base** is the newest remote `dev/MAJOR.MINOR.x` branch
(e.g. `dev/1.0.x`, `dev/1.1.x`) and whose **head** is the current feature branch.

Do **not** default to `master` / `main` for this workflow.

---

## Workflow

Copy and track:

```
Create-PR Progress:
- [ ] 1. Resolve latest dev base
- [ ] 2. Inspect branch state
- [ ] 3. Push head if needed
- [ ] 4. Create PR with gh
- [ ] 5. Return PR URL
```

### 1. Resolve latest `dev/z.y.x` base

From the repo root:

```bash
python3 .agents/skills/create-pr/scripts/resolve_dev_base.py
```

- Fetches `origin` (prune) and prints the highest `(MAJOR, MINOR)` among
  `origin/dev/MAJOR.MINOR.x` (example: `dev/1.0.x`).
- If the script exits non-zero, **stop** and tell the user no matching remote
  `dev/*` branch exists. Do not invent a base or fall back to `master`.

Optional override: if the user explicitly names a base (e.g. `dev/1.1.x`), use
that instead of the script output.

### 2. Inspect branch state (parallel)

Run together:

```bash
git status
git branch --show-current
git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || true
git log --oneline origin/<BASE>..HEAD
git diff --stat origin/<BASE>...HEAD
```

Rules:

- Must be on a **feature / work** branch (not `master`, `main`, or `dev/*.x`).
  If currently on `dev/*.x` or default branch, stop and ask for the feature
  branch name (or offer to create one).
- Uncommitted changes: ask whether to commit first; do **not** silently commit.
- Empty `origin/<BASE>..HEAD`: stop — nothing to PR.

### 3. Push head if needed

```bash
git push -u origin HEAD
```

Required when there is no upstream or local commits are ahead of the remote.

### 4. Create the PR

Use `gh pr create` with **explicit base**:

```bash
gh pr create --base <BASE> --head <CURRENT_BRANCH> --title "<title>" --body "$(cat <<'EOF'
## Summary
<1-3 bullets of what / why>

## Test plan
- [ ] <verification steps>

EOF
)"
```

Title / body:

- Prefer the repo’s recent commit / PR style (`fix:`, `feat:`, etc.).
- Summarize **all** commits on the feature branch vs base, not only the tip.
- If a PR for this head→base already exists, print its URL instead of creating
  a duplicate (`gh pr list --head <branch> --base <BASE>`).

### 5. Return the PR URL

Always end with the PR URL so the user can open it.

---

## Anti-patterns

- Targeting `master` / `main` unless the user explicitly overrides.
- Force-pushing or rewriting history.
- Creating an empty PR when `origin/<BASE>..HEAD` is empty.
- Opening a PR from `dev/*.x` into itself.

---

## Helper

`scripts/resolve_dev_base.py` — prints latest `dev/MAJOR.MINOR.x` short name.
