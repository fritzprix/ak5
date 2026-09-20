import { Actor, Board } from "./types";
import { API_BASE, getEventsStreamUrl, getGatewayOrigin, getHealthUrl } from "./api";

function columnIdOrFallback(board: Board, stage: string, fallbackSuffix: string): string {
  const found = board.columns.find((c) => c.stage === stage);
  return found?.column_id ?? `${board.board_id}_${fallbackSuffix}`;
}

export function buildAgentSetupMarkdown(board: Board, actors: Actor[]): string {
  const boardId = board.board_id;
  const boardName = board.name;
  const gateway = getGatewayOrigin();
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

## 1. Gateway Verification
\`\`\`bash
curl -s ${healthUrl}
# Expected: {"status":"ok","project":"AK5","version":"1.0.0"}
\`\`\`

## 2. Agent Login (Copy & Paste)
\`\`\`bash
uv run ak5 login \\
  --id "<YOUR_AGENT_ID>" \\
  --role "<YOUR_ROLE>" \\
  --caps "<comma-separated,capabilities>" \\
  --type agent
\`\`\`

- **API Base URL**: \`${API_BASE}\`
- **Gateway Origin**: \`${gateway}\`
- **Session Token**: Saved to \`~/.ak5_session.json\`
- **Board**: \`${boardId}\` (${boardName})
- **Columns**:
  - open: \`${openColId}\`
  - in_progress: \`${inProgressColId}\`
  - review: \`${reviewColId}\`
  - done: \`${doneColId}\`

## 3. Available Peer Agents
${agentsList || "  (No agents registered yet)"}

## 4. Quick Commands
\`\`\`bash
# View board
uv run ak5 board --board-id ${boardId}

# Search agents by capability
uv run ak5 agents --cap "<capability>"

# Delegate subtask
uv run ak5 delegate <TICKET_ID> \\
  --to <AGENT_ID> \\
  --title "<Title>" \\
  --desc "<Description>" \\
  --priority <low|medium|high|urgent>
\`\`\`

## 5. REST API Examples
\`\`\`bash
# Get board details
curl -s ${API_BASE}/boards/${boardId}

# Move ticket
curl -s -X PATCH ${API_BASE}/tickets/<TICKET_ID>/move \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer <TOKEN>" \\
  -d '{"target_column_id": "${inProgressColId}"}'
\`\`\`

## 6. Backlog Work Loop (Required)

Do **not** stop after login. Keep pulling or watching the backlog until stopped.

Claim rule: only tickets where \`assigned_to == <YOUR_AGENT_ID>\` and column stage is \`open\` (or newly delegated to you). Respect column WIP limits (HTTP 409 = wait / pick another).

### A. Event-driven (preferred)
\`\`\`bash
# Live terminal board + SSE
uv run ak5 board --watch --board-id ${boardId}

# Or subscribe directly
curl -N ${eventsUrl}
\`\`\`
On \`TICKET_CREATED\` / \`TICKET_DELEGATED\` / \`TICKET_MOVED\` / \`TICKET_UPDATED\`:
1. If ticket is assigned to you and still open → claim it
2. Move to in_progress: \`${inProgressColId}\`
3. Do the work; post comments with progress/artifacts
4. Move to review (\`${reviewColId}\`) or done (\`${doneColId}\`) when finished
5. If blocked: set status blocked + comment @user_pm

### B. Periodic pull (fallback when SSE is unavailable)
Every **60–120 seconds**:
\`\`\`bash
uv run ak5 board --board-id ${boardId}
# or: curl -s ${API_BASE}/boards/${boardId}
\`\`\`
Then:
1. Select at most one open ticket assigned to you (WIP-safe)
2. Move → in_progress → execute → comment → review/done
3. If none: idle until the next tick

### C. Minimal claim snippet
\`\`\`bash
TOKEN=$(jq -r .token ~/.ak5_session.json)
ME=$(jq -r .actor_id ~/.ak5_session.json)
BOARD=$(curl -s ${API_BASE}/boards/${boardId})
# Parse open-column tickets where assigned_to == $ME, then:
curl -s -X PATCH ${API_BASE}/tickets/<TICKET_ID>/move \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer $TOKEN" \\
  -d '{"target_column_id": "${inProgressColId}"}'
\`\`\`
`;
}
