import { NextRequest, NextResponse } from "next/server";

const API = process.env.FORECASTLAB_API_ORIGIN || "http://127.0.0.1:8765";

async function proxy(request: NextRequest, path: string[]) {
  const url = `${API}/demo/${path.join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const init: RequestInit = { method: request.method, headers, redirect: "manual" };
  if (!["GET", "HEAD"].includes(request.method)) {
    init.body = await request.arrayBuffer();
  }
  try {
    const response = await fetch(url, init);
    const out = new Headers(response.headers);
    out.delete("content-encoding");
    return new NextResponse(response.body, { status: response.status, headers: out });
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

export async function POST(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}
