import type { TicketUpdatePayload } from "./ticketEdit";
import type { ReviewDecision } from "./reviewDecision";
import {
  buildCreateTicketPayload,
  type CreateTicketOptions,
} from "./createTicketPayload";
import {
  Board,
  BoardSummary,
  Ticket,
  TicketAttachment,
  TicketComment,
  TicketHistory,
  Actor,
  BlockedKind,
  ApiErrorBody,
} from "./types";
import {
  type CachedAuthToken,
  expiresAtFromNow,
  fetchWithAuthRetry,
  isCachedTokenUsable,
  sharedInFlight,
} from "./authToken";
import { attachmentUrl, fetchAttachmentBlob, saveBlob } from "./attachmentDownload";
import { fetchWithCsrf, mapOauthDeviceRequest } from "./webSession";
import {
  assertionToJSON,
  describeWebAuthnError,
  isPresenceRequiredMessage,
  passkeysSupported,
  registrationToJSON,
  toCreationOptions,
  toRequestOptions,
  USER_PRESENCE_REQUIRED,
  type CreationOptionsJSON,
  type RequestOptionsJSON,
} from "./passkey";

export type { CreateTicketOptions } from "./createTicketPayload";
export { buildCreateTicketPayload } from "./createTicketPayload";

/** Same-origin by default so embedded FastAPI (`ak5 web`) needs no CORS/port split. */
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api/v1";

export const WORKSPACE_STORAGE_KEY = "ak5_workspace_id";

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

/** Dashboard identity used by getAuthToken identify (dev fallback). */
export const DASHBOARD_ACTOR_ID = "user_pm";

let cachedAuth: CachedAuthToken | null = null;
const mintInFlight = new Map<string, Promise<string>>();

export function getStoredWorkspaceId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(WORKSPACE_STORAGE_KEY);
}

/** Persist workspace selection and drop the cached Principal JWT. */
export function setStoredWorkspaceId(workspaceId: string | null): void {
  if (typeof window === "undefined") return;
  if (workspaceId) {
    localStorage.setItem(WORKSPACE_STORAGE_KEY, workspaceId);
  } else {
    localStorage.removeItem(WORKSPACE_STORAGE_KEY);
  }
  cachedAuth = null;
}

export function clearAuthTokenCache(): void {
  cachedAuth = null;
}

export async function getAuthToken(): Promise<string> {
  const desiredWs = getStoredWorkspaceId();
  if (isCachedTokenUsable(cachedAuth, desiredWs)) {
    return cachedAuth.token;
  }
  // Parallel API calls on board load share one mint (#72).
  return sharedInFlight(mintInFlight, desiredWs ?? "", () => mintAuthToken(desiredWs));
}

async function mintAuthToken(desiredWs: string | null): Promise<string> {
  // Prefer web-session → Principal JWT so workspace APIs see the signed-in user (#27).
  try {
    const res = await fetchWithCsrf("/auth/session/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(desiredWs ? { workspace_id: desiredWs } : {}),
    });
    if (res.ok) {
      const data = (await res.json()) as {
        access_token: string;
        workspace_id: string;
        expires_in?: number;
      };
      cachedAuth = {
        token: data.access_token,
        workspaceId: data.workspace_id,
        expiresAt: expiresAtFromNow(data.expires_in),
      };
      if (typeof window !== "undefined" && data.workspace_id) {
        localStorage.setItem(WORKSPACE_STORAGE_KEY, data.workspace_id);
      }
      return data.access_token;
    }
  } catch (err) {
    console.warn("Session token mint failed:", err);
  }

  try {
    // Dev fallback: insecure identify when no web session (local loopback).
    const res = await fetch(`${API_BASE}/auth/identify`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        actor_id: DASHBOARD_ACTOR_ID,
        actor_type: "human",
        name: "David (Lead PM)",
        role: "human",
        capabilities: ["planning", "review"],
      }),
    });
    if (res.ok) {
      const data = (await res.json()) as {
        access_token: string;
        expires_in?: number;
      };
      cachedAuth = {
        token: data.access_token,
        workspaceId: desiredWs,
        expiresAt: expiresAtFromNow(data.expires_in),
      };
      return data.access_token;
    }
  } catch (err) {
    console.warn("Failed to auto-authenticate:", err);
  }
  return "";
}

