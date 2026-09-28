---
name: audit-local
description: >-
  Audit uncommitted local working-tree changes for bugs, security issues,
  regressions, and missing tests. Use when the user asks to audit/review local
  changes, dirty tree, uncommitted diff, ``/audit-local``, or wants a hands-on
  review instead of only launching Bugbot.
---

# Audit Local Changes

**You** review the local diff. Do not only spawn a subagent and wait — read the
diff, open the touched code, and report findings yourself.

Default scope: **uncommitted** changes (staged + unstaged + untracked that belong
to the change). If the user asks for branch / committed work vs `dev/*.x` or
`master`, widen the scope accordingly.

Do **not** fix findings or commit unless the user explicitly asks next.

---

## Workflow

```
Audit-Local Progress:
- [ ] 1. Collect diff
- [ ] 2. Read critical new/changed code
- [ ] 3. Check tests & call sites
- [ ] 4. Report findings table
```

### 1. Collect diff (parallel)

From the repo root:

```bash
git status
git diff          # unstaged
git diff --cached # staged
git diff --stat HEAD
```

Also list untracked files from `git status` and **read** new source/test files
that are part of the change (do not skip them because they are absent from
`git diff`).

If there is no staged/unstaged/untracked change: say so in one sentence and stop.

Empty or tiny formatting-only diffs: say so briefly; still flag risk if relevant.

### 2. Read critical code

Prioritize (in order):

1. New modules / public APIs / auth / DB / crypto / filesystem / shell exec
2. Control-flow changes (error handling, gates, migrations, defaults)
3. Routers/CLI entrypoints and clients that must stay in sync with API auth
4. Tests that claim to lock the new behavior

Open full files when the hunk is insufficient (imports, helpers, callers).

### 3. Check tests & call sites

For each behavior change ask:

- Is there a regression test that would fail if this were reverted?
- Did callers (CLI, MCP, frontend, Docker/env) get updated with the new contract?
- Can import-time / startup side effects break recovery paths (e.g. migrate)?
- Are defaults safe (auto-restore, destructive flags, open auth)?

Run a **focused** pytest subset only when it clearly validates the audit claim
and is cheap; do not block the report on a full suite.

### 4. Report

Start with 1–2 sentences: overall risk / theme of the change.

Then a markdown table, **severity highest first**:

| Severity | Location (file:line) | Finding |
|---|---|---|
| High/Medium/Low | `path:line` | Imperative, concrete, actionable |

Severity guide:

- **High** — data loss, auth bypass, RCE, broken recovery path, silent wrong DB
- **Medium** — incorrect behavior under realistic use, security hygiene, multi-worker footgun
- **Low** — dead code, docs/test gaps, minor robustness

Also include a short **Coverage gaps** bullet list when tests clearly miss a new
gate or failure mode.

If no issues: one line, e.g. `Audit: no blocking issues in local diff (N files).`

Optional: if the user also wants Bugbot, run it **after** (or in parallel with)
your own audit and merge unique findings into the same table — never replace
your own review with Bugbot alone.

---

## Anti-patterns

- Launching Bugbot / another agent and producing no direct analysis
- Reviewing only `git diff` hunks while ignoring new untracked files
- Vague findings (“looks fine”, “consider improving”) without location + reason
- Auto-fixing or committing as part of the audit
