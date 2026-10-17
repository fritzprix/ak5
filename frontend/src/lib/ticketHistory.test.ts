import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  buildHistoryTimeline,
  unblockRequiresResolution,
} from "./ticketHistory";
import type { TicketHistory } from "./types";

const history: TicketHistory = {
  ticket_id: "t1",
  revisions: [
    {
      revision_id: 1,
      ticket_id: "t1",
      spec: "Spec v1",
      acceptance_criteria: ["a"],
      author: "triage_1",
      reason: "route",
      source: "route",
      created_at: "2026-01-01T10:00:00Z",
    },
  ],
  reviews: [
    {
      review_id: 2,
      ticket_id: "t1",
      stage: "human_review",
      decision: "reject",
      findings: [],
      reviewer: "user_pm",
      attempt: 1,
      reason: "missing tests",
      misrouted: false,
      created_at: "2026-01-02T12:00:00Z",
    },
  ],
  postmortems: [
    {
      postmortem_id: 3,
      ticket_id: "t1",
      what_went_wrong: "Tests were skipped",
      root_cause: "Time pressure",
      author: "gk_1",
      created_at: "2026-01-02T14:00:00Z",
    },
  ],
  audit: [],
};

describe("buildHistoryTimeline", () => {
  it("merges revisions, reviews, postmortems newest-first", () => {
    const timeline = buildHistoryTimeline(history);
    assert.equal(timeline.length, 3);
    assert.equal(timeline[0].kind, "postmortem");
    assert.equal(timeline[1].kind, "review");
    assert.equal(timeline[2].kind, "revision");
    assert.match(timeline[1].title, /reject/);
    assert.match(timeline[1].summary, /missing tests/);
  });
});

describe("unblockRequiresResolution", () => {
  it("requires resolution for needs_human and escalation only", () => {
    assert.equal(unblockRequiresResolution("needs_human"), true);
    assert.equal(unblockRequiresResolution("escalation"), true);
    assert.equal(unblockRequiresResolution("dependency"), false);
    assert.equal(unblockRequiresResolution("external"), false);
    assert.equal(unblockRequiresResolution(null), false);
  });
});
