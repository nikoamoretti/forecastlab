"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, pct } from "@/lib/api";

type Policy = { schema_version: string; method: string; weekly_usd: number; forecast_usd: number;
  new_per_week: number; refreshes_per_week: number; horizon_days: number; cooldown_hours: number;
  stop_minutes: number; indicators: string[]; outcome_confirmation: boolean };
type Score = { brier_score: number | null; log_loss: number | null; scored_questions: number; resolved_questions: number };
type Outcome = { id: string; question_id: string; indicator: string; period: string; value: number; units: string;
  source_url: string; quote: string; outcome: number; confirmed: boolean; adjudication_revision: number | null; confirmed_outcome: number | null };
type Dashboard = {
  enabled: boolean; qualification_enabled: boolean; pause_reason: string; policy: Policy; policy_revision: number | null;
  last_tick_at: string | null; next_tick_at: string | null; queue_count: number; enable_gaps: string[];
  budget: { used_usd: number; reserved_usd: number; final_estimate_capacity_usd: number; remaining_usd: number; week: string; resets_at: string };
  qualification: { complete: boolean; qualified_indicators: string[]; attempts: { run_id: string; indicator: string; status: string; cost_usd: number; qualified: boolean }[] };
  managed_questions: { question_id: string; indicator: string; period: string; status: string; release_at: string; release_event: string }[];
  activity: { id: string; action: string; reason: string; created_at: string }[];
  inbox: { id: string; title: string; detail: string; question_id: string | null; created_at: string; read_at: string | null }[];
  unresolved_calls: { id: string; run_id: string; stage: string; started_at: string }[];
  outcomes: Outcome[];
  metrics: { questions: number; versions: number; resolved_questions: number; coverage: number | null; abstentions: number;
    failures: number; cost_usd: number; latency_ms: number | null; initial: Score; latest_prerelease: Score;
    matched: { questions: number; initial: Score; latest_prerelease: Score };
    release_groups: { release_event: string; question_ids: string[] }[] };
};
const button = "border border-ink px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40";
const time = (value: string | null) => value ? new Date(value.endsWith("Z") || /[+-]\d\d:\d\d$/.test(value) ? value : value + "Z").toLocaleString() : "Not checked yet";
const money = (value: number) => `$${value.toFixed(2)}`;

