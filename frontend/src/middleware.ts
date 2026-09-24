import { NextRequest, NextResponse } from "next/server";
import { getAuthConfig, verifySessionCookie } from "./lib/auth";

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const { isEnabled } = getAuthConfig();

  // If authentication is disabled, pass all requests
  if (!isEnabled) {
    return NextResponse.next();
  }

  const cookie = request.cookies.get("ak5_auth")?.value;
  const isAuthenticated = await verifySessionCookie(cookie);

  // Allow auth API routes unconditionally
  if (pathname.startsWith("/api/auth/")) {
    return NextResponse.next();
  }

  // If navigating to /login
  if (pathname === "/login") {
    if (isAuthenticated) {
      return NextResponse.redirect(new URL("/", request.url));
    }
    return NextResponse.next();
  }

  // Unauthenticated requests
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
