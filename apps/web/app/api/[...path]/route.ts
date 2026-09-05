import { after, NextRequest, NextResponse } from "next/server";

export const maxDuration = 600;
const API = process.env.FORECASTLAB_API_ORIGIN || "http://127.0.0.1:8765";

async function proxy(request: NextRequest, path: string[]) {
  if (path.some(part => part === "." || part === ".." || /[\\/]/.test(part))) {
    return NextResponse.json({ detail: "Invalid API path" }, { status: 400 });
  }
  if (process.env.VERCEL && process.env.VERCEL_ENV !== "production" && !process.env.FORECASTLAB_PREVIEW_API_ORIGIN) {
    return NextResponse.json({ detail: "This preview has no production data or provider access" }, { status: 503 });
  }
  const origin = process.env.VERCEL_ENV === "preview" ? process.env.FORECASTLAB_PREVIEW_API_ORIGIN || API : API;
  const url = `${origin}/api/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  for (const key of ["content-type", "cookie", "origin", "x-csrf-token"]) {
    const value = request.headers.get(key);
    if (value) headers.set(key, value);
  }
  if (process.env.FORECASTLAB_INTERNAL_SECRET) headers.set("x-forecastlab-internal", process.env.FORECASTLAB_INTERNAL_SECRET);
  const init: RequestInit = { method: request.method, headers, redirect: "manual", cache: "no-store" };
  if (!["GET", "HEAD"].includes(request.method)) init.body = await request.arrayBuffer();
  try {
    const response = await fetch(url, init);
    const out = new Headers();
    for (const key of ["content-type", "content-disposition", "location"]) {
      const value = response.headers.get(key);
      if (value) out.set(key, value);
    }
    for (const cookie of response.headers.getSetCookie()) out.append("set-cookie", cookie);
    out.set("cache-control", "private, no-store");
    const route = path.join("/");
    const queuedWork = request.method === "POST" && response.ok && (
      /^forecast-drafts(?:\/[^/]+\/launch)?$/.test(route) ||
      /^questions\/[^/]+\/(?:run|rerun)$/.test(route) ||
      /^prospective\/cohorts\/[^/]+\/launch$/.test(route) || route === "autopilot/qualify");
    if (queuedWork && process.env.FORECASTLAB_INTERNAL_SECRET) {
      after(async () => {
        try {
          const result = await fetch(`${origin}/internal/process`, { method: "POST", cache: "no-store",
            headers: { authorization: `Bearer ${process.env.FORECASTLAB_INTERNAL_SECRET}` } });
          if (!result.ok) console.error("forecastlab_worker_kick_failed", { status: result.status });
          await result.arrayBuffer();
        } catch {
          console.error("forecastlab_worker_kick_unreachable; queued work will be recovered by cron");
        }
      });
    }
    return new NextResponse(response.body, { status: response.status, headers: out });
  } catch {
    return NextResponse.json({ error: "api_unreachable", detail: "The forecasting service is temporarily unavailable" }, { status: 502 });
  }
}

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}
export async function POST(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}
export async function PUT(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}
export async function PATCH(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}
