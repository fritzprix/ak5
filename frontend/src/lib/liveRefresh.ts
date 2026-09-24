/**
 * Live board-refresh policy for the Kanban dashboard.
 * Pure helpers so SSE debounce / compose-pause behavior can be unit-tested
 * without mounting React.
 */

export type LiveRefreshDecision = "refresh" | "defer";

/** While overlays (dialogs / ticket drawer) are open, SSE must not thrash the UI. */
export function decideLiveRefresh(uiBlocking: boolean): LiveRefreshDecision {
  return uiBlocking ? "defer" : "refresh";
}

/**
 * After an overlay closes, flush only if SSE events arrived while blocked.
 */
export function shouldFlushDeferredRefresh(
  uiBlocking: boolean,
  pendingRefresh: boolean
): boolean {
  return !uiBlocking && pendingRefresh;
}

/** Stable ticket identity — drawer must key fetches on id, not object reference. */
export function ticketFetchKey(ticket: { ticket_id: string } | null): string | null {
  return ticket?.ticket_id ?? null;
}
