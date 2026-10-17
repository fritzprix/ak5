import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  buildTicketIndex,
  collectHumanBlockedTickets,
  emptyColumnSlotId,
  evaluateBoardDrop,
  evaluateStageMove,
  findTicketColumnId,
  HUMAN_MOVE_STAGE_PAIRS,
  isEmptyColumnSlotId,
  isInboxBoard,
  resolveDropLabel,
  resolveTargetColumnId,
  resolveTicketTitle,
} from "./boardDnD";
import type { Board, ColumnStage, Ticket } from "./types";

function ticket(overrides: Partial<Ticket> & Pick<Ticket, "ticket_id" | "column_id">): Ticket {
  return {
    board_id: "b1",
    title: "t",
    priority: "medium",
    rank: "0|a",
    labels: [],
    created_by: "user_pm",
    status: "open",
    created_at: "",
    updated_at: "",
    subtask_count: 0,
    subtask_done_count: 0,
    ...overrides,
  };
}

const board: Board = {
  board_id: "b1",
  name: "Core",
  created_by: "user_pm",
  created_at: "",
  kind: "standard",
  columns: [
    {
      column_id: "col_backlog",
      board_id: "b1",
      name: "Backlog",
      stage: "backlog" as ColumnStage,
      position: 1,
      wip_limit: 0,
      created_at: "",
      tickets: [
        ticket({
          ticket_id: "t1",
          column_id: "col_backlog",
          title: "Implement WebP Avatar Resizer",
          priority: "high",
        }),
      ],
    },
    {
      column_id: "col_human",
      board_id: "b1",
      name: "Human Review",
      stage: "human_review" as ColumnStage,
      position: 5,
      wip_limit: 0,
      created_at: "",
      tickets: [
        ticket({
          ticket_id: "t_hr",
          column_id: "col_human",
          title: "Needs approval",
          status: "in_progress",
          stage: "human_review",
        }),
      ],
    },
    {
      column_id: "col_pm",
      board_id: "b1",
      name: "Postmortem",
      stage: "postmortem_pending" as ColumnStage,
      position: 4,
      wip_limit: 0,
      created_at: "",
      tickets: [],
    },
    {
      column_id: "col_done",
      board_id: "b1",
      name: "Done",
      stage: "done" as ColumnStage,
      position: 6,
      wip_limit: 0,
      created_at: "",
      tickets: [
        ticket({
          ticket_id: "t_done",
          column_id: "col_done",
          title: "Shipped",
          status: "done",
          stage: "done",
        }),
      ],
    },
    {
      column_id: "col_wip",
      board_id: "b1",
      name: "In Progress",
      stage: "in_progress" as ColumnStage,
      position: 2,
      wip_limit: 0,
      created_at: "",
      tickets: [
        ticket({
          ticket_id: "t_blocked",
          column_id: "col_wip",
          title: "Waiting on human",
          status: "blocked",
          blocked_kind: "needs_human",
          blocked_by: "Need product decision",
        }),
        ticket({
          ticket_id: "t_esc",
          column_id: "col_wip",
          title: "Escalated",
          status: "blocked",
          blocked_kind: "escalation",
        }),
        ticket({
          ticket_id: "t_dep",
          column_id: "col_wip",
          title: "Dep wait",
          status: "blocked",
          blocked_kind: "dependency",
        }),
      ],
    },
  ],
};

describe("empty column slot ids", () => {
  it("round-trips column id through placeholder suffix", () => {
    const slot = emptyColumnSlotId("col_done");
    assert.equal(isEmptyColumnSlotId(slot), true);
    assert.equal(resolveTargetColumnId(slot, board), "col_done");
  });
});

describe("resolveDropLabel", () => {
  it("uses human column and ticket titles", () => {
    const index = buildTicketIndex(board);
    assert.equal(resolveDropLabel("col_done", board.columns, index), "Done column");
    assert.equal(
      resolveDropLabel(emptyColumnSlotId("col_done"), board.columns, index),
      "Done column"
    );
    assert.match(resolveDropLabel("t1", board.columns, index), /WebP Avatar Resizer/);
  });
});

describe("resolveTicketTitle", () => {
  it("falls back to id when unknown", () => {
    const index = buildTicketIndex(board);
    assert.equal(resolveTicketTitle("t1", index), "Implement WebP Avatar Resizer");
    assert.equal(resolveTicketTitle("missing", index), "missing");
  });
});

describe("evaluateStageMove", () => {
  it("allows same-stage reorders", () => {
    const result = evaluateStageMove("backlog", "backlog");
    assert.equal(result.allowed, true);
    if (result.allowed) {
      assert.equal(result.command, "same_stage");
      assert.equal(result.requiresReason, false);
    }
  });

  it("allows human_review → done (T15) without reason", () => {
    const result = evaluateStageMove("human_review", "done");
    assert.equal(result.allowed, true);
    if (result.allowed) {
      assert.equal(result.command, "verdict_approve");
      assert.equal(result.requiresReason, false);
    }
  });

  it("allows human_review → postmortem_pending (T16) with reason", () => {
    const result = evaluateStageMove("human_review", "postmortem_pending");
    assert.equal(result.allowed, true);
    if (result.allowed) {
      assert.equal(result.command, "verdict_reject");
      assert.equal(result.requiresReason, true);
    }
  });

  it("allows done → postmortem_pending (T17) with reason", () => {
    const result = evaluateStageMove("done", "postmortem_pending");
    assert.equal(result.allowed, true);
    if (result.allowed) {
      assert.equal(result.command, "reopen");
      assert.equal(result.requiresReason, true);
    }
  });

  it("rejects disallowed stage changes (e.g. backlog → done)", () => {
    const result = evaluateStageMove("backlog", "done");
    assert.equal(result.allowed, false);
    if (!result.allowed) {
      assert.match(result.reason, /not an allowed human move/i);
    }
  });

  it("rejects backlog → in_progress (agent claim path)", () => {
    const result = evaluateStageMove("backlog", "in_progress");
    assert.equal(result.allowed, false);
  });

  it("covers every HUMAN_MOVE_STAGE_PAIR", () => {
    for (const [from, to] of HUMAN_MOVE_STAGE_PAIRS) {
      assert.equal(evaluateStageMove(from, to).allowed, true, `${from}→${to}`);
    }
  });
});

describe("evaluateBoardDrop", () => {
  it("resolves columns and rejects illegal drops", () => {
    const ok = evaluateBoardDrop(board, "col_human", "col_done");
    assert.equal(ok.allowed, true);
    const bad = evaluateBoardDrop(board, "col_backlog", "col_done");
    assert.equal(bad.allowed, false);
  });
});

describe("collectHumanBlockedTickets", () => {
  it("includes needs_human and escalation, excludes dependency", () => {
    const blocked = collectHumanBlockedTickets(board);
    const ids = blocked.map((t) => t.ticket_id).sort();
    assert.deepEqual(ids, ["t_blocked", "t_esc"]);
  });
});

describe("isInboxBoard / findTicketColumnId", () => {
  it("detects inbox by kind or id", () => {
    assert.equal(isInboxBoard({ board_id: "inbox", kind: "inbox" }), true);
    assert.equal(isInboxBoard({ board_id: "inbox" }), true);
    assert.equal(isInboxBoard({ board_id: "b1", kind: "standard" }), false);
  });

  it("finds column for a ticket", () => {
    assert.equal(findTicketColumnId(board, "t_hr"), "col_human");
    assert.equal(findTicketColumnId(board, "missing"), null);
  });
});
