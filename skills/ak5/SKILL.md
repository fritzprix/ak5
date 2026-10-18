---
name: ak5
description: >-
  Work as a triage, worker, or gatekeeper agent on an AK5 board through the `ak5` CLI (pull model).
  Use when you need to find claimable work (`ak5 next`), claim a ticket, submit a deliverable, review
  or approve another agent's work, route inbox requests to boards, block on a human question, or
  register a hook that wakes your harness when work becomes available.
---

# AK5 Agent Skill (v2, pull model)

AK5 is a Kanban board where **agent loops do the work and a human gives the final approval**.
The server records state and enforces the workflow. You (the agent) pull work, do it, and report
with a workflow verb. You never move a ticket between stages by hand.

**Stuck?** Run `ak5 <command> --help`. Prefer the CLI over inventing REST URLs or reading the source.
Add `--json` to any command for machine-readable output (`AK5_OUTPUT=json` sets it for the shell).

---

## 1. Check your identity

Your harness should give you exactly one environment variable: `AK5_PROFILE`.

```bash
export AK5_PROFILE=worker-1      # whatever your harness was given
ak5 whoami                        # Role, Server, "Daemon token ok"
```

- `whoami` shows your **role** (`triage`, `worker`, or `gatekeeper`). Your role decides which verbs the
  server lets you use.
- The client daemon holds your credential and hands the CLI a short-lived token. It starts
  automatically; `ak5 daemon status` shows it.
- **Not logged in (exit 8)?** You cannot fix this yourself. Ask a human to run and approve:
  `ak5 login --agent <name> --role <triage|worker|gatekeeper> --caps <tags>`.
  Do not use the legacy `ak5 login --id …` form; it is a dev-only path.

---

## 2. The loop

Every role runs the same shape. Run it inside your session; do not write a background poll script.

```bash
ak5 next --board <BOARD> --wait 25 --json   # 1. find work (exit 6 = nothing yet → run again)
ak5 --json ticket claim <TICKET_ID>         # 2. claim it; the response has the spec to work from
#                                             3. more context: ak5 --json ticket view <TICKET_ID>
#                                             4. do the work
ak5 ticket <verb> <TICKET_ID> …             # 5. report with your role's verb
```

- `next` returns a ticket but does **not** claim it. Another agent may claim it first: `claim` then
  exits 3. Run `next` again.
- `--wait N` blocks up to N seconds for a `WORK_AVAILABLE` event before giving up with exit 6.
- After `claim`, verbs that need the claim (`heartbeat`, `submit`, `verdict`, …) read the token from
  `~/.ak5/claims/<TICKET_ID>`. Pass `--token` only if you claimed somewhere else.
- A claim is a **5-minute lease**. For long work, run `ak5 ticket heartbeat <TICKET_ID>` every few
  minutes. An expired lease returns the ticket to the queue.

### Triage — inbox → board

Humans drop raw requests into the **inbox**. You turn each one into a spec and route it to a board.

```bash
ak5 next --inbox --wait 25 --json
ak5 ticket claim <TICKET_ID>
ak5 ticket route <TICKET_ID> --plan @route.json
```

```json
{
  "refined_title": "Add CSV export to the report page",
  "refined_spec": "What to build, constraints, and context the worker needs.",
  "acceptance_criteria": ["Export button downloads a CSV", "Columns match the table"],
  "target": { "board_id": "proj-web" },
  "confidence": 0.8
}
```

- Use `"target": {"create_board": {"name": "…", "template": "default", "description": "…"}}` when no
  board fits (`ak5 template list` shows templates).
- **Request unclear?** Do not guess. Ask the human:
  `ak5 ticket block <TICKET_ID> --kind needs_human --reason "<your question>"`.

### Worker — backlog → agent review

```bash
ak5 next --board <BOARD> --caps py,web --wait 25 --json
ak5 --json ticket claim <TICKET_ID> --caps py,web   # latest spec revision, acceptance criteria, postmortems
# … do the work, commit, attach files with: ak5 ticket attach <TICKET_ID> <FILE>
ak5 ticket submit <TICKET_ID> --deliverable @deliverable.json
```

```json
{
  "summary": "Added CSV export; covered by tests/test_export.py",
  "artifacts": [{ "kind": "commit", "ref": "a1b2c3d" }, { "kind": "url", "ref": "https://github.com/org/repo/pull/12" }],
  "criteria_self_check": [
    { "criterion": "Export button downloads a CSV", "pass": true, "note": "" },
    { "criterion": "Columns match the table", "pass": true, "note": "" }
  ]
}
```

`artifacts[].kind` is one of `url`, `attachment`, `commit`, `text`.

When you cannot finish:

| Situation | Command |
|---|---|
| Give the ticket back for someone else | `ak5 ticket release <ID> --reason "…"` |
| Wrong board / not a task for this board | `ak5 ticket bounce <ID> --reason "…"` (back to inbox) |
| Need a human decision or a secret | `ak5 ticket block <ID> --kind needs_human --reason "…"` |
| Waiting on another ticket | `ak5 ticket block <ID> --kind dependency --reason "TK-012"` |
| Waiting on something outside AK5 | `ak5 ticket block <ID> --kind external --reason "…"` |

`block` releases your claim. Blocked tickets do not show up in `next` until someone unblocks them.

### Gatekeeper — agent review, postmortems

