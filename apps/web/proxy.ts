import { NextResponse, type NextRequest } from "next/server";

// The app has no login and is meant to be reached as localhost. Refusing other Host headers blocks
// DNS rebinding: a page on evil.example that re-points its own name at 127.0.0.1 could otherwise
// read every page and /api response with the victim's browser. Add names you really serve the app
// under (e.g. a Tailscale host behind your own auth) to ALLOWED_HOSTS, comma-separated.
const ALLOWED = new Set(
  ["localhost", "127.0.0.1", "[::1]", ...(process.env.ALLOWED_HOSTS ?? "").split(",")]
    .map((h) => h.trim().toLowerCase())
    .filter(Boolean),
);

export function proxy(req: NextRequest) {
  const host = (req.headers.get("host") ?? "").toLowerCase().replace(/:\d+$/, "");
  if (!ALLOWED.has(host)) {
    return new NextResponse("Unknown host", { status: 421 });
  }
  return NextResponse.next();
}

export const config = {
  matcher: "/((?!_next/static|_next/image|favicon.ico).*)",
};
