"use client";
import { useLayoutEffect, useRef, useState } from "react";
import { CallPolicy, Progress, ProgressWeek, VersusMarket, Week, points, shortDate, trend } from "@/lib/trackRecord";

const DAY = 86_400_000;

// The width the chart has on screen. Charts are drawn at their real size so the lettering stays
// readable on a phone instead of shrinking with the picture.
function useWidth<T extends HTMLElement>(fallback = 300) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const read = () => setWidth(Math.max(260, Math.round(element.clientWidth)));
    read();
    const observer = new ResizeObserver(read);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

// The small box that follows the pointer. Every value in it is also in the table under the chart.
function Tip({ x, y, width, children }: { x: number; y: number; width: number; children: React.ReactNode }) {
  const half = 84;
  return <div role="presentation" className="pointer-events-none absolute z-10 w-[168px] -translate-x-1/2 -translate-y-full rounded-lg border border-rule bg-white px-3 py-2 text-[13px] leading-snug shadow-lg"
    style={{ left: Math.min(Math.max(x, half), Math.max(half, width - half)), top: y - 8 }}>{children}</div>;
}

// What each mark means, under the chart: identity is never left to colour alone.
function Key({ children }: { children: React.ReactNode }) {
  return <p className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-[13px] text-ink/70">{children}</p>;
}
const lineKey = (color: string) => <span aria-hidden className={`mr-1.5 inline-block h-0.5 w-5 rounded-full align-middle ${color}`} />;
const dashKey = <span aria-hidden className="mr-1.5 inline-block w-5 border-t-2 border-dashed border-ink/40 align-middle" />;

