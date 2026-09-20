import { Board, BoardSummary, Ticket, Actor } from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/api/v1";

export function getGatewayOrigin(): string {
  try {
    const url = new URL(API_BASE);
    return url.origin;
  } catch {
    return "http://127.0.0.1:8000";
  }
}

export function getHealthUrl(): string {
  return `${getGatewayOrigin()}/health`;
}

export function getEventsStreamUrl(): string {
  return `${API_BASE}/events/stream`;
}

let cachedToken: string | null = null;

export async function getAuthToken(): Promise<string> {
  if (cachedToken) return cachedToken;
  try {
    const res = await fetch(`${API_BASE}/auth/identify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        actor_id: "user_pm",
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

export function subscribeToBoardEvents(
  onEvent: (eventType: string, data: unknown) => void,
  options?: {
    onOpen?: () => void;
    onError?: () => void;
  }
): () => void {
  const eventSource = new EventSource(`${API_BASE}/events/stream`);

  const events = ["TICKET_CREATED", "TICKET_MOVED", "TICKET_DELEGATED", "TICKET_UPDATED", "COMMENT_ADDED"];

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
