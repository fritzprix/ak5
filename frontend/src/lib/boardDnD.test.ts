import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  buildTicketIndex,
  emptyColumnSlotId,
  isEmptyColumnSlotId,
  resolveDropLabel,
  resolveTargetColumnId,
  resolveTicketTitle,
} from "./boardDnD";
import type { Board, ColumnStage } from "./types";

const board: Board = {
  board_id: "b1",
  name: "Core",
  created_by: "user_pm",
  created_at: "",
  columns: [
    {
      column_id: "col_todo",
      board_id: "b1",
      name: "To Do",
      stage: "open" as ColumnStage,
      position: 0,
      wip_limit: 0,
      created_at: "",
      tickets: [
        {
          ticket_id: "t1",
          board_id: "b1",
          column_id: "col_todo",
          title: "Implement WebP Avatar Resizer",
          priority: "high",
          rank: "0|a",
          labels: [],
          created_by: "user_pm",
          status: "open",
          created_at: "",
          updated_at: "",
          subtask_count: 0,
          subtask_done_count: 0,
        },
      ],
    },
    {
      column_id: "col_done",
      board_id: "b1",
      name: "Done",
      stage: "done" as ColumnStage,
      position: 1,
      wip_limit: 0,
      created_at: "",
      tickets: [],
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