// The same figures as the chart, as a table, for anyone who would rather read them (or cannot hover).
function Numbers({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return <details className="mt-3 text-sm">
    <summary className="cursor-pointer text-ink/60 hover:text-ink">See the numbers</summary>
    <div className="mt-2 overflow-x-auto"><table className="w-full text-left tabular-nums">
      <thead><tr className="text-ink/60">{head.map(h => <th key={h} className="border-b border-rule py-1.5 pr-4 font-normal">{h}</th>)}</tr></thead>
      <tbody>{rows.map((row, n) => <tr key={n}>{row.map((cell, k) => <td key={k} className="border-b border-rule/50 py-1.5 pr-4">{cell}</td>)}</tr>)}</tbody>
    </table></div>
  </details>;
}

// A column with a rounded top and a square foot on the baseline.
function column(x: number, width: number, top: number, base: number) {
  const r = Math.min(4, width / 2, Math.max(0, base - top));
  return `M${x},${base} V${top + r} Q${x},${top} ${x + r},${top} H${x + width - r} Q${x + width},${top} ${x + width},${top + r} V${base} Z`;
}

// The share of calls we got right in each week, with a coin flip (50%) to compare against.
export function OverTime({ weeks: all }: { weeks: Week[] }) {
  const [box, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const weeks = all.filter(wk => wk.right + wk.wrong > 0).slice(-8);
  if (weeks.length < 2) return null;
  const hit = (wk: Week) => wk.right / (wk.right + wk.wrong);
  const fit = trend(weeks, hit)!;
  const narrow = width < 420;
  const h = 250, left = narrow ? 34 : 42, right = narrow ? 2 : 10, top = 24, bottom = 46, base = h - bottom;
  const slot = (width - left - right) / weeks.length, bar = Math.min(24, slot * 0.56);
  const x = (i: number) => left + slot * (i + 0.5);
  const y = (v: number) => top + (1 - Math.min(1, Math.max(0, v))) * (base - top);
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  const change = fit.end - fit.start;
  const summary = Math.abs(change) < 0.05 ? `Holding steady at about ${pct(fit.end)} of calls right.`
    : `${change > 0 ? "Getting better" : "Slipping"}: from about ${pct(fit.start)} to ${pct(fit.end)} of calls right.`;
  const small = narrow ? "text-[10px]" : "text-[11px]";
  // Where the weeks stand too close for every date to fit, every second week is named, counting back from the latest.
  const labelled = (i: number) => slot >= 44 || (weeks.length - 1 - i) % 2 === 0;
  return <section aria-label="How we've done over time" className="card min-w-0 p-5 sm:p-6">
    <h3 className="font-serif text-2xl">Over time</h3>
    <p className="mt-2 font-medium">{summary}</p>
    <div ref={box} className="relative mt-4" onPointerLeave={() => setHover(null)}>
      <svg width={width} height={h} viewBox={`0 0 ${width} ${h}`} className="block max-w-full" role="img"
        aria-label={`By week: ${weeks.map(wk => `${shortDate(wk.start)} ${pct(hit(wk))} (${wk.right} of ${wk.right + wk.wrong} right)`).join(", ")}. ${summary}`}>
        {[0, 0.25, 0.5, 0.75, 1].map(t => <g key={t}>
          <line x1={left} x2={width - right} y1={y(t)} y2={y(t)} className={t === 0 ? "stroke-ink/40" : "stroke-ink/10"} />
          <text x={left - 8} y={y(t) + 4} textAnchor="end" className={`fill-ink/60 tabular-nums ${small}`}>{pct(t)}</text>
        </g>)}
        <line x1={left} x2={width - right} y1={y(0.5)} y2={y(0.5)} className="stroke-ink/40" strokeWidth={1.5} strokeDasharray="5 4" />
        {weeks.map((wk, i) => hit(wk) > 0
          && <path key={wk.start} d={column(x(i) - bar / 2, bar, y(hit(wk)), base)} className={hover === i ? "fill-chart-us/80" : "fill-chart-us"} />)}
        <line x1={x(0)} x2={x(weeks.length - 1)} y1={y(fit.start)} y2={y(fit.end)} className="stroke-white" strokeWidth={5} strokeLinecap="round" />
        <line x1={x(0)} x2={x(weeks.length - 1)} y1={y(fit.start)} y2={y(fit.end)} className="stroke-ink/80" strokeWidth={2} strokeLinecap="round" />
        {weeks.map((wk, i) => <g key={wk.start}>
          {/* The value sits on the cap, with a ring of the card's colour so the trend line never strikes through it. */}
          <text x={x(i)} y={y(hit(wk)) - 6} textAnchor="middle" className={`fill-ink stroke-white font-medium tabular-nums ${small}`}
            strokeWidth={4} strokeLinejoin="round" style={{ paintOrder: "stroke" }}>{pct(hit(wk))}</text>
          {labelled(i) && <>
            <text x={x(i)} y={h - 26} textAnchor="middle" className={`fill-ink/75 ${small}`}>{shortDate(wk.start)}</text>
            <text x={x(i)} y={h - 10} textAnchor="middle" className="fill-ink/50 text-[10px] tabular-nums">{wk.right}/{wk.right + wk.wrong}</text></>}
        </g>)}
        {weeks.map((wk, i) => <rect key={wk.start} x={left + slot * i} width={slot} y={top - 20} height={base - top + 20} fill="transparent"
          onPointerEnter={() => setHover(i)} onPointerMove={() => setHover(i)} />)}
      </svg>
      {hover != null && <Tip x={x(hover)} y={y(hit(weeks[hover]))} width={width}>
        <strong className="block text-base tabular-nums">{pct(hit(weeks[hover]))} right</strong>
        <span className="block text-ink/70">{weeks[hover].right} of {weeks[hover].right + weeks[hover].wrong} {weeks[hover].right + weeks[hover].wrong === 1 ? "call" : "calls"} right</span>
        <span className="block text-ink/50">Week of {shortDate(weeks[hover].start)}</span>
      </Tip>}
    </div>
    <Key>
      <span><span aria-hidden className="mr-1.5 inline-block h-2.5 w-2.5 rounded-[3px] bg-chart-us align-baseline" />Calls right that week</span>
      <span>{lineKey("bg-ink/80")}Trend</span>
      <span>{dashKey}Coin flip (50%)</span>
    </Key>
    <p className="mt-3 text-sm text-ink/60">Each bar is the share of calls we got right in the week the questions were decided. The trend counts busier weeks for more. Under each week: calls right / calls decided.</p>
    <Numbers head={["Week of", "Calls right", "Share"]} rows={weeks.map(wk => [shortDate(wk.start), `${wk.right} of ${wk.right + wk.wrong}`, pct(hit(wk))])} />
  </section>;
}

const ENOUGH_TO_JUDGE = 40, TOO_EARLY = 10, THIN_WEEK = 3;
type Series = { key: "market" | "claude" | "ours"; name: string; line: string; fill: string; ring: string; bg: string };
// Drawn in this order, so our own line lies on top. A series keeps its colour wherever it appears on the site.
const SERIES: Series[] = [
  { key: "market", name: "Market", line: "stroke-chart-market", fill: "fill-chart-market", ring: "stroke-chart-market", bg: "bg-chart-market" },
  { key: "claude", name: "Claude alone", line: "stroke-chart-claude", fill: "fill-chart-claude", ring: "stroke-chart-claude", bg: "bg-chart-claude" },
  { key: "ours", name: "Our call", line: "stroke-chart-us", fill: "fill-chart-us", ring: "stroke-chart-us", bg: "bg-chart-us" },
];
const LEGEND = ["ours", "market", "claude"].map(key => SERIES.find(s => s.key === key)!);

// Gives each marker a row: 0 normally, one lower each time it sits within 20px of the marker before it.
function stagger<T extends { cx: number }>(marks: T[]): (T & { row: number })[] {
  const out: (T & { row: number })[] = [];
  for (const mark of marks) {
    const last = out[out.length - 1];
    out.push({ ...mark, row: last && mark.cx - last.cx < 20 ? last.row + 1 : 0 });
  }
  return out;
}

// Brier score by the week we made the forecast, ours against the market's and Claude alone, with a numbered marker where we changed the method.
export function Improving({ progress, policy }: { progress: Progress; policy: CallPolicy }) {
  const [box, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const weeks = progress.weeks.filter(wk => wk.ours != null && wk.market != null);
  const { before, after, date } = progress.since_last_change;
  if (weeks.length < 2) return null;
  const f = (v: number | null | undefined) => v!.toFixed(2);
  const headline = after.questions < TOO_EARLY
    ? `Too early to tell: ${after.questions} ${after.questions === 1 ? "question" : "questions"} decided since ${shortDate(date)}, we need about ${ENOUGH_TO_JUDGE}.`
    : `Since the ${shortDate(date)} changes: our score ${f(after.ours)} vs the market's ${f(after.market)} on ${after.questions} questions`
      + (before.questions > 0 ? ` (before: ${f(before.ours)} vs ${f(before.market)} on ${before.questions}).` : ".");
  const series = SERIES.filter(s => weeks.some(wk => wk[s.key] != null));
  const narrow = width < 420;
  const day = (iso: string) => new Date(`${iso}T12:00:00Z`).getTime() / DAY;
  const changes = progress.changes;
  const t0 = day(weeks[0].start), t1 = Math.max(day(weeks[weeks.length - 1].start) + 7, ...changes.map(c => day(c.date) + 1));
  const left = narrow ? 34 : 42, right = 12, bottom = 46;
  const x = (t: number) => left + (t - t0) / (t1 - t0) * (width - left - right);
  // Changes that fall close together get their number on a second (or third) row, so no two numbers overlap.
  const shown = stagger(changes.map((c, n) => ({ ...c, n: n + 1, cx: x(day(c.date)) })).filter(c => day(c.date) >= t0 && day(c.date) <= t1));
  const rows = Math.max(0, ...shown.map(c => c.row)) + 1;
  const top = 14 + rows * 20, h = top + 170 + bottom, base = h - bottom;
  const centre = (wk: { start: string }) => x(day(wk.start) + 3.5);
  const peak = Math.max(...weeks.flatMap(wk => series.map(s => wk[s.key] ?? 0)));
  const top_ = Math.max(0.3, Math.ceil(peak * 10) / 10);
  const y = (v: number) => top + (1 - v / top_) * (base - top);
  const ticks = Array.from({ length: Math.round(top_ * 10) + 1 }, (_, n) => n / 10);
  const small = narrow ? "text-[10px]" : "text-[11px]";
  const nearest = (px: number) => weeks.reduce((best, wk, i) => Math.abs(centre(wk) - px) < Math.abs(centre(weeks[best]) - px) ? i : best, 0);
  const at = hover != null ? weeks[hover] : null;
  return <section aria-label="Are we improving?" className="card min-w-0 p-5 sm:p-6">
    <h3 className="font-serif text-2xl">Are we improving?</h3>
    <p className="mt-2 font-medium">{headline}</p>
    <div ref={box} className="relative mt-4" onPointerLeave={() => setHover(null)}
      onPointerMove={event => setHover(nearest(event.clientX - event.currentTarget.getBoundingClientRect().left))}>
      <svg width={width} height={h} viewBox={`0 0 ${width} ${h}`} className="block max-w-full" role="img"
        aria-label={`Score by forecast week, ours against the market, lower is better: ${weeks.map(wk => `week of ${shortDate(wk.start)} us ${f(wk.ours)}, market ${f(wk.market)}${wk.claude != null ? `, Claude alone ${f(wk.claude)}` : ""}, ${wk.questions} questions`).join("; ")}. Changes: ${changes.map(c => `${c.label} on ${shortDate(c.date)}`).join(", ")}. ${headline}`}>
        {ticks.map(t => <g key={t}>
          <line x1={left} x2={width - right} y1={y(t)} y2={y(t)} className={t === 0 ? "stroke-ink/40" : "stroke-ink/10"} />
          <text x={left - 8} y={y(t) + 4} textAnchor="end" className={`fill-ink/60 tabular-nums ${small}`}>{t.toFixed(1)}</text>
        </g>)}
        <line x1={left} x2={width - right} y1={y(0.25)} y2={y(0.25)} className="stroke-ink/40" strokeWidth={1.5} strokeDasharray="5 4" />
        {shown.map(c => <g key={c.date}>
          <line x1={c.cx} x2={c.cx} y1={24 + c.row * 20} y2={base} className="stroke-ink/25" />
          <circle cx={c.cx} cy={14 + c.row * 20} r={8.5} className="fill-white stroke-ink/40" />
          <text x={c.cx} y={17.5 + c.row * 20} textAnchor="middle" className="fill-ink/80 text-[10px] font-medium">{c.n}</text>
        </g>)}
        {at && <line x1={centre(at)} x2={centre(at)} y1={top} y2={base} className="stroke-ink/50" />}
        {series.map(s => {
          const drawn = weeks.filter(wk => wk[s.key] != null);
          return <g key={s.key}>
            <polyline points={drawn.map(wk => `${centre(wk)},${y(wk[s.key]!)}`).join(" ")} fill="none" className={s.line} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {drawn.map(wk => wk.questions < THIN_WEEK
              ? <circle key={wk.start} cx={centre(wk)} cy={y(wk[s.key]!)} r={3.5} className={`fill-white ${s.ring}`} strokeWidth={2} />
              : <circle key={wk.start} cx={centre(wk)} cy={y(wk[s.key]!)} r={5} className={`${s.fill} stroke-white`} strokeWidth={2} />)}
          </g>;
        })}
        {weeks.map(wk => <g key={wk.start}>
          <text x={centre(wk)} y={h - 26} textAnchor="middle" className={`fill-ink/75 ${small}`}>{shortDate(wk.start)}</text>
          <text x={centre(wk)} y={h - 10} textAnchor="middle" className="fill-ink/50 text-[10px] tabular-nums">{wk.questions} q</text>
        </g>)}
      </svg>
      {at && <Tip x={centre(at)} y={top + 6} width={width}>
        <span className="block text-ink/60">Week of {shortDate(at.start)} · {at.questions} {at.questions === 1 ? "question" : "questions"}</span>
        {LEGEND.filter(s => at[s.key] != null).map(s => <span key={s.key} className="mt-0.5 flex items-center">
          {lineKey(s.bg)}<strong className="mr-1.5 tabular-nums">{f(at[s.key])}</strong><span className="text-ink/70">{s.name}</span></span>)}
      </Tip>}
    </div>
    <Key>
      {LEGEND.filter(s => series.includes(s)).map(s => <span key={s.key}>{lineKey(s.bg)}{s.name}</span>)}
      <span>{dashKey}Coin flip (0.25)</span>
    </Key>
    {shown.length > 0 && <ol className="mt-3 grid gap-x-6 gap-y-1 text-[13px] text-ink/70 sm:grid-cols-2">{shown.map(c => <li key={c.date} className="flex items-baseline gap-2">
      <span aria-hidden className="inline-flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border border-ink/40 text-[10px] font-medium text-ink/80">{c.n}</span>
      <span><span className="text-ink/50">{shortDate(c.date)}</span> {c.label}</span></li>)}</ol>}
    <p className="mt-3 text-sm text-ink/60">Score by the week we made the forecast, on questions the market also priced. Lower is better. Numbered lines mark when we changed how we forecast. Hollow dots are weeks with fewer than {THIN_WEEK} decided questions, so read them lightly; weeks fill in as questions are decided. From Nov 10 our call {policy.default_move > 0 ? `stays within ${points(policy.default_move)} of the market` : "is the market’s price unless a tested edge applies"}, so those two lines will sit {policy.default_move > 0 ? "closer together" : "very close together"}; Claude alone is the independent comparison.</p>
    <Numbers head={["Week of", "Questions", ...LEGEND.filter(s => series.includes(s)).map(s => s.name)]}
      rows={weeks.map((wk: ProgressWeek) => [shortDate(wk.start), wk.questions, ...LEGEND.filter(s => series.includes(s)).map(s => wk[s.key] != null ? f(wk[s.key]) : "")])} />
  </section>;
}

// Three scores on one scale: the same questions, so the bars compare fairly. A shorter bar is a better score.
function Meters({ rows }: { rows: { name: string; value: number; bg: string }[] }) {
  const limit = Math.max(0.3, Math.ceil(Math.max(...rows.map(r => r.value)) * 20) / 20);
  const flip = `${0.25 / limit * 100}%`;
  return <div>
    <ul className="space-y-2.5">{rows.map(r => <li key={r.name} className="grid grid-cols-[6.5rem_1fr_2.75rem] items-center gap-x-3">
      <span className="text-sm">{r.name}</span>
      <span aria-hidden className="relative block h-2 rounded-full bg-ink/10">
        <span className={`absolute inset-y-0 left-0 rounded-full ${r.bg}`} style={{ width: `${Math.max(1.5, r.value / limit * 100)}%` }} />
        <span className="absolute -inset-y-1 w-px bg-ink/45" style={{ left: flip }} />
      </span>
      <strong className="text-right tabular-nums">{r.value.toFixed(2)}</strong>
    </li>)}</ul>
    <p aria-hidden className="mt-1.5 grid grid-cols-[6.5rem_1fr_2.75rem] gap-x-3 text-[11px] text-ink/50">
      <span /><span className="relative block h-4"><span className="absolute left-0">0 = perfect</span>
        <span className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: flip }}>0.25 = coin flip</span></span><span /></p>
  </div>;
}

const bg = (key: Series["key"]) => SERIES.find(s => s.key === key)!.bg;

// Our call, the market and Claude alone on the questions the market also priced: over the whole record and,
// once there are results, since the call began to start from the market's price.
export function AgainstMarket({ vs }: { vs: VersusMarket }) {
  const since = vs.since_anchored && vs.since_anchored.questions > 0 && vs.since_anchored.ours != null
    && vs.since_anchored.claude != null && vs.since_anchored.market != null ? vs.since_anchored : null;
  const rows = (ours: number, market: number, claude: number | null | undefined) => [
    { name: "Our call", value: ours, bg: bg("ours") }, { name: "Market", value: market, bg: bg("market") },
    ...(claude != null ? [{ name: "Claude alone", value: claude, bg: bg("claude") }] : [])];
  return <div className="space-y-5">
    <div role="group" aria-label="Scores on the questions the market also priced">
      <p className="mb-3 text-sm text-ink/65">On the {vs.questions} questions the market also priced{vs.claude != null && vs.claude_questions !== vs.questions && ` (Claude alone forecast ${vs.claude_questions} of them)`}</p>
      <Meters rows={rows(vs.ours!, vs.market!, vs.claude)} />
    </div>
    {since && <div role="group" aria-label={`Scores since ${shortDate(since.date)}`}>
      <p className="mb-3 text-sm text-ink/65">Since {shortDate(since.date)}, on {since.questions} questions the market also priced</p>
      <Meters rows={rows(since.ours!, since.market!, since.claude)} />
    </div>}
  </div>;
}
