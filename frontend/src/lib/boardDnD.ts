import type { Board, Column, Ticket } from "./types";

export const EMPTY_COLUMN_SLOT_SUFFIX = "__empty_slot";

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
