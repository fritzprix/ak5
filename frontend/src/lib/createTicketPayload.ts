export type CreateTicketOptions = {
  /** When true (or boardId is "inbox"), creates via T1 into global inbox. */
  toInbox?: boolean;
  /** T2 acceptance criteria; defaults to a single criterion from the description. */
  acceptanceCriteria?: string[];
};

/** Pure payload builder — assignee is claim-derived; never include ``assigned_to``. */
export function buildCreateTicketPayload(
  boardId: string,
  columnId: string,
  title: string,
  description?: string,
  priority: string = "medium",
  options?: CreateTicketOptions
): Record<string, unknown> {
  const toInbox = Boolean(options?.toInbox) || boardId === "inbox";
  const payload: Record<string, unknown> = {
    title,
    description: description || undefined,
    priority,
    labels: ["manual"],
  };
  if (toInbox) {
    payload.original_request = description?.trim() || title;
  } else {
    payload.board_id = boardId;
    payload.column_id = columnId;
    payload.spec = description?.trim() || title;
    payload.acceptance_criteria =
      options?.acceptanceCriteria && options.acceptanceCriteria.length > 0
        ? options.acceptanceCriteria
        : ["Ticket accepted when deliverable meets the description"];
  }
  return payload;
}
