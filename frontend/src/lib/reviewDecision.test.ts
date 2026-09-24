import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  buildReviewDecisionComment,
  columnStageForTicket,
  resolveReviewTargetColumnId,
  reviewDecisionRequiresNote,
} from "./reviewDecision";
import type { ColumnStage } from "./types";

const columns = [
  { column_id: "c_open", stage: "open" as ColumnStage },
  { column_id: "c_wip", stage: "in_progress" as ColumnStage },
  { column_id: "c_rev", stage: "review" as ColumnStage },
  { column_id: "c_done", stage: "done" as ColumnStage },
];

describe("columnStageForTicket", () => {
  it("resolves stage from column_id", () => {
    assert.equal(columnStageForTicket(columns, "c_rev"), "review");
    assert.equal(columnStageForTicket(columns, "missing"), null);
  });
});

describe("resolveReviewTargetColumnId", () => {
  it("maps approve → done and request_changes → in_progress", () => {
    assert.equal(resolveReviewTargetColumnId(columns, "approve"), "c_done");
    assert.equal(resolveReviewTargetColumnId(columns, "request_changes"), "c_wip");
  });
});

describe("buildReviewDecisionComment", () => {
  it("prefixes approve and changes-requested notes", () => {
    assert.equal(buildReviewDecisionComment("approve", "LGTM"), "✅ Approved\nLGTM");
    assert.match(buildReviewDecisionComment("request_changes", "fix tests"), /Changes requested/);
  });
});

describe("reviewDecisionRequiresNote", () => {
  it("requires a non-empty note for both decisions", () => {
    assert.equal(reviewDecisionRequiresNote("approve", "  "), true);
    assert.equal(reviewDecisionRequiresNote("approve", "LGTM"), false);
    assert.equal(reviewDecisionRequiresNote("request_changes", ""), true);
  });
});
