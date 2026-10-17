/**
 * Live-server SDK contract for #29.
 * Env: AK5_LIVE_BASE_URL=http://127.0.0.1:PORT/api/v1
 */
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const sdkDist = path.join(__dirname, "..", "..", "..", "packages", "sdk", "dist", "index.mjs");
const { AK5Client } = await import(sdkDist);

const baseUrl = process.env.AK5_LIVE_BASE_URL;
if (!baseUrl) {
  console.error("AK5_LIVE_BASE_URL required");
  process.exit(2);
}

function client(opts) {
  return new AK5Client({
    baseUrl,
    skipVersionCheck: true,
    timeout: 15000,
    ...opts,
  });
}

const human = client({
  actorId: "sdk_live_pm",
  actorType: "human",
  actorRole: "human",
});
await human.ensureToken();

const triage = client({
  actorId: "sdk_live_triage",
  actorType: "agent",
  actorRole: "triage",
});
await triage.ensureToken();

const worker = client({
  actorId: "sdk_live_worker",
  actorType: "agent",
  actorRole: "worker",
});
await worker.ensureToken();

const created = await human.createTicket({
  title: "SDK live regression",
  original_request: "create → next → claim → route → claim → submit → history",
});
assert.ok(created.ticket_id);
assert.equal(created.board_id, "inbox");

const nxt = await triage.nextTicket("inbox");
assert.ok(nxt);
assert.equal(nxt.ticket_id, created.ticket_id);

const claimed = await triage.claimTicket(created.ticket_id);
assert.ok(claimed.claim_token);

const routed = await triage.routeTicket(created.ticket_id, {
  claim_token: claimed.claim_token,
  plan: {
    refined_title: "SDK live",
    refined_spec: "SDK live regression path",
    acceptance_criteria: ["history readable"],
    target: { board_id: "proj-core-engine" },
    confidence: 0.9,
  },
});
assert.equal(routed.board_id, "proj-core-engine");
assert.equal(routed.stage, "backlog");

const wNext = await worker.nextTicket("proj-core-engine");
assert.ok(wNext);
assert.equal(wNext.ticket_id, created.ticket_id);

const wClaim = await worker.claimTicket(created.ticket_id);
assert.ok(wClaim.claim_token);

const submitted = await worker.submitTicket(created.ticket_id, {
  claim_token: wClaim.claim_token,
  deliverable: {
    summary: "sdk live submit",
    artifacts: [],
    criteria_self_check: [{ criterion: "history readable", pass: true }],
  },
});
assert.equal(submitted.stage, "agent_review");

const history = await human.getTicketHistory(created.ticket_id);
assert.ok(history);
assert.ok(Array.isArray(history.revisions));
assert.ok(Array.isArray(history.audit));

console.log(JSON.stringify({ ok: true, ticket_id: created.ticket_id }));
