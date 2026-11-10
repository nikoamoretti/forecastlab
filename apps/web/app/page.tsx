"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { CallPill, ResultMark } from "@/components/CallBadges";
import { api } from "@/lib/api";
import { CallPolicy, DEFAULT_POLICY, EdgeTest, Progress, Question, QuestionGroup, TrackRecord, Week, byWeek, callText, groupBySeries, lateDeadline, marketNameOf, nextMilestone, points, relativeDay, shortDate, todayUTC, trend, versusGuessing } from "@/lib/trackRecord";

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

const settledBy = (q: Question) => marketNameOf(q.source_url) ?? "the official figure";

function OpenRow({ q, today }: { q: Question; today: string }) {
  const overdue = q.resolves_on < today;
  const late = overdue && settledBy(q) !== "the official figure" ? lateDeadline(q.detail ?? "", q.resolves_on) : null;
  return <li><Link href={`/q/${q.id}`} className="grid gap-x-4 gap-y-1 py-4 hover:bg-white/50 sm:grid-cols-[6rem_1fr_auto] sm:items-center">
    <span className="text-sm text-ink/60">{overdue ? `Due ${shortDate(q.resolves_on)}` : relativeDay(q.resolves_on, today)}</span>
    <span>{q.title}{overdue && <span className="mt-1 block text-sm text-ink/60">Waiting for {settledBy(q)}{late && `; its rules allow until ${shortDate(late, true)}`}</span>}</span>
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
  const days = g.items.map(q => q.resolves_on).sort();
  const first = shortDate(days[0]), last = shortDate(days[days.length - 1]);
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
    <p className="mt-1 max-w-3xl text-sm text-ink/60">Each bar is the share of calls we got right in the week the questions were decided. The dashed line is a coin flip (50%). The copper line is the trend, with busier weeks counting for more. Under each week: calls right / calls decided.</p>
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
          <text x={x(i)} y={h - 10} textAnchor="middle" className="fill-ink/50 text-[9px]">{wk.right}/{wk.right + wk.wrong}</text>
        </g>;
      })}
      <line x1={x(0)} x2={x(weeks.length - 1)} y1={y(fit.start)} y2={y(fit.end)} className="stroke-copper" strokeWidth={2.5} strokeLinecap="round" />
    </svg>
  </section>;
}

// Brier score by the week we made the forecast, ours against the market's, with a marker where we changed the method.
// One row per tested edge: how many new results it has, how it scores against the market on them, and where it stands.
function EdgesUnderTest({ edges, policy }: { edges: EdgeTest[]; policy: CallPolicy }) {
  const { questions, dates } = policy.bar;
  return <section aria-label="Edges under test">
    <h3 className="font-serif text-3xl">Edges under test</h3>
    <p className="mt-1 max-w-3xl text-sm text-ink/60">An idea joins our call only after it beats the market on at least {questions} new results across {dates} or more resolution days.</p>
    <ul className="mt-4 divide-y divide-rule border-y border-rule">{edges.map(e => <li key={e.id} className="grid gap-x-6 gap-y-1 py-4 sm:grid-cols-[1fr_auto] sm:items-center">
      <span><span className="font-medium">{e.label}</span>
        <span className="block text-sm text-ink/60">{e.counted.questions} of {questions} results since {shortDate(e.registered)} · {e.counted.dates} of {dates} days</span></span>
      <span className="sm:text-right">
        {e.counted.questions > 0 && e.counted.rule != null && e.counted.market != null
          && <span className="block text-sm text-ink/70">Edge {e.counted.rule.toFixed(2)} vs market {e.counted.market.toFixed(2)}</span>}
        <strong>{e.status === "promoted" ? "Proven: part of our call" : "Testing"}</strong></span>
    </li>)}</ul>
  </section>;
}

const ENOUGH_TO_JUDGE = 40, TOO_EARLY = 10, THIN_WEEK = 3;