export default function AutopilotPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    const result = await api<Dashboard>("/api/autopilot");
    setData(result);
    setPolicy(previous => previous || result.policy);
  }, []);
  useEffect(() => {
    let active = true;
    api<Dashboard>("/api/autopilot").then(result => { if (active) { setData(result); setPolicy(result.policy); } })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!data?.queue_count) return;
    const timer = setInterval(() => { if (!document.hidden) load().catch(e => setError(e.message)); }, 15000);
    return () => clearInterval(timer);
  }, [data?.queue_count, load]);
  async function change(path: string, body?: unknown, method = "POST") {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api<{ queued?: string[]; gaps?: string[] }>(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
      await load();
      setNotice(result.queued ? `${result.queued.length} qualification forecasts queued.${result.gaps?.length ? " " + result.gaps.join("; ") : ""}` : "Saved. Your activity record has been updated.");
    } catch (e) { setError(e instanceof Error ? e.message : "The action could not be completed"); }
    finally { setBusy(false); }
  }
  if (!data || !policy) return <div><h2 className="font-serif text-4xl">Autopilot</h2>{error ? <p role="alert" className="mt-4">{error}</p> : <p className="mt-4">Loading your forecasting policy…</p>}</div>;
  const pending = data.outcomes.filter(o => !o.confirmed);
  return <div className="space-y-9">
    <header className="flex flex-wrap items-end justify-between gap-5">
      <div><p className="font-mono text-xs uppercase tracking-widest text-copper">Your ongoing forecasting record</p>
        <h2 className="mt-3 font-serif text-5xl">Autopilot</h2>
        <p className="mt-4 max-w-2xl text-ink/70">ForecastLab chooses upcoming macro questions, follows the evidence, and brings outcomes back for your confirmation.</p></div>
      <div className="flex items-center gap-3"><span className="rounded-full border border-rule px-3 py-1 text-sm">{data.enabled ? "Enabled" : data.qualification_enabled ? "Qualification only" : "Paused"}</span>
        <button className={button} disabled={busy} onClick={() => load().catch(e => setError(e.message))}>Refresh status</button>
        {(data.enabled || data.qualification_enabled) && <button className={button} disabled={busy} onClick={() => change("/api/autopilot/enable", { enabled: false })}>Pause forecasting</button>}</div>
    </header>
    {error && <p role="alert" className="border-l-2 border-copper pl-4">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!data.enabled && <section className="border border-rule bg-white/40 p-5">
      <h3 className="font-serif text-2xl">{data.pause_reason || "Automatic forecasting is paused"}</h3>
      {data.enable_gaps.length > 0 && <ul className="mt-3 list-inside list-disc space-y-1 text-sm">{data.enable_gaps.map(gap => <li key={gap}>{gap}</li>)}</ul>}
      <button className={`${button} mt-5 bg-ink text-paper`} disabled={busy || data.enable_gaps.length > 0} onClick={() => change("/api/autopilot/enable", { enabled: true })}>Enable Autopilot</button>
    </section>}
    <section aria-label="Weekly spending" className="grid gap-5 border-y border-rule py-6 sm:grid-cols-3">
      {[["Used this week", data.budget.used_usd], ["Reserved", data.budget.reserved_usd + (data.budget.final_estimate_capacity_usd || 0)], ["Remaining", data.budget.remaining_usd]].map(([label, amount]) => <div key={label as string}><p className="text-sm text-ink/60">{label}</p><p className="mt-1 font-serif text-4xl">{money(amount as number)}</p></div>)}
      <p className="text-xs text-ink/60 sm:col-span-3">Week of {data.budget.week}, America/Los_Angeles. Includes retries, capacity for remaining estimates, and conservative charges when usage is unknown. Resets {time(data.budget.resets_at)}.</p>
    </section>
    <div className="grid gap-8 lg:grid-cols-2">
      <section><h3 className="font-serif text-3xl">Next actions</h3><p className="mt-3 text-sm text-ink/60">Last check: {time(data.last_tick_at)}. Scheduled every 15 minutes. {data.queue_count} queued or running jobs.</p>
        <ul className="mt-4 divide-y divide-rule">{data.managed_questions.filter(q => !["resolved", "cancelled"].includes(q.status)).map(q => <li className="py-4" key={q.question_id}>
          <Link className="underline" href={`/forecasts/${q.question_id}`}>{q.indicator} · {q.period}</Link>
          <p className="mt-1 text-sm">{q.status.replaceAll("_", " ")} · Release {time(q.release_at)}</p>
          {q.status === "schedule_review" && <div className="mt-3 flex flex-wrap gap-2"><button className={button} disabled={busy} onClick={() => change(`/api/autopilot/questions/${q.question_id}/schedule-review`, { action: "resume_unchanged" })}>Verify original schedule</button><button className={button} disabled={busy} onClick={() => change(`/api/autopilot/questions/${q.question_id}/schedule-review`, { action: "cancel" })}>Cancel this event</button></div>}
        </li>)}</ul>
        {!data.managed_questions.length && <p className="mt-4 text-ink/70">Questions appear when verified releases enter the seven-day window. Volume limits are ceilings, not targets.</p>}
      </section>
      <section><h3 className="font-serif text-3xl">Policy {data.policy_revision ? `· revision ${data.policy_revision}` : "· awaiting approval"}</h3>
        <p className="my-3 text-sm text-ink/70">Unemployment, nonfarm payrolls, CPI. Same-event ensemble. Refreshes wait at least 24 hours; forecasting stops 15 minutes before release.</p>
        <form onSubmit={e => { e.preventDefault(); change("/api/autopilot/policy", policy, "PUT"); }}>
          <div className="grid grid-cols-2 gap-4">{([['weekly_usd', 'Weekly budget ($)', 25], ['forecast_usd', 'Per forecast ($)', 5], ['new_per_week', 'New forecasts per week', 3], ['refreshes_per_week', 'Refreshes per week', 2]] as const).map(([key, label, max]) => <label className="text-sm" key={key}>{label}<input className="mt-1 w-full border border-rule bg-white/70 p-2" type="number" min={key.includes("usd") ? 0.01 : 0} max={max} step={key.includes("usd") ? 0.01 : 1} required value={policy[key]} onChange={e => setPolicy({ ...policy, [key]: Number(e.target.value) })} /></label>)}</div>
          <button className={`${button} mt-4`} disabled={busy}>Approve policy</button>
          <p className="mt-2 text-xs text-ink/60">A revision pauses dispatch until its method and settings have passed validation.</p>
        </form>
      </section>
    </div>
    <section><h3 className="font-serif text-3xl">Outcomes to confirm <span className="text-ink/50">{pending.length}</span></h3>
      {!pending.length && <p className="mt-3 text-ink/70">No outcomes are awaiting confirmation. Unconfirmed proposals never enter your scores.</p>}
      <div className="mt-4 space-y-4">{pending.map(o => <article className="border border-rule p-5" key={o.id}>
        <Link className="font-serif text-2xl underline" href={`/forecasts/${o.question_id}`}>{o.indicator} · {o.period}</Link>
        <p className="mt-2">First release: {o.value} {o.units.replaceAll("_", " ")} · Proposed outcome: <strong>{o.outcome ? "Yes" : "No"}</strong></p>
        <blockquote className="my-3 border-l border-rule pl-3 text-sm text-ink/70">{o.quote}</blockquote>
        <div className="flex flex-wrap items-center gap-4"><a className="text-sm underline" href={o.source_url} target="_blank" rel="noreferrer">Official source</a><a className="text-sm underline" href={`/api/autopilot/outcomes/${o.id}/source`}>Retained release</a>
          <button className={`${button} bg-ink text-paper`} disabled={busy} onClick={() => change(`/api/autopilot/outcomes/${o.id}/confirm`)}>Confirm outcome</button></div>
      </article>)}</div>
    </section>
    <section><h3 className="font-serif text-3xl">Your track record</h3>
      <p className="my-3 text-sm">{data.metrics.questions} questions · {data.metrics.versions} versions · {pct(data.metrics.coverage)} coverage · {data.metrics.abstentions} abstentions · {data.metrics.failures} failures</p>
      <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-y border-rule"><tr>{['Forecast', 'Scored / resolved', 'Brier score', 'Log loss'].map(t => <th className="py-3 pr-4 font-medium" key={t}>{t}</th>)}</tr></thead>
        <tbody>{[['Initial forecast', data.metrics.initial], ['Latest eligible prerelease version', data.metrics.latest_prerelease]].map(([label, score]) => { const s = score as Score; return <tr className="border-b border-rule" key={label as string}><td className="py-3 pr-4">{label as string}</td><td>{s.scored_questions} / {s.resolved_questions}</td><td>{s.brier_score?.toFixed(4) ?? '—'}</td><td>{s.log_loss?.toFixed(4) ?? '—'}</td></tr>; })}</tbody></table></div>
      <p className="mt-3 text-xs text-ink/60">{data.metrics.matched.questions} matched resolved questions. Total cost {money(data.metrics.cost_usd)}. Mean execution {data.metrics.latency_ms == null ? '—' : `${Math.round(data.metrics.latency_ms / 1000)}s`}. Lower scores are better; a small cohort does not establish superior accuracy.</p>
      <details className="mt-4"><summary className="cursor-pointer text-sm underline">Shared release events</summary><ul className="mt-2 space-y-2 text-sm">{data.metrics.release_groups.map(g => <li key={g.release_event}>{g.release_event}: {g.question_ids.length} correlated questions</li>)}</ul></details>
    </section>
    <section><h3 className="font-serif text-3xl">Inbox</h3><ul className="mt-4 divide-y divide-rule">{data.inbox.map(e => <li className="py-4" key={e.id}><div className="flex items-center justify-between gap-4"><p className={e.read_at ? "text-ink/60" : "font-medium"}>{e.title}</p>{!e.read_at && <button className="text-sm underline" disabled={busy} onClick={() => change(`/api/autopilot/inbox/${encodeURIComponent(e.id)}/read`)}>Mark read</button>}</div><p className="mt-1 text-sm text-ink/70">{e.detail}</p><p className="mt-2 text-xs text-ink/50">{time(e.created_at)} {e.question_id && <Link className="underline" href={`/forecasts/${e.question_id}`}>Open forecast</Link>}</p></li>)}</ul>{!data.inbox.length && <p className="mt-3 text-ink/70">Meaningful updates, incidents, and weekly summaries will appear here.</p>}</section>
    <details className="border-t border-rule pt-5"><summary className="cursor-pointer">Qualification and execution records</summary>
      <p className="my-3 text-sm">{data.qualification.qualified_indicators.length} / 3 indicators qualified. Qualification uses each indicator’s next official release, shares the weekly budget, and has a $15 total ceiling. Ordinary discovery stays within seven days.</p>
      <button className={button} disabled={busy || !data.policy_revision || data.qualification.complete} onClick={() => change("/api/autopilot/qualify")}>Queue eligible qualification forecasts</button>
      <ul className="mt-3 text-sm">{data.qualification.attempts.map(a => <li className="py-1" key={a.run_id}>{a.indicator}: {a.status} · {money(a.cost_usd)} · {a.qualified ? 'Qualified' : 'Not qualified'}</li>)}</ul>
      {data.unresolved_calls.length > 0 && <div className="mt-5"><h4 className="font-medium">Calls requiring reconciliation</h4><p className="mt-1 text-sm">These calls may have been charged. Their saved reservations remain; they will not be replayed automatically.</p><ul className="text-sm">{data.unresolved_calls.map(c => <li key={c.id}>{c.stage} · {time(c.started_at)} · run {c.run_id}</li>)}</ul></div>}
      <ul className="mt-5 space-y-2 text-sm">{data.activity.map(a => <li key={a.id}>{time(a.created_at)} · {a.action.replaceAll('_', ' ')} · {a.reason}</li>)}</ul>
    </details>
    <details><summary className="cursor-pointer">Confirmed outcomes and corrections</summary><div className="mt-4 space-y-4">{data.outcomes.filter(o => o.confirmed).map(o => <CorrectionForm key={o.id} outcome={o} busy={busy} save={change} />)}</div></details>
  </div>;
}

