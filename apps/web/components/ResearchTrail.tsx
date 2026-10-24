"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Question } from "@/lib/trackRecord";
import { shortDate } from "@/lib/trackRecord";

type Source = { title: string; url: string; published?: string; verified?: boolean | null; quotes?: string[] };
type Judge = { method: string; model: string; at?: string; ok: boolean; error?: string | null; cost_usd?: number | null };
export type Research = {
  researched_by: string | null; researched_at: string | null; quotes_checked_at: string | null; research_failed: string | null;
  research_seconds?: number | null; research_cost_usd?: number | null;
  facts: { fact: string; source: number }[]; base_rate: string | null; different_now?: string | null; unknowns: string[]; sources: Source[];
  brief: string | null; brief_written_at: string | null; judges: Judge[];
};

const when = (iso?: string | null) => iso
  ? `${shortDate(iso)}, ${new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "UTC" })} UTC` : null;
const count = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const effort = (r: Research) => {
  const parts = [r.research_seconds != null && `${Math.max(1, Math.round(r.research_seconds))} seconds`,
    r.research_cost_usd != null && `$${r.research_cost_usd.toFixed(2)}`].filter(Boolean);
  return parts.length ? ` (${parts.join(", ")})` : "";
};
const checkLabel = (s: Source) => s.verified === true ? "quotes checked on the page" : s.verified === false ? "quotes not found on the page" : "not checked";

function Step({ at, children }: { at?: string | null; children: React.ReactNode }) {
  return <li className="grid gap-x-4 gap-y-0.5 py-3 sm:grid-cols-[11rem_1fr]">
    <span className="text-sm tabular-nums text-ink/50">{when(at) ?? ""}</span><span>{children}</span></li>;
}

// Everything done to research and forecast one question, in order, with the evidence itself.
export function ResearchTrail({ q, labels }: { q: Question; labels: Record<string, string> }) {
  const [research, setResearch] = useState<Research | null | undefined>(undefined);
  useEffect(() => {
    api<Research>(`/api/research/${encodeURIComponent(q.source)}/${encodeURIComponent(q.id)}`).then(setResearch).catch(() => setResearch(null));
  }, [q.source, q.id]);
  if (research === undefined) return null;
  const claude = q.forecasts.find(f => f.method === "claude_code_forecaster_v1");
  const answered = research?.judges.filter(j => j.ok) ?? [];
  const failed = research?.judges.filter(j => !j.ok) ?? [];
  const cost = research?.judges.reduce((sum, j) => sum + (j.cost_usd ?? 0), 0) ?? 0;
  const checked = research?.sources.filter(s => s.verified === true).length ?? 0;

  return <>
    <section>
      <h3 className="font-serif text-2xl">How this forecast was made</h3>
      <ol className="mt-3 divide-y divide-rule/60 border-y border-rule/60">
        {!research && <Step>No research log was saved for this question; it predates the research pipeline (Oct 14). Claude searched the web itself, and its sources are listed above.</Step>}
        {research?.researched_by && (research.research_failed
          ? <Step at={research.researched_at}>Research by {research.researched_by} failed: {research.research_failed}. No forecast was made from it.</Step>
          : research.facts.length > 0 && <Step at={research.researched_at}>{research.researched_by} researched the question on the web: {count(research.facts.length, "dated fact")} from {count(research.sources.length, "source")}, plus a base rate and open questions{effort(research)}. It never sees market odds.</Step>)}
        {research?.quotes_checked_at && research.sources.length > 0 && <Step at={research.quotes_checked_at}>Every cited page was downloaded and the quoted lines looked up on it: confirmed on {checked} of {count(research.sources.length, "source")}.</Step>}
        {claude && <Step at={claude.made_at}>{research
          ? `Claude read the research${(claude.made_at ?? "") >= "2026-10-24" ? " and its own record so far" : ""} and made its forecast: ${Math.round(claude.probability * 100)}% yes. It does not search the web itself.`
          : `Claude made its forecast: ${Math.round(claude.probability * 100)}% yes.`}</Step>}
        {research?.brief && <Step at={research.brief_written_at}>Claude wrote a brief from the research for the judges, with no probability or lean in it.</Step>}
        {research && research.judges.length > 0 && <Step at={research.judges[0].at}>
          {answered.length} of {count(research.judges.length, "judge model")} answered from the brief and the checked sources{cost > 0 && ` (cost $${cost.toFixed(3)})`}.
          {failed.length > 0 && <span className="mt-1 block text-sm text-ink/60">No answer from {failed.map(j => `${labels[j.method] ?? j.model} (${j.error ?? "failed"})`).join("; ")}.</span>}</Step>}
        {q.call && <Step>Our call is the middle of the forecasters’ probabilities{q.call.weights ? ", with better records counting more" : ""}: {Math.round(q.call.probability * 100)}% yes. The prediction market is only a benchmark and never part of it.</Step>}
        {q.status === "resolved" && <Step>The result came in on {shortDate(q.resolves_on, true)}: {q.outcome ? "yes" : "no"}{q.actual ? ` (${q.actual})` : ""}.</Step>}
      </ol>
    </section>

    {research && (research.facts.length > 0 || research.sources.length > 0) && <section>
      <h3 className="font-serif text-2xl">The research</h3>
      <p className="mt-1 text-sm text-ink/60">Exactly what the forecasters read{research.researched_by ? `, gathered by ${research.researched_by}` : ""}.</p>
      {research.facts.length > 0 && <ul className="mt-3 list-disc space-y-2 pl-5 leading-relaxed [overflow-wrap:anywhere]">{research.facts.map((f, n) =>
        <li key={n} className="whitespace-pre-line">{f.fact} <a className="text-sm text-copper underline" href={`#source-${f.source + 1}`}>[{f.source + 1}]</a></li>)}</ul>}
      {research.base_rate && <p className="mt-4 whitespace-pre-line leading-relaxed"><span className="font-medium">Base rate: </span>{research.base_rate}</p>}
      {research.different_now && <p className="mt-2 whitespace-pre-line leading-relaxed"><span className="font-medium">Different this time: </span>{research.different_now}</p>}
      {research.unknowns.length > 0 && <div className="mt-4"><p className="font-medium">Still unknown</p>
        <ul className="mt-1 list-disc space-y-1 pl-5">{research.unknowns.map((u, n) => <li key={n}>{u}</li>)}</ul></div>}
      {research.sources.length > 0 && <div className="mt-5"><p className="text-sm text-ink/60">Sources</p>
        <ol className="mt-1 space-y-2 text-sm">{research.sources.map((s, n) => <li key={n} id={`source-${n + 1}`}>
          <span className="tabular-nums text-ink/50">[{n + 1}] </span>
          <a className="underline [overflow-wrap:anywhere]" href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a>
          <span className="text-ink/60">{s.published ? ` · ${shortDate(s.published, true)}` : ""} · {checkLabel(s)}</span>
          {!!s.quotes?.length && <details className="mt-1"><summary className="cursor-pointer text-ink/60">Quoted lines</summary>
            <ul className="mt-1 space-y-1 border-l-2 border-rule pl-3 text-ink/80">{s.quotes.map((quote, k) =>
              <li key={k} className="whitespace-pre-line [overflow-wrap:anywhere]">“{quote}”</li>)}</ul></details>}
        </li>)}</ol></div>}
    </section>}

    {research?.brief && <section>
      <details><summary className="cursor-pointer font-serif text-2xl">The brief the judges read</summary>
        <p className="mt-3 whitespace-pre-line leading-relaxed [overflow-wrap:anywhere]">{research.brief}</p></details>
    </section>}
  </>;
}