function Improving({ progress, policy }: { progress: Progress; policy: CallPolicy }) {
  const weeks = progress.weeks.filter(wk => wk.ours != null && wk.market != null);
  const { before, after, date } = progress.since_last_change;
  if (weeks.length < 2) return null;
  const f = (v: number | null) => v!.toFixed(2);
  const headline = after.questions < TOO_EARLY
    ? `Too early to tell: ${after.questions} ${after.questions === 1 ? "question" : "questions"} decided since ${shortDate(date)}, we need about ${ENOUGH_TO_JUDGE}.`
    : `Since the ${shortDate(date)} changes: our score ${f(after.ours)} vs the market's ${f(after.market)} on ${after.questions} questions`
      + (before.questions > 0 ? ` (before: ${f(before.ours)} vs ${f(before.market)} on ${before.questions}).` : ".");
  const w = 360, h = 220, left = 36, right = 12, top = 52, bottom = 40;
  const day = (iso: string) => new Date(`${iso}T12:00:00Z`).getTime() / DAY;
  const changes = progress.changes;
  const t0 = day(weeks[0].start), t1 = Math.max(day(weeks[weeks.length - 1].start) + 7, ...changes.map(c => day(c.date) + 1));
  const x = (t: number) => left + (t - t0) / (t1 - t0) * (w - left - right);
  const centre = (wk: { start: string }) => x(day(wk.start) + 3.5);
  const top_ = Math.max(0.3, Math.ceil(Math.max(...weeks.flatMap(wk => [wk.ours!, wk.market!])) * 20) / 20);
  const y = (v: number) => top + (1 - v / top_) * (h - top - bottom);
  const ticks = [0, top_ / 2, top_];
  const line = (pick: (wk: typeof weeks[number]) => number | null) => weeks.map(wk => `${centre(wk)},${y(pick(wk)!)}`).join(" ");
  const shown = changes.filter(c => day(c.date) >= t0 && day(c.date) <= t1);
  return <section aria-label="Are we improving?">
    <h3 className="font-serif text-3xl">Are we improving?</h3>
    <p className="mt-3 text-lg font-medium">{headline}</p>
    <p className="mt-1 max-w-3xl text-sm text-ink/60">Score by the week we made the forecast, on questions the market also priced. Lower is better. The copper line is us, the dashed line is the market. Dotted lines mark when we changed how we forecast. Small dots are weeks with fewer than {THIN_WEEK} decided questions, so read them lightly; weeks fill in as questions are decided. From Nov 10 our call {policy.default_move > 0 ? `stays within ${points(policy.default_move)} of the market` : "is the market’s price unless a tested edge applies"}, so the two lines will sit {policy.default_move > 0 ? "closer together" : "very close together"}; Claude alone is the independent comparison.</p>
    <svg viewBox={`0 0 ${w} ${h}`} className="mt-4 w-full max-w-xl" role="img"
      aria-label={`Score by forecast week, ours against the market, lower is better: ${weeks.map(wk => `week of ${shortDate(wk.start)} us ${f(wk.ours)}, market ${f(wk.market)}, ${wk.questions} questions`).join("; ")}. Changes: ${changes.map(c => `${c.label} on ${shortDate(c.date)}`).join(", ")}. ${headline}`}>
      {ticks.map(t => <g key={t}>
        <line x1={left} x2={w - right} y1={y(t)} y2={y(t)} className={t === 0 ? "stroke-ink/40" : "stroke-rule"} />
        <text x={left - 6} y={y(t) + 3} textAnchor="end" className="fill-ink/60 text-[9px]">{t.toFixed(2)}</text>
      </g>)}
      {shown.map((c, i) => {
        const cx = x(day(c.date)), flip = cx > w * 0.6;
        return <g key={c.date}>
          <line x1={cx} x2={cx} y1={top - 6 - (i % 3) * 12} y2={y(0)} className="stroke-ink/50" strokeDasharray="1 3" strokeWidth={1.5} />
          <text x={flip ? cx - 3 : cx + 3} y={top - 8 - (i % 3) * 12} textAnchor={flip ? "end" : "start"} className="fill-ink/70 text-[9px]">{shortDate(c.date)} {c.label}</text>
        </g>;
      })}
      <polyline points={line(wk => wk.market)} fill="none" className="stroke-ink/60" strokeWidth={2} strokeDasharray="4 4" />
      <polyline points={line(wk => wk.ours)} fill="none" className="stroke-copper" strokeWidth={2.5} strokeLinejoin="round" />
      {weeks.map(wk => <g key={wk.start}>
        <circle cx={centre(wk)} cy={y(wk.market!)} r={wk.questions < THIN_WEEK ? 2 : 3} className="fill-ink/60" />
        <circle cx={centre(wk)} cy={y(wk.ours!)} r={wk.questions < THIN_WEEK ? 2.5 : 4} className="fill-copper" />
        <text x={centre(wk)} y={h - 24} textAnchor="middle" className="fill-ink/70 text-[10px]">{shortDate(wk.start)}</text>
        <text x={centre(wk)} y={h - 10} textAnchor="middle" className="fill-ink/50 text-[9px]">{wk.questions} q</text>
      </g>)}
    </svg>
    <p className="mt-2 flex gap-6 text-sm text-ink/70"><span><span className="text-copper">●</span> Us</span><span><span className="text-ink/60">●</span> Market</span></p>
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
  const vs = s.vs_market && s.vs_market.questions > 0 && s.vs_market.ours != null && s.vs_market.market != null ? s.vs_market : null;
  const policy = s.call_policy ?? DEFAULT_POLICY;
  const anchored = vs?.since_anchored && vs.since_anchored.questions > 0 && vs.since_anchored.ours != null && vs.since_anchored.claude != null && vs.since_anchored.market != null ? vs.since_anchored : null;
  const weeks = byWeek(resolved);
  const goal = nextMilestone(s.resolved);
  const due = dueThisWeek > 0 ? ` ${dueThisWeek} more ${dueThisWeek === 1 ? "is" : "are"} due this week.` : "";

  return <div className="space-y-12">
    <header className="flex flex-wrap items-end justify-between gap-6">
      <div>
        <p className="font-mono text-xs uppercase tracking-widest text-copper">Track record</p>
        <h2 className="mt-3 font-serif text-5xl">{s.brier != null ? <>Score {s.brier.toFixed(2)}{" "}
          <span className="block text-2xl text-ink/70 sm:inline">on all {s.resolved} decided questions</span></> : "No results yet"}</h2>
        {accuracy && <p className="mt-3 text-lg">{accuracy} so far
          <span className="block text-sm text-ink/60 sm:inline"> · lower is better; always saying 50% scores 0.25</span></p>}
        {vs && <p className="mt-2 text-lg">On the {vs.questions} questions the market also priced: us {vs.ours!.toFixed(2)}, market {vs.market!.toFixed(2)}.</p>}
        {vs?.claude != null && <p className="mt-1 text-sm text-ink/70">Claude alone on the same questions: {vs.claude.toFixed(2)}{vs.claude_questions !== vs.questions && ` (the ${vs.claude_questions} it forecast)`}.</p>}
        {anchored && <p className="mt-1 text-sm text-ink/70">Since {shortDate(anchored.date)}, on {anchored.questions} questions the market also priced: our call {anchored.ours!.toFixed(2)}, Claude alone {anchored.claude!.toFixed(2)}, market {anchored.market!.toFixed(2)}.</p>}
        {vs?.since_anchored && <p className="mt-1 max-w-2xl text-sm text-ink/60">From {shortDate(vs.since_anchored.date)} our call starts from the market{policy.default_move > 0
          ? ` and cannot differ from it by more than ${points(policy.default_move)}, so “ours vs market” mostly measures those ${points(policy.default_move)}.`
          : " and is its price unless a tested edge applies, so “ours vs market” mostly measures those edges."} “Claude alone vs market” is the test of independent skill.</p>}
        {calls > 0 && <p className="mt-2 text-sm text-ink/60">{s.right} of {calls} calls right{s.toss_ups > 0 && ` · ${s.toss_ups} ${s.toss_ups === 1 ? "toss-up isn’t" : "toss-ups aren’t"} counted as right or wrong`}</p>}
      </div>
      <Link href="/ask" className="bg-ink px-6 py-3 text-paper hover:bg-ink/85">Ask a question</Link>
    </header>

    <section aria-label="Progress and next result" className="grid gap-4 md:grid-cols-2">
      <div className="border border-rule bg-white/40 p-5">
        {goal ? <>
          <p className="font-medium">{s.resolved} of {goal.at} results</p>
          <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-ink/10" role="progressbar" aria-label="Decided questions"
            aria-valuenow={s.resolved} aria-valuemin={0} aria-valuemax={goal.at}>
            <div className="h-full rounded-full bg-pine" style={{ width: `${Math.max(s.resolved / goal.at * 100, 2)}%` }} /></div>
          <p className="mt-3 text-sm text-ink/70">{goal.why}{due}</p>
        </> : <>
          <p className="font-medium">{s.resolved} results</p>
          <p className="mt-3 text-sm text-ink/70">Enough decided questions for the score to be reliable.{due}</p>
        </>}
      </div>
      {next && <Link href={`/q/${next.id}`} className="block border-2 border-copper bg-white/60 p-5 hover:bg-white">
        <p className="font-mono text-xs uppercase tracking-widest text-copper">Next result · {relativeDay(next.resolves_on, today)}</p>
        <p className="mt-2 text-lg">{next.title}</p>
        {next.call && <p className="mt-2 text-sm"><span className="text-ink/60">Our call: </span><strong>{callText(next.call.probability)}</strong></p>}
      </Link>}
    </section>

    {weeks.length > 1 && <OverTime weeks={weeks} />}

    {s.progress && <Improving progress={s.progress} policy={policy} />}

    {s.edge_tests && s.edge_tests.length > 0 && <EdgesUnderTest edges={s.edge_tests} policy={policy} />}

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
      <p className="mt-1 max-w-3xl text-sm text-ink/60">Our call has changed over time: first the middle of the forecasters’ probabilities, then (from Oct 24) a median where better records counted for more, from Nov 5 Claude’s own forecast, and from Nov 10 the market’s price{policy.default_move > 0 ? ` moved toward Claude’s forecast by at most ${points(policy.default_move)}` : ", moved toward Claude’s forecast only under a tested edge"}, held at the price when the research was thin. Claude’s forecast never sees the price; it is added only when the call is fixed. Each call is frozen when made. Claude alone stays on the list as its own forecaster, so the page shows both numbers; it is the test of independent skill, because our call {policy.default_move > 0 ? `can no longer differ from the market by more than ${points(policy.default_move)}` : "is the market’s price unless a tested edge applies"}. A score only compares fairly on the same questions: the market comparison at the top uses the questions the market also priced, while each row below covers the questions that forecaster answered.</p>
      <ul className="mt-4 divide-y divide-rule border-y border-rule">{data.forecasters.map(f => <li key={f.method}
        className="grid gap-x-6 gap-y-1 py-4 sm:grid-cols-[1fr_auto] sm:items-center">
        <span><span className="font-medium">{f.label}</span><span className="block text-sm text-ink/60">{f.about}</span></span>
        <span className="sm:text-right">{f.resolved ? <><strong className="tabular-nums">{f.right} of {f.right + f.wrong} right</strong>
          <span className="block text-sm text-ink/60">{versusGuessing(f.brier, f.coin_flip_brier)}: score {f.brier!.toFixed(2)} on {f.resolved} decided questions · {f.forecasts} forecasts</span></>
          : <span className="text-sm text-ink/60">No results yet · {f.forecasts} forecasts</span>}</span>
      </li>)}</ul>
    </section>

    <p className="text-sm text-ink/50">Updated {shortDate(data.generated_on, true)} from official first releases. Test runs and drafts don’t count; they’re under More › All runs.</p>
  </div>;
}
