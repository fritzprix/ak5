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

  const backlogColId = columnIdOrFallback(board, "backlog", "backlog");
  const inProgressColId = columnIdOrFallback(board, "in_progress", "in_progress");
  const agentReviewColId = columnIdOrFallback(board, "agent_review", "agent_review");
  const humanReviewColId = columnIdOrFallback(board, "human_review", "human_review");
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
2. \`ak5 subscribe list\` shows a row for board \`${boardId}\`
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
- **Columns** (7-stage workflow):
  - backlog: \`${backlogColId}\`
  - in_progress: \`${inProgressColId}\`
  - agent_review: \`${agentReviewColId}\`
  - human_review: \`${humanReviewColId}\`
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
  --role worker \\
  --caps "<comma-separated,capabilities>" \\
  --type agent \\
  --board ${boardId}
ak5 whoami
\`\`\`

Session files: \`.ak5/sessions/<actor_id>.json\` (+ identity claim under \`.ak5/identity/\`).

## 3. Available Peer Agents (for delegation only — not your id)
${agentsList || "  (No agents registered yet)"}

## 4. Register hook once (required)

\`\`\`bash
ak5 subscribe add ${boardId} \\
  --events WORK_AVAILABLE \\
  --hook-exec '<YOUR_HARNESS_WAKE_CMD>'
# Examples:
#   --hook-exec 'agent -p "$AK5_SUMMARY"'
ak5 subscribe list
ak5 daemon start
\`\`\`

## 5. When woken (pull model)
1. \`ak5 next --board ${boardId} --json\` (exit 6 = nothing)
2. \`ak5 ticket claim <ID>\`
3. Work; \`ak5 ticket heartbeat <ID>\` while holding the lease
4. \`ak5 ticket submit <ID> --deliverable @file.json\` → agent_review (\`${agentReviewColId}\`)
5. Humans approve at human_review (\`${humanReviewColId}\`); do **not** drag across stages unless the drop maps to a human command

## 6. Quick CLI (prefer over raw REST)
\`\`\`bash
ak5 board ${boardId}
ak5 ticket view <TICKET_ID>
ak5 ticket comment <TICKET_ID> "progress note"
ak5 ticket block <ID> --kind needs_human --reason "…"
\`\`\`

## 7. REST only if CLI unavailable (absolute URLs)
\`\`\`bash
TOKEN=$(jq -r .token .ak5/sessions/$AK5_ACTOR_ID.json)
curl -s -H "Authorization: Bearer $TOKEN" ${apiBase}/boards/${boardId}
curl -s -X POST ${apiBase}/tickets/<TICKET_ID>/claim \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer $TOKEN" \\
  -d '{}'
\`\`\`
`;
}
