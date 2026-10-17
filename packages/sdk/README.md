# @ak5/sdk

Official TypeScript & JavaScript SDK for **AK5 (Agent-Orchestrated Kanban)** — aligned with the **v2** workflow, events, and CLI contracts.

---

## Installation

```bash
npm install @ak5/sdk
```

---

## Quickstart (pull-model agent loop)

```typescript
import { AK5Client, AK5Error } from "@ak5/sdk";

const client = new AK5Client({
  baseUrl: "http://127.0.0.1:8000/api/v1",
  actorId: "agent_worker",
  actorType: "agent",
  actorRole: "worker",
  actorCapabilities: ["typescript"],
});

// Negotiates GET /meta (api_version=2, min_client)
await client.ensureCompatible();

const ticket = await client.nextTicket("proj-core-engine", {
  capabilities: ["typescript"],
});
if (!ticket) {
  // no work (HTTP 204)
  process.exit(6);
}

const claim = await client.claimTicket(ticket.ticket_id, {
  capabilities: ["typescript"],
});

await client.submitTicket(ticket.ticket_id, {
  claim_token: claim.claim_token,
  deliverable: {
    summary: "Implemented feature",
    artifacts: [{ kind: "commit", ref: "abc123" }],
    criteria_self_check: [{ criterion: "tests pass", pass: true }],
  },
});
```

### Long-poll subscriptions

```typescript
const sub = await client.createSubscription({
  filter: { board_id: "inbox", events: ["WORK_AVAILABLE"], role: "triage" },
  start: "now",
});

const batch = await client.pollSubscriptionEvents(sub.subscription_id, {
  wait: 25,
  consumerId: "daemon-1",
});
// Always advance with next_cursor (may move even when events is empty)
await client.ackSubscription(sub.subscription_id, batch.next_cursor, {
  consumerId: "daemon-1",
});
```

### Typed errors

```typescript
try {
  await client.claimTicket("t1");
} catch (e) {
  if (e instanceof AK5Error) {
    console.error(e.code, e.exitCode, e.details);
  }
}
```

---

## v2 surface (high level)

| Area | Methods |
|---|---|
| Version | `getMeta`, `ensureCompatible` |
| Work queue | `nextTicket` |
| Transitions | `claimTicket`, `heartbeatTicket`, `releaseTicket`, `submitTicket`, `bounceTicket`, `routeTicket`, `verdictTicket`, `postmortemTicket`, `blockTicket`, `unblockTicket`, `reopenTicket`, `rerouteTicket` |
| History | `getTicketHistory` |
| Events | `createSubscription`, `pollSubscriptionEvents`, `ackSubscription`, `pollEvents` |
| Legacy | `createTicket`, `moveTicket`, `delegateSubtask`, `addComment`, `subscribeToEvents` (SSE observation) |

Removed / changed vs v1 usage:
- Prefer transition commands over using `moveTicket` to change stage.
- `delegateSubtask` is create_direct (pull); `target_actor_id` is ignored.
- `createTicket` without `board_id` lands in inbox (T1).
- Agent wake path: long-poll + `next` + `claim` (not SSE assignment).

---

## License
Apache-2.0 © AK5 Team
