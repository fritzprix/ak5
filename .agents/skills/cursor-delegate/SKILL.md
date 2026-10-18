---
name: cursor-delegate
description: >-
  Delegate code reviews, refactoring, bug fixes, or implementation tasks to the Cursor Agent CLI (`cursor agent` / `agent`).
  Use when running Cursor as an AK5 worker (claim → run → submit), executing headless/non-interactive code
  reviews or refactoring, waking Cursor from AK5 WORK_AVAILABLE events, or syncing Cursor output back to AK5 tickets.
---

# Cursor Agent Delegation Skill

Use this skill when you need to delegate code review, architecture analysis, bug fixing, or test generation tasks to the **Cursor Agent CLI** (`cursor agent` or `agent`) within the AK5 Kanban ecosystem.

---

## 1. Overview & Architecture

This skill bridges **Antigravity** (Lead Orchestrator), the **AK5 Kanban System** (State & Task Tracker), and the **Cursor Agent CLI** (Autonomous Worker / Reviewer).

```
                     ┌───────────────────────────────┐
                     │ Antigravity (Orchestrator)    │
                     └───────────────┬───────────────┘
                                     │ 1. Subtask Delegation / Plan
                                     ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │ AK5 Kanban Board (Tickets, Dependencies, Attachments, SSE Events) │
 └───────────────┬───────────────────────────────────▲───────────────┘
                 │ 2. Event stream or CLI trigger    │ 4. Comments & Reports
                 ▼                                   │    Attachments & Status
 ┌───────────────────────────────────────────────────┴───────────────┐
 │ Cursor Agent CLI (`agent -p` / `cursor_delegate.py`)              │
 │ • Headless execution (Claude 3.7 / Sonnet / GPT-5)                │
 │ • Local workspace & Git diff inspections                          │
 │ • Isolated Git Worktrees (-w) for safe code modifications         │
 └───────────────────────────────────────────────────────────────────┘
```

### When to Delegate to Cursor Agent:
- **Second-Opinion Code Reviews**: Independent verification of newly implemented features or PRs.
- **Security & Shell Injection Audits**: Specialized scrutiny of shell subprocess runners and API payloads.
- **Isolated Refactoring**: Refactoring code in an isolated git worktree without disturbing current workspace files.
- **Autonomous Ticket Execution**: Acting as an AK5 **worker** profile that pulls backlog tickets (`ak5 next` / `ak5 ticket claim`) and submits deliverables.

---

## 2. Pre-flight & Environment Verification

Before dispatching tasks, verify that the Cursor Agent CLI is installed and authenticated:

```bash
# Check CLI binary resolution
which agent || which cursor

# Verify login status
cursor agent status
# Expected output: ✓ Logged in as <user@domain.com>

# List available models (optional)
agent --list-models
```

> [!NOTE]
> The binary can typically be invoked as either `agent` or `cursor agent` (located at `~/.local/bin/agent`). Both support the same non-interactive flags.

---

---

## 3. Recommended Execution Methods

There are two ways to invoke Cursor Agent. **Always prefer Method 1 (`cursor_delegate.py`)** to avoid CLI quoting issues and empty-output quirks.

### Method 1 (Primary & Strongly Recommended): Companion Runner Script

