/**
 * Server-side proxy to the backend API.
 *
 * The browser never sees the API token: this handler attaches it. Because the dashboard is a
 * local, single-owner tool, the proxy also enforces:
 *  - Host must be localhost/127.0.0.1 (DNS-rebinding defence),
 *  - state-changing requests must be same-origin (CSRF defence),
 *  - only plain path segments are forwarded (no traversal or scheme tricks).
 */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BACKEND = process.env.QAI_BACKEND_URL ?? "http://127.0.0.1:8000";
const SAFE_SEGMENT = /^[A-Za-z0-9._-]+$/;
const LOCAL_HOST = /^(localhost|127\.0\.0\.1)(:\d+)?$/;
const FORWARDED_RESPONSE_HEADERS = ["content-type", "content-disposition", "x-content-type-options"];

function reject(status: number, detail: string): Response {
  return Response.json({ detail }, { status });
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const token = process.env.QAI_API_TOKEN;
  if (!token) return reject(500, "QAI_API_TOKEN is not configured (run scripts/setup-env.ps1)");

  const host = request.headers.get("host") ?? "";
  if (!LOCAL_HOST.test(host)) return reject(403, "The dashboard is only served on localhost.");

  if (request.method !== "GET" && request.method !== "HEAD") {
    const origin = request.headers.get("origin");
    if (!origin || new URL(origin).host !== host) return reject(403, "Cross-origin request blocked.");
  }

  const { path } = await context.params;
  if (path.some((segment) => !SAFE_SEGMENT.test(segment) || segment === "." || segment === "..")) {
    return reject(400, "Invalid path.");
  }

  const headers = new Headers({ authorization: `Bearer ${token}` });
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND}/api/${path.join("/")}${request.nextUrl.search}`, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return reject(502, "The backend API is not reachable. Start it with scripts/dev.ps1.");
  }

  const responseHeaders = new Headers({ "cache-control": "no-store" });
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export { proxy as DELETE, proxy as GET, proxy as PATCH, proxy as POST, proxy as PUT };
