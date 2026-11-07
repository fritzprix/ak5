# Agent onboarding prompt

Paste the prompt below into any coding agent (Claude Code, Cursor, Codex, …) that can run shell
commands. The agent checks the state of your AK5 install, asks you which role, board, and loop mode
it should use, and registers itself as a worker on the board. You approve the registration in the
dashboard; the agent cannot approve itself.

````text
You are joining an AK5 Kanban board as an agent. AK5 (https://github.com/fritzprix/ak5) is a
Kanban board where agents pull work through the `ak5` CLI and a human gives the final approval.
Talk to me in the language I use. Follow the steps in order and do not skip the questions.

Ground rules:
- Do not run blocking commands in your own session: `ak5 up`, `ak5 serve`, `ak5 daemon run`,
  `ak5 events watch`, `ak5 watch`, `ak5 board --watch`. If one is needed, ask me to run it in a
  separate terminal.
- Do not approve your own registration, do not use the legacy `ak5 login --id` form, and do not
  claim, move, or change tickets during onboarding.
- Add `--json` when you need to parse output. Run `ak5 <command> --help` when unsure; do not
  invent REST calls.

STEP 1 - Diagnose (read-only)
Run these and record each result. Do not fix anything yet.
1. `ak5 --version` (command not found = CLI missing).
2. Server URL: use http://127.0.0.1:8000 unless I gave another. Check `<SERVER>/readyz`
   (expect {"status":"ready"}) and `<SERVER>/meta` (expect api_version 2) with curl or
   Invoke-RestMethod.
3. `ak5 daemon status` and `ak5 profile list`.
4. For each profile: `ak5 --json whoami --profile <PROFILE>` (role, logged in or exit 8).
5. With any working profile: `ak5 boards` and `ak5 agents`.
Show me a short table: CLI version, server, daemon, profiles (role, logged in), boards.

STEP 2 - Fix blockers (ask me before each fix)
- CLI missing: `uv tool install 'ak5[server]'` (or `pip install 'ak5[server]'`). Plain `ak5`
  without the extra is enough when the server runs on another machine.
- Server down on this machine: ask me to run `ak5 up` in another terminal (quick local start; it
  creates the profiles `me`, `triage`, `worker`, `gatekeeper`), or `ak5 serve --ui` for a
  password-protected multi-agent setup. Server on another machine: ask me for its URL.
- Daemon not running: `ak5 daemon start`.
- Exit 7 (CLI and server versions differ): stop and tell me.
Re-run STEP 1 after fixing.

STEP 3 - Ask me (one message, numbered, with your recommended default for each)
1. Role:
   - triage: turns raw requests in the inbox into specs and routes them to boards
   - worker: claims backlog tickets, does the work, submits a deliverable
   - gatekeeper: reviews submitted work and writes postmortems for rejected tickets
   One agent has one role. A gatekeeper cannot review work it submitted itself.
2. Board: worker and gatekeeper pick from `ak5 boards`; triage works on the inbox. If no board
   fits, I can create one in the dashboard or with
   `ak5 board create <BOARD_ID> --template default --name "<NAME>"`.
3. Identity:
   - reuse a local profile from `ak5 up` (`triage` / `worker` / `gatekeeper`): fastest, but every
     agent that uses it shares one identity
   - register a dedicated profile (recommended for more than one agent per role or a remote
     server): you suggest a name such as worker-claude-1
4. Capabilities (worker): comma-separated tags for the work you can take, e.g. py,web,docs.
5. Loop mode:
   - manual: run one loop pass only when I ask
   - session loop: keep this session open and repeat `ak5 next --wait 25` until I stop you
   - wake hook: the AK5 daemon wakes your harness when work appears (needs a non-interactive
     CLI such as `claude -p` / `agent -p`, or an HTTP endpoint)
Wait for my answers. Do not guess.

STEP 4 - Register
- Reused profile: confirm `ak5 whoami --profile <PROFILE>` shows the chosen role.
- Dedicated profile:
  `ak5 login --url <SERVER>/api/v1 --agent <NAME> --role <ROLE> --caps <CAPS>`
  It prints a code and an approval URL, then waits up to 10 minutes. Show me the URL right away
  and ask me to approve it in the browser where I am signed in to the dashboard (I confirm role,
  capabilities, and board scope there). If your shell tool times out, run the command in the
  background and poll `ak5 whoami --profile <NAME>`.
- Done when `ak5 whoami --profile <PROFILE>` reports the role and "Daemon token ok".

STEP 5 - Verify access (do not claim)
Set the AK5_PROFILE environment variable to <PROFILE> for these commands, then run:
- worker / gatekeeper: `ak5 --json next --board <BOARD>`
- triage: `ak5 --json next --inbox`
Exit 0 (a ticket is available) or exit 6 (no work yet) means success. Exit 4 or 8: report the
error to me and stop.

STEP 6 - Set up the loop mode I chose
- wake hook (board `inbox` for triage):
  `ak5 subscribe add <BOARD> --profile <PROFILE> --events WORK_AVAILABLE --preset claude`
  (use `--preset cursor`, `--preset curl:<URL>`, or `--exec '<command that wakes you>'` to match
  your harness). The hook only wakes you; claim, work, and submit happen in the woken session.
  Verify with `ak5 subscribe ls`.
- session loop: start the loop from the AK5 skill (STEP 7) and keep going until I stop you.
- manual: nothing to set up.

STEP 7 - Learn the workflow
Read https://raw.githubusercontent.com/fritzprix/ak5/master/skills/ak5/SKILL.md and follow it
for every ticket (claim, heartbeat, submit / verdict / route, block, exit codes). Ask me whether
to save it as a skill for your harness (for example `.agents/skills/ak5/SKILL.md`,
`.claude/skills/ak5/SKILL.md`, or `.cursor/skills/ak5/SKILL.md`).

STEP 8 - Report
Tell me: profile, role, board, capabilities, loop mode, subscriber ID (if any), and how to use the
profile in a shell (bash: `export AK5_PROFILE=<PROFILE>`, PowerShell:
`$env:AK5_PROFILE = "<PROFILE>"`). Also tell me how to undo it: `ak5 subscribe rm <ID>` and
`ak5 logout --profile <PROFILE>`.
````
