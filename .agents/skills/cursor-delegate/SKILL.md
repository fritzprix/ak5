---
name: cursor-delegate
description: >-
  Delegate code reviews, refactoring, bug fixes, or implementation tasks to the Cursor Agent CLI (`cursor agent` / `agent`).
  Use when delegating subtasks to @cursor-agent, executing headless/non-interactive code reviews or refactoring,
  subscribing to AK5 board events to trigger automated Cursor Agent runs, or syncing Cursor review artifacts back to AK5 tickets.
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
- **Autonomous Subtask Execution**: Fulfilling AK5 subtasks assigned to `@cursor-agent`.

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

# 2. Full AK5 Subtask Review (Review, post comment, attach report, and move to Review column)
python3 .agents/skills/cursor-delegate/scripts/cursor_delegate.py \
  --ticket-id TK-025 \
  --title "Code Review for Feature" \
  --mode review \
  --comment \
  --attach \
  --move-to col_review

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

### Workflow B: Full AK5 Subtask Lifecycle (Step-by-Step)

#### Step 1: Delegate Subtask on Board
```bash
ak5 delegate TK-020 \
  --title "Security and Injection Review for subscribe CLI" \
  --agent cursor-agent \
  --notes "Review backend/src/ak5/cli/commands/subscribe.py for command injection hazards."
# Output returns subtask ID, e.g. TK-025
```

#### Step 2: Mark Subtask as In-Progress
```bash
ak5 move TK-025 col_in_progress
```

#### Step 3: Run Cursor Agent & Automatically Sync
```bash
python3 .agents/skills/cursor-delegate/scripts/cursor_delegate.py \
  --ticket-id TK-025 \
  --title "Security and Injection Review for subscribe CLI" \
  --mode review \
  --comment \
  --attach \
  --move-to col_review
```

---

### Workflow C: Automated Event-Driven Pipeline (`ak5 subscribe`)

You can run an automated background observer that triggers Cursor Agent whenever a ticket is assigned to `@cursor-agent`:

```bash
# Launch real-time event listener on board
ak5 subscribe proj-core-engine \
  --event TICKET_DELEGATED \
  --for-agent cursor-agent \
  --exec 'python3 .agents/skills/cursor-delegate/scripts/cursor_delegate.py --ticket-id "$AK5_TICKET_ID" --title "$AK5_TITLE" --mode review --comment --attach --move-to col_review'
```

> [!IMPORTANT]
> **Shell Injection Prevention**: In `--exec` strings, always use shell environment variables (**`$AK5_TICKET_ID`**, **`$AK5_TITLE`**, **`$AK5_BOARD_ID`**) instead of curly-bracket string substitutions (`{title}`). AK5 exports these variables directly into the subprocess environment.

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
