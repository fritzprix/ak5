import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { buildCreateTicketPayload } from "./createTicketPayload";

describe("buildCreateTicketPayload", () => {
  it("omits assigned_to on T1 inbox create", () => {
    const payload = buildCreateTicketPayload("inbox", "col_x", "Hello", "req", "high", {
      toInbox: true,
    });
    assert.equal(payload.original_request, "req");
    assert.equal("assigned_to" in payload, false);
    assert.equal(payload.board_id, undefined);
  });

  it("omits assigned_to on T2 board create", () => {
    const payload = buildCreateTicketPayload("proj-core-engine", "col_backlog", "Hello", "spec", "medium");
    assert.equal(payload.board_id, "proj-core-engine");
    assert.equal("assigned_to" in payload, false);
  });
});
