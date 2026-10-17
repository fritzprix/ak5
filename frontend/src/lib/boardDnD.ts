import type { Board, Column, ColumnStage, Ticket } from "./types";

export const EMPTY_COLUMN_SLOT_SUFFIX = "__empty_slot";

/**
 * Human PATCH /move stage pairs that mirror §3 commands (workflow.md §8).
 * Same-stage reorders are always allowed.
 */
export const HUMAN_MOVE_STAGE_PAIRS: ReadonlyArray<readonly [ColumnStage, ColumnStage]> = [
  ["human_review", "done"], // T15 approve
  ["human_review", "postmortem_pending"], // T16 reject
  ["done", "postmortem_pending"], // T17 reopen
] as const;

export type HumanMoveCommand = "verdict_approve" | "verdict_reject" | "reopen" | "same_stage";

export type StageMoveEvaluation =
  | {
      allowed: true;
      command: HumanMoveCommand;
      requiresReason: boolean;
      fromStage: ColumnStage;
      toStage: ColumnStage;
    }
  | {
      allowed: false;
      reason: string;
      fromStage: ColumnStage | null;
      toStage: ColumnStage | null;
    };

export function emptyColumnSlotId(columnId: string): string {
  return `${columnId}${EMPTY_COLUMN_SLOT_SUFFIX}`;
}

export function isEmptyColumnSlotId(id: string): boolean {
  return id.endsWith(EMPTY_COLUMN_SLOT_SUFFIX);
}

export function columnIdFromEmptySlot(id: string): string {
  return id.slice(0, -EMPTY_COLUMN_SLOT_SUFFIX.length);
}

/** Resolve a droppable/sortable over-id to a human column name. */
export function resolveDropLabel(
  overId: string,
  columns: Column[],
  ticketsById: Map<string, Ticket>
): string {
  if (isEmptyColumnSlotId(overId)) {
    const colId = columnIdFromEmptySlot(overId);
    const col = columns.find((c) => c.column_id === colId);
    return col ? `${col.name} column` : colId;
  }
  const asColumn = columns.find((c) => c.column_id === overId);
  if (asColumn) return `${asColumn.name} column`;
  const ticket = ticketsById.get(overId);
  if (ticket) {
    const col = columns.find((c) => c.column_id === ticket.column_id);
    return col ? `"${ticket.title}" in ${col.name}` : `"${ticket.title}"`;
  }
  return overId;
}

export function resolveTicketTitle(ticketId: string, ticketsById: Map<string, Ticket>): string {
  return ticketsById.get(ticketId)?.title ?? ticketId;
}

export function buildTicketIndex(board: Board): Map<string, Ticket> {
  const map = new Map<string, Ticket>();
  for (const col of board.columns) {
    for (const t of col.tickets) map.set(t.ticket_id, t);
  }
  return map;
}

/** Map drag over-id to the target column id used by moveTicket. */
export function resolveTargetColumnId(overId: string, board: Board): string | null {
  if (isEmptyColumnSlotId(overId)) return columnIdFromEmptySlot(overId);
  if (board.columns.some((c) => c.column_id === overId)) return overId;
  for (const col of board.columns) {
    if (col.tickets.some((t) => t.ticket_id === overId)) return col.column_id;
  }
  return null;
}

export function columnStageById(
  columns: { column_id: string; stage: ColumnStage }[],
  columnId: string
): ColumnStage | null {
  return columns.find((c) => c.column_id === columnId)?.stage ?? null;
}

function isHumanMovePair(from: ColumnStage, to: ColumnStage): boolean {
  return HUMAN_MOVE_STAGE_PAIRS.some(([a, b]) => a === from && b === to);
}

function commandForPair(from: ColumnStage, to: ColumnStage): HumanMoveCommand {
  if (from === "human_review" && to === "done") return "verdict_approve";
  if (from === "human_review" && to === "postmortem_pending") return "verdict_reject";
  if (from === "done" && to === "postmortem_pending") return "reopen";
  return "same_stage";
}

/**
 * Evaluate whether a board drop that may change stage is allowed for a human.
 * Same-stage moves always pass; stage changes must map to T15/T16/T17.
 */
export function evaluateStageMove(
  fromStage: ColumnStage | null,
  toStage: ColumnStage | null
): StageMoveEvaluation {
  if (!fromStage || !toStage) {
    return {
      allowed: false,
      reason: "Cannot resolve source or target stage for this drop.",
      fromStage,
      toStage,
    };
  }

  if (fromStage === toStage) {
    return {
      allowed: true,
      command: "same_stage",
      requiresReason: false,
      fromStage,
      toStage,
    };
  }

  if (!isHumanMovePair(fromStage, toStage)) {
    return {
      allowed: false,
      reason: `Stage change ${fromStage} → ${toStage} is not an allowed human move. Use Approve / Reject / Reopen, or a workflow command.`,
      fromStage,
      toStage,
    };
  }

  const command = commandForPair(fromStage, toStage);
  const requiresReason = command === "verdict_reject" || command === "reopen";
  return {
    allowed: true,
    command,
    requiresReason,
    fromStage,
    toStage,
  };
}

/** Convenience: evaluate a drop given column ids on a board. */
export function evaluateBoardDrop(
  board: Board,
  sourceColumnId: string,
  targetColumnId: string
): StageMoveEvaluation {
  const fromStage = columnStageById(board.columns, sourceColumnId);
  const toStage = columnStageById(board.columns, targetColumnId);
  return evaluateStageMove(fromStage, toStage);
}

export function findTicketColumnId(board: Board, ticketId: string): string | null {
  for (const col of board.columns) {
    if (col.tickets.some((t) => t.ticket_id === ticketId)) return col.column_id;
  }
  return null;
}

/** Collect blocked tickets that need human attention (needs_human / escalation). */
export function collectHumanBlockedTickets(board: Board): Ticket[] {
  const out: Ticket[] = [];
  for (const col of board.columns) {
    for (const t of col.tickets) {
      const kind = t.blocked_kind;
      if (kind === "needs_human" || kind === "escalation") {
        out.push(t);
      } else if (t.status === "blocked" && (kind == null || kind === "")) {
        // Legacy blocked without kind — surface in blocked panel for humans.
        out.push(t);
      }
    }
  }
  return out;
}

export function isInboxBoard(board: Pick<Board, "kind" | "board_id">): boolean {
  return board.kind === "inbox" || board.board_id === "inbox";
}
