import type { ColumnStage } from "./types";

export type ReviewDecision = "approve" | "request_changes";

export function columnStageForTicket(
  columns: { column_id: string; stage: ColumnStage }[],
  columnId: string
): ColumnStage | null {
  return columns.find((c) => c.column_id === columnId)?.stage ?? null;
}

export function resolveReviewTargetColumnId(
  columns: { column_id: string; stage: ColumnStage }[],
  decision: ReviewDecision
): string | null {
  const stage: ColumnStage = decision === "approve" ? "done" : "in_progress";
  return columns.find((c) => c.stage === stage)?.column_id ?? null;
}

/** Structured note so agents scanning comments see the decision clearly. */
export function buildReviewDecisionComment(decision: ReviewDecision, note: string): string {
  const body = note.trim();
  if (decision === "approve") {
    return body ? `✅ Approved\n${body}` : "✅ Approved";
  }
  return body ? `↩️ Changes requested\n${body}` : "↩️ Changes requested";
}

export function reviewDecisionRequiresNote(decision: ReviewDecision, note: string): boolean {
  // Sending back always needs a reason; approve may use a short default but we still
  // require an explicit note so the audit trail is intentional.
  void decision;
  return note.trim().length === 0;
}
