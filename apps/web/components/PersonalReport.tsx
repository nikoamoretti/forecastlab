"use client";
import Link from "next/link";
import { useState } from "react";
import { api, pct } from "@/lib/api";
import { verdictShort } from "@/lib/verdict";
import VerdictHeadline from "@/components/VerdictHeadline";

export default function PersonalReport({ data, rerun }: { data: any; rerun: () => Promise<void> }) {
  const [evidenceUrl, setEvidenceUrl] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const run = data.latest_run;
  const result = data.personal_report || {};
  const evidence = result.evidence_assessments || [];
  const contract = result.contract || data.forecast_contract || {};
  const history = data.runs?.length ? data.runs.map((historicalRun: any) => {
    const version = (data.versions || []).find((v: any) => v.run_id === historicalRun.id);
    return { ...version, id: historicalRun.id, run_id: historicalRun.id, created_at: historicalRun.created_at,
      profile_id: historicalRun.profile_id, status: historicalRun.status,
      ensemble_probability: historicalRun.status === "completed" ? version?.ensemble_probability : null };
  }) : data.versions || [];
  const terminal = ["completed", "failed"].includes(run.status);
  return <article className="space-y-8">
    {data.is_historical_view && <p className="border border-rule p-3">Viewing a historical run. <Link className="underline" href={`/forecasts/${data.id}`}>Open latest forecast</Link></p>}
    <header><p className="font-mono text-xs uppercase tracking-widest text-copper">{run.mode} · Personal V1 · {String(data.outcome_status || run.status).replaceAll("_", " ")}</p>
      <h2 className="mt-3 max-w-4xl font-serif text-4xl">{data.original_text}</h2>
      {run.status !== "failed" && result.probability != null
        ? <div className="mt-5"><VerdictHeadline probability={result.probability} /></div>
        : <p className="mt-5 font-serif text-5xl">{run.status === "failed" ? "Forecast failed" : terminal ? "Probability withheld" : "Research in progress"}</p>}
      <p className="mt-3" aria-live="polite">{run.progress_message}</p>
      {run.error_message && <p role="alert" className="mt-2 text-copper">{run.error_message}</p>}
      <p className="mt-2 text-sm">${Number(run.total_cost_usd || 0).toFixed(4)} including preparation · {data.automation ? `Autopilot question · ${data.automation.latest_version_kind} version · ${data.automation.status.replaceAll("_", " ")}` : data.stale ? "Stale after a watched change" : "Manual updates"}</p>
      {result.forecast_cutoff && <p className="mt-2 text-sm">Information cutoff: {result.forecast_cutoff}</p>}
    </header>
    <section className="border-y border-rule py-5"><h3 className="font-serif text-2xl">What resolves this question</h3>
      <p className="mt-2">{contract.yes_condition}</p><p className="mt-2 text-sm">{contract.no_condition}</p>
      <p className="mt-2 text-sm">{contract.resolution_date} · <a href={contract.authoritative_source} className="underline" rel="noreferrer">Authoritative source</a></p>
      <p className="mt-2 text-sm">{contract.resolution_method}</p></section>
    {(result.evidence_gaps || []).length > 0 && <section><h3 className="font-serif text-2xl">Research gaps</h3><ul className="mt-3 list-disc space-y-2 pl-5">{result.evidence_gaps.map((s: string) => <li key={s}>{s.replaceAll("_", " ")}</li>)}</ul></section>}
    {(["supporting", "opposing", "background"] as const).map(kind => <section key={kind}><h3 className="font-serif text-2xl">{kind === "supporting" ? "Supporting evidence" : kind === "opposing" ? "Opposing evidence" : "Background and measurements"}</h3>
      <ul className="mt-3 space-y-4">{evidence.filter((e: any) => e.usable && e.classification === kind).map((e: any) => <li key={e.claim_id} className="border-l-2 border-rule pl-4">
        <a href={e.url} className="underline" rel="noreferrer">{e.title || e.url}</a>{e.claim?.length > 1200 ? <details className="mt-2 text-sm"><summary className="cursor-pointer">Inspect the full observation history</summary><p className="mt-2 whitespace-pre-wrap">{e.claim}</p></details> : <p className="mt-1 whitespace-pre-wrap text-sm">{e.claim}</p>}
        {e.quote && <blockquote className="mt-2 text-sm text-ink/70">“{e.quote}”</blockquote>}<p className="mt-1 text-xs text-ink/60">Available {e.source_available_at} · {e.source_lineage}</p></li>)}</ul>
      {!evidence.some((e: any) => e.usable && e.classification === kind) && <p className="mt-2 text-sm text-ink/60">No validated findings in this category.</p>}</section>)}
    {(result.estimates || []).length > 0 && <section><h3 className="font-serif text-2xl">Three estimates of the same event</h3>
      <p className="mt-2 text-sm">Shared evidence; estimates do not see each other. Spread is disagreement, not a confidence interval.</p>
      {result.aggregation?.spread != null && <p className="mt-2 text-sm">Estimate spread: {(result.aggregation.spread * 100).toFixed(1)} percentage points.</p>}
      <div className="mt-4 grid gap-4 md:grid-cols-3">{result.estimates.map((e: any) => <div className="border border-rule p-4" key={e.role}><p>{e.role.replaceAll("_", " ")}</p><p className="mt-2 font-serif text-3xl">{pct(e.probability)}</p><p className="mt-3 text-sm">{e.reasoning}</p></div>)}</div></section>}
    <section><h3 className="font-serif text-2xl">Developments to watch</h3><ul className="mt-3 list-disc space-y-2 pl-5">{(result.developments_to_watch || []).map((s: string) => <li key={s}>{s}</li>)}</ul></section>
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
    <section><h3 className="font-serif text-2xl">Version history</h3><ul className="mt-3 space-y-2">{history.map((v: any) => <li key={v.id}><Link className="underline" href={`/forecasts/${data.id}?run=${v.run_id}`}>{v.created_at} · {v.status === "failed" ? "Forecast failed" : v.status && v.status !== "completed" ? v.status.replaceAll("_", " ") : v.ensemble_probability == null ? "Probability withheld" : verdictShort(v.ensemble_probability)} · {v.profile_id}</Link></li>)}</ul></section>
    <details className="border-t border-rule pt-4"><summary className="cursor-pointer">Execution receipts, prompts, graph, and rejected evidence</summary><pre className="mt-4 max-h-[40rem] overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify({ graph: run.forecast_graph, budget: run.budget, attempts: run.run_attempts, ledger: run.provider_call_ledger, prompts: run.frozen_prompts, research: result.research_diagnostics, rejected: evidence.filter((e: any) => !e.usable), aggregation: result.aggregation }, null, 2)}</pre></details>
    <Link className="text-sm underline" href="/">Back to board</Link>
  </article>;
}
