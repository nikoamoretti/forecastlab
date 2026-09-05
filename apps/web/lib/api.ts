const API = process.env.NEXT_PUBLIC_API_URL || "";

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = typeof FormData !== "undefined" && init?.body instanceof FormData;
  const method = (init?.method || "GET").toUpperCase();
  // Writes can create Questions, contracts, runs, and provider-backed work. A
  // transport failure after the server has accepted one must stay observable,
  // rather than being replayed by the browser and creating duplicate work.
  const attempts = ["GET", "HEAD", "OPTIONS"].includes(method) ? 3 : 1;
  let lastError: Error | null = null;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const headers = new Headers(init?.headers);
    if (!["GET", "HEAD", "OPTIONS"].includes(method) && typeof document !== "undefined") {
      const csrf = document.cookie.split("; ").find(c => c.startsWith("forecastlab_csrf="))?.split("=").slice(1).join("=");
      if (csrf) headers.set("x-csrf-token", decodeURIComponent(csrf));
    }
    if (!isForm && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const response = await fetch(`${API}${path}`, {
      ...init,
      headers,
      cache: "no-store"
    });
    const text = await response.text();
    if (response.status === 401 && typeof window !== "undefined" && window.location.pathname !== "/login") {
      // A full navigation clears cached private client state at the auth boundary.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/login");
      throw new Error("Sign in to ForecastLab");
    }
    if (response.status === 404 && attempt < attempts - 1) {
      await sleep(120 * (attempt + 1));
      continue;
    }
    if (!response.ok) {
      let detail = `${response.status} ${path}`;
      try {
        const body = text ? JSON.parse(text) : {};
        if (typeof body.detail === "string") detail = body.detail;
        else if (Array.isArray(body.detail?.gaps)) detail = body.detail.gaps.join(". ");
        else if (Array.isArray(body.reasons) && body.reasons.length) detail = body.reasons.join(", ");
      } catch {
        if (text) detail = text.slice(0, 240);
      }
      lastError = new Error(detail);
      if (response.status >= 500 && attempt < attempts - 1) {
        await sleep(150 * (attempt + 1));
        continue;
      }
      throw lastError;
    }
    return (text ? JSON.parse(text) : {}) as T;
  }
  throw lastError || new Error(`failed ${path}`);
}

export function pct(value?: number | null) {
  if (value == null) return "—";
  return `${(value * 100).toFixed(1)}%`;
}
