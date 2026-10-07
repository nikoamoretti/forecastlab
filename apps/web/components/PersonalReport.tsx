"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, pct } from "@/lib/api";
import { callShort } from "@/lib/verdict";
import VerdictHeadline from "@/components/VerdictHeadline";
import { ResultMark } from "@/components/CallBadges";
import type { Question, TrackRecord } from "@/lib/trackRecord";

const STATISTICAL_BASELINE_PROFILE = "statistical_baseline_v1";

function baselineValue(indicator: string, value: number | null | undefined) {
  if (value == null) return "—";
  if (indicator === "unemployment" || indicator === "cpi") return `${value.toFixed(1)}%`;
  if (indicator === "treasury_10y") return `${value.toFixed(2)}%`;
  const whole = Math.round(value).toLocaleString("en-US");
  return indicator === "jobless_claims" ? `${whole} claims` : `${value > 0 ? "+" : ""}${whole} jobs`;
}

function StatisticalBaselineDetails({ baseline }: { baseline: any }) {
  const value = (v: number | null | undefined) => baselineValue(baseline.indicator, v);
  const unit = baseline.horizon.h === 1 ? baseline.horizon.unit : `${baseline.horizon.unit}s`;
  return <section className="border-y border-rule py-5"><h3 className="font-serif text-2xl">How the statistical baseline got this number</h3>
    <p className="mt-2">{baseline.rationale}</p>
    <p className="mt-3">Most likely published value {value(baseline.mode)} · 80% range {value(baseline.interval_80[0])} to {value(baseline.interval_80[1])} · 90% range {value(baseline.interval_90[0])} to {value(baseline.interval_90[1])}</p>
    <div className="mt-3 overflow-x-auto"><table className="text-left text-sm"><thead><tr><th className="border-b border-rule p-2">Percentile</th>{Object.keys(baseline.quantiles).map((level: string) => <th key={level} className="border-b border-rule p-2">{level}th</th>)}</tr></thead>
      <tbody><tr><td className="p-2">Published value</td>{Object.entries(baseline.quantiles).map(([level, v]) => <td key={level} className="p-2">{value(v as number)}</td>)}</tr></tbody></table></div>
    <p className="mt-3 text-xs text-ink/70">Deterministic rule {baseline.rule_version}: {String(baseline.point_rule).replaceAll("_", " ")}, plus the rule&apos;s own errors {baseline.horizon.h} {unit} ahead over {baseline.window.start} to {baseline.window.end} ({baseline.window.observations} observations, n = {baseline.n}, k = {baseline.k}). {baseline.probability_rule}; unbounded {(baseline.raw_probability * 100).toFixed(1)}%. No model or search call; cost $0.</p>
    {baseline.snapshot && <p className="mt-1 text-xs text-ink/70">Official data: <a className="underline" href={baseline.snapshot.source_url} rel="noreferrer">{baseline.snapshot.source_url}</a> · retrieved {baseline.snapshot.retrieved_at} · {String(baseline.snapshot.revision_basis || "").replaceAll("_", " ")} · last observation {baseline.last_observation.period} = {value(baseline.last_observation.value)} · raw response SHA-256 <span className="break-all font-mono">{baseline.snapshot.raw_hash}</span></p>}
  </section>;
}

