import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  buildReviewDecisionComment,
  buildVerdictPayload,
  columnStageForTicket,
  isDoneStage,
  isHumanReviewStage,
  reopenRequiresReason,
  resolveReopenTargetColumnId,
  resolveReviewTargetColumnId,
  resolveTicketStage,
  reviewDecisionRequiresNote,
} from "./reviewDecision";
import type { ColumnStage } from "./types";

const columns = [
  { column_id: "c_backlog", stage: "backlog" as ColumnStage },
  { column_id: "c_wip", stage: "in_progress" as ColumnStage },
  { column_id: "c_agent", stage: "agent_review" as ColumnStage },
  { column_id: "c_pm", stage: "postmortem_pending" as ColumnStage },
  { column_id: "c_human", stage: "human_review" as ColumnStage },
  { column_id: "c_done", stage: "done" as ColumnStage },
];

describe("columnStageForTicket / resolveTicketStage", () => {
  it("resolves stage from column_id", () => {
    assert.equal(columnStageForTicket(columns, "c_human"), "human_review");
    assert.equal(columnStageForTicket(columns, "missing"), null);
  });

  it("prefers ticket.stage over column lookup", () => {
    assert.equal(
      resolveTicketStage(columns, { column_id: "c_wip", stage: "human_review" }),
      "human_review"
    );
    assert.equal(resolveTicketStage(columns, { column_id: "c_human" }), "human_review");
  });
});

describe("isHumanReviewStage / isDoneStage", () => {
  it("detects review and done stages", () => {
    assert.equal(isHumanReviewStage("human_review"), true);
    assert.equal(isHumanReviewStage("agent_review"), false);
    assert.equal(isDoneStage("done"), true);
    assert.equal(isDoneStage("human_review"), false);
  });
});

describe("resolveReviewTargetColumnId", () => {
  it("maps approve → done and reject → postmortem_pending", () => {
    assert.equal(resolveReviewTargetColumnId(columns, "approve"), "c_done");
    assert.equal(resolveReviewTargetColumnId(columns, "reject"), "c_pm");
    assert.equal(resolveReopenTargetColumnId(columns), "c_pm");
  });
});

describe("buildReviewDecisionComment", () => {
  it("prefixes approve and reject notes", () => {
    assert.equal(buildReviewDecisionComment("approve", "LGTM"), "Approved\nLGTM");
    assert.match(buildReviewDecisionComment("reject", "fix tests"), /Rejected/);
  });
});

describe("reviewDecisionRequiresNote", () => {
  it("requires a note only for reject (T16)", () => {
    assert.equal(reviewDecisionRequiresNote("approve", "  "), false);
    assert.equal(reviewDecisionRequiresNote("approve", ""), false);
    assert.equal(reviewDecisionRequiresNote("approve", "LGTM"), false);
    assert.equal(reviewDecisionRequiresNote("reject", ""), true);
    assert.equal(reviewDecisionRequiresNote("reject", "  "), true);
    assert.equal(reviewDecisionRequiresNote("reject", "needs tests"), false);
  });
});

describe("reopenRequiresReason / buildVerdictPayload", () => {
  it("requires reopen reason", () => {
    assert.equal(reopenRequiresReason(""), true);
    assert.equal(reopenRequiresReason("regression found"), false);
  });

  it("builds verdict API body", () => {
    assert.deepEqual(buildVerdictPayload("reject", "  no "), {
      claim_token: null,
      verdict: {
        decision: "reject",
        reason: "no",
        misrouted: false,
        findings: [],
      },
    });
  });
});
