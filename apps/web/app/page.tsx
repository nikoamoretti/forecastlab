"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { CallPill, ResultMark } from "@/components/CallBadges";
import { AgainstMarket, Improving, OverTime } from "@/components/TrackCharts";
import { api } from "@/lib/api";
import { CallPolicy, DEFAULT_POLICY, EdgeTest, Forecaster, Question, QuestionGroup, TrackRecord, byWeek, callText, groupBySeries, lateDeadline, marketNameOf, nextMilestone, points, relativeDay, shortDate, todayUTC, versusGuessing } from "@/lib/trackRecord";

const SHOWN = 5;
const DAY = 86_400_000;
// A forecaster with no forecast for this long is listed under "earlier forecasters".
const QUIET_AFTER_DAYS = 14;

const Chevron = () => <svg aria-hidden viewBox="0 0 20 20" className="hidden h-5 w-5 shrink-0 text-ink/30 sm:block"><path d="m7.5 4.5 5.5 5.5-5.5 5.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>;

function ResultRow({ q }: { q: Question }) {
  return <li><Link href={`/q/${q.id}`} className="row grid gap-x-4 gap-y-1.5 sm:grid-cols-[5.75rem_1fr_auto] sm:items-center">
    <span><ResultMark verdict={q.verdict} scored={q.scored !== false} /></span>
    <span className="min-w-0">{q.title}
      <span className="mt-1 block text-sm text-ink/60">We said {callText(q.call!.probability)} · actual {q.actual ?? (q.outcome ? "yes" : "no")} · {shortDate(q.resolves_on)}</span></span>
    <Chevron />
  </Link></li>;
}

const settledBy = (q: Question) => marketNameOf(q.source_url) ?? "the official figure";

function OpenRow({ q, today }: { q: Question; today: string }) {
  const overdue = q.resolves_on < today;
  const late = overdue && settledBy(q) !== "the official figure" ? lateDeadline(q.detail ?? "", q.resolves_on) : null;
  return <li><Link href={`/q/${q.id}`} className="row">
    <span className="flex items-center justify-between gap-3">
      <span className="text-[13px] font-medium text-ink/55">{overdue ? `Due ${shortDate(q.resolves_on)}` : relativeDay(q.resolves_on, today)} · {q.topic}</span>
      {q.call && <CallPill probability={q.call.probability} />}</span>
    <span className="mt-1 block">{q.title}</span>
    {overdue && <span className="mt-1 block text-sm text-ink/60">Waiting for {settledBy(q)}{late && `; its rules allow until ${shortDate(late, true)}`}</span>}
  </Link></li>;
}

// Several questions on one indicator collapse into one row that opens to show each of them.
function GroupRow({ group, children, summary }: { group: QuestionGroup; children: (q: Question) => React.ReactNode; summary: string }) {
  const [open, setOpen] = useState(false);
  if (group.items.length === 1) return <>{children(group.items[0])}</>;
  return <li>
    <button className="row grid w-full gap-x-4 gap-y-1.5 text-left sm:grid-cols-[5.75rem_1fr_auto] sm:items-center"
      aria-expanded={open} onClick={() => setOpen(!open)}>
      <span><span className="chip">{group.items.length} questions</span></span>
      <span className="min-w-0">{group.label}<span className="mt-1 block text-sm text-ink/60">{summary}</span></span>
      <span aria-hidden className="hidden w-5 text-center text-xl leading-none text-ink/40 sm:block">{open ? "−" : "+"}</span>
    </button>
    {open && <ul className="rows border-t border-rule/60 bg-ink/[0.02] pl-3 sm:pl-6">{group.items.map(children)}</ul>}
  </li>;
}

function Expandable({ groups, label, children, summary }: {
  groups: QuestionGroup[]; label: string; children: (q: Question) => React.ReactNode; summary: (g: QuestionGroup) => string }) {
  const [all, setAll] = useState(false);
  return <>
    <ul className="rows">{(all ? groups : groups.slice(0, SHOWN)).map(g =>
      <GroupRow key={g.key} group={g} summary={summary(g)}>{children}</GroupRow>)}</ul>
    {groups.length > SHOWN && <p className="border-t border-rule/60 px-5 py-3 sm:px-6"><button className="btn-quiet" onClick={() => setAll(!all)}>
      {all ? "Show fewer" : `Show all ${groups.length} ${label}`}</button></p>}
  </>;
}

