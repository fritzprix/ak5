/** Pure helpers for board-scoped SSE (issue #31). */

export type SseBoardAction = "connected" | "resync" | "refresh" | "ignore";

const REFRESH_EVENTS = new Set([
  "TICKET_CREATED",
  "TICKET_MOVED",
  "TICKET_DELEGATED",
  "TICKET_UPDATED",
  "TICKET_TRANSITIONED",
  "TICKET_BLOCKED",
  "TICKET_UNBLOCKED",
  "HUMAN_ACTION_REQUIRED",
  "WORK_AVAILABLE",
  "COMMENT_ADDED",
  "ATTACHMENT_ADDED",
  "ATTACHMENT_DELETED",
]);

/** Map an SSE event name to a board UI action. */
export function reduceSseBoardEvent(eventType: string): SseBoardAction {
  if (eventType === "CONNECTED") return "connected";
  if (eventType === "RESYNC") return "resync";
  if (REFRESH_EVENTS.has(eventType)) return "refresh";
  return "ignore";
}

/** Exponential backoff for reconnect attempts (ms), capped. */
export function nextSseBackoffMs(attempt: number, baseMs = 500, maxMs = 15_000): number {
  const exp = Math.max(0, attempt);
  return Math.min(maxMs, baseMs * 2 ** exp);
}

export function buildBoardStreamUrl(
  apiBase: string,
  boardId: string,
  accessToken: string | null
): string {
  const params = new URLSearchParams({ board_id: boardId });
  if (accessToken) {
    params.set("access_token", accessToken);
  }
  return `${apiBase}/events/stream?${params.toString()}`;
}

/** True when an event payload belongs to the watched board (or has no board_id). */
export function eventMatchesBoard(data: unknown, boardId: string): boolean {
  if (!data || typeof data !== "object") return true;
  const row = data as { board_id?: unknown; data?: { board_id?: unknown } };
  const bid =
    typeof row.board_id === "string"
      ? row.board_id
      : typeof row.data?.board_id === "string"
        ? row.data.board_id
        : null;
  if (bid === null) return true;
  return bid === boardId;
}
