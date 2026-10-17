import type { ColumnStage, Ticket, TicketPriority, TicketStatus } from "./types";

/** Local edit form state for the ticket detail drawer. */
export type TicketEditFields = {
  title: string;
  description: string;
  priority: TicketPriority;
  /** Empty string means unassigned. */
  assigned_to: string;
  blocked: boolean;
  blocked_by: string;
};

/** Payload for PATCH /tickets/{id}. Only dirty fields should be present. */
export type TicketUpdatePayload = {
  title?: string;
  description?: string | null;
  priority?: TicketPriority;
  assigned_to?: string | null;
  status?: TicketStatus;
  blocked_by?: string | null;
};

/** Map column stage → derived status when unblocking via legacy PATCH. */
const STAGE_TO_STATUS: Record<ColumnStage, TicketStatus> = {
  unclassified: "open",
  backlog: "open",
  in_progress: "in_progress",
  agent_review: "in_progress",
  postmortem_pending: "in_progress",
  human_review: "in_progress",
  done: "done",
};

export function statusForColumnStage(stage: ColumnStage | null): TicketStatus {
  if (!stage) return "open";
  return STAGE_TO_STATUS[stage] ?? "open";
}

export function ticketToEditFields(ticket: Ticket): TicketEditFields {
  return {
    title: ticket.title,
    description: ticket.description ?? "",
    priority: ticket.priority,
    assigned_to: ticket.assigned_to ?? "",
    blocked: ticket.status === "blocked" || Boolean(ticket.blocked_kind),
    blocked_by: ticket.blocked_by ?? "",
  };
}

export function isTicketEditDirty(original: Ticket, edit: TicketEditFields): boolean {
  return buildTicketUpdatePayload(original, edit, null) !== null;
}

/**
 * Build a minimal PATCH body from drawer edits.
 * When unblocking, `columnStage` restores status from the ticket's column stage.
 * Prefer POST /unblock for needs_human / escalation (B3).
 * Returns null when nothing changed.
 */
export function buildTicketUpdatePayload(
  original: Ticket,
  edit: TicketEditFields,
  columnStage: ColumnStage | null
): TicketUpdatePayload | null {
  const payload: TicketUpdatePayload = {};

  const title = edit.title.trim();
  if (title && title !== original.title) {
    payload.title = title;
  }

  const description = edit.description;
  const originalDescription = original.description ?? "";
  if (description !== originalDescription) {
    payload.description = description.length > 0 ? description : null;
  }

  if (edit.priority !== original.priority) {
    payload.priority = edit.priority;
  }

  const nextAssignee = edit.assigned_to.trim() || null;
  const prevAssignee = original.assigned_to ?? null;
  if (nextAssignee !== prevAssignee) {
    payload.assigned_to = nextAssignee;
  }

  const wasBlocked = original.status === "blocked" || Boolean(original.blocked_kind);
  if (edit.blocked && !wasBlocked) {
    payload.status = "blocked";
  } else if (!edit.blocked && wasBlocked) {
    payload.status = statusForColumnStage(columnStage);
  }

  const nextBlockedBy = edit.blocked ? edit.blocked_by.trim() || null : null;
  const prevBlockedBy = original.blocked_by ?? null;
  if (nextBlockedBy !== prevBlockedBy) {
    payload.blocked_by = nextBlockedBy;
  }

  return Object.keys(payload).length > 0 ? payload : null;
}