export default function PersonalReport({ data, rerun }: { data: any; rerun: () => Promise<void> }) {
  const [evidenceUrl, setEvidenceUrl] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [decided, setDecided] = useState<Question | null>(null);
  useEffect(() => {
    api<TrackRecord>("/api/track-record")
      .then(record => setDecided(record.questions.find(q => q.id === data.id && q.status === "resolved") ?? null))
      .catch(() => undefined);
  }, [data.id]);
  const run = data.latest_run;
  const result = data.personal_report || {};
  const baseline = result.statistical_baseline;
  const isBaseline = run.profile_id === STATISTICAL_BASELINE_PROFILE || result.method === STATISTICAL_BASELINE_PROFILE;
  const evidence = result.evidence_assessments || [];
  const contract = result.contract || data.forecast_contract || {};
  const history = data.runs?.length ? data.runs.map((historicalRun: any) => {
    const version = (data.versions || []).find((v: any) => v.run_id === historicalRun.id);
    return { ...version, id: historicalRun.id, run_id: historicalRun.id, created_at: historicalRun.created_at,
      profile_id: historicalRun.profile_id, status: historicalRun.status,
      ensemble_probability: historicalRun.status === "completed" ? version?.ensemble_probability : null };
  }) : data.versions || [];
  const terminal = ["completed", "failed"].includes(run.status);
  return <article className="mx-auto max-w-4xl space-y-8 [overflow-wrap:anywhere]">
    <Link href="/" className="inline-block py-1 text-sm text-ink/70 hover:text-ink">‹ Track record</Link>
    {data.is_historical_view && <p className="border border-rule p-3">Viewing a historical run. <Link className="underline" href={`/forecasts/${data.id}`}>Open latest forecast</Link></p>}
    <header>
      <h2 className="font-serif text-4xl leading-tight">{data.original_text}</h2>
      {decided && <Link href={`/q/${decided.id}`} className="mt-5 flex flex-wrap items-center gap-3 border border-ink/30 bg-white/70 p-4 hover:bg-white">
        <ResultMark verdict={decided.verdict} large /><span>Decided: <strong>{decided.outcome ? "Yes" : "No"}</strong>{decided.actual && ` (actual ${decided.actual})`}</span>
        <span className="text-sm text-ink/60">See the result ›</span></Link>}
      {run.status !== "failed" && result.probability != null
        ? <div className="mt-5"><VerdictHeadline probability={result.probability} /></div>
        : <p className="mt-5 font-serif text-5xl">{run.status === "failed" ? "Forecast failed" : terminal ? "No forecast: not enough evidence" : "Research in progress"}</p>}
      {!terminal && <p className="mt-3" aria-live="polite">{run.progress_message}</p>}
      {run.error_message && <p role="alert" className="mt-2 text-copper">{run.error_message}</p>}
    </header>
    <section className="border-y border-rule py-5"><h3 className="font-serif text-2xl">What resolves this question</h3>
      <p className="mt-2">{contract.yes_condition}</p><p className="mt-2 text-sm">{contract.no_condition}</p>
      <p className="mt-2 text-sm">{contract.resolution_date} · <a href={contract.authoritative_source} className="underline" rel="noreferrer">Authoritative source</a></p>
      <p className="mt-2 text-sm">{contract.resolution_method}</p></section>
    {(result.evidence_gaps || []).length > 0 && <section><h3 className="font-serif text-2xl">Research gaps</h3><ul className="mt-3 list-disc space-y-2 pl-5">{result.evidence_gaps.map((s: string) => <li key={s}>{s.replaceAll("_", " ")}</li>)}</ul></section>}
    {baseline && <StatisticalBaselineDetails baseline={baseline} />}
    {!isBaseline && (["supporting", "opposing", "background"] as const).filter(kind => evidence.some((e: any) => e.usable && e.classification === kind)).map(kind => <section key={kind}><h3 className="font-serif text-2xl">{kind === "supporting" ? "Supporting evidence" : kind === "opposing" ? "Opposing evidence" : "Background and measurements"}</h3>
      <ul className="mt-3 space-y-4">{evidence.filter((e: any) => e.usable && e.classification === kind).map((e: any) => <li key={e.claim_id} className="border-l-2 border-rule pl-4">
        <a href={e.url} className="underline" rel="noreferrer">{e.title || e.url}</a>{e.claim?.length > 1200 ? <details className="mt-2 text-sm"><summary className="cursor-pointer">Inspect the full observation history</summary><p className="mt-2 whitespace-pre-wrap">{e.claim}</p></details> : <p className="mt-1 whitespace-pre-wrap text-sm">{e.claim}</p>}
        {e.quote && <blockquote className="mt-2 text-sm text-ink/70">“{e.quote}”</blockquote>}<p className="mt-1 text-xs text-ink/60">Available {e.source_available_at} · {e.source_lineage}</p></li>)}</ul>
</section>)}
    {(result.estimates || []).length > 0 && <section><h3 className="font-serif text-2xl">Three estimates of the same event</h3>
      <p className="mt-2 text-sm">Shared evidence; estimates do not see each other. Spread is disagreement, not a confidence interval.</p>
      {result.aggregation?.spread != null && <p className="mt-2 text-sm">Estimate spread: {(result.aggregation.spread * 100).toFixed(1)} percentage points.</p>}
      <div className="mt-4 grid gap-4 md:grid-cols-3">{result.estimates.map((e: any) => <div className="border border-rule p-4" key={e.role}><p>{e.role.replaceAll("_", " ")}</p><p className="mt-2 font-serif text-3xl">{pct(e.probability)}</p><p className="mt-3 text-sm">{e.reasoning}</p></div>)}</div></section>}
    {!isBaseline && (result.developments_to_watch || []).length > 0 && <section><h3 className="font-serif text-2xl">Developments to watch</h3><ul className="mt-3 list-disc space-y-2 pl-5">{(result.developments_to_watch || []).map((s: string) => <li key={s}>{s}</li>)}</ul></section>}
    {terminal && !data.is_benchmark && !data.is_historical_view && <button disabled={busy} onClick={async () => { setBusy(true); setMessage(""); try { await rerun(); } catch (e) { setMessage(e instanceof Error ? e.message : "Rerun failed"); } finally { setBusy(false); } }} className="border border-ink px-4 py-2">Run a fresh forecast</button>}
    {run.mode !== "demo" && <details><summary className="cursor-pointer">Add evidence for a future rerun</summary>
      <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={async event => { event.preventDefault(); setBusy(true); setMessage("");
        try { const receipt = await api<any>(`/api/questions/${data.id}/evidence-urls`, { method: "POST", body: JSON.stringify({ url: evidenceUrl, intended_use: "general_question_evidence", mode: run.mode, as_of: run.mode === "backtest" ? run.as_of : undefined }) });
          setMessage(receipt.accepted ? "Evidence accepted. Run a fresh forecast to include it." : `Evidence rejected: ${receipt.rejection_reason}`); setEvidenceUrl("");
        } catch (e) { setMessage(e instanceof Error ? e.message : "Evidence intake failed"); } finally { setBusy(false); }
      }}><label className="flex-1">Evidence URL<input type="url" required className="mt-1 w-full border border-rule p-2" value={evidenceUrl} onChange={e => setEvidenceUrl(e.target.value)} /></label><button disabled={busy} className="border border-ink px-4 py-2">Check evidence URL</button></form>
    </details>}
    {message && <p role="status">{message}</p>}
    {result.question_selection && <details className="border-t border-rule pt-4"><summary className="cursor-pointer">Why ForecastLab picked this question</summary>
      <p className="mt-3 text-sm">{result.question_selection.reason}</p>
      <p className="mt-2 text-sm"><a className="underline" href={result.question_selection.schedule.source_url}>Release calendar</a> · <a className="underline" href={result.question_selection.baseline.source_url}>Threshold source</a></p>
      <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(result.question_selection, null, 2)}</pre></details>}
    <section><h3 className="font-serif text-2xl">Version history</h3><ul className="mt-3 space-y-2">{history.map((v: any) => <li key={v.id}><Link className="underline" href={`/forecasts/${data.id}?run=${v.run_id}`}>{v.created_at} · {v.status === "failed" ? "Forecast failed" : v.status && v.status !== "completed" ? v.status.replaceAll("_", " ") : v.ensemble_probability == null ? "No forecast: not enough evidence" : callShort(v.ensemble_probability)} · {v.profile_id}</Link></li>)}</ul></section>
    <details className="border-t border-rule pt-4"><summary className="cursor-pointer">Technical details</summary>
      <p className="mt-3 text-sm">{run.mode} mode · {run.profile_id} · ${Number(run.total_cost_usd || 0).toFixed(4)} including preparation · {data.automation ? `Autopilot question, ${data.automation.latest_version_kind} version, ${data.automation.status.replaceAll("_", " ")}` : data.stale ? "stale after a watched change" : "manual updates"}{result.forecast_cutoff ? ` · information cutoff ${result.forecast_cutoff}` : ""}</p>
      <pre className="mt-4 max-h-[40rem] overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify({ graph: run.forecast_graph, budget: run.budget, attempts: run.run_attempts, ledger: run.provider_call_ledger, prompts: run.frozen_prompts, research: result.research_diagnostics, rejected: evidence.filter((e: any) => !e.usable), aggregation: result.aggregation }, null, 2)}</pre></details>
  </article>;
}
