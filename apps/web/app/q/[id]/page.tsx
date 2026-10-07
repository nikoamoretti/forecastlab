"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ResultMark } from "@/components/CallBadges";
import { api, pct } from "@/lib/api";
import { TrackRecord, callText, relativeDay, shortDate, sourceLabel, sourceUrl } from "@/lib/trackRecord";

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
  const lead = q.forecasts.find(f => f.method === q.call?.method);
  const explained = q.forecasts.find(f => f.rationale) ?? null;
  const reasoning = lead?.rationale ? lead : explained;

  return <article className="mx-auto max-w-3xl space-y-10">
    <Link href="/" className="inline-block py-1 text-sm text-ink/70 hover:text-ink">‹ Track record</Link>

    <header>
      <p className="text-sm text-ink/60">{q.topic} · {q.status === "resolved" ? `decided ${shortDate(q.resolves_on, true)}`
        : q.status === "cancelled" ? "cancelled" : `result due ${relativeDay(q.resolves_on, data.generated_on).replace(/^(Today|Tomorrow|Yesterday)$/, day => day.toLowerCase())}`}</p>
      <h2 className="mt-2 font-serif text-4xl leading-tight">{q.title}</h2>
    </header>

    <section className="grid gap-4 sm:grid-cols-2">
      <div className="border border-rule bg-white/50 p-5">
        <p className="font-mono text-xs uppercase tracking-widest text-copper">Our call</p>
        <p className="mt-2 font-serif text-4xl">{q.call ? callText(q.call.probability) : "No call"}</p>
        {q.call && <p className="mt-2 text-sm text-ink/60">{pct(q.call.probability)} chance of yes · by {labels[q.call.method] ?? q.call.method}</p>}
      </div>
      <div className={`border p-5 ${q.status === "resolved" ? "border-ink/30 bg-white/70" : "border-dashed border-rule"}`}>
        <p className="font-mono text-xs uppercase tracking-widest text-copper">What happened</p>
        {q.status === "resolved" ? <>
          <p className="mt-2 font-serif text-4xl">{q.outcome ? "Yes" : "No"}</p>
          <p className="mt-2 flex flex-wrap items-center gap-3 text-sm">{q.actual && <span className="text-ink/60">Actual: {q.actual}</span>}<ResultMark verdict={q.verdict} /></p>
        </> : q.status === "cancelled"
          ? <p className="mt-2 text-ink/70">Cancelled: no official value was published (market holiday). It doesn’t count.</p>
          : <p className="mt-2 text-ink/70">Not decided yet. The official figure is due {shortDate(q.resolves_on, true)}.</p>}
      </div>
    </section>

    {reasoning?.rationale && <section>
      <h3 className="font-serif text-2xl">Why {reasoning === lead ? "we" : labels[reasoning.method] ?? "the forecaster"} said {callText(reasoning.probability).split(",")[0].toLowerCase()}</h3>
      <p className="mt-3 whitespace-pre-line leading-relaxed [overflow-wrap:anywhere]">{reasoning.rationale}</p>
      {!!reasoning.sources?.length && <div className="mt-4"><p className="text-sm text-ink/60">Sources</p>
        <ul className="mt-1 space-y-1 text-sm">{reasoning.sources.map(source => <li key={sourceUrl(source)}>
          <a className="underline [overflow-wrap:anywhere]" href={sourceUrl(source)} target="_blank" rel="noreferrer">{sourceLabel(source)}</a></li>)}</ul></div>}
      {reasoning.made_at && <p className="mt-3 text-sm text-ink/50">Forecast made {shortDate(reasoning.made_at, true)}, before the result was known.</p>}
    </section>}

    <section>
      <h3 className="font-serif text-2xl">What each forecaster said</h3>
      <table className="mt-3 w-full text-left">
        <tbody>{q.forecasts.map(f => <tr key={f.method} className="border-b border-rule/60">
          <td className="py-2 pr-4">{labels[f.method] ?? f.method}{f.method === q.call?.method && <span className="ml-2 text-xs text-copper">our call</span>}</td>
          <td className="py-2 pr-4 font-medium tabular-nums">{callText(f.probability)}</td>
          <td className="py-2 text-right text-sm text-ink/50 tabular-nums">{pct(f.probability)} yes</td></tr>)}</tbody>
      </table>
    </section>

    <section className="border-t border-rule pt-6 text-sm text-ink/70">
      <h3 className="font-medium text-ink">How it’s decided</h3>
      <p className="mt-2 [overflow-wrap:anywhere]">{q.detail}</p>
      <p className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
        {q.source_url && <a className="underline" href={q.source_url} target="_blank" rel="noreferrer">Official data source</a>}
        {q.report_url && <Link className="underline" href={q.report_url}>Full research report</Link>}</p>
    </section>
  </article>;
}
