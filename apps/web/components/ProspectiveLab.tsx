"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { verdictShort } from "@/lib/verdict";

export default function ProspectiveLab() {
  const [cohorts, setCohorts] = useState<any[]>([]);
  const [selected, setSelected] = useState("");
  const [report, setReport] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [cohortName, setCohortName] = useState("U.S. macro pilot");
  const [forecastCutoff, setForecastCutoff] = useState("");
  const [newQuestions, setNewQuestions] = useState([{ indicator: "unemployment", observation_period: "", threshold: "5", comparison: "gt", release_at: "", revision_policy: "first_release" }]);
  const [reviewer, setReviewer] = useState("");
  const [entryId, setEntryId] = useState("");
  const [outcome, setOutcome] = useState("1");
  const [source, setSource] = useState("");
  const [evidence, setEvidence] = useState("");
  const [fredKey, setFredKey] = useState("");
  const [fredSet, setFredSet] = useState(false);
  const [cloudSecrets, setCloudSecrets] = useState(false);
  const refresh = useCallback(async () => {
    setCohorts((await api<any>("/api/prospective/cohorts")).cohorts);
    if (selected) setReport(await api<any>(`/api/prospective/cohorts/${selected}`));
  }, [selected]);
  useEffect(() => { refresh().catch(e => setError(e.message)); }, [refresh]);
  useEffect(() => { api<any>("/api/macro/settings").then(data => { setFredSet(data.fred_api_key_set); setCloudSecrets(Boolean(data.secrets_managed_externally)); }).catch(e => setError(e.message)); }, []);
  async function action(path: string, body?: any) {
    setBusy(true); setError("");
    try { const result = await api<any>(path, { method: "POST", body: JSON.stringify(body || {}) });
      if (result.questions) { setSelected(result.id); setReport(result); setCohorts((await api<any>("/api/prospective/cohorts")).cohorts); }
      else await refresh();
      return true;
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); return false; }
    finally { setBusy(false); }
  }
  const field = "mt-1 w-full border border-rule bg-white p-2";
  return <section className="space-y-5 border-y border-rule py-6">
    <div><p className="font-mono text-xs uppercase tracking-widest text-copper">Prospective evaluation</p>
      <h3 className="mt-2 font-serif text-3xl">Record first. Resolve later.</h3>
      <p className="mt-3 text-sm">Four frozen methods for new cohorts: three AI methods at up to $5 per assignment, and the deterministic statistical baseline at $0, the bar the AI methods must beat. Up to 10 macro questions and $150 total. Earlier cohorts keep the methods they were frozen with. A verified dated BLS first release can be system-adjudicated; exceptions remain unresolved. All methods use the same matched question set.</p></div>
    {error && <p role="alert" className="text-copper">{error}</p>}
    <label className="block">Cohort<select aria-label="Cohort" className={field} value={selected} onChange={e => { setSelected(e.target.value); setReport(null); }}><option value="">Select a cohort</option>{cohorts.map(c => <option key={c.id} value={c.id}>{c.name} · {c.status}</option>)}</select></label>
    {report && <div className="space-y-5">
      <p>{report.status.replaceAll("_", " ")} · ${report.cost_usd.toFixed(4)} / ${report.budget_usd.toFixed(2)} · {report.matched_resolved_count} matched resolved questions · {report.release_event_count} release events</p>
      <p className="text-sm text-ink/70">{report.interpretation}</p>
      <label className="block">Contract reviewer / exception resolver<input className={field} value={reviewer} onChange={e => setReviewer(e.target.value)} placeholder="Your name" /></label>
      {report.status === "draft" && <button disabled={busy || !reviewer.trim()} className="border border-ink px-4 py-2 disabled:opacity-40" onClick={() => action(`/api/prospective/cohorts/${selected}/freeze`, { reviewed_by: reviewer })}>Confirm reviewed questions and freeze methods</button>}
      {report.status === "frozen" && <button disabled={busy} className="border border-ink bg-ink px-4 py-2 text-paper" onClick={() => action(`/api/prospective/cohorts/${selected}/launch`)}>Launch within the cohort budget</button>}
      <button disabled={busy} className="ml-3 underline" onClick={() => action(`/api/prospective/cohorts/${selected}/score`)}>Refresh results</button>
      <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr>{["Method", "Forecasts / assigned", "Abstentions", "Failures", "Matched n", "Brier", "Log loss", "Cost", "Mean latency"].map(s => <th key={s} className="border-b border-rule p-2">{s}</th>)}</tr></thead><tbody>{report.methods.map((m: any) => <tr key={m.method}><td className="p-2">{m.method}</td><td>{m.forecasted} / {m.assigned}</td><td>{m.abstentions}</td><td>{m.failures}</td><td>{m.matched_resolved_count}</td><td>{m.brier_score?.toFixed(3) ?? "Pending"}</td><td>{m.log_loss?.toFixed(3) ?? "Pending"}</td><td>${m.cost_usd.toFixed(4)}</td><td>{m.mean_latency_ms == null ? "—" : `${(m.mean_latency_ms / 1000).toFixed(1)}s`}</td></tr>)}</tbody></table></div>
      <ul className="space-y-4">{report.questions.map((q: any) => <li key={q.id} className="border border-rule p-4"><Link href={`/forecasts/${q.question_id}`} className="font-medium underline">{q.contract.normalized_question}</Link>
        <p className="mt-2 text-sm">{q.contract.yes_condition}</p><p className="mt-1 text-sm">{q.contract.resolution_method}</p>
        <p className="mt-2 text-xs">Forecast deadline: {q.cutoff} · Release: {q.contract.resolution_date} · {q.release_event}</p>
        <ul className="mt-2 space-y-1 text-sm">{q.cells.map((c: any) => <li key={c.run_id}><Link className="underline" href={`/forecasts/${q.question_id}?run=${c.run_id}`}>{c.method}: {verdictShort(c.probability)} ({c.status})</Link></li>)}</ul>
        {q.outcome && <p className="mt-2 text-sm">Outcome: {q.outcome.value == null ? "Cancelled" : q.outcome.value ? "Yes" : "No"} · revision {q.outcome.revision} · confirmed by {q.outcome.confirmed_by}</p>}
        {q.official_outcome_amendment && <p className="mt-1 text-xs text-ink/70">Official outcome amendment: {q.official_outcome_amendment.status} · {q.official_outcome_amendment.policy_version} · known {q.official_outcome_amendment.outcome_known_at ?? "—"} · retrieved {q.official_outcome_amendment.retrieved_at ?? "—"}{q.official_outcome_amendment.exception_code ? ` · ${q.official_outcome_amendment.exception_code}` : ""}</p>}
        {new Date(q.contract.resolution_date) <= new Date() && (q.outcome
          ? <button className="mt-2 text-sm underline" onClick={() => setEntryId(q.id)}>Record a correction</button>
          : q.official_outcome_amendment?.status === "exception"
            ? <button className="mt-2 text-sm underline" onClick={() => setEntryId(q.id)}>Resolve official-source exception manually</button>
            : <p className="mt-2 text-xs text-ink/70">Awaiting automatic official-release check</p>)}</li>)}</ul>
      {entryId && <form className="space-y-3 border border-rule p-4" onSubmit={e => { e.preventDefault(); action(`/api/prospective/entries/${entryId}/outcomes`, { outcome: outcome === "cancelled" ? null : Number(outcome), source_url: source, evidence, confirmed_by: reviewer }).then(ok => { if (ok) setEntryId(""); }); }}>
        <h4 className="font-serif text-xl">Manual exception or correction</h4><label className="block">Outcome<select className={field} value={outcome} onChange={e => setOutcome(e.target.value)}><option value="1">Yes</option><option value="0">No</option><option value="cancelled">Cancelled</option></select></label>
        <label className="block">Official dated source<input className={field} type="url" required value={source} onChange={e => setSource(e.target.value)} /></label>
        <label className="block">Evidence and reason for correction, if applicable<textarea className={field} required value={evidence} onChange={e => setEvidence(e.target.value)} /></label>
        <button className="border border-ink px-4 py-2" disabled={busy || !reviewer.trim()}>Append reviewed outcome</button>
      </form>}
    </div>}
    <details><summary className="cursor-pointer">Create a prospective cohort from macro templates</summary>
      <p className="mt-3 text-sm">Set a deadline for producing all forecasts before the earliest release. Preparation is free; review the exact questions before freezing and launching.</p>
      <form className="mt-4 space-y-4" onSubmit={e => { e.preventDefault(); action("/api/prospective/cohorts", { name: cohortName, budget_usd: newQuestions.length * 15,
        questions: newQuestions.map(q => ({ macro: { ...q, threshold: Number(q.threshold), release_at: `${q.release_at}:00Z` }, cutoff: `${forecastCutoff}:00Z`, release_event: `${q.indicator === "cpi" ? "cpi" : "employment"}-${q.release_at.slice(0, 10)}` })) }); }}>
        <label className="block">Cohort name<input required className={field} value={cohortName} onChange={e => setCohortName(e.target.value)} /></label>
        <label className="block">Forecast completion deadline (UTC)<input required type="datetime-local" className={field} value={forecastCutoff} onChange={e => setForecastCutoff(e.target.value)} /></label>
        {newQuestions.map((q, index) => <fieldset key={index} className="grid gap-3 border border-rule p-4 md:grid-cols-3"><legend>Question {index + 1}</legend>
          <label>Indicator<select className={field} value={q.indicator} onChange={e => setNewQuestions(newQuestions.map((v, i) => i === index ? { ...v, indicator: e.target.value } : v))}><option value="unemployment">Unemployment rate (%)</option><option value="payrolls">Payroll monthly change (jobs)</option><option value="cpi">CPI year-over-year (%)</option></select></label>
          <label>Observation month<input required type="month" className={field} value={q.observation_period} onChange={e => setNewQuestions(newQuestions.map((v, i) => i === index ? { ...v, observation_period: e.target.value } : v))} /></label>
          <label>Release time (UTC)<input required type="datetime-local" className={field} value={q.release_at} onChange={e => setNewQuestions(newQuestions.map((v, i) => i === index ? { ...v, release_at: e.target.value } : v))} /></label>
          <label>Comparison<select className={field} value={q.comparison} onChange={e => setNewQuestions(newQuestions.map((v, i) => i === index ? { ...v, comparison: e.target.value } : v))}><option value="gt">Greater than</option><option value="ge">At least</option><option value="lt">Less than</option><option value="le">At most</option></select></label>
          <label>Threshold<input required type="number" step="any" className={field} value={q.threshold} onChange={e => setNewQuestions(newQuestions.map((v, i) => i === index ? { ...v, threshold: e.target.value } : v))} /></label>
          <label>Resolution value<select className={field} value={q.revision_policy} onChange={e => setNewQuestions(newQuestions.map((v, i) => i === index ? { ...v, revision_policy: e.target.value } : v))}><option value="first_release">First published value</option><option value="as_of_resolution">Latest at resolution</option></select></label>
        </fieldset>)}
        {newQuestions.length < 10 && <button type="button" className="underline" onClick={() => setNewQuestions([...newQuestions, { indicator: "unemployment", observation_period: "", threshold: "5", comparison: "gt", release_at: "", revision_policy: "first_release" }])}>Add question</button>}
        <p className="text-sm">{newQuestions.length * 4} assignments · maximum ${newQuestions.length * 15} including retries (the statistical baseline costs $0)</p>
        <button disabled={busy} className="border border-ink px-4 py-2">Create reviewable cohort</button>
      </form>
    </details>
    <details><summary className="cursor-pointer">Historical macro data settings</summary><p className="mt-3 text-sm">BLS live data requires no key. ALFRED historical vintages: {fredSet ? "key configured" : "key not configured"}.</p>
      <form className="mt-3 flex gap-3" onSubmit={async e => { e.preventDefault(); setError(""); try { const result = await api<any>("/api/macro/settings", { method: "PATCH", body: JSON.stringify({ fred_api_key: fredKey }) }); setFredSet(result.fred_api_key_set); setFredKey(""); } catch (err) { setError(err instanceof Error ? err.message : "Could not save key"); } }}>
        <input aria-label="FRED API key" disabled={cloudSecrets} type="password" autoComplete="off" className="border border-rule p-2" value={fredKey} onChange={e => setFredKey(e.target.value)} /><button disabled={cloudSecrets} className="border border-ink px-4 py-2">{cloudSecrets ? "Manage key in Vercel" : "Save key locally"}</button></form></details>
  </section>;
}
