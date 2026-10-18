import { NextRequest, NextResponse } from "next/server";
import { getAuthConfig, verifySessionCookie } from "./lib/auth";
import {
  authMeIndicatesSession,
  isPublicPath,
  looksLikeSessionCookie,
} from "./lib/middlewareGate";

async function verifyAk5Session(request: NextRequest): Promise<boolean> {
  const sessionCookie = request.cookies.get("ak5_session")?.value;
  if (!looksLikeSessionCookie(sessionCookie)) {
    return false;
  }
  try {
    const meUrl = new URL("/auth/me", request.url);
    const res = await fetch(meUrl, {
      headers: { cookie: request.headers.get("cookie") ?? "" },
      cache: "no-store",
    });
    if (!res.ok) return false;
    const body = (await res.json()) as { authenticated?: unknown };
    return authMeIndicatesSession(body);
  } catch {
    return false;
  }
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const { isEnabled } = getAuthConfig();

  if (pathname.startsWith("/api/auth/") || isPublicPath(pathname)) {
    if (pathname === "/login" || pathname.startsWith("/login/")) {
      const hasSession = await verifyAk5Session(request);
      const legacy = request.cookies.get("ak5_auth")?.value;
      if (hasSession || (legacy && (await verifySessionCookie(legacy)))) {
        return NextResponse.redirect(new URL("/", request.url));
      }
    }
    return NextResponse.next();
  }

  // Env-password gate off → gateway / embed owns auth.
  if (!isEnabled) {
    return NextResponse.next();
  }

  const hasSession = await verifyAk5Session(request);
  const legacyCookie = request.cookies.get("ak5_auth")?.value;
  const isAuthenticated =
    hasSession || (await verifySessionCookie(legacyCookie));

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