// A titled card that holds a list: the heading, an optional count and a line of explanation, then the rows.
function ListCard({ title, count, note, children, className = "" }: { title: string; count?: number; note?: string; children: React.ReactNode; className?: string }) {
  return <section className={`card min-w-0 overflow-hidden ${className}`}>
    <div className="px-5 pb-4 pt-5 sm:px-6">
      <h3 className="font-serif text-2xl">{title}{count != null && <> <span className="chip ml-1 align-middle font-sans">{count}</span></>}</h3>
      {note && <p className="mt-1.5 text-sm text-ink/60">{note}</p>}
    </div>
    <div className="border-t border-rule/60">{children}</div>
  </section>;
}

const span = (g: QuestionGroup) => {
  const days = g.items.map(q => q.resolves_on).sort();
  const first = shortDate(days[0]), last = shortDate(days[days.length - 1]);
  return first === last ? first : `${first} to ${last}`;
};

// One card per tested edge: how many new results it has, how it scores against the market on them, and where it stands.
function EdgesUnderTest({ edges, policy }: { edges: EdgeTest[]; policy: CallPolicy }) {
  const { questions, dates } = policy.bar;
  return <section aria-label="Edges under test">
    <h3 className="font-serif text-3xl">Edges under test</h3>
    <p className="mt-2 max-w-3xl text-ink/65">An idea joins our call only after it beats the market on at least {questions} new results across {dates} or more resolution days.</p>
    <ul className={`mt-5 grid grid-cols-1 gap-4 ${edges.length % 3 === 0 ? "md:grid-cols-3" : "sm:grid-cols-2"}`}>{edges.map(e => {
      const proven = e.status === "promoted";
      return <li key={e.id} className="card flex flex-col p-5">
        <p className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-medium">{e.label}</span>
          <strong className={`rounded-full px-2.5 py-0.5 text-sm font-medium ${proven ? "bg-pine/10 text-pine" : "bg-ink/[0.06] text-ink/70"}`}>{proven ? "Proven: part of our call" : "Testing"}</strong></p>
        {e.about && <p className="mt-2 text-sm leading-relaxed text-ink/65">{e.about}</p>}
        <div className="mt-auto pt-4">
          <div className="meter" aria-hidden><div className={proven ? "bg-pine" : "bg-ink/50"} style={{ width: `${Math.min(100, e.counted.questions / questions * 100)}%` }} /></div>
          <p className="mt-2.5 text-sm text-ink/65">{e.counted.questions} of {questions} results since {shortDate(e.registered)} · {e.counted.dates} of {dates} days</p>
          {e.counted.questions > 0 && e.counted.rule != null && e.counted.market != null
            && <p className="mt-1 text-sm tabular-nums">Edge {e.counted.rule.toFixed(2)} vs market {e.counted.market.toFixed(2)}</p>}
        </div>
      </li>;
    })}</ul>
  </section>;
}

function ForecasterRow({ f }: { f: Forecaster }) {
  const calls = f.right + f.wrong;
  return <li className="grid gap-x-8 gap-y-2 px-5 py-4 sm:grid-cols-[1fr_17rem] sm:items-center sm:px-6">
    <span className="min-w-0"><span className="font-medium">{f.label}</span><span className="mt-0.5 block text-sm text-ink/60">{f.about}</span></span>
    <span>{f.resolved ? <>
      <span className="flex items-center gap-3">
        <strong className="whitespace-nowrap tabular-nums">{f.right} of {calls} right</strong>
        {calls > 0 && <span className="meter" aria-hidden><span className="block bg-ink/55" style={{ width: `${f.right / calls * 100}%` }} /></span>}</span>
      <span className="mt-1 block text-sm text-ink/60">{versusGuessing(f.brier, f.coin_flip_brier)}: score {f.brier!.toFixed(2)} on {f.resolved} decided questions · {f.forecasts} forecasts</span></>
      : <span className="text-sm text-ink/60">No results yet · {f.forecasts} forecasts</span>}</span>
  </li>;
}

