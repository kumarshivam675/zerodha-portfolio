import type { NextRequest } from "next/server";

// Proxies /api/* to the FastAPI service. Done as a route handler (not a rewrite)
// so the backend URL is read at runtime and agent SSE responses stream through.
const API_URL = process.env.API_URL ?? "http://localhost:8000";
const HOP_BY_HOP = ["host", "connection", "content-length", "accept-encoding", "transfer-encoding"];

async function proxy(req: NextRequest, ctx: RouteContext<"/api/[...path]">) {
  const { path } = await ctx.params;
  const target = `${API_URL}/api/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;

  const headers = new Headers(req.headers);
  HOP_BY_HOP.forEach((h) => headers.delete(h));
  const init: RequestInit & { duplex?: "half" } = {
    method: req.method,
    headers,
    redirect: "manual",
    cache: "no-store",
    signal: req.signal,
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = req.body;
    init.duplex = "half";
  }

  const res = await fetch(target, init);
  const out = new Headers(res.headers);
  ["content-encoding", "content-length", "transfer-encoding", "connection"].forEach((h) => out.delete(h));
  return new Response(res.body, { status: res.status, headers: out });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
