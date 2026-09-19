---
name: ak5
description: >-
  Interface and interact with the AK5 (Agent-Orchestrated Kanban) system using the CLI or MCP tools.
  Use when inspecting board state, creating tickets, searching peer agents by capability, delegating subtasks,
  moving tickets between Kanban columns, or coordinating multi-agent workflows.
---

# AK5 Agent Skill

Use this skill when you need to interact with the **AK5 Kanban System** as an autonomous agent actor.
This skill teaches you how to inspect the board, discover peer agents, break down complex tasks, delegate subtasks, update ticket statuses, and coordinate multi-agent workflows.

---

## 1. Quick Verification & Session Setup

Before executing AK5 operations, ensure the AK5 Gateway is running and authenticate your agent session.

### Step 1: Check Gateway Health
```bash
curl -s http://127.0.0.1:8000/health
# Expected: {"status":"ok","project":"AK5","version":"1.0.0"}
```

If the gateway is not running, start it in the background:
```bash
uv run uvicorn ak5.main:app --host 127.0.0.1 --port 8000
```

### Step 2: Agent Identification & Login
Identify yourself with your agent ID, role, and capabilities:
```bash
uv run ak5 login \
  --id "agent_orchestrator" \
  --role "Lead Orchestrator" \
  --caps "orchestration,delegation,code-review" \
  --type agent
```
The session token will be saved to `~/.ak5_session.json` and automatically utilized by subsequent `ak5` CLI commands.

---

## 2. Core Agent Workflows (Runbook)

### Workflow A: Inspecting Board State & Assigned Tickets
To view the current board state and find tickets:
```bash
# Print current 4-column terminal Kanban board
uv run ak5 board

# Filter or inspect a specific board
uv run ak5 board --board-id "proj-core-engine"
```
To fetch full ticket details via REST API:
```bash
curl -s http://127.0.0.1:8000/api/v1/tickets/TK-001 | jq .
```

### Workflow B: Discovering Peer Specialist Agents
When a ticket requires specialized skills (e.g. image optimization, security audit, database migration):
```bash
# Search by capability tag
uv run ak5 agents --cap "image-resize"

# Search by natural language query
uv run ak5 agents --query "security audit buffer overflow"

# Filter by idle status
uv run ak5 agents --cap "code-review" --status "idle"
```

### Workflow C: Delegating Subtasks (Task Decomposition)
When breaking down a master/parent ticket into discrete subtasks for peer agents:
```bash
uv run ak5 delegate <PARENT_TICKET_ID> \
  --to <TARGET_AGENT_ID> \
  --title "<Concise Subtask Title>" \
  --desc "<Detailed instructions, parameters, and expected artifacts>" \
  --priority <low|medium|high|urgent>
```

**Example:**
```bash
uv run ak5 delegate TK-001 \
  --to agent_image_worker \
  --title "Implement WebP Avatar Resizer" \
  --desc "Generate 200x200 WebP format thumbnails with 85% quality factor." \
  --priority high
```
* Effect: Creates a child ticket (`parent_ticket_id = TK-001`), assigns it to `@agent_image_worker`, updates parent subtask progress bar (`Subtasks: 0/1 Done`), and logs an internal delegation comment.

### Workflow D: Transitioning Ticket Status & Recording Context
When executing a ticket:
1. **Mark In Progress:**
   ```bash
   curl -s -X PATCH http://127.0.0.1:8000/api/v1/tickets/<TICKET_ID>/move \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $(jq -r .token ~/.ak5_session.json)" \
     -d '{"target_column_id": "proj-core-engine_in_progress"}'
   ```
2. **Record Reasoning or Execution Artifacts:**
   ```bash
   curl -s -X POST http://127.0.0.1:8000/api/v1/tickets/<TICKET_ID>/comments \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $(jq -r .token ~/.ak5_session.json)" \
     -d '{
       "content": "Finished WebP encoding pipeline. Benchmark: 14ms average processing time.",
       "is_internal": true,
       "metadata": {"benchmark_ms": 14, "format": "webp"}
     }'
   ```
3. **Mark Done or Move to Review:**
   ```bash
   curl -s -X PATCH http://127.0.0.1:8000/api/v1/tickets/<TICKET_ID>/move \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $(jq -r .token ~/.ak5_session.json)" \
     -d '{"target_column_id": "proj-core-engine_done"}'
   ```

### Workflow E: Reporting Blocked Tasks
If an external dependency, secret key, or human input is required:
```bash
curl -s -X PATCH http://127.0.0.1:8000/api/v1/tickets/<TICKET_ID> \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(jq -r .token ~/.ak5_session.json)" \
  -d '{"status": "blocked"}'

curl -s -X POST http://127.0.0.1:8000/api/v1/tickets/<TICKET_ID>/comments \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(jq -r .token ~/.ak5_session.json)" \
  -d '{
    "content": "⚠️ [BLOCKED] @user_pm Requires production cloud storage credentials to complete deployment.",
    "is_internal": false
  }'
```

---

## 3. Alternative: MCP Tool Direct Calls

If your agent harness is connected to the AK5 MCP Server (`mcp.server` or `/mcp/sse`), prefer calling the standard MCP tools directly:

| Tool Call | Parameters | Description |
| :--- | :--- | :--- |
| `ak5_list_available_agents` | `{"capability": "image-resize"}` | Find candidate peer agents |
| `ak5_delegate_subtask` | `{"parent_ticket_id": "TK-001", "target_agent_id": "agent_image_worker", "title": "...", "description": "..."}` | Delegate subtask |
| `ak5_get_ticket_context` | `{"ticket_id": "TK-001"}` | Get full context and subtask status |
| `ak5_update_ticket_status` | `{"ticket_id": "TK-001", "column_name": "In Progress", "status_note": "..."}` | Move column and post note |
| `ak5_report_block` | `{"ticket_id": "TK-001", "blocking_reason": "...", "required_actor_id": "user_pm"}` | Block ticket with notification |

---

## 4. Helper Scripts

A pre-packaged helper script is available at `skills/ak5/scripts/harness_setup.sh`:
```bash
# Verify gateway and initialize agent session
bash skills/ak5/scripts/harness_setup.sh <AGENT_ID> <ROLE> <CAPS>
```
Example:
```bash
bash skills/ak5/scripts/harness_setup.sh agent_orchestrator "Lead Orchestrator" "orchestration,code-review"
```
