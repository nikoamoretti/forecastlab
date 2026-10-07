"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { CallPill, ResultMark } from "@/components/CallBadges";
import { api } from "@/lib/api";
import { MEANINGFUL_RESULTS, Question, TrackRecord, callText, relativeDay, shortDate, versusGuessing } from "@/lib/trackRecord";

const SHOWN = 5;
const DAY = 86_400_000;

function ResultRow({ q }: { q: Question }) {
  return <li><Link href={`/q/${q.id}`} className="grid gap-x-4 gap-y-1 py-4 hover:bg-white/50 sm:grid-cols-[6rem_1fr_auto] sm:items-center">
    <span><ResultMark verdict={q.verdict} scored={q.scored !== false} /></span>
    <span>{q.title}
      <span className="mt-1 block text-sm text-ink/60">We said {callText(q.call!.probability)} · actual {q.actual ?? (q.outcome ? "yes" : "no")} · {shortDate(q.resolves_on)}</span></span>
    <span aria-hidden className="hidden text-xl text-ink/30 sm:block">›</span>
  </Link></li>;
}

function OpenRow({ q, today }: { q: Question; today: string }) {
  return <li><Link href={`/q/${q.id}`} className="grid gap-x-4 gap-y-1 py-4 hover:bg-white/50 sm:grid-cols-[6rem_1fr_auto] sm:items-center">
    <span className="text-sm text-ink/60">{relativeDay(q.resolves_on, today)}</span>
    <span>{q.title}</span>
    {q.call && <span><CallPill probability={q.call.probability} /></span>}
  </Link></li>;
}

function Expandable({ items, label, children }: { items: Question[]; label: string; children: (q: Question) => React.ReactNode }) {
  const [all, setAll] = useState(false);
  return <>
    <ul className="divide-y divide-rule border-y border-rule">{(all ? items : items.slice(0, SHOWN)).map(children)}</ul>
    {items.length > SHOWN && <button className="mt-3 py-2 text-sm underline" onClick={() => setAll(!all)}>
      {all ? "Show fewer" : `Show all ${items.length} ${label}`}</button>}
  </>;
}

export default function TrackRecordPage() {
  const [data, setData] = useState<TrackRecord | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api<TrackRecord>("/api/track-record").then(setData).catch(e => setError(e.message)); }, []);
  if (error) return <p role="alert">The track record could not be loaded: {error}</p>;
  if (!data) return <p className="text-ink/60">Loading your track record…</p>;

  const s = data.summary, today = data.generated_on;
  const resolved = data.questions.filter(q => q.status === "resolved");
  const open = data.questions.filter(q => q.status === "pending");
  const next = open[0];
  const dueThisWeek = open.filter(q => (new Date(q.resolves_on).getTime() - new Date(today).getTime()) / DAY <= 7).length;
  const calls = s.right + s.wrong;
  const accuracy = versusGuessing(s.brier, s.coin_flip_brier);

  return <div className="space-y-12">
    <header className="flex flex-wrap items-end justify-between gap-6">
      <div>
        <p className="font-mono text-xs uppercase tracking-widest text-copper">Track record</p>
        <h2 className="mt-3 font-serif text-5xl">{calls ? `${s.right} of ${calls} calls right` : "No results yet"}</h2>
        {accuracy && <p className="mt-3 text-lg">{accuracy} so far
          <span className="block text-sm text-ink/60 sm:inline"> · accuracy score {s.brier!.toFixed(2)}; always saying 50% scores 0.25, lower is better</span></p>}
      </div>
      <Link href="/ask" className="bg-ink px-6 py-3 text-paper hover:bg-ink/85">Ask a question</Link>
    </header>

    <section aria-label="Progress and next result" className="grid gap-4 md:grid-cols-2">
      <div className="border border-rule bg-white/40 p-5">
        <p className="font-medium">{s.resolved} of {MEANINGFUL_RESULTS} results</p>
        <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-ink/10" role="progressbar" aria-label="Decided questions"
          aria-valuenow={s.resolved} aria-valuemin={0} aria-valuemax={MEANINGFUL_RESULTS}>
          <div className="h-full rounded-full bg-pine" style={{ width: `${Math.max(Math.min(1, s.resolved / MEANINGFUL_RESULTS) * 100, 2)}%` }} /></div>
        <p className="mt-3 text-sm text-ink/70">At about {MEANINGFUL_RESULTS} decided questions the score starts to show skill rather than luck.{dueThisWeek > 0 && ` ${dueThisWeek} more ${dueThisWeek === 1 ? "is" : "are"} due this week.`}</p>
      </div>
      {next && <Link href={`/q/${next.id}`} className="block border-2 border-copper bg-white/60 p-5 hover:bg-white">
        <p className="font-mono text-xs uppercase tracking-widest text-copper">Next result · {relativeDay(next.resolves_on, today)}</p>
        <p className="mt-2 text-lg">{next.title}</p>
        {next.call && <p className="mt-2 text-sm"><span className="text-ink/60">Our call: </span><strong>{callText(next.call.probability)}</strong></p>}
      </Link>}
    </section>

    <section>
      <h3 className="font-serif text-3xl">Latest results</h3>
      {resolved.length
        ? <div className="mt-4"><Expandable items={resolved} label="results">{q => <ResultRow key={q.id} q={q} />}</Expandable></div>
        : <p className="mt-3 text-ink/70">Nothing has been decided yet.</p>}
    </section>

    <section>
      <h3 className="font-serif text-3xl">Coming up <span className="text-ink/40">{open.length}</span></h3>
      <p className="mt-1 text-sm text-ink/60">Our call on each open question, soonest first.</p>
      <div className="mt-4"><Expandable items={open} label="open questions">{q => <OpenRow key={q.id} q={q} today={today} />}</Expandable></div>
    </section>

    <section>
      <h3 className="font-serif text-3xl">Who forecasts best</h3>
      <p className="mt-1 max-w-3xl text-sm text-ink/60">Every question goes to several forecasters. Our call is the combined forecast: the median of their probabilities. The prediction market is only a benchmark to beat; it is never part of our call. Each forecaster’s own record shows whether the combination beats it.</p>
      <ul className="mt-4 divide-y divide-rule border-y border-rule">{data.forecasters.map(f => <li key={f.method}
        className="grid gap-x-6 gap-y-1 py-4 sm:grid-cols-[1fr_auto] sm:items-center">
        <span><span className="font-medium">{f.label}</span><span className="block text-sm text-ink/60">{f.about}</span></span>
        <span className="sm:text-right">{f.resolved ? <><strong className="tabular-nums">{f.right} of {f.right + f.wrong} right</strong>
          <span className="block text-sm text-ink/60">{versusGuessing(f.brier, f.coin_flip_brier)} ({f.brier!.toFixed(2)}) · {f.forecasts} forecasts</span></>
          : <span className="text-sm text-ink/60">No results yet · {f.forecasts} forecasts</span>}</span>
      </li>)}</ul>
    </section>

    <p className="text-sm text-ink/50">Updated {shortDate(today, true)} from official first releases. Test runs and drafts don’t count; they’re under More › All runs.</p>
  </div>;
}
