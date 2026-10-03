"use client";
import { useState } from "react";

export default function LoginPage() {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/auth/code", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: code.trim() }) });
      if (response.ok) { window.location.assign("/autopilot"); return; }
      const body = await response.json().catch(() => ({}));
      setError(typeof body.detail === "string" ? body.detail : "Sign-in failed");
    } catch { setError("Sign-in failed. Check your connection and try again."); }
    finally { setBusy(false); }
  }
  return <section className="mx-auto max-w-lg py-16">
    <p className="font-mono text-xs uppercase tracking-widest text-copper">Your forecasting laboratory</p>
    <h2 className="mt-4 font-serif text-5xl">Welcome back.</h2>
    <p className="my-6 text-ink/70">Enter your private access code to view forecasts, manage Autopilot, and confirm outcomes. You stay signed in on this device for seven days.</p>
    <form onSubmit={submit} className="space-y-3">
      <label className="block text-sm">Access code
        <input type="password" autoComplete="current-password" required value={code} onChange={e => setCode(e.target.value)} className="mt-1 block w-full border border-rule bg-white/70 p-3 font-mono" /></label>
      {error && <p role="alert" className="text-sm text-copper">{error}</p>}
      <button disabled={busy || !code.trim()} className="inline-block bg-ink px-5 py-3 text-paper disabled:opacity-40">{busy ? "Signing in…" : "Sign in"}</button>
    </form>
    <form action="/api/auth/github" method="get" className="mt-6"><button className="text-sm underline">Continue with GitHub instead</button></form>
    <p className="mt-5 text-sm text-ink/60">ForecastLab is private and restricted to its configured owner.</p>
  </section>;
}
