import type { ColumnStage } from "./types";

/** Human review decisions (workflow.md T15 / T16). */
export type ReviewDecision = "approve" | "reject";

export function columnStageForTicket(
  columns: { column_id: string; stage: ColumnStage }[],
  columnId: string
): ColumnStage | null {
  return columns.find((c) => c.column_id === columnId)?.stage ?? null;
}

function asColumnStage(value: string | null | undefined): ColumnStage | null {
  switch (value) {
    case "unclassified":
    case "backlog":
    case "in_progress":
    case "agent_review":
    case "postmortem_pending":
    case "human_review":
    case "done":
      return value;
    default:
      return null;
  }
}

/** Effective ticket stage — prefer ticket.stage, fall back to column. */
export function resolveTicketStage(
  columns: { column_id: string; stage: ColumnStage }[],
  ticket: { column_id: string; stage?: string | null }
): ColumnStage | null {
  const fromTicket = asColumnStage(ticket.stage ?? null);
  if (fromTicket) return fromTicket;
  return columnStageForTicket(columns, ticket.column_id);
}

export function isHumanReviewStage(stage: ColumnStage | null): boolean {
  return stage === "human_review";
}

export function isDoneStage(stage: ColumnStage | null): boolean {
  return stage === "done";
}

/**
 * Target column for a human verdict when using move (legacy path).
 * Prefer the dedicated verdict / reopen API endpoints instead.
 */
export function resolveReviewTargetColumnId(
  columns: { column_id: string; stage: ColumnStage }[],
  decision: ReviewDecision
): string | null {
  const stage: ColumnStage = decision === "approve" ? "done" : "postmortem_pending";
  return columns.find((c) => c.stage === stage)?.column_id ?? null;
}

export function resolveReopenTargetColumnId(
  columns: { column_id: string; stage: ColumnStage }[]
): string | null {
  return columns.find((c) => c.stage === "postmortem_pending")?.column_id ?? null;
}

/** Structured note for comments when posting alongside a verdict. */
export function buildReviewDecisionComment(decision: ReviewDecision, note: string): string {
  const body = note.trim();
  if (decision === "approve") {
    return body ? `Approved\n${body}` : "Approved";
  }
  return body ? `Rejected\n${body}` : "Rejected";
}

/**
 * Approve (T15) may omit a reason; reject (T16) always requires one.
 */
export function reviewDecisionRequiresNote(decision: ReviewDecision, note: string): boolean {
  if (decision === "approve") return false;
  return note.trim().length === 0;
}

export function reopenRequiresReason(reason: string): boolean {
  return reason.trim().length === 0;
}

/** Build verdict API body for POST /tickets/{id}/verdict. */
export function buildVerdictPayload(
  decision: ReviewDecision,
  reason: string
): {
  claim_token: null;
  verdict: { decision: ReviewDecision; reason: string; misrouted: boolean; findings: [] };
} {
  return {
    claim_token: null,
    verdict: {
      decision,
      reason: reason.trim(),
      misrouted: false,
      findings: [],
    },
  };
}
