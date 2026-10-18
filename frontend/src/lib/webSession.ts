/** Browser web-session helpers (identity.md §3.2 / #27) — CSRF + cookie-aware fetch. */

export const CSRF_COOKIE = "ak5_csrf";
export const CSRF_HEADER = "X-CSRF-Token";
export const SESSION_COOKIE = "ak5_session";

/** Read ``ak5_csrf`` from ``document.cookie`` (browser only). */
export function readCsrfToken(cookieHeader?: string): string {
  const raw =
    cookieHeader ??
    (typeof document !== "undefined" ? document.cookie : "");
  const match = raw.match(/(?:^|;\s*)ak5_csrf=([^;]*)/);
  if (!match) return "";
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return match[1];
  }
}

export function hasSessionCookie(cookieHeader?: string): boolean {
  const raw =
    cookieHeader ??
    (typeof document !== "undefined" ? document.cookie : "");
  return /(?:^|;\s*)ak5_session=/.test(raw) || /(?:^|;\s*)ak5_auth=/.test(raw);
}

/** Merge CSRF header when a token is available. */
export function withCsrfHeaders(
  init?: HeadersInit,
  csrf?: string
): Record<string, string> {
  const base: Record<string, string> = {};
  if (init instanceof Headers) {
    init.forEach((value, key) => {
      base[key] = value;
    });
  } else if (Array.isArray(init)) {
    for (const [key, value] of init) {
      base[key] = value;
    }
  } else if (init) {
    Object.assign(base, init);
  }
  const token = csrf ?? readCsrfToken();
  if (token) {
    base[CSRF_HEADER] = token;
  }
  return base;
}

/**
 * Same-origin fetch that includes cookies and attaches ``X-CSRF-Token`` for
 * mutating methods (POST/PUT/PATCH/DELETE).
 */
export async function fetchWithCsrf(
  input: RequestInfo | URL,
  init: RequestInit = {}
): Promise<Response> {
  const method = (init.method || "GET").toUpperCase();
  const mutating = !["GET", "HEAD", "OPTIONS"].includes(method);
  const headers = mutating
    ? withCsrfHeaders(init.headers)
    : (() => {
        const h: Record<string, string> = {};
        if (init.headers instanceof Headers) {
          init.headers.forEach((v, k) => {
            h[k] = v;
          });
        } else if (Array.isArray(init.headers)) {
          for (const [k, v] of init.headers) h[k] = v;
        } else if (init.headers) {
          Object.assign(h, init.headers);
        }
        return h;
      })();
  return fetch(input, {
    ...init,
    credentials: init.credentials ?? "same-origin",
    headers,
  });
}

export interface AuthMeResponse {
  authenticated: boolean;
  user_id: string | null;
  oidc_configured: boolean;
  local_owner_configured: boolean;
  providers: Array<{ id: string; issuer: string }>;
}

export async function fetchAuthMe(): Promise<AuthMeResponse | null> {
  try {
    const res = await fetch("/auth/me", { credentials: "same-origin", cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as AuthMeResponse;
  } catch {
    return null;
  }
}

/** Normalize OAuth device lookup JSON into the dashboard view model. */
export function mapOauthDeviceRequest(raw: Record<string, unknown>): {
  user_code: string;
  actor_id: string;
  actor_type: string;
  name: string;
  role: string;
  capabilities: string[];
  status: string;
  target_board_ids: string[];
  expires_at: string;
  principal: string;
  workspace_id: string;
} {
  const principal = String(raw.principal || "agent");
  const name = String(raw.requested_name || raw.name || principal);
  const role = String(raw.requested_role || raw.role || (principal === "user" ? "human" : "worker"));
  let capabilities: string[] = [];
  const caps = raw.requested_capabilities ?? raw.capabilities;
  if (Array.isArray(caps)) {
    capabilities = caps.map(String);
  } else if (typeof caps === "string") {
    try {
      const parsed = JSON.parse(caps) as unknown;
      if (Array.isArray(parsed)) capabilities = parsed.map(String);
    } catch {
      capabilities = [];
    }
  }
  let boardScope: string[] = [];
  const scope = raw.requested_board_scope ?? raw.target_board_ids ?? raw.board_scope;
  if (Array.isArray(scope)) {
    boardScope = scope.map(String);
  }
  return {
    user_code: String(raw.user_code || ""),
    actor_id: String(raw.actor_id || name || principal),
    actor_type: principal === "user" ? "human" : "agent",
    name,
    role,
    capabilities,
    status: String(raw.status || "pending"),
    target_board_ids: boardScope,
    expires_at: String(raw.expires_at || ""),
    principal,
    workspace_id: String(raw.workspace_id || ""),
  };
}
