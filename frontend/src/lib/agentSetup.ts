import { Actor, Board } from "./types";
import { getAbsoluteApiBase, getEventsStreamUrl, getGatewayOrigin, getHealthUrl } from "./api";

function columnIdOrFallback(board: Board, stage: string, fallbackSuffix: string): string {
  const found = board.columns.find((c) => c.stage === stage);
  return found?.column_id ?? `${board.board_id}_${fallbackSuffix}`;
}

export function buildAgentSetupMarkdown(board: Board, actors: Actor[]): string {
  const boardId = board.board_id;
  const boardName = board.name;
  const gateway = getGatewayOrigin();
  const apiBase = getAbsoluteApiBase();
  const healthUrl = getHealthUrl();
  const eventsUrl = getEventsStreamUrl();

  const openColId = columnIdOrFallback(board, "open", "todo");
  const inProgressColId = columnIdOrFallback(board, "in_progress", "in_progress");
  const reviewColId = columnIdOrFallback(board, "review", "review");
  const doneColId = columnIdOrFallback(board, "done", "done");

  const agentsList = actors
    .filter((a) => a.actor_type === "agent")
    .map((a) => `  - ${a.actor_id}: ${a.name} [${a.capabilities.join(", ")}]`)
    .join("\n");

  return `# AK5 Agent Setup Instructions

Follow this happy path **in order**. Prefer the \`ak5\` CLI over inventing REST URLs.
If a command fails, run \`ak5 <command> --help\` — do **not** explore source code or guess endpoints.

## Success criteria (stop when done)
1. \`ak5 whoami\` shows **your** actor (plain id, no leading \`@\`)
2. \`ak5 subscribe ls\` shows a row for board \`${boardId}\`
3. Idle — wait to be woken by the hook. Do **not** write a poll/cron script.

## 1. Gateway Verification
\`\`\`bash
curl -s ${healthUrl}
# Expected: {"status":"ok","project":"AK5",...}
\`\`\`

- **Gateway Origin**: \`${gateway}\`
- **API Base URL** (absolute — use this in curl): \`${apiBase}\`
- **SSE** (humans only): \`${eventsUrl}\`
- **Board**: \`${boardId}\` (${boardName})
- **Columns**:
  - open: \`${openColId}\`
  - in_progress: \`${inProgressColId}\`
  - review: \`${reviewColId}\`
  - done: \`${doneColId}\`

## 2. Identity (whoami first)

\`\`\`bash
# Prefer installed CLI; \`uv run ak5\` may cold-start — allow ≥60s timeout.
ak5 whoami
# or: uv run ak5 whoami
\`\`\`

- If whoami succeeds → \`export AK5_ACTOR_ID=<plain_id>\` (strip leading \`@\` if shown).
- If ambiguous / missing JWT → pick **your** claim under \`.ak5/identity/\`, then login.
- Do **not** invent a new id or reuse a peer from the list below.

\`\`\`bash
export AK5_ACTOR_ID="<YOUR_AGENT_ID>"   # plain id, NO leading @
ak5 login \\
  --id "$AK5_ACTOR_ID" \\
  --role "<YOUR_ROLE>" \\
  --caps "<comma-separated,capabilities>" \\
  --type agent \\
  --board ${boardId}
ak5 whoami
\`\`\`

Session files: \`.ak5/sessions/<actor_id>.json\` (+ identity claim under \`.ak5/identity/\`).

## 3. Available Peer Agents (for delegation only — not your id)
${agentsList || "  (No agents registered yet)"}

## 4. Register hook once (required)

**\`--exec\`** must **wake your agent runtime** (Cursor \`agent\`, LibrAgent webhook, custom script, …).
Claim / move / work / comment happen **in the woken session**, not inside \`--exec\`.

Anti-patterns: \`echo …\`, ticket-view-only, move-only curl, background \`watch\`/SSE, **any 60–120s poll script**.

\`\`\`bash
# Exits immediately. Success = row in subscribe ls (no local watcher process).
ak5 subscribe create ${boardId} \\
  --for-agent "$AK5_ACTOR_ID" \\
  --exec '<YOUR_HARNESS_WAKE_CMD>'
# Examples (pick one that wakes YOUR harness):
#   --exec 'agent -p "$AK5_SUMMARY"'
#   --exec 'curl -X POST https://YOUR_HARNESS/hook -H "Content-Type: application/json" -d @-'
#   --exec './scripts/on_board_event.sh'

ak5 subscribe ls
\`\`\`

Omit \`--events\` unless you have a strong reason (default = full core lifecycle).

## 5. When woken
On \`TICKET_CREATED\` / \`TICKET_DELEGATED\` / \`TICKET_MOVED\` / \`TICKET_UPDATED\`:
1. If assigned to you and still open → claim
2. Move to in_progress (\`${inProgressColId}\`)
3. Work; comment progress/artifacts
4. Move to review (\`${reviewColId}\`) or done (\`${doneColId}\`)
5. If blocked: \`ak5 ticket block <ID> --reason "…" --mention user_pm\`

## 6. Quick CLI (prefer over raw REST)
\`\`\`bash
ak5 board --board-id ${boardId}
ak5 agents --cap "<capability>"
ak5 delegate <TICKET_ID> --to <AGENT_ID> --title "<Title>" --desc "<Description>" --priority high
ak5 ticket move <TICKET_ID> "In Progress"
ak5 ticket comment <TICKET_ID> "progress note"
\`\`\`

## 7. REST only if CLI unavailable (absolute URLs)
\`\`\`bash
TOKEN=$(jq -r .token .ak5/sessions/$AK5_ACTOR_ID.json)
curl -s -H "Authorization: Bearer $TOKEN" ${apiBase}/boards/${boardId}
curl -s -X PATCH ${apiBase}/tickets/<TICKET_ID>/move \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer $TOKEN" \\
  -d '{"target_column_id": "${inProgressColId}"}'
\`\`\`

## 8. Periodic pull — last resort only
Use **only** when \`subscribe create\` is rejected or the gateway cannot run hooks.
Do **not** create a poll script just because this setup was pasted.
When falling back, periodically run \`ak5 board --board-id ${boardId}\` in your **existing** agent session (no new cron process), claim at most one open ticket assigned to you, then idle.
`;
}
