/** Path / session helpers for Next middleware (#64). */

export function isPublicDocsEnabled(
  env: NodeJS.ProcessEnv = process.env
): boolean {
  const raw = (env.AK5_PUBLIC_DOCS || "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

export interface PublicPathOptions {
  publicDocs?: boolean;
}

/** Paths that never require a dashboard session. */
export function isPublicPath(
  pathname: string,
  options: PublicPathOptions = {}
): boolean {
  const publicDocs = options.publicDocs ?? isPublicDocsEnabled();
  const publicExact = new Set([
    "/login",
    "/device",
    "/auth/device",
    "/auth/me",
    "/auth/login",
    "/auth/callback",
    "/health",
  ]);
  if (publicDocs) {
    publicExact.add("/openapi.json");
  }
  if (publicExact.has(pathname)) return true;

  const prefixes = [
    "/login/",
    "/device/",
    "/auth/device/",
    "/oauth/",
    "/auth/local/",
    "/_next/",
  ];
  if (prefixes.some((p) => pathname.startsWith(p))) return true;
  if (publicDocs) {
    if (pathname === "/docs" || pathname.startsWith("/docs/")) return true;
    if (pathname === "/redoc" || pathname.startsWith("/redoc/")) return true;
  }
  return false;
}

/** Cheap shape check before hitting ``/auth/me`` (``session_id.token``). */
export function looksLikeSessionCookie(value: string | undefined): boolean {
  if (!value) return false;
  const dot = value.indexOf(".");
  return dot > 0 && dot < value.length - 1;
}

export type AuthMeLike = { authenticated?: unknown };

/** True when ``/auth/me`` JSON says the browser session is valid. */
export function authMeIndicatesSession(body: AuthMeLike | null | undefined): boolean {
  return body?.authenticated === true;
}
