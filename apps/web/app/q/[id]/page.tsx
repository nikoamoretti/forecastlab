"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ResultMark } from "@/components/CallBadges";
import { ResearchTrail } from "@/components/ResearchTrail";
import { api, pct } from "@/lib/api";
import { TrackRecord, anchoredCallText, callText, percent, lateDeadline, marketNameOf, relativeDay, shortDate, sourceLabel, sourceUrl, todayUTC } from "@/lib/trackRecord";

// Text with any web address in it turned into a short link (its site name), so a long address never breaks the line.
function Linked({ text }: { text: string }) {
  return <>{text.split(/(https?:\/\/[^\s<>"”’)\]]+)/g).map((part, n) => {
    if (n % 2 === 0) return part;
    const url = part.replace(/[.,;:!?]+$/, "");
    return <span key={n}><a className="underline decoration-ink/30 underline-offset-2 hover:decoration-ink" href={url} target="_blank" rel="noreferrer">{sourceLabel(url)}</a>{part.slice(url.length)}</span>;
  })}</>;
}


export default function QuestionPage() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<TrackRecord | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api<TrackRecord>("/api/track-record").then(setData).catch(e => setError(e.message)); }, []);
  if (error) return <p role="alert">This question could not be loaded: {error}</p>;
  if (!data) return <p className="text-ink/60">Loading…</p>;
  const q = data.questions.find(item => item.id === id);
  if (!q) return <div className="space-y-4"><p>This question is not in the track record.</p><Link className="underline" href="/">Back to track record</Link></div>;

  const labels = Object.fromEntries(data.forecasters.map(f => [f.method, f.label]));
  // Forecasters that wrote down their reasoning (Claude does); the first one is shown.
  const reasoning = q.forecasts.find(f => f.rationale) ?? null;
  const overdue = q.status === "pending" && q.resolves_on < todayUTC();
  const marketName = marketNameOf(q.source_url);
  const market = marketName !== null;
  const anchored = q.call?.method === "market_anchored_call_v1";
  const claudeAlone = q.forecasts.find(f => f.method === "claude_code_forecaster_v1");
  // Claude's probability corrected for how its earlier calls at each confidence level turned out; frozen when the question was built.
  const corrected = q.forecasts.find(f => f.method === "claude_calibrated_v1" && f.raw != null) ?? null;
  const settlesBy = overdue && market ? lateDeadline(q.detail ?? "", q.resolves_on) : null;

  // A series keeps its colour across the site: our call, the market's price, Claude alone; everyone else is neutral.
  const barOf = (method: string) => method === "claude_code_forecaster_v1" ? "bg-chart-claude"
    : /_price_v1$/.test(method) ? "bg-chart-market" : "bg-ink/45";
  const chance = (probability: number, bar: string) => <span aria-hidden className="relative block h-2 rounded-full bg-ink/10">
    <span className={`absolute inset-y-0 left-0 rounded-full ${bar}`} style={{ width: `${Math.max(1.5, probability * 100)}%` }} />
    <span className="absolute -inset-y-1 left-1/2 w-px bg-ink/40" /></span>;
  const status = q.status === "resolved" ? `decided ${shortDate(q.resolves_on, true)}`
    : q.status === "cancelled" ? "cancelled" : overdue ? "waiting for the result"
    : `result due ${relativeDay(q.resolves_on, todayUTC()).replace(/^(Today|Tomorrow|Yesterday)$/, day => day.toLowerCase())}`;

  return <article className="mx-auto max-w-3xl space-y-6 sm:space-y-8">
    <Link href="/" className="btn-quiet">‹ Track record</Link>

    <header>
      <p className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm text-ink/60"><span className="chip">{q.topic}</span><span>{status}</span></p>
      <h2 className="mt-3 font-serif text-3xl leading-tight sm:text-[2.5rem]">{q.title}</h2>
    </header>

    <section className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div className="card p-5 sm:p-6">
        <p className="eyebrow">{anchored ? "Our call (starts from the market)" : "Our call"}</p>
        <p className="mt-3 font-sans text-3xl font-semibold tracking-tight sm:text-4xl">{q.call ? callText(q.call.probability) : "No call"}</p>
        {q.call && <div className="mt-4">{chance(q.call.probability, "bg-chart-us")}
          <p aria-hidden className="mt-1 flex justify-between text-[11px] text-ink/50"><span>0% yes</span><span>50%</span><span>100% yes</span></p></div>}
        {q.call && <p className="mt-3 text-sm text-ink/65">{pct(q.call.probability)} chance of yes · {q.call.method === "market_anchored_call_v1"
          ? anchoredCallText(q.call, pct(q.call.market!), data.summary.edge_tests)
          : q.call.method === "claude_call_v1"
          ? "Claude’s own forecast"
          : q.call.method === "claude_corrected_call_v1"
          ? "Claude’s forecast, corrected for how its past calls turned out"
          : q.call.members
          ? `the middle (median) of ${q.call.members} forecaster${q.call.members === 1 ? "" : "s"}${q.call.weights ? ", better records counting more" : ""}`
          : `by ${labels[q.call.method] ?? q.call.method}`}</p>}
        {anchored && claudeAlone && <p className="mt-4 border-t border-rule/60 pt-3 text-sm"><span className="font-medium">Claude alone:</span> {callText(claudeAlone.probability)} ({pct(claudeAlone.probability)} yes)
          <span className="mt-0.5 block text-ink/60">Claude’s own forecast, made without seeing the market price, tracked and scored beside our call.</span></p>}
      </div>
      <div className={q.status === "resolved" ? "card p-5 sm:p-6" : "rounded-2xl border border-dashed border-ink/25 p-5 sm:p-6"}>
        <p className="eyebrow">What happened</p>
        {q.status === "resolved" ? <>
          <p className="mt-3 font-sans text-3xl font-semibold tracking-tight sm:text-4xl">{q.outcome ? "Yes" : "No"}</p>
          <p className="mt-4 flex flex-wrap items-center gap-3 text-sm"><ResultMark verdict={q.verdict} scored={q.scored !== false} large />{q.actual && <span className="text-ink/65">Actual: {q.actual}</span>}</p>
        </> : q.status === "cancelled"
          ? <p className="mt-3 text-ink/70">Cancelled: {q.actual ?? "no official value was published (market holiday)"}. It doesn’t count.</p>
          : <p className="mt-3 leading-relaxed text-ink/70">Not decided yet. {settlesBy ? `${marketName} listed ${shortDate(q.resolves_on, true)}, but its rules let it settle as late as ${shortDate(settlesBy, true)}, so this one can take a while. We check every evening.`
            : overdue ? `It was due ${shortDate(q.resolves_on, true)}, but ${market ? `${marketName} hasn’t settled it` : "the official figure isn’t out"} yet. We check every evening.`
            : `${market ? `${marketName} settles it after` : "The official figure is due"} ${shortDate(q.resolves_on, true)}.`}</p>}
      </div>
    </section>

    {corrected && <aside className="card border-l-4 border-l-ink/30 p-5 text-sm" aria-label="Confidence correction">
      <p className="text-base">Claude said {percent(corrected.raw!)}. Corrected for its track record: <strong>{percent(corrected.probability)}</strong>.</p>
      <p className="mt-1 text-ink/70">Both are chances of yes. The correction comes from how Claude’s {corrected.results} earlier decided calls turned out at each confidence level.{" "}
        {q.call?.method === "claude_corrected_call_v1" ? "This is our call for this question." : "Shown for comparison; it is not part of our call."}</p></aside>}

    {q.note && <aside className="card border-l-4 border-l-copper p-5 text-sm" aria-label="Integrity note">
      <p className="font-medium">Integrity note{q.scored === false && " · not counted in any score"}</p><p className="mt-1 text-ink/80">{q.note}</p></aside>}

    {reasoning?.rationale && <section className="card p-5 sm:p-6">
      <h3 className="font-serif text-2xl">Why {labels[reasoning.method] ?? "the forecaster"} said {callText(reasoning.probability).split(",")[0].toLowerCase()}</h3>
      <p className="mt-3 whitespace-pre-line leading-relaxed [overflow-wrap:anywhere]"><Linked text={reasoning.rationale} /></p>
      {!!reasoning.sources?.length && <div className="mt-5"><p className="text-sm text-ink/60">Sources</p>
        <ul className="mt-2 flex flex-wrap gap-2 text-sm">{reasoning.sources.map(source => <li key={sourceUrl(source)}>
          <a className="inline-block rounded-full border border-rule bg-ink/[0.03] px-3 py-1 hover:border-ink/40 [overflow-wrap:anywhere]" href={sourceUrl(source)} target="_blank" rel="noreferrer">{sourceLabel(source)}</a></li>)}</ul></div>}
      {reasoning.made_at && <p className="mt-4 text-sm text-ink/50">Forecast made {shortDate(reasoning.made_at, true)}, before the result was known.</p>}
    </section>}

    <section className="card overflow-hidden">
      <h3 className="px-5 pb-4 pt-5 font-serif text-2xl sm:px-6">What each forecaster said</h3>
      <table className="w-full border-t border-rule/60 text-left">
        <tbody>{q.forecasts.map(f => <tr key={f.method} className="border-b border-rule/60 align-top">
          <td className="py-3 pl-5 pr-3 sm:pl-6">{labels[f.method] ?? f.method}
            {f.rationale && f !== reasoning && <details className="mt-1 text-sm text-ink/70"><summary className="cursor-pointer text-ink/60">Reasoning</summary>
              <p className="mt-1 whitespace-pre-line leading-relaxed [overflow-wrap:anywhere]"><Linked text={f.rationale} /></p></details>}</td>
          <td className="hidden w-40 py-3 pr-4 pt-[1.15rem] sm:table-cell">{chance(f.probability, barOf(f.method))}</td>
          <td className="whitespace-nowrap py-3 pr-3 font-medium tabular-nums">{callText(f.probability)}</td>
          <td className="whitespace-nowrap py-3 pr-5 text-right text-sm text-ink/55 tabular-nums sm:pr-6">{pct(f.probability)} yes</td></tr>)}
          {q.call && q.forecasts.length > 1 && <tr className="bg-ink/[0.03] align-top">
            <td className="py-3 pl-5 pr-3 font-medium sm:pl-6">{anchored ? "Our call (starts from the market)" : "Combined (our call)"}</td>
            <td className="hidden w-40 py-3 pr-4 pt-[1.15rem] sm:table-cell">{chance(q.call.probability, "bg-chart-us")}</td>
            <td className="whitespace-nowrap py-3 pr-3 font-medium tabular-nums">{callText(q.call.probability)}</td>
            <td className="whitespace-nowrap py-3 pr-5 text-right text-sm text-ink/55 tabular-nums sm:pr-6">{pct(q.call.probability)} yes</td></tr>}</tbody>
      </table>
    </section>

    <ResearchTrail q={q} labels={labels} edges={data.summary.edge_tests} />

    <section className="rounded-2xl border border-rule/70 p-5 text-sm text-ink/70 sm:p-6">
      <h3 className="font-medium text-ink">How it’s decided</h3>
      <p className="mt-2 leading-relaxed [overflow-wrap:anywhere]"><Linked text={q.detail ?? ""} /></p>
      <p className="mt-3 flex flex-wrap gap-x-5 gap-y-1">
        {q.source_url && <a className="underline" href={q.source_url} target="_blank" rel="noreferrer">{market ? `The market and its rules (${marketName})` : "Official data source"}</a>}
        {q.report_url && <Link className="underline" href={q.report_url}>Full research report</Link>}</p>
    </section>
  </article>;
}