The companion runner at [`.agents/skills/cursor-delegate/scripts/cursor_delegate.py`](file:///home/fritzprix/my_works/ak5/.agents/skills/cursor-delegate/scripts/cursor_delegate.py) auto-detects the binary, constructs hardened read-only prompts, captures stdout safely, and handles AK5 ticket synchronization.

```bash
# 1. Quick Ad-hoc Code Review (Review uncommitted working tree diff without a ticket)
python3 .agents/skills/cursor-delegate/scripts/cursor_delegate.py \
  --title "Review uncommitted working tree changes" \
  --mode review

# 2. Full AK5 worker run (claim, review, comment, attach report, submit → agent_review)
AK5_PROFILE=cursor-1 python3 .agents/skills/cursor-delegate/scripts/cursor_delegate.py \
  --ticket-id TK-025 \
  --title "Code Review for Feature" \
  --mode review \
  --claim --comment --attach --submit

# 3. Autonomous Fix in an Isolated Git Worktree (-w)
python3 .agents/skills/cursor-delegate/scripts/cursor_delegate.py \
  --ticket-id TK-026 \
  --title "Fix shlex.quote in command renderer" \
  --mode fix \
  --worktree \
  --comment
```

---

### Method 2 (Fallback / Direct CLI): Low-Level Invocation

> [!CAUTION]
> **CLI Quirk with `--mode plan` in `-p` mode**:
> In non-interactive print mode (`-p` / `--print`), passing `--mode plan` can cause the Cursor Agent CLI to initialize an interactive planning session and exit with **empty output (0 bytes)**.
> **DO NOT** use `agent -p --mode plan`. Instead, omit `--mode plan` and explicitly instruct read-only behavior inside the prompt string:

```bash
# ✅ CORRECT Direct Review Pattern (Instruct read-only in the prompt):
agent -p "
You are an autonomous code reviewer.
Review the latest git diff (or uncommitted changes) in the workspace.
DO NOT edit or modify any files.
Provide:
1. Executive Summary
2. Potential Bugs & Security/Edge Cases
3. Actionable Recommendations
" > /tmp/cursor_review.md

# ❌ INCORRECT (May exit with empty output!):
# agent -p --mode plan "Review git diff" > /tmp/cursor_review.md
```

| Goal | Direct CLI Pattern | Notes |
|---|---|---|
| **Read-Only Review** | `agent -p "Review diff... DO NOT edit files."` | Do NOT use `--mode plan` with `-p`. |
| **Active Fix / Code Edit** | `agent -p --force "<PROMPT>"` | Allows file edits and test execution. |
| **Isolated Worktree Fix** | `agent -p -w --force "<PROMPT>"` | Runs in `~/.cursor/worktrees/<repo>/`. |
| **Model Selection** | `agent -p --model claude-3.7-sonnet "<PROMPT>"` | Overrides default model. |

---

## 4. AK5 Task Delegation Workflows

### Workflow A: Quick Ad-hoc Workspace Review (No Ticket Required)

When you simply need a second-opinion review on current uncommitted changes or recent commits:

```bash
python3 .agents/skills/cursor-delegate/scripts/cursor_delegate.py \
  --title "Review current working tree diff" \
  --mode review
```

---

### Workflow B: Cursor as an AK5 worker (pull model)

Cursor runs under its own **worker profile**. A human registers it once and approves the code in the browser:

```bash
ak5 login --agent cursor-1 --role worker --caps code-review,py
export AK5_PROFILE=cursor-1
```

Tickets reach the board backlog through triage (`ak5 ticket route`) or a direct create
(`ak5 ticket create --board <BOARD> --spec … --criteria …`, or `ak5 delegate <PARENT> --title …` for a child ticket).
There is no push assignment: Cursor pulls the work.

```bash
# 1. Find work (exit 6 = nothing yet)
TICKET=$(ak5 --json next --board proj-core-engine --caps code-review --wait 25 | jq -r .ticket_id)

# 2. Claim, run Cursor, comment, attach the report, submit the deliverable
python3 .agents/skills/cursor-delegate/scripts/cursor_delegate.py \
  --ticket-id "$TICKET" \
  --mode review \
  --claim --comment --attach --submit
```

- `--claim` stops with the CLI exit code if the ticket is taken (3), not claimable by a worker (4), or blocked (5).
- `--submit` sends a deliverable (`summary` = first line of the Cursor output, `artifacts` = the attached report)
  and moves the ticket to `agent_review`. A gatekeeper reviews it next; a human approves it last.
- `--move-to` is gone: columns are not a workflow step in v2.

---

### Workflow C: Wake Cursor on new work (`ak5 subscribe add`)

Register a **local daemon subscriber** once. The daemon runs `--exec` whenever the board has claimable work:

```bash
ak5 subscribe add proj-core-engine \
  --profile cursor-1 \
  --events WORK_AVAILABLE \
  --exec 'AK5_PROFILE=cursor-1 python3 .agents/skills/cursor-delegate/scripts/cursor_delegate.py --ticket-id "$AK5_TICKET_ID" --title "$AK5_TITLE" --mode review --claim --comment --attach --submit'
ak5 subscribe ls
```

> [!IMPORTANT]
> **Hook contract**: `--exec` is run **literally** by the daemon. Event data arrives as **`$AK5_TICKET_ID`**, **`$AK5_TITLE`**, **`$AK5_SUMMARY`**, **`$AK5_BOARD_ID`**, or stdin JSON. Curly-brace tokens like `{title}` are **not** expanded. The hook does not inherit your shell's `AK5_PROFILE`, so set it inside `--exec`.
>
> `WORK_AVAILABLE` also fires for stages a worker cannot claim (for example `agent_review`); `--claim` then exits 4 and the run stops without calling Cursor.

---

## 6. Prompt Crafting Best Practices

When crafting prompts for Cursor Agent, follow these guidelines for optimal results:

1. **Target Specific Diffs or Commits**:
   - Provide concrete references: `Review commit f5a1515 (git show f5a1515)` or `Review uncommitted changes in backend/src/`.
2. **Constrain Output Format**:
   - Request structured sections: `1. Summary`, `2. Security & Edge Cases`, `3. Quality & Tests`, `4. Actionable Steps`.
3. **Specify Tool Access Level**:
   - For review / audit: use `--mode plan` (guarantees zero unintended file edits).
   - For bug fixes: use `--force` or `--yolo` (permits editing files and executing tests).
4. **Isolate Major Refactors**:
   - Always pass `-w` (`--worktree`) when delegating multi-file changes or risky refactors.