/** Authorized API fetch: attaches the Principal JWT and retries once on 401 (#72). */
async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  return fetchWithAuthRetry(
    (token) => {
      const headers = new Headers(init.headers);
      if (token) headers.set("Authorization", `Bearer ${token}`);
      return fetch(input, { ...init, headers });
    },
    getAuthToken,
    clearAuthTokenCache
  );
}

export async function fetchBoards(): Promise<BoardSummary[]> {
  const res = await apiFetch(`${API_BASE}/boards`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch boards: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchBoard(boardId: string): Promise<Board> {
  const res = await apiFetch(`${API_BASE}/boards/${boardId}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch board: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchTicket(ticketId: string): Promise<Ticket> {
  const res = await apiFetch(`${API_BASE}/tickets/${ticketId}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch ticket: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchActors(): Promise<Actor[]> {
  const res = await apiFetch(`${API_BASE}/actors`, {
    cache: "no-store",
  });
  if (!res.ok) return [];
  return res.json();
}

async function readErrorDetail(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (typeof body === "object" && body !== null) {
      // v2 contract: { error: { code, message, details } }
      if ("error" in body) {
        const err = (body as ApiErrorBody).error;
        if (err && typeof err.message === "string") {
          return err.code ? `${err.code}: ${err.message}` : err.message;
        }
      }
      if ("detail" in body) {
        const detail = Reflect.get(body, "detail");
        if (typeof detail === "string") return detail;
      }
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
  nextTicketId?: string | null,
  reason?: string | null
): Promise<Ticket> {
  const body: Record<string, unknown> = {
    target_column_id: targetColumnId,
    previous_ticket_id: previousTicketId ?? null,
    next_ticket_id: nextTicketId ?? null,
  };
  if (reason != null && reason.trim().length > 0) {
    body.reason = reason.trim();
  }
  const res = await apiFetchWithPresence(`${API_BASE}/tickets/${ticketId}/move`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
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
  options?: CreateTicketOptions
): Promise<Ticket> {
  const payload = buildCreateTicketPayload(boardId, columnId, title, description, priority, options);

  const res = await apiFetch(`${API_BASE}/tickets`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
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
  const res = await apiFetch(`${API_BASE}/tickets/${parentTicketId}/delegate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
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
  const res = await apiFetch(`${API_BASE}/tickets/${ticketId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
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
  const res = await apiFetch(`${API_BASE}/tickets/${ticketId}/comments`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
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

/** Download at click time via the authorized path (re-mints on 401; no token in the URL). */
export async function downloadAttachment(
  ticketId: string,
  attachmentId: string,
  filename: string
): Promise<void> {
  const blob = await fetchAttachmentBlob(attachmentUrl(API_BASE, ticketId, attachmentId), {
    fetch: (url, init) => fetch(url, init),
    getToken: getAuthToken,
    invalidate: clearAuthTokenCache,
  });
  saveBlob(blob, filename);
}

export async function uploadAttachment(
  ticketId: string,
  file: File
): Promise<TicketAttachment> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await apiFetch(`${API_BASE}/tickets/${ticketId}/attachments`, {
    method: "POST",
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
  const res = await apiFetch(`${API_BASE}/tickets/${ticketId}/attachments/${attachmentId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
}

export function subscribeToBoardEvents(
  boardId: string,
  onEvent: (eventType: string, data: unknown) => void,
  options?: {
    onOpen?: () => void;
    onError?: () => void;
    onConnected?: () => void;
    onResync?: (payload: unknown) => void;
  }
): () => void {
  // fetch-based SSE: the JWT goes in Authorization, never in the URL (#81).
  const controller = new AbortController();
  let reconnectAttempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let lastEventId: string | null = null;

  const attach = async () => {
    if (controller.signal.aborted) return;
    const {
      BOARD_SSE_NAMED_EVENTS,
      buildBoardStreamUrl,
      eventMatchesBoard,
      nextSseBackoffMs,
      reduceSseBoardEvent,
    } = await import("./sseBoardEvents");
    const { streamSse } = await import("./sseFetch");
    const named = new Set<string>(BOARD_SSE_NAMED_EVENTS);
    try {
      await streamSse(buildBoardStreamUrl(API_BASE, boardId), {
        fetch: (url, init) => fetch(url, init),
        getToken: getAuthToken,
        invalidate: clearAuthTokenCache,
        lastEventId,
        signal: controller.signal,
        onOpen: () => {
          reconnectAttempt = 0;
          options?.onOpen?.();
        },
        onMessage: (message) => {
          if (message.id) lastEventId = message.id;
          if (!named.has(message.event)) return;
          let parsed: unknown = message.data;
          try {
            parsed = JSON.parse(message.data);
          } catch {
            // CONNECTED/RESYNC may still be JSON; keep raw string on failure
          }
          if (!eventMatchesBoard(parsed, boardId)) return;
          const action = reduceSseBoardEvent(message.event);
          if (action === "connected") {
            options?.onConnected?.();
            options?.onOpen?.();
            return;
          }
          if (action === "resync") {
            options?.onResync?.(parsed);
            return;
          }
          if (action === "refresh") {
            onEvent(message.event, parsed);
          }
        },
      });
    } catch {
      if (controller.signal.aborted) return;
      options?.onError?.();
    }
    if (controller.signal.aborted) return;
    // Stream ended or failed: reconnect with a fresh token (re-minted if expired).
    const delay = nextSseBackoffMs(reconnectAttempt);
    reconnectAttempt += 1;
    reconnectTimer = setTimeout(() => {
      void attach();
    }, delay);
  };

  void attach();

  return () => {
    controller.abort();
    if (reconnectTimer) clearTimeout(reconnectTimer);
  };
}

export async function fetchBoardMembers(boardId: string): Promise<Actor[]> {
  const res = await apiFetch(`${API_BASE}/boards/${boardId}/members`, {
    cache: "no-store",
  });
  if (!res.ok) return [];
  return res.json();
}

export async function addBoardMember(
  boardId: string,
  actorId: string,
  role: string = "member"
): Promise<unknown> {
  const res = await apiFetch(`${API_BASE}/boards/${boardId}/members`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
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
  const res = await apiFetch(`${API_BASE}/boards/${boardId}/members/${actorId}`, {
    method: "DELETE",
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
  const res = await apiFetch(`${API_BASE}/boards/${boardId}/claim`, {
    method: "POST",
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
  principal?: string;
  workspace_id?: string;
}

/** Lookup pending OAuth device grant (identity.md §3.3) — requires web session. */
export async function fetchDeviceRequest(code: string): Promise<DeviceRequestView> {
  const res = await fetch(
    `/oauth/device/request?code=${encodeURIComponent(code)}`,
    { cache: "no-store", credentials: "same-origin" }
  );
  if (!res.ok) {
    if (res.status === 401) {
      throw new Error("Sign in required to approve a device grant");
    }
    const detail = await readErrorDetail(res);
    throw new Error(detail || "Device request not found");
  }
  const raw = (await res.json()) as Record<string, unknown>;
  return mapOauthDeviceRequest(raw);
}

/** Approve/deny OAuth device grant — CSRF header + session cookie. */
export async function approveDeviceRequest(
  userCode: string,
  approved: boolean,
  boardIds: string[] = [],
  options?: { workflowRole?: string; capabilities?: string[]; workspaceId?: string }
): Promise<unknown> {
  const body: Record<string, unknown> = {
    user_code: userCode,
    approved,
  };
  if (approved && boardIds.length > 0) {
    body.board_scope = boardIds;
  }
  if (options?.workflowRole) body.workflow_role = options.workflowRole;
  if (options?.capabilities) body.capabilities = options.capabilities;
  if (options?.workspaceId) body.workspace_id = options.workspaceId;

  const res = await fetchWithCsrf("/oauth/device/approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new Error(detail || "Device approval failed");
  }
  return res.json();
}

/** POST /tickets/{id}/verdict — human T15/T16 or gatekeeper T12–T14. */
export async function submitVerdict(
  ticketId: string,
  decision: ReviewDecision,
  reason: string = "",
  options?: { claimToken?: string | null; misrouted?: boolean }
): Promise<Ticket> {
  const res = await apiFetchWithPresence(`${API_BASE}/tickets/${ticketId}/verdict`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      claim_token: options?.claimToken ?? null,
      verdict: {
        decision,
        reason: reason.trim(),
        misrouted: Boolean(options?.misrouted),
        findings: [],
      },
    }),
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

export interface RouteTicketPlan {
  target:
    | { board_id: string }
    | { create_board: { name: string; template: string; description?: string } };
  refined_title?: string;
  refined_spec?: string;
  acceptance_criteria?: string[];
  confidence?: number;
}

/** POST /tickets/{id}/route — T4 triage or human direct routing. */
export async function routeTicket(
  ticketId: string,
  plan: RouteTicketPlan,
  claimToken?: string | null
): Promise<Ticket> {
  const body: Record<string, unknown> = { plan };
  if (claimToken != null) {
    body.claim_token = claimToken;
  }
  const res = await apiFetch(`${API_BASE}/tickets/${ticketId}/route`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

/** POST /tickets/{id}/reopen — T17 done → postmortem_pending. */
export async function reopenTicket(ticketId: string, reason: string): Promise<Ticket> {
  const res = await apiFetch(`${API_BASE}/tickets/${ticketId}/reopen`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ reason: reason.trim() }),
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

/** POST /tickets/{id}/block — B1. */
export async function blockTicket(
  ticketId: string,
  kind: BlockedKind,
  reason: string,
  claimToken?: string | null
): Promise<Ticket> {
  const res = await apiFetch(`${API_BASE}/tickets/${ticketId}/block`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      kind,
      reason: reason.trim(),
      claim_token: claimToken ?? null,
    }),
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

/** POST /tickets/{id}/unblock — B2/B3 (resolution required for needs_human / escalation). */
export async function unblockTicket(
  ticketId: string,
  resolution?: string | null
): Promise<Ticket> {
  const body: Record<string, unknown> = {};
  if (resolution != null && resolution.trim().length > 0) {
    body.resolution = resolution.trim();
  }
  const res = await apiFetch(`${API_BASE}/tickets/${ticketId}/unblock`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

/** GET /tickets/{id}/history — revisions · reviews · postmortems · audit. */
export async function fetchTicketHistory(ticketId: string): Promise<TicketHistory> {
  const res = await apiFetch(`${API_BASE}/tickets/${ticketId}/history`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

export interface WorkspaceSummary {
  workspace_id: string;
  name: string;
  policy: Record<string, unknown>;
  inbox_board_id: string | null;
  membership_role: string | null;
}

export interface WorkspaceMember {
  user_id: string;
  email: string | null;
  display_name: string;
  role: string;
  membership_id: string;
}

export interface AgentCredentialInfo {
  credential_id: string;
  board_scope: string[];
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  access_token?: string | null;
}

export interface WorkspaceAgent {
  agent_id: string;
  workspace_id: string;
  name: string;
  workflow_role: string;
  capabilities: string[];
  board_scope: string[];
  created_by: string;
  disabled_at: string | null;
  credentials: AgentCredentialInfo[];
}

export async function fetchWorkspaces(): Promise<WorkspaceSummary[]> {
  const res = await apiFetch(`${API_BASE}/workspaces`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(await readErrorDetail(res));
  return res.json();
}

export async function createWorkspace(name: string): Promise<WorkspaceSummary> {
  const res = await apiFetch(`${API_BASE}/workspaces`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(await readErrorDetail(res));
  return res.json();
}

export async function fetchWorkspaceMembers(
  workspaceId: string
): Promise<WorkspaceMember[]> {
  const res = await apiFetch(
    `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}/members`,
    { cache: "no-store" }
  );
  if (!res.ok) throw new Error(await readErrorDetail(res));
  return res.json();
}

export async function inviteWorkspaceMember(
  workspaceId: string,
  email: string,
  role: "admin" | "member" = "member"
): Promise<{ invite_id: string; token: string; email: string; role: string }> {
  const res = await apiFetch(
    `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}/members`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ email, role }),
    }
  );
  if (!res.ok) throw new Error(await readErrorDetail(res));
  return res.json();
}

export async function updateWorkspaceMemberRole(
  workspaceId: string,
  userId: string,
  role: "owner" | "admin" | "member"
): Promise<WorkspaceMember> {
  const res = await apiFetch(
    `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(userId)}`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ role }),
    }
  );
  if (!res.ok) throw new Error(await readErrorDetail(res));
  return res.json();
}

export interface QuotaUsageItem {
  limit: number;
  current: number;
}

export interface WorkspaceUsage {
  workspace_id: string;
  usage: Record<string, QuotaUsageItem>;
}

export async function fetchWorkspaceUsage(
  workspaceId: string
): Promise<WorkspaceUsage> {
  const res = await apiFetch(
    `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}/usage`,
    { cache: "no-store" }
  );
  if (!res.ok) throw new Error(await readErrorDetail(res));
  return res.json();
}

export async function fetchWorkspaceAgents(
  workspaceId: string
): Promise<WorkspaceAgent[]> {
  const res = await apiFetch(
    `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}/agents`,
    { cache: "no-store" }
  );
  if (!res.ok) throw new Error(await readErrorDetail(res));
  return res.json();
}

export async function createWorkspaceAgent(
  workspaceId: string,
  body: {
    name: string;
    workflow_role: "triage" | "worker" | "gatekeeper";
    capabilities?: string[];
    board_scope?: string[];
  }
): Promise<WorkspaceAgent> {
  const res = await apiFetch(
    `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}/agents`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    }
  );
  if (!res.ok) throw new Error(await readErrorDetail(res));
  return res.json();
}

export async function disableWorkspaceAgent(
  workspaceId: string,
  agentId: string
): Promise<WorkspaceAgent> {
  const res = await apiFetch(
    `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}/agents/${encodeURIComponent(agentId)}/disable`,
    { method: "POST" }
  );
  if (!res.ok) throw new Error(await readErrorDetail(res));
  return res.json();
}

export async function issueAgentCredential(
  workspaceId: string,
  agentId: string
): Promise<AgentCredentialInfo> {
  const res = await apiFetch(
    `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}/agents/${encodeURIComponent(agentId)}/credentials`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({}),
    }
  );
  if (!res.ok) throw new Error(await readErrorDetail(res));
  return res.json();
}

export async function revokeAgentCredential(
  workspaceId: string,
  agentId: string,
  credentialId: string
): Promise<AgentCredentialInfo> {
  const res = await apiFetch(
    `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}/agents/${encodeURIComponent(agentId)}/credentials/${encodeURIComponent(credentialId)}/revoke`,
    { method: "POST" }
  );
  if (!res.ok) throw new Error(await readErrorDetail(res));
  return res.json();
}

// ---------------------------------------------------------------------------
// Passkeys / user presence (#28, identity.md §6.1)
// ---------------------------------------------------------------------------

export interface PasskeySummary {
  passkey_id: string;
  label: string;
  credential_id: string;
  created_at: string | null;
  last_used_at: string | null;
}

/**
 * JSON call to the passkey endpoints (session cookie + CSRF). Changing the set of
 * passkeys needs a fresh assertion once one exists (#99): a 403 with code
 * USER_PRESENCE_REQUIRED asks for a passkey and replays once with the presence token.
 */
async function passkeyJson<T>(
  path: string,
  init: RequestInit = {},
  allowPresenceRetry = true
): Promise<T> {
  const res = await fetchWithCsrf(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers as Record<string, string>) },
  });
  if (!res.ok) {
    let message = res.statusText || `HTTP ${res.status}`;
    let code: string | null = null;
    try {
      const body = (await res.json()) as { error?: unknown; code?: unknown };
      if (typeof body.error === "string") message = body.error;
      if (typeof body.code === "string") code = body.code;
    } catch {
      // keep status text
    }
    if (res.status === 403 && code === USER_PRESENCE_REQUIRED && allowPresenceRetry) {
      const token = await requestPresenceToken();
      return passkeyJson<T>(
        path,
        { ...init, headers: { ...(init.headers as Record<string, string>), Authorization: `Bearer ${token}` } },
        false
      );
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export async function fetchPasskeys(): Promise<PasskeySummary[]> {
  const data = await passkeyJson<{ passkeys: PasskeySummary[] }>("/auth/passkeys");
  return data.passkeys;
}

/** Create a passkey with the browser authenticator and register it (session + CSRF). */
export async function registerPasskey(label: string): Promise<PasskeySummary> {
  if (!passkeysSupported()) throw new Error("This browser does not support passkeys.");
  const begin = await passkeyJson<{ challenge_id: string; options: CreationOptionsJSON }>(
    "/auth/passkeys/register/options",
    { method: "POST" }
  );
  let credential: Credential | null;
  try {
    credential = await navigator.credentials.create({
      publicKey: toCreationOptions(begin.options),
    });
  } catch (err) {
    throw new Error(describeWebAuthnError(err));
  }
  if (!credential) throw new Error("No passkey was created.");
  return passkeyJson<PasskeySummary>("/auth/passkeys/register/verify", {
    method: "POST",
    body: JSON.stringify({
      challenge_id: begin.challenge_id,
      credential: registrationToJSON(credential as PublicKeyCredential),
      label: label.trim(),
    }),
  });
}

export async function deletePasskey(passkeyId: string): Promise<void> {
  await passkeyJson<{ success: boolean }>(`/auth/passkeys/${encodeURIComponent(passkeyId)}`, {
    method: "DELETE",
  });
}

/**
 * Ask the user for a passkey and return a short-lived (60 s) user JWT that
 * carries the `pres` claim. Never cached: presence is per approval.
 */
export async function requestPresenceToken(): Promise<string> {
  if (!passkeysSupported()) {
    throw new Error("Approving here needs a passkey, but this browser does not support them.");
  }
  const workspaceId = cachedAuth?.workspaceId ?? getStoredWorkspaceId();
  if (!workspaceId) throw new Error("No workspace selected.");
  let begin: { challenge_id: string; options: RequestOptionsJSON };
  try {
    begin = await passkeyJson("/auth/passkeys/assert/options", { method: "POST" });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "";
    if (/No passkey registered/i.test(msg)) {
      throw new Error("This workspace requires a passkey to approve. Register one in Settings → Passkeys.");
    }
    throw err;
  }
  let credential: Credential | null;
  try {
    credential = await navigator.credentials.get({ publicKey: toRequestOptions(begin.options) });
  } catch (err) {
    throw new Error(describeWebAuthnError(err));
  }
  if (!credential) throw new Error("No passkey was provided.");
  const out = await passkeyJson<{ access_token: string }>("/auth/passkeys/assert/verify", {
    method: "POST",
    body: JSON.stringify({
      challenge_id: begin.challenge_id,
      credential: assertionToJSON(credential as PublicKeyCredential),
      workspace_id: workspaceId,
    }),
  });
  return out.access_token;
}

/**
 * ``apiFetch`` that answers a 403 USER_PRESENCE_REQUIRED by asking for a passkey
 * and replaying the request once with the presence token. ``init.body`` must be
 * re-sendable (a string).
 */
async function apiFetchWithPresence(input: string, init: RequestInit = {}): Promise<Response> {
  const res = await apiFetch(input, init);
  if (res.status !== 403) return res;
  let required = false;
  try {
    required = isPresenceRequiredMessage(await readErrorDetail(res.clone()));
  } catch {
    required = false;
  }
  if (!required) return res;
  const token = await requestPresenceToken();
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return fetch(input, { ...init, headers });
}

export async function updateWorkspacePolicy(
  workspaceId: string,
  policy: Record<string, unknown>
): Promise<WorkspaceSummary> {
  const res = await apiFetchWithPresence(`${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ policy }),
  });
  if (!res.ok) throw new Error(await readErrorDetail(res));
  return res.json();
}
