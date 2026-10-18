import { NextRequest, NextResponse } from "next/server";
import { getAuthConfig, verifySessionCookie } from "./lib/auth";

function isPublicPath(pathname: string): boolean {
  const publicExact = new Set(["/login", "/device", "/auth/device", "/auth/me", "/auth/login", "/auth/callback", "/health", "/openapi.json"]);
  if (publicExact.has(pathname)) return true;
  const prefixes = ["/login/", "/device/", "/auth/device/", "/oauth/", "/auth/local/", "/docs", "/_next/"];
  return prefixes.some((p) => pathname.startsWith(p));
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const { isEnabled } = getAuthConfig();

  if (pathname.startsWith("/api/auth/") || isPublicPath(pathname)) {
    if (pathname === "/login" || pathname.startsWith("/login/")) {
      const session = request.cookies.get("ak5_session")?.value;
      const legacy = request.cookies.get("ak5_auth")?.value;
      if (session || (legacy && (await verifySessionCookie(legacy)))) {
        return NextResponse.redirect(new URL("/", request.url));
      }
    }
    return NextResponse.next();
  }

  // Env-password gate off → gateway / embed owns auth.
  if (!isEnabled) {
    return NextResponse.next();
  }

  const sessionCookie = request.cookies.get("ak5_session")?.value;
  const legacyCookie = request.cookies.get("ak5_auth")?.value;
  const isAuthenticated = Boolean(sessionCookie) || (await verifySessionCookie(legacyCookie));

  if (!isAuthenticated) {
    if (pathname.startsWith("/api/")) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const loginUrl = new URL("/login", request.url);
    if (pathname !== "/") {
      loginUrl.searchParams.set("from", pathname);
    }
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
