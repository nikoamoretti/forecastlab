"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { CallPill, ResultMark } from "@/components/CallBadges";
import { api } from "@/lib/api";
import { MEANINGFUL_RESULTS, Question, QuestionGroup, TrackRecord, Week, byWeek, callText, groupBySeries, relativeDay, shortDate, todayUTC, trend, versusGuessing } from "@/lib/trackRecord";

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

const settledBy = (q: Question) => q.source_url?.includes("polymarket.com") ? "Polymarket"
  : q.source_url?.includes("manifold.markets") ? "Manifold" : "the official figure";

function OpenRow({ q, today }: { q: Question; today: string }) {
  const overdue = q.resolves_on < today;
  return <li><Link href={`/q/${q.id}`} className="grid gap-x-4 gap-y-1 py-4 hover:bg-white/50 sm:grid-cols-[6rem_1fr_auto] sm:items-center">
    <span className="text-sm text-ink/60">{overdue ? `Due ${shortDate(q.resolves_on)}` : relativeDay(q.resolves_on, today)}</span>
    <span>{q.title}{overdue && <span className="mt-1 block text-sm text-ink/60">Waiting for {settledBy(q)}</span>}</span>
    {q.call && <span><CallPill probability={q.call.probability} /></span>}
  </Link></li>;
}

// Several questions on one indicator collapse into one row that opens to show each of them.
function GroupRow({ group, children, summary }: { group: QuestionGroup; children: (q: Question) => React.ReactNode; summary: string }) {
  const [open, setOpen] = useState(false);
  if (group.items.length === 1) return <>{children(group.items[0])}</>;
  return <li>
    <button className="grid w-full gap-x-4 gap-y-1 py-4 text-left hover:bg-white/50 sm:grid-cols-[6rem_1fr_auto] sm:items-center"
      aria-expanded={open} onClick={() => setOpen(!open)}>
      <span className="text-sm text-ink/60">{group.items.length} questions</span>
      <span>{group.label}<span className="mt-1 block text-sm text-ink/60">{summary}</span></span>
      <span aria-hidden className="hidden text-xl text-ink/30 sm:block">{open ? "−" : "+"}</span>
    </button>
    {open && <ul className="mb-3 divide-y divide-rule border-y border-rule pl-4 sm:pl-8">{group.items.map(children)}</ul>}
  </li>;
}

function Expandable({ groups, label, children, summary }: {
  groups: QuestionGroup[]; label: string; children: (q: Question) => React.ReactNode; summary: (g: QuestionGroup) => string }) {
  const [all, setAll] = useState(false);
  return <>
    <ul className="divide-y divide-rule border-y border-rule">{(all ? groups : groups.slice(0, SHOWN)).map(g =>
      <GroupRow key={g.key} group={g} summary={summary(g)}>{children}</GroupRow>)}</ul>
    {groups.length > SHOWN && <button className="mt-3 py-2 text-sm underline" onClick={() => setAll(!all)}>
      {all ? "Show fewer" : `Show all ${groups.length} ${label}`}</button>}
  </>;
}

const span = (g: QuestionGroup) => {
  const first = shortDate(g.items[0].resolves_on), last = shortDate(g.items[g.items.length - 1].resolves_on);
  return first === last ? first : `${first} to ${last}`;
};

// The score so far after each week, against always answering 50% (0.25). Lower is better.
function OverTime({ weeks: all }: { weeks: Week[] }) {
  const weeks = all.filter(wk => wk.right + wk.wrong > 0).slice(-8);
  if (weeks.length < 2) return null;
  const hit = (wk: Week) => wk.right / (wk.right + wk.wrong);
  const fit = trend(weeks, hit)!;
  const w = 360, h = 200, left = 36, right = 8, top = 18, bottom = 40;
  const slot = (w - left - right) / weeks.length, bar = Math.min(36, slot * 0.6);
  const x = (i: number) => left + slot * (i + 0.5);
  const y = (v: number) => top + (1 - Math.min(1, Math.max(0, v))) * (h - top - bottom);
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  const change = fit.end - fit.start;
  const summary = Math.abs(change) < 0.05 ? `Holding steady at about ${pct(fit.end)} of calls right.`
    : `${change > 0 ? "Getting better" : "Slipping"}: from about ${pct(fit.start)} to ${pct(fit.end)} of calls right.`;
  return <section aria-label="How we've done over time">
    <h3 className="font-serif text-3xl">Over time</h3>
    <p className="mt-3 text-lg font-medium">{summary}</p>
    <p className="mt-1 max-w-3xl text-sm text-ink/60">Each bar is the share of calls we got right in the week the questions were decided. The dashed line is a coin flip (50%). The copper line is the trend, with busier weeks counting for more.</p>
    <svg viewBox={`0 0 ${w} ${h}`} className="mt-4 w-full max-w-xl" role="img"
      aria-label={`By week: ${weeks.map(wk => `${shortDate(wk.start)} ${pct(hit(wk))} (${wk.right} of ${wk.right + wk.wrong} right)`).join(", ")}. ${summary}`}>
      {[0, 0.25, 0.75, 1].map(t => <g key={t}>
        <line x1={left} x2={w - right} y1={y(t)} y2={y(t)} className={t === 0 ? "stroke-ink/40" : "stroke-rule"} />
        <text x={left - 6} y={y(t) + 3} textAnchor="end" className="fill-ink/60 text-[9px]">{pct(t)}</text>
      </g>)}
      <line x1={left} x2={w - right} y1={y(0.5)} y2={y(0.5)} className="stroke-ink/50" strokeDasharray="4 4" />
      <text x={left - 6} y={y(0.5) + 3} textAnchor="end" className="fill-ink/60 text-[9px]">50%</text>
      {weeks.map((wk, i) => {
        const v = hit(wk);
        return <g key={wk.start}>
          <rect x={x(i) - bar / 2} width={bar} y={y(v)} height={y(0) - y(v)} className={v < 0.5 ? "fill-brick/70" : "fill-pine/70"} />
          <text x={x(i)} y={y(v) - 4} textAnchor="middle" className="fill-ink text-[10px] font-medium">{pct(v)}</text>
          <text x={x(i)} y={h - 24} textAnchor="middle" className="fill-ink/70 text-[10px]">{shortDate(wk.start)}</text>
          <text x={x(i)} y={h - 10} textAnchor="middle" className="fill-ink/50 text-[9px]">{wk.right} of {wk.right + wk.wrong} right</text>
        </g>;
      })}
      <line x1={x(0)} x2={x(weeks.length - 1)} y1={y(fit.start)} y2={y(fit.end)} className="stroke-copper" strokeWidth={2.5} strokeLinecap="round" />
    </svg>
  </section>;
}

