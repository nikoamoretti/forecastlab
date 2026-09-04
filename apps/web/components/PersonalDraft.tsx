"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

type Draft = { run_id: string; question_id: string; status: string; progress_message: string;
  cost_usd: number; max_cost_usd: number; error?: string; contract: Record<string, any> | null; macro: Record<string, any> };
const contractFields = ["normalized_question", "yes_condition", "no_condition", "resolution_date", "authoritative_source", "resolution_method"];

export default function PersonalDraft({ onBack }: { onBack: () => void }) {
  const router = useRouter();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [kind, setKind] = useState("macro");
  const [mode, setMode] = useState("live");
  const [question, setQuestion] = useState("");
  const [indicator, setIndicator] = useState("unemployment");
  const [period, setPeriod] = useState("");
  const [threshold, setThreshold] = useState("5");
  const [comparison, setComparison] = useState("gt");
  const [release, setRelease] = useState("");
  const [revision, setRevision] = useState("first_release");
  const [cutoff, setCutoff] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const requestKey = useRef<string | null>(null);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("draft");
    if (id) api<Draft>(`/api/forecast-drafts/${id}`).then(setDraft).catch(e => setError(e.message));
  }, []);
  useEffect(() => {
    if (!draft || draft.status !== "preparing") return;
    const id = draft.run_id;
    const timer = setInterval(() => api<Draft>(`/api/forecast-drafts/${id}`)
      .then(setDraft).catch(e => setError(e.message)), 1500);
    return () => clearInterval(timer);
  }, [draft]);

  async function prepare(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    requestKey.current ||= crypto.randomUUID();
    try {
      const payload = await api<Draft>("/api/forecast-drafts", { method: "POST", body: JSON.stringify({
        request_key: requestKey.current, question, mode, as_of: mode === "backtest" ? cutoff : null,
        macro: kind === "macro" ? { indicator, observation_period: period, threshold: Number(threshold), comparison,
          release_at: `${release}:00Z`, revision_policy: revision } : null
      }) });
      setDraft(payload);
      window.history.replaceState(null, "", `/new?profile=root_event_ensemble_v1&draft=${payload.run_id}`);
    } catch (e) { setError(e instanceof Error ? e.message : "Preparation failed"); }
    finally { setBusy(false); }
  }
  async function launch() {
    if (!draft?.contract) return;
    setBusy(true); setError("");
    try {
      const review = Object.keys(draft.macro).length ? {} : Object.fromEntries(contractFields.map(key => [key, draft.contract![key]]));
      await api(`/api/forecast-drafts/${draft.run_id}/launch`, { method: "POST", body: JSON.stringify({ review }) });
      router.push(`/forecasts/${draft.question_id}`);
    } catch (e) { setError(e instanceof Error ? e.message : "Launch failed"); }
    finally { setBusy(false); }
  }
  const input = "mt-1 w-full border border-rule bg-white p-2";
  return <section className="mx-auto max-w-3xl space-y-6">
    <button onClick={onBack} className="text-sm underline">All forecast profiles</button>
    <div><p className="font-mono text-xs uppercase tracking-widest text-copper">Personal V1 · pilot</p>
      <h2 className="mt-2 font-serif text-4xl">One question. A traceable forecast.</h2>
      <p className="mt-3">Review the event once. Research and three estimates follow within a $5 estimated ceiling, including preparation. Insufficient evidence withholds the probability.</p></div>
    {error && <p role="alert" className="text-copper">{error}</p>}
    {!draft ? <form onSubmit={prepare} className="space-y-4"><fieldset disabled={busy} className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2"><label>Question type<select aria-label="Question type" className={input} value={kind} onChange={e => setKind(e.target.value)}>
        <option value="macro">U.S. macro template</option><option value="general">General binary question</option></select></label>
      <label>Mode<select aria-label="Mode" className={input} value={mode} onChange={e => setMode(e.target.value)}><option value="live">Live</option><option value="demo">Demo fixtures</option><option value="backtest">Historical cutoff</option></select></label></div>
      {kind === "macro" ? <div className="grid gap-4 md:grid-cols-2">
        <label>Indicator<select className={input} value={indicator} onChange={e => setIndicator(e.target.value)}><option value="unemployment">Unemployment rate (%)</option><option value="payrolls">Monthly nonfarm payroll change (jobs)</option><option value="cpi">CPI inflation (year-over-year %)</option></select></label>
        <label>Observation month<input className={input} type="month" required value={period} onChange={e => setPeriod(e.target.value)} /></label>
        <label>Comparison<select className={input} value={comparison} onChange={e => setComparison(e.target.value)}><option value="gt">Greater than</option><option value="ge">At least</option><option value="lt">Less than</option><option value="le">At most</option></select></label>
        <label>Threshold<input className={input} type="number" step="any" required value={threshold} onChange={e => setThreshold(e.target.value)} /></label>
        <label>Release time (UTC)<input className={input} type="datetime-local" required value={release} onChange={e => setRelease(e.target.value)} /></label>
        <label>Resolution value<select className={input} value={revision} onChange={e => setRevision(e.target.value)}><option value="first_release">First published value</option><option value="as_of_resolution">Latest value at resolution time</option></select></label>
      </div> : <label className="block">Binary question<textarea className={input} rows={4} required value={question} onChange={e => setQuestion(e.target.value)} /></label>}
      {mode === "backtest" && <label className="block">Information cutoff (ISO timestamp with timezone)<input className={input} required placeholder="2026-01-01T00:00:00Z" value={cutoff} onChange={e => setCutoff(e.target.value)} /></label>}
      <button className="border border-ink bg-ink px-5 py-3 text-paper" disabled={busy}>{busy ? "Preparing…" : "Prepare for review"}</button>
    </fieldset></form> : <div className="space-y-5">
      <p aria-live="polite">{draft.progress_message} · ${draft.cost_usd.toFixed(4)} spent of ${draft.max_cost_usd.toFixed(2)}</p>
      {draft.error && <p role="alert">{draft.error}</p>}
      {draft.status === "awaiting_review" && draft.contract && <>
        {contractFields.map(key => <label className="block" key={key}>{key.replaceAll("_", " ")}
          <textarea aria-label={key.replaceAll("_", " ")} className={input} rows={key === "resolution_method" ? 4 : 2} value={draft.contract![key] || ""}
            readOnly={Object.keys(draft.macro).length > 0} onChange={e => setDraft({ ...draft, contract: { ...draft.contract, [key]: e.target.value } })} /></label>)}
        <button onClick={launch} disabled={busy} className="border border-ink bg-ink px-5 py-3 text-paper">{busy ? "Launching…" : "Approve question and forecast"}</button>
      </>}
      {!["preparing", "awaiting_review", "failed"].includes(draft.status) && <button className="underline" onClick={() => router.push(`/forecasts/${draft.question_id}`)}>Open forecast</button>}
      <button className="block text-sm underline" onClick={() => { setDraft(null); requestKey.current = null; setError(""); window.history.replaceState(null, "", "/new?profile=root_event_ensemble_v1"); }}>Start a new draft</button>
    </div>}
  </section>;
}
