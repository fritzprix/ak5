import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  buildTicketUpdatePayload,
  isTicketEditDirty,
  statusForColumnStage,
  ticketToEditFields,
  type TicketEditFields,
} from "./ticketEdit";
import type { Ticket } from "./types";

function baseTicket(overrides: Partial<Ticket> = {}): Ticket {
  return {
    ticket_id: "TK-001",
    board_id: "proj-core-engine",
    column_id: "col_todo",
    title: "Original title",
    description: "Original desc",
    priority: "medium",
    rank: "0|aaaaaa:",
    labels: [],
    assigned_to: "user_pm",
    created_by: "user_pm",
    status: "open",
    blocked_by: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    subtask_count: 0,
    subtask_done_count: 0,
    ...overrides,
  };
}

describe("ticketToEditFields", () => {
  it("maps nullables to empty strings and blocked from status", () => {
    const fields = ticketToEditFields(
      baseTicket({
        description: null,
        assigned_to: null,
        status: "blocked",
        blocked_by: "waiting on design",
      })
    );
    assert.equal(fields.description, "");
    assert.equal(fields.assigned_to, "");
    assert.equal(fields.blocked, true);
    assert.equal(fields.blocked_by, "waiting on design");
  });
});

describe("statusForColumnStage", () => {
  it("maps review to in_progress like move sync", () => {
    assert.equal(statusForColumnStage("review"), "in_progress");
    assert.equal(statusForColumnStage("done"), "done");
    assert.equal(statusForColumnStage(null), "open");
  });
});

describe("buildTicketUpdatePayload", () => {
  it("returns null when unchanged", () => {
    const ticket = baseTicket();
    const edit = ticketToEditFields(ticket);
    assert.equal(buildTicketUpdatePayload(ticket, edit, "open"), null);
    assert.equal(isTicketEditDirty(ticket, edit), false);
  });

  it("emits only dirty fields", () => {
    const ticket = baseTicket();
    const edit: TicketEditFields = {
      ...ticketToEditFields(ticket),
      title: "  New title  ",
      priority: "high",
    };
    assert.deepEqual(buildTicketUpdatePayload(ticket, edit, "open"), {
      title: "New title",
      priority: "high",
    });
  });

  it("clears assignee and description with null", () => {
    const ticket = baseTicket();
    const edit: TicketEditFields = {
      ...ticketToEditFields(ticket),
      assigned_to: "",
      description: "",
    };
    assert.deepEqual(buildTicketUpdatePayload(ticket, edit, "open"), {
      description: null,
      assigned_to: null,
    });
  });

  it("blocks and records blocked_by", () => {
    const ticket = baseTicket({ status: "in_progress" });
    const edit: TicketEditFields = {
      ...ticketToEditFields(ticket),
      blocked: true,
      blocked_by: " needs API key ",
    };
    assert.deepEqual(buildTicketUpdatePayload(ticket, edit, "in_progress"), {
      status: "blocked",
      blocked_by: "needs API key",
    });
  });

  it("unblocks by restoring column stage status and clearing blocked_by", () => {
    const ticket = baseTicket({
      status: "blocked",
      blocked_by: "needs API key",
      column_id: "col_review",
    });
    const edit: TicketEditFields = {
      ...ticketToEditFields(ticket),
      blocked: false,
      blocked_by: "",
    };
    assert.deepEqual(buildTicketUpdatePayload(ticket, edit, "review"), {
      status: "in_progress",
      blocked_by: null,
    });
  });

  it("ignores whitespace-only title (keeps original)", () => {
    const ticket = baseTicket();
    const edit: TicketEditFields = {
      ...ticketToEditFields(ticket),
      title: "   ",
    };
    assert.equal(buildTicketUpdatePayload(ticket, edit, "open"), null);
  });
});
