import type { TicketUpdatePayload } from "./ticketEdit";
import { Board, BoardSummary, Ticket, TicketAttachment, TicketComment, Actor } from "./types";

/** Same-origin by default so embedded FastAPI (`ak5 web`) needs no CORS/port split. */
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api/v1";

export function getGatewayOrigin(): string {
  if (typeof window !== "undefined") {
    return window.location.origin;
  }
  try {
    return new URL(API_BASE, "http://127.0.0.1:8000").origin;
  } catch {
    return "http://127.0.0.1:8000";
  }
}

/** Absolute `/api/v1` base for shell/curl (never a relative `/api/v1`). */
export function getAbsoluteApiBase(): string {
  if (API_BASE.startsWith("http://") || API_BASE.startsWith("https://")) {
    return API_BASE.replace(/\/$/, "");
  }
  const path = API_BASE.startsWith("/") ? API_BASE : `/${API_BASE}`;
  return `${getGatewayOrigin()}${path}`.replace(/\/$/, "");
}

export function getHealthUrl(): string {
  return `${getGatewayOrigin()}/health`;
}

export function getEventsStreamUrl(): string {
  return `${getAbsoluteApiBase()}/events/stream`;
}

/** Dashboard identity used by getAuthToken identify. */
export const DASHBOARD_ACTOR_ID = "user_pm";

let cachedToken: string | null = null;

export async function getAuthToken(): Promise<string> {
  if (cachedToken) return cachedToken;
  try {
    // credentials: include so a web-auth cookie can authorize identify when
    // AK5_IDENTIFY_SECRET is set (no change to login UX for agents/CLI).
    const res = await fetch(`${API_BASE}/auth/identify`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        actor_id: DASHBOARD_ACTOR_ID,
        actor_type: "human",
        name: "David (Lead PM)",
        role: "PM",
        capabilities: ["planning", "review"],
      }),
    });
    if (res.ok) {
      const data = await res.json();
      cachedToken = data.access_token;
      return cachedToken!;
    }
  } catch (err) {
    console.warn("Failed to auto-authenticate:", err);
  }
  return "";
}