function CorrectionForm({ outcome, busy, save }: { outcome: Outcome; busy: boolean; save: (path: string, body: unknown) => Promise<void> }) {
  const [value, setValue] = useState(String(outcome.confirmed_outcome ?? "cancel"));
  const [reason, setReason] = useState("");
  const [url, setUrl] = useState(outcome.source_url);
  return <form className="border border-rule p-4" onSubmit={e => { e.preventDefault(); save(`/api/autopilot/outcomes/${outcome.id}/corrections`, { outcome: value === "cancel" ? null : Number(value), reason, evidence_url: url }); }}>
    <p className="font-medium">{outcome.indicator} · {outcome.period} · adjudication {outcome.adjudication_revision}</p>
    <div className="my-3 flex flex-wrap gap-3"><label className="text-sm">Corrected outcome<select className="ml-2 border border-rule p-2" value={value} onChange={e => setValue(e.target.value)}><option value="1">Yes</option><option value="0">No</option><option value="cancel">Cancel / unresolvable</option></select></label></div>
    <label className="block text-sm">Official evidence URL<input type="url" required value={url} onChange={e => setUrl(e.target.value)} className="mt-1 block w-full border border-rule p-2" /></label>
    <label className="mt-3 block text-sm">Correction reason<textarea required minLength={10} value={reason} onChange={e => setReason(e.target.value)} className="mt-1 block w-full border border-rule p-2" /></label>
    <button className={`${button} mt-3`} disabled={busy}>Record correction</button><p className="mt-2 text-xs text-ink/60">Creates another adjudication. The original evidence and confirmation remain in history.</p>
  </form>;
}
