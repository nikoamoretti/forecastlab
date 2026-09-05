import { NextRequest, NextResponse } from "next/server";

const API = process.env.FORECASTLAB_API_ORIGIN || "http://127.0.0.1:8765";

async function proxy(request: NextRequest, path: string[]) {
  if (path.some(p => p === "." || p === ".." || /[\\/]/.test(p))) return new NextResponse(null, { status: 400 });
  if (process.env.VERCEL && process.env.VERCEL_ENV !== "production") return new NextResponse(null, { status: 503 });
  const url = `${API}/health/${path.join("/")}${request.nextUrl.search}`;
  try {
    const response = await fetch(url, {
      method: request.method,
      headers: { cookie: request.headers.get("cookie") || "", "x-forecastlab-internal": process.env.FORECASTLAB_INTERNAL_SECRET || "" },
      cache: "no-store",
      redirect: "manual"
    });
    const headers = new Headers(response.headers);
    headers.delete("content-encoding");
    headers.delete("transfer-encoding");
    headers.set("cache-control", "private, no-store");
    return new NextResponse(response.body, { status: response.status, headers });
  } catch (error) {
    return NextResponse.json(
      { error: "api_unreachable", detail: error instanceof Error ? error.message : "fetch failed" },
      { status: 502 }
    );
  }
}

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}