export async function fetchBoards(): Promise<BoardSummary[]> {
  const res = await fetch(`${API_BASE}/boards`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`Failed to fetch boards: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchBoard(boardId: string): Promise<Board> {
  const res = await fetch(`${API_BASE}/boards/${boardId}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`Failed to fetch board: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchTicket(ticketId: string): Promise<Ticket> {
  const res = await fetch(`${API_BASE}/tickets/${ticketId}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`Failed to fetch ticket: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchActors(): Promise<Actor[]> {
  const res = await fetch(`${API_BASE}/actors`, { cache: "no-store" });
  if (!res.ok) return [];
  return res.json();
}

async function readErrorDetail(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (typeof body === "object" && body !== null && "detail" in body) {
      const detail = Reflect.get(body, "detail");
      if (typeof detail === "string") return detail;
    }
  } catch {
    // fall through
  }
  return res.statusText || `HTTP ${res.status}`;
}

export async function moveTicket(
  ticketId: string,
  targetColumnId: string,
  previousTicketId?: string | null,
  nextTicketId?: string | null
): Promise<Ticket> {
  const token = await getAuthToken();
  const res = await fetch(`${API_BASE}/tickets/${ticketId}/move`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: token ? `Bearer ${token}` : "",
    },
    body: JSON.stringify({
      target_column_id: targetColumnId,
      previous_ticket_id: previousTicketId,
      next_ticket_id: nextTicketId,
    }),
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

export async function createTicket(
  boardId: string,
  columnId: string,
  title: string,
  description?: string,
  priority: string = "medium",
  assignedTo?: string | null
): Promise<Ticket> {
  const token = await getAuthToken();
  const res = await fetch(`${API_BASE}/tickets`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: token ? `Bearer ${token}` : "",
    },
    body: JSON.stringify({
      board_id: boardId,
      column_id: columnId,
      title,
      description,
      priority,
      assigned_to: assignedTo || null,
      labels: ["manual"],
    }),
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

export async function delegateSubtask(
  parentTicketId: string,
  targetActorId: string,
  subtaskTitle: string,
  subtaskDescription?: string,
  priority: string = "medium"
): Promise<Ticket> {
  const token = await getAuthToken();
  const res = await fetch(`${API_BASE}/tickets/${parentTicketId}/delegate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: token ? `Bearer ${token}` : "",
    },
    body: JSON.stringify({
      target_actor_id: targetActorId,
      subtask_title: subtaskTitle,
      subtask_description: subtaskDescription,
      priority,
      labels: ["delegated"],
    }),
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

export async function updateTicket(
  ticketId: string,
  payload: TicketUpdatePayload
): Promise<Ticket> {
  const token = await getAuthToken();
  const res = await fetch(`${API_BASE}/tickets/${ticketId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: token ? `Bearer ${token}` : "",
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

export async function addComment(
  ticketId: string,
  content: string,
  isInternal: boolean = false
): Promise<TicketComment> {
  const token = await getAuthToken();
  const res = await fetch(`${API_BASE}/tickets/${ticketId}/comments`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: token ? `Bearer ${token}` : "",
    },
    body: JSON.stringify({
      content,
      is_internal: isInternal,
      metadata: {},
    }),
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

export function getAttachmentDownloadUrl(ticketId: string, attachmentId: string): string {
  return `${API_BASE}/tickets/${ticketId}/attachments/${attachmentId}`;
}

export async function uploadAttachment(
  ticketId: string,
  file: File
): Promise<TicketAttachment> {
  const token = await getAuthToken();
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${API_BASE}/tickets/${ticketId}/attachments`, {
    method: "POST",
    headers: {
      Authorization: token ? `Bearer ${token}` : "",
    },
    body: formData,
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

export async function deleteAttachment(
  ticketId: string,
  attachmentId: string
): Promise<void> {
  const token = await getAuthToken();
  const res = await fetch(`${API_BASE}/tickets/${ticketId}/attachments/${attachmentId}`, {
    method: "DELETE",
    headers: {
      Authorization: token ? `Bearer ${token}` : "",
    },
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
}

export function subscribeToBoardEvents(
  onEvent: (eventType: string, data: unknown) => void,
  options?: {
    onOpen?: () => void;
    onError?: () => void;
  }
): () => void {
  const eventSource = new EventSource(`${API_BASE}/events/stream`);

  const events = [
    "TICKET_CREATED",
    "TICKET_MOVED",
    "TICKET_DELEGATED",
    "TICKET_UPDATED",
    "COMMENT_ADDED",
    "ATTACHMENT_ADDED",
    "ATTACHMENT_DELETED",
  ];

  eventSource.onopen = () => {
    options?.onOpen?.();
  };

  eventSource.onerror = () => {
    options?.onError?.();
  };

  events.forEach((evt) => {
    eventSource.addEventListener(evt, (e: MessageEvent) => {
      try {
        const parsed: unknown = JSON.parse(e.data);
        onEvent(evt, parsed);
      } catch (err) {
        console.error("Failed to parse SSE event:", err);
      }
    });
  });

  return () => {
    eventSource.close();
  };
}

export async function fetchBoardMembers(boardId: string): Promise<Actor[]> {
  const res = await fetch(`${API_BASE}/boards/${boardId}/members`, { cache: "no-store" });
  if (!res.ok) return [];
  return res.json();
}

export async function addBoardMember(
  boardId: string,
  actorId: string,
  role: string = "member"
): Promise<any> {
  const token = await getAuthToken();
  const res = await fetch(`${API_BASE}/boards/${boardId}/members`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ actor_id: actorId, role }),
  });
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new Error(detail);
  }
  return res.json();
}

export async function removeBoardMember(
  boardId: string,
  actorId: string
): Promise<unknown> {
  const token = await getAuthToken();
  const res = await fetch(`${API_BASE}/boards/${boardId}/members/${actorId}`, {
    method: "DELETE",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new Error(detail);
  }
  return res.json();
}

export interface BoardClaimResult {
  board_id: string;
  actor_id: string;
  role: string;
  message: string;
}

export async function claimBoard(boardId: string): Promise<BoardClaimResult> {
  const token = await getAuthToken();
  const res = await fetch(`${API_BASE}/boards/${boardId}/claim`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new Error(detail);
  }
  return res.json() as Promise<BoardClaimResult>;
}

export interface DeviceRequestView {
  user_code: string;
  actor_id: string;
  actor_type: string;
  name: string;
  role: string;
  capabilities: string[];
  status: string;
  target_board_ids: string[];
  expires_at: string;
}

export async function fetchDeviceRequest(code: string): Promise<DeviceRequestView> {
  const res = await fetch(`${API_BASE}/auth/device/request?code=${encodeURIComponent(code)}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new Error(detail);
  }
  return res.json();
}

export async function approveDeviceRequest(
  userCode: string,
  approved: boolean,
  boardIds: string[] = []
): Promise<any> {
  const token = await getAuthToken();
  const res = await fetch(`${API_BASE}/auth/device/approve`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ user_code: userCode, approved, board_ids: boardIds }),
  });
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new Error(detail);
  }
  return res.json();
}
