import { NextResponse, type NextRequest } from "next/server";

/**
 * A signed-in visitor to "/" goes straight to the app. Decided by the session
 * cookie alone (set first-party through /api), never by asking the API: that
 * kept every visitor on a loader while a sleeping API started, and woke it for
 * people who only came to read the page. A stale cookie is caught by /chat's
 * own session check, which sends them to /login.
 */
export function middleware(request: NextRequest) {
  if (request.cookies.has("session")) {
    return NextResponse.redirect(new URL("/chat", request.url));
  }
}

export const config = { matcher: "/" };
