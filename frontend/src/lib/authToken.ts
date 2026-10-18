/** Principal JWT cache helpers for the dashboard (#72) — short TTL + 401 retry. */

/** Re-mint this long before ``exp`` so in-flight requests do not race expiry. */
export const TOKEN_REFRESH_SKEW_MS = 60_000;

export interface CachedAuthToken {
  token: string;
  workspaceId: string | null;
  /** Epoch ms; ``null`` when the server did not report ``expires_in``. */
  expiresAt: number | null;
}

export function expiresAtFromNow(
  expiresInSeconds: number | null | undefined,
  now: number = Date.now()
): number | null {
  if (typeof expiresInSeconds !== "number" || !Number.isFinite(expiresInSeconds)) {
    return null;
  }
  return now + expiresInSeconds * 1000;
}

/** True when ``entry`` can be reused for ``desiredWorkspaceId`` at ``now``. */
export function isCachedTokenUsable(
  entry: CachedAuthToken | null,
  desiredWorkspaceId: string | null,
  now: number = Date.now()
): entry is CachedAuthToken {
  if (!entry || !entry.token) return false;
  if (desiredWorkspaceId && desiredWorkspaceId !== entry.workspaceId) return false;
  if (entry.expiresAt !== null && now >= entry.expiresAt - TOKEN_REFRESH_SKEW_MS) {
    return false;
  }
  return true;
}

/**
 * Run ``send`` with a token; on 401 drop the cache, mint again, and retry once.
 * ``send`` must be safe to call twice (same body).
 */
export async function fetchWithAuthRetry(
  send: (token: string) => Promise<Response>,
  getToken: () => Promise<string>,
  invalidate: () => void
): Promise<Response> {
  const res = await send(await getToken());
  if (res.status !== 401) return res;
  invalidate();
  return send(await getToken());
}
