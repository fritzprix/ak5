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

## 3. Cursor Agent CLI Execution Reference

Cursor Agent supports headless, non-interactive execution via the `-p` (`--print`) flag.

### Common Execution Modes

| Goal | Command Pattern | Description |
|---|---|---|
| **Read-Only Review** | `agent -p --mode plan "<PROMPT>"` | Analyzes codebase/diff without modifying files. |
| **Q&A / Assessment** | `agent -p --mode ask "<PROMPT>"` | Explanations and advisory queries (read-only). |
| **Active Fix / Code Edit** | `agent -p --force "<PROMPT>"` | Applies code modifications and tool calls without prompting. |
| **Isolated Worktree** | `agent -p -w --force "<PROMPT>"` | Creates and runs in a separate git worktree at `~/.cursor/worktrees/`. |
| **Model Selection** | `agent -p --model claude-3.7-sonnet "<PROMPT>"` | Explicitly specifies the model to utilize. |

---

## 4. AK5 Task Delegation Workflows

### Workflow A: Manual / Direct Delegation (Step-by-Step)

#### Step 1: Create or Delegate an AK5 Subtask
```bash
# Delegate code review subtask to @cursor-agent
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

#### Step 3: Run Cursor Agent Review
```bash
agent -p --mode plan "
Review the latest git diff (git diff HEAD~1) for ticket TK-025.
Focus on backend/src/ak5/cli/commands/subscribe.py.
Evaluate shell safety, quoting, and error handling.
Provide a 3-bullet executive summary followed by prioritized recommendations.
" > /tmp/cursor_review.md
```

#### Step 4: Sync Findings back to AK5 Ticket
```bash
# 1. Post findings as a comment
ak5 comment TK-025 --file /tmp/cursor_review.md

# 2. Attach the full review report as a deliverable
ak5 attach TK-025 /tmp/cursor_review.md --desc "Cursor Agent Code Review Report"

# 3. Move subtask to Review or Done
ak5 move TK-025 col_review
```

---

### Workflow B: Automated Event-Driven Pipeline (`ak5 subscribe`)

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

## 5. Companion Runner Script (`cursor_delegate.py`)

AK5 provides a turnkey companion runner located at:
[`.agents/skills/cursor-delegate/scripts/cursor_delegate.py`](file:///home/fritzprix/my_works/ak5/.agents/skills/cursor-delegate/scripts/cursor_delegate.py)

### Capabilities
- Auto-detects `agent` or `cursor agent` executable.
- Crafts tailored, structured prompts based on mode (`review`, `plan`, `fix`, `custom`).
- Captures output and optionally:
  - Posts concise summary comment via `ak5 comment` (`--comment`).
  - Attaches markdown report file via `ak5 attach` (`--attach`).
  - Transitions ticket to target column via `ak5 move` (`--move-to <col_id>`).
  - Supports `--dry-run` to inspect commands before running.

### Command Usage Examples:

```bash
# 1. Preview prompt and command without running
python3 .agents/skills/cursor-delegate/scripts/cursor_delegate.py \
  --dry-run \
  --ticket-id TK-025 \
  --title "Code Review for Subscription Feature" \
  --mode review

# 2. Execute Code Review, comment, attach report, and move to Review column
python3 .agents/skills/cursor-delegate/scripts/cursor_delegate.py \
  --ticket-id TK-025 \
  --title "Code Review for Subscription Feature" \
  --mode review \
  --comment \
  --attach \
  --move-to col_review

# 3. Autonomous Bugfix in an isolated Git Worktree
python3 .agents/skills/cursor-delegate/scripts/cursor_delegate.py \
  --ticket-id TK-026 \
  --title "Fix shlex.quote in render_command_string" \
  --mode fix \
  --worktree \
  --comment
```

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
