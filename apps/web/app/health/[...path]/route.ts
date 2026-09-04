import { NextRequest, NextResponse } from "next/server";

const API = process.env.FORECASTLAB_API_ORIGIN || "http://127.0.0.1:8765";

async function proxy(request: NextRequest, path: string[]) {
  const url = `${API}/health/${path.join("/")}${request.nextUrl.search}`;
  try {
    const response = await fetch(url, {
      method: request.method,
      redirect: "manual"
    });
    const headers = new Headers(response.headers);
    headers.delete("content-encoding");
    headers.delete("transfer-encoding");
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