// The forecasters still forecasting come first; the ones that have gone quiet sit behind a button.
function Forecasters({ forecasters, asOf }: { forecasters: Forecaster[]; asOf: string }) {
  const [all, setAll] = useState(false);
  const quiet = (f: Forecaster) => !f.last_forecast_on
    || (new Date(asOf).getTime() - new Date(f.last_forecast_on).getTime()) / DAY > QUIET_AFTER_DAYS;
  // A record built before the dates were added lists everyone together.
  const dated = forecasters.some(f => f.last_forecast_on);
  const now = dated ? forecasters.filter(f => !quiet(f)) : forecasters;
  const earlier = dated ? forecasters.filter(quiet) : [];
  return <>
    <ul className="rows">{now.map(f => <ForecasterRow key={f.method} f={f} />)}</ul>
    {earlier.length > 0 && <>
      <p className="border-t border-rule/60 px-5 py-3 sm:px-6"><button className="btn-quiet" aria-expanded={all} onClick={() => setAll(!all)}>
        {all ? "Hide earlier forecasters" : `Show ${earlier.length} earlier ${earlier.length === 1 ? "forecaster" : "forecasters"}`}</button>
        <span className="ml-3 text-sm text-ink/55">No forecast in the last two weeks.</span></p>
      {all && <ul className="rows border-t border-rule/60 bg-ink/[0.02]">{earlier.map(f => <ForecasterRow key={f.method} f={f} />)}</ul>}
    </>}
  </>;
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
  const vs = s.vs_market && s.vs_market.questions > 0 && s.vs_market.ours != null && s.vs_market.market != null ? s.vs_market : null;
  const policy = s.call_policy ?? DEFAULT_POLICY;
  const weeks = byWeek(resolved);
  const goal = nextMilestone(s.resolved);
  const due = dueThisWeek > 0 ? ` ${dueThisWeek} more ${dueThisWeek === 1 ? "is" : "are"} due this week.` : "";
  const figure = "font-sans text-4xl font-semibold tracking-tight";

  return <div className="space-y-12 sm:space-y-14">
    <header className="grid grid-cols-1 gap-x-10 gap-y-8 lg:grid-cols-[1fr_24rem] lg:items-end">
      <div>
        <p className="eyebrow">Track record</p>
        <h2 className="mt-3">{s.brier != null ? <><span className="font-sans text-5xl font-semibold tracking-tight sm:text-6xl">Score {s.brier.toFixed(2)}</span>{" "}
          <span className="mt-1 block font-serif text-2xl text-ink/65 sm:ml-1 sm:mt-0 sm:inline">on all {s.resolved} decided questions</span></>
          : <span className="font-serif text-5xl">No results yet</span>}</h2>
        {accuracy && <p className="mt-4 text-lg font-medium">{accuracy} so far
          <span className="block text-sm font-normal text-ink/60 sm:inline"> · lower is better; always saying 50% scores 0.25</span></p>}
        <p className="mt-3 max-w-xl text-ink/70">Yes-or-no questions about the real world, forecast before the answer is known. Every call stays on this page and is scored once the result is in.</p>
      </div>
      {next ? <Link href={`/q/${next.id}`} className="card block border-l-4 border-l-copper p-5 transition-colors hover:border-copper">
        <p className="eyebrow">Next result · {relativeDay(next.resolves_on, today)}</p>
        <p className="mt-2 text-lg leading-snug">{next.title}</p>
        {next.call && <p className="mt-3 text-sm"><span className="text-ink/60">Our call: </span><strong>{callText(next.call.probability)}</strong></p>}
      </Link> : <Link href="/ask" className="btn-primary justify-self-start lg:justify-self-end">Ask a question</Link>}
    </header>

    <section aria-label="The record in numbers">
     <div className={`grid grid-cols-1 gap-4 ${vs ? "lg:grid-cols-[1fr_1.35fr_1fr]" : "sm:grid-cols-2"}`}>
      <div className="card p-5 sm:p-6">
        <p className="eyebrow">Calls right</p>
        {calls > 0 ? <>
          <p className={`mt-3 ${figure}`}>{Math.round(s.right / calls * 100)}%</p>
          <div className="meter mt-4" aria-hidden><div className="bg-pine" style={{ width: `${s.right / calls * 100}%` }} /></div>
          <p className="mt-3 text-sm text-ink/65">{s.right} of {calls} calls right{s.toss_ups > 0 && ` · ${s.toss_ups} ${s.toss_ups === 1 ? "toss-up isn’t" : "toss-ups aren’t"} counted as right or wrong`}</p>
        </> : <p className="mt-3 text-ink/65">No call has been decided yet.</p>}
      </div>
      {vs && <div className="card p-5 sm:p-6">
        <p className="eyebrow">Against the market</p>
        <div className="mt-4"><AgainstMarket vs={vs} /></div>
      </div>}
      <div className="card p-5 sm:p-6">
        <p className="eyebrow">Decided so far</p>
        {goal ? <>
          <p className="mt-3"><span className={figure}>{s.resolved}</span> <span className="text-ink/65">of {goal.at} results</span></p>
          <div className="meter mt-4" role="progressbar" aria-label="Decided questions"
            aria-valuenow={s.resolved} aria-valuemin={0} aria-valuemax={goal.at}>
            <div className="bg-pine" style={{ width: `${Math.max(s.resolved / goal.at * 100, 2)}%` }} /></div>
          <p className="mt-3 text-sm text-ink/65">{goal.why}{due}</p>
        </> : <>
          <p className="mt-3"><span className={figure}>{s.resolved}</span> <span className="text-ink/65">results</span></p>
          <p className="mt-3 text-sm text-ink/65">Enough decided questions for the score to be reliable.{due}</p>
        </>}
        {pending.length > 0 && <p className="mt-2 text-sm text-ink/65">{pending.length} still open: {open.length} coming up, {waiting.length} waiting for a result.</p>}
      </div>
     </div>
     {vs?.since_anchored && <p className="mt-4 max-w-4xl text-sm leading-relaxed text-ink/60">From {shortDate(vs.since_anchored.date)} our call starts from the market{policy.default_move > 0
       ? ` and cannot differ from it by more than ${points(policy.default_move)}, so “ours vs market” mostly measures those ${points(policy.default_move)}.`
       : " and is its price unless a tested edge applies, so “ours vs market” mostly measures those edges."} “Claude alone vs market” is the test of independent skill.</p>}
    </section>

    {(weeks.length > 1 || s.progress) && <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
      {weeks.length > 1 && <OverTime weeks={weeks} />}
      {s.progress && <Improving progress={s.progress} policy={policy} />}
    </div>}

    {s.edge_tests && s.edge_tests.length > 0 && <EdgesUnderTest edges={s.edge_tests} policy={policy} />}

    <ListCard title="Latest results">
      {resolved.length
        ? <Expandable groups={groupBySeries(resolved)} label="results"
            summary={g => `${g.items.filter(q => q.verdict === "right").length} of ${g.items.length} right · decided ${span(g)}`}>
            {q => <ResultRow key={q.id} q={q} />}</Expandable>
        : <p className="px-5 py-4 text-ink/70 sm:px-6">Nothing has been decided yet.</p>}
    </ListCard>

    <div className={`grid grid-cols-1 items-start gap-4 ${waiting.length > 0 ? "lg:grid-cols-2" : ""}`}>
      <ListCard title="Coming up" count={open.length} note="Our call on each open question, soonest first. Questions on the same figure are grouped.">
        {open.length
          ? <Expandable groups={groupBySeries(open)} label="topics"
              summary={g => `Different thresholds or days · results ${span(g)}`}>
              {q => <OpenRow key={q.id} q={q} today={today} />}</Expandable>
          : <p className="px-5 py-4 text-ink/70 sm:px-6">No open question right now.</p>}
      </ListCard>
      {waiting.length > 0 && <ListCard title="Waiting for results" count={waiting.length}
        note="The date has passed; we score these as soon as the market settles or the official figure is out. Results are checked every day.">
        <Expandable groups={waiting.map(q => ({ key: q.id, label: q.title, items: [q] }))} label="waiting" summary={() => ""}>
          {q => <OpenRow key={q.id} q={q} today={today} />}</Expandable>
      </ListCard>}
    </div>

    <ListCard title="Who forecasts best"
      note="A score only compares fairly on the same questions: the market comparison at the top uses the questions the market also priced, while each row below covers the questions that forecaster answered.">
      <details className="border-b border-rule/60 px-5 py-3 text-sm sm:px-6">
        <summary className="cursor-pointer text-ink/70 hover:text-ink">How our call has changed over time</summary>
        <p className="mt-2 max-w-3xl leading-relaxed text-ink/65">First it was the middle of the forecasters’ probabilities, then (from Oct 24) a median where better records counted for more, from Nov 5 Claude’s own forecast, and from Nov 10 the market’s price{policy.default_move > 0 ? ` moved toward Claude’s forecast by at most ${points(policy.default_move)}` : ", moved toward Claude’s forecast only under a tested edge"}, held at the price when the research was thin. Claude’s forecast never sees the price; it is added only when the call is fixed. Each call is frozen when made. Claude alone stays on the list as its own forecaster, so the page shows both numbers; it is the test of independent skill, because our call {policy.default_move > 0 ? `can no longer differ from the market by more than ${points(policy.default_move)}` : "is the market’s price unless a tested edge applies"}.</p>
      </details>
      <Forecasters forecasters={data.forecasters} asOf={data.generated_on} />
    </ListCard>

    <footer className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-t border-rule/70 pt-6 text-sm text-ink/55">
      <p>Updated {shortDate(data.generated_on, true)} from official first releases. Test runs and drafts don’t count; they’re under More › All runs.</p>
      <Link href="/ask" className="btn-quiet">Ask a question</Link>
    </footer>
  </div>;
}