export default function TrackRecordPage() {
  const [data, setData] = useState<TrackRecord | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api<TrackRecord>("/api/track-record").then(setData).catch(e => setError(e.message)); }, []);
  if (error) return <p role="alert">The track record could not be loaded: {error}</p>;
  if (!data) return <p className="text-ink/60">Loading your track record…</p>;

  const s = data.summary, today = todayUTC();
  const resolved = data.questions.filter(q => q.status === "resolved");
  const pending = data.questions.filter(q => q.status === "pending");
  // Past-due questions wait for their source to publish; they are not "coming up".
  const waiting = pending.filter(q => q.resolves_on < today);
  const open = pending.filter(q => q.resolves_on >= today);
  const next = open[0];
  const dueThisWeek = open.filter(q => (new Date(q.resolves_on).getTime() - new Date(today).getTime()) / DAY <= 7).length;
  const calls = s.right + s.wrong;
  const accuracy = versusGuessing(s.brier, s.coin_flip_brier);
  const weeks = byWeek(resolved);

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

    {weeks.length > 1 && <OverTime weeks={weeks} />}

    <section>
      <h3 className="font-serif text-3xl">Latest results</h3>
      {resolved.length
        ? <div className="mt-4"><Expandable groups={groupBySeries(resolved)} label="results"
            summary={g => `${g.items.filter(q => q.verdict === "right").length} of ${g.items.length} right · decided ${span(g)}`}>
            {q => <ResultRow key={q.id} q={q} />}</Expandable></div>
        : <p className="mt-3 text-ink/70">Nothing has been decided yet.</p>}
    </section>

    {waiting.length > 0 && <section>
      <h3 className="font-serif text-3xl">Waiting for results <span className="text-ink/40">{waiting.length}</span></h3>
      <p className="mt-1 text-sm text-ink/60">The date has passed; we score these as soon as the market settles or the official figure is out. Results are checked every day.</p>
      <ul className="mt-4 divide-y divide-rule border-y border-rule">{waiting.map(q => <OpenRow key={q.id} q={q} today={today} />)}</ul>
    </section>}

    <section>
      <h3 className="font-serif text-3xl">Coming up <span className="text-ink/40">{open.length}</span></h3>
      <p className="mt-1 text-sm text-ink/60">Our call on each open question, soonest first. Questions on the same figure are grouped.</p>
      <div className="mt-4"><Expandable groups={groupBySeries(open)} label="topics"
        summary={g => `Different thresholds or days · results ${span(g)}`}>
        {q => <OpenRow key={q.id} q={q} today={today} />}</Expandable></div>
    </section>

    <section>
      <h3 className="font-serif text-3xl">Who forecasts best</h3>
      <p className="mt-1 max-w-3xl text-sm text-ink/60">Every question goes to several forecasters. Our call is the combined forecast: the middle of their probabilities. Since Oct 24, forecasters with better records count for more, using only results known when the call was made. The prediction market is only a benchmark to beat; it is never part of our call. Each forecaster’s own record shows whether the combination beats it.</p>
      <ul className="mt-4 divide-y divide-rule border-y border-rule">{data.forecasters.map(f => <li key={f.method}
        className="grid gap-x-6 gap-y-1 py-4 sm:grid-cols-[1fr_auto] sm:items-center">
        <span><span className="font-medium">{f.label}</span><span className="block text-sm text-ink/60">{f.about}</span></span>
        <span className="sm:text-right">{f.resolved ? <><strong className="tabular-nums">{f.right} of {f.right + f.wrong} right</strong>
          <span className="block text-sm text-ink/60">{versusGuessing(f.brier, f.coin_flip_brier)} ({f.brier!.toFixed(2)}) · {f.forecasts} forecasts</span></>
          : <span className="text-sm text-ink/60">No results yet · {f.forecasts} forecasts</span>}</span>
      </li>)}</ul>
    </section>

    <p className="text-sm text-ink/50">Updated {shortDate(data.generated_on, true)} from official first releases. Test runs and drafts don’t count; they’re under More › All runs.</p>
  </div>;
}
