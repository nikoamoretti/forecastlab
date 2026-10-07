"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, pct } from "@/lib/api";
import { callShort } from "@/lib/verdict";

type Summary = { id: string; original_text: string; created_at: string; stale: boolean; run_id: string | null;
  profile_id: string | null; mode: string | null; status: string | null; outcome_status: string | null;
  probability: number | null; cost_usd: number | null; finished_at: string | null; run_created_at: string | null;
  automation_status?: string | null; latest_version?: number; pending_outcome?: boolean; sources_checked_at?: string | null };
type Page = { items: Summary[]; total: number; offset: number; limit: number };

function age(timestamp: string) {
  const date = new Date(/[Zz]|[+-]\d\d:\d\d$/.test(timestamp) ? timestamp : `${timestamp}Z`);
  const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
  return minutes < 60 ? `${minutes}m old` : minutes < 1440 ? `${Math.floor(minutes / 60)}h old` : `${Math.floor(minutes / 1440)}d old`;
}

export default function RunsPage() {
  const [data, setData] = useState<Page | null>(null);
  const [offset, setOffset] = useState(0);
  const [fixtures, setFixtures] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    api<Page>(`/api/forecast-summaries?offset=${offset}&limit=25&include_fixtures=${fixtures}`)
      .then(result => { if (!cancelled) { setData(result); setError(""); } }).catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [offset, fixtures]);
  return <div className="space-y-8">
    <header className="flex flex-wrap items-end justify-between gap-6"><div>
      <p className="font-mono text-xs uppercase tracking-widest text-copper">Advanced</p>
      <h2 className="mt-3 font-serif text-4xl">All runs</h2>
      <p className="mt-4 max-w-2xl text-ink/70">Every question and run in the database, including pipeline tests, drafts and failed runs. Your results are on the <Link className="underline" href="/">Track record</Link>.</p></div>
      <div className="flex gap-3"><Link className="border border-ink px-4 py-2" href="/new">New forecast</Link>
        <Link className="border border-ink bg-ink px-4 py-2 text-paper" href="/new?profile=root_event_ensemble_v1">Pick questions for me</Link></div></header>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={fixtures} onChange={e => { setFixtures(e.target.checked); setOffset(0); }} />Include demo fixtures</label>
    {error && <p role="alert">{error}</p>}
    {!data ? <p>Loading board…</p> : <>
      <p className="text-sm text-ink/60">{data.total} questions</p>
      {!data.items.length && <p>No questions in this view. Create a forecast or include demo fixtures.</p>}
      <ul className="divide-y divide-rule border-y border-rule">{data.items.map(row => <li key={row.id} className="grid grid-cols-[1fr_auto] gap-5 py-5">
        <div><Link className="text-lg" href={row.profile_id === "root_event_ensemble_v1" && ["preparing", "awaiting_review"].includes(row.status || "") ? `/new?profile=root_event_ensemble_v1&draft=${row.run_id}` : `/forecasts/${row.id}`}>{row.original_text}</Link>
          <p className="mt-2 text-sm text-ink/70">{row.mode || "draft"} · {row.profile_id || "No method yet"} · {(row.outcome_status || row.status || "draft").replaceAll("_", " ")}{row.stale ? " · stale" : ""}</p>
          <p className="mt-1 text-xs text-ink/60">{age(row.finished_at || row.run_created_at || row.created_at)} · ${Number(row.cost_usd || 0).toFixed(4)}</p></div>
        {row.automation_status && <p className="col-start-1 text-xs text-copper">Autopilot · version {row.latest_version} · {row.automation_status.replaceAll('_', ' ')}{row.pending_outcome ? ' · Outcome awaiting confirmation' : ''}{row.sources_checked_at ? ` · Sources checked ${age(row.sources_checked_at)}` : ''}</p>}
        <div className="col-start-2 row-start-1 text-right">{row.outcome_status === "insufficient_evidence" || row.probability == null
          ? <p className="font-serif text-3xl">{row.outcome_status === "insufficient_evidence" ? "Withheld" : pct(row.probability)}</p>
          : <><p className="font-serif text-2xl">{callShort(row.probability)}</p>
            <p className="mt-1 text-sm text-ink/70">{pct(row.probability)} chance of yes</p></>}</div>
      </li>)}</ul>
      <nav className="flex items-center gap-5" aria-label="Forecast pages"><button disabled={offset === 0} className="underline disabled:opacity-30" onClick={() => setOffset(Math.max(0, offset - 25))}>Previous</button>
        <span className="text-sm">{data.total ? offset + 1 : 0}–{Math.min(offset + 25, data.total)} of {data.total}</span>
        <button disabled={offset + 25 >= data.total} className="underline disabled:opacity-30" onClick={() => setOffset(offset + 25)}>Next</button></nav>
    </>}
  </div>;
}
