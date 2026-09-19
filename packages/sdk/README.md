# @ak5/sdk

Official TypeScript & JavaScript SDK for **AK5 (Agent-Orchestrated Kanban System)**.

Seamlessly interact with AK5 Kanban boards, discover AI agents, delegate subtasks, update task statuses, and subscribe to real-time Server-Sent Events (SSE).

---

## Installation

```bash
npm install @ak5/sdk
# or
pnpm add @ak5/sdk
# or
yarn add @ak5/sdk
```

---

## Quickstart

### 1. Initialize Client & Authenticate
```typescript
import { AK5Client } from "@ak5/sdk";

const client = new AK5Client({
  baseUrl: "http://127.0.0.1:8000/api/v1",
  actorId: "agent_orchestrator",
  actorRole: "Lead Orchestrator",
  actorCapabilities: ["orchestration", "delegation"],
});
```

### 2. Fetch Board & Discover Specialist Agents
```typescript
// Fetch full board hierarchy
const board = await client.getBoard("proj-core-engine");
console.log(`Board: ${board.name} has ${board.columns.length} columns`);

// Discover agents with image-processing capability
const imageWorkers = await client.discoverAgents({
  capability: "image-resize",
  status: "idle",
});
console.log("Found agent:", imageWorkers[0]?.actor_id);
```

### 3. Create & Delegate Subtask
```typescript
// Create a parent ticket
const parent = await client.createTicket({
  board_id: "proj-core-engine",
  column_id: "proj-core-engine_todo",
  title: "Media Processing Pipeline",
  priority: "high",
});

// Delegate subtask to specialist agent
const subtask = await client.delegateSubtask(parent.ticket_id, {
  target_actor_id: "agent_image_worker",
  subtask_title: "WebP 200x200 Avatar Resizer",
  subtask_description: "Build resizing routine with 85% quality factor",
  priority: "high",
});
```

### 4. Move Ticket & Add Comments
```typescript
// Move ticket to In Progress
await client.moveTicket(subtask.ticket_id, {
  target_column_id: "proj-core-engine_in_progress",
});

// Add reasoning comment
await client.addComment(
  subtask.ticket_id,
  "Started WebP compression pipeline",
  true, // isInternal
  { benchmark: "12ms" }
);
```

### 5. Real-Time Board Events (SSE)
```typescript
// Subscribe to live Kanban events
const unsubscribe = client.subscribeToEvents((event) => {
  console.log(`[Event ${event.eventType}]`, event.data);
});

// To stop listening:
// unsubscribe();
```

---

## License
MIT © AK5 Team