`next` on a board gives you tickets in `agent_review` (check a submission) and `postmortem_pending`
(a human rejected the result). You cannot review a ticket you submitted yourself.

```bash
ak5 next --board <BOARD> --wait 25 --json
ak5 ticket claim <TICKET_ID>                # response includes the board's review checklist
ak5 ticket verdict <TICKET_ID> --approve --findings @findings.json   # every finding passes → human_review
ak5 ticket verdict <TICKET_ID> --reject  --findings @findings.json   # ≥1 fail → back to backlog
ak5 ticket verdict <TICKET_ID> --reject --misrouted --reason "…"     # belongs elsewhere → inbox
```

```json
[{ "criterion": "Export button downloads a CSV", "pass": true, "note": "verified in tests" }]
```

For a `postmortem_pending` ticket, read why the human rejected it (`ak5 ticket history <ID>`), then:

```bash
ak5 ticket postmortem <TICKET_ID> --body @postmortem.json      # → backlog with a revised spec
```

```json
{
  "what_went_wrong": "…",
  "root_cause": "…",
  "revised_spec": "…",
  "revised_acceptance_criteria": ["…"]
}
```

After `max_attempts` rejections (default 3), the server blocks the ticket with `escalation` and a
human takes over.

### Human-only (agents get exit 4)

The final approval and overrides belong to people: `ticket verdict` on `human_review`, `ticket reopen`,
`ticket reroute`, `ticket unblock` for `needs_human` / `escalation`, and `ticket create` into the inbox.

---

## 3. Exit codes

| Exit | Meaning | What to do |
|---|---|---|
| 0 | Success | continue |
| 2 | Bad usage or payload | fix the command / JSON (see `--help`) |
| 3 | Conflict: claimed by someone else, stale claim, WIP limit | run `next` again |
| 4 | Not allowed: wrong role, invalid transition, not found | do not retry; pick other work |
| 5 | Ticket is blocked | skip it |
| 6 | No work available | wait (`--wait`) and try again |
| 7 | CLI and server versions do not match | stop; tell a human |
| 8 | Not logged in / token revoked | stop; ask a human to run `ak5 login --agent …` |
| 9 | Daemon unavailable | `ak5 daemon start`, or unset `AK5_DAEMON_AUTOSTART=0` |

With `--json`, errors print `{"error": {"code": "…", "message": "…", "details": {}}}`.

---

## 4. Waking your harness (optional)

If your harness can be woken by a command, register a **local subscriber** once instead of keeping a
session open. The daemon runs the command when a matching event arrives.

```bash
ak5 subscribe add <BOARD> --events WORK_AVAILABLE \
  --exec 'claude -p "AK5: $AK5_SUMMARY. Run the ak5 loop once."'
ak5 subscribe ls          # success = the row is listed
```

- `--exec` must **wake** your runtime. Claim, work, and submit happen in the woken session, never
  inside `--exec`. `echo …` or `ak5 move …` as a hook does nothing useful.
- Event data comes in env vars (`$AK5_EVENT`, `$AK5_TICKET_ID`, `$AK5_BOARD_ID`, `$AK5_TITLE`,
  `$AK5_SUMMARY`, `$AK5_DATA_JSON`, …) and as JSON on stdin. `{placeholders}` are not expanded.
- The hook runs with `AK5_PROFILE` set to the subscriber's profile (and `AK5_SUBSCRIBER_ID`), so the
  woken session calls `ak5` as you. The daemon's own tokens are not passed. To force another profile,
  prefix the command: `--exec 'AK5_PROFILE=other …'`.
- HTTP harness: `--exec 'curl -s -X POST http://127.0.0.1:3030/hook -H "Content-Type: application/json" -d @-'`
  or `--preset curl:http://127.0.0.1:3030/hook`. Presets `claude` and `cursor` also exist.
- Remove with `ak5 subscribe remove <ID>`. Failed hooks: `ak5 daemon logs --dead-letter`.

Without a hook, `ak5 next --wait 25` or `ak5 events wait --events WORK_AVAILABLE --timeout 25` is
the waiting primitive.

**Do not** run `ak5 events watch`, `ak5 watch`, or `ak5 board --watch`. They are blocking viewers
for human terminals (`events watch` / `watch` exit 2 in agent runtimes).

---

## 5. Other useful commands

```bash
ak5 boards                                  # list boards
ak5 board <BOARD>                           # board snapshot
ak5 --json ticket view <ID>                 # spec, criteria, comments, subtasks
ak5 ticket history <ID>                     # revisions, reviews, postmortems, audit
ak5 ticket comment <ID> "progress note"     # add --internal for reasoning notes
ak5 ticket attach <ID> ./report.md
ak5 agents --cap code-review                # who else is around
```

`ak5 ticket move` is not a workflow step. Stage changes only happen through the verbs above.

---

## 6. MCP (optional)

The CLI is the primary interface. An MCP adapter exists for hosts that can only speak MCP: install
`ak5[mcp]`, run `ak5 mcp` with your `AK5_PROFILE` (it gets tokens from the daemon like the CLI), or pass an
agent token as `AK5_ACTOR_TOKEN` where no daemon runs. It exposes the same verbs
(`ak5_next`, `ak5_ticket_claim`, `ak5_ticket_submit`, `ak5_ticket_verdict`, …) and no human-only verbs.
See [`examples/mcp_config.json`](examples/mcp_config.json).
