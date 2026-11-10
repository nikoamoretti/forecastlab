import { call } from "@/lib/verdict";

export type Source = string | { url: string; published?: string };
export type Forecast = { method: string; probability: number; rationale?: string; sources?: Source[]; made_at?: string; members?: number; weights?: Record<string, number>;
  // A market-anchored call keeps both numbers it was made from; "held" is set when weak research kept it at the price.
  // max_move is how far the call was allowed to move (absent on calls frozen before it was recorded: 0.10); rule is the
  // id of the tested edge that set it, absent when only the default applied.
  claude?: number; market?: number; held?: string; max_move?: number; rule?: string;
  // Claude's confidence-corrected forecast carries the raw probability it corrects and the fit it came from.
  raw?: number; slope?: number; intercept?: number; results?: number; as_of?: string };
export type Question = {
  id: string; title: string; detail: string; topic: string; resolves_on: string;
  status: "pending" | "resolved" | "cancelled"; outcome: 0 | 1 | null; actual: string | null;
  source_url: string | null; report_url: string | null; note?: string | null; scored?: boolean;
  series?: string; series_label?: string; source: string;
  forecasts: Forecast[]; call: Forecast | null; verdict: "right" | "wrong" | "toss_up" | null;
};
export type Tally = { resolved: number; right: number; wrong: number; toss_ups: number; brier: number | null; coin_flip_brier: number };
export type Forecaster = Tally & { method: string; label: string; about: string; forecasts: number };
// The headline score is summary.brier over summary.resolved questions; vs_market compares our call with the market
// on the questions the market also priced.
// claude is Claude alone on the same priced questions (claude_questions of them have a Claude forecast); since_anchored
// is the three scores on the questions first forecast from the date our call began to start from the market price.
export type AnchoredScores = { questions: number; ours: number | null; claude: number | null; market: number | null };
export type VersusMarket = { questions: number; ours: number | null; market: number | null; claude?: number | null; claude_questions?: number;
  since_anchored?: AnchoredScores & { date: string } };
// Decided questions the market priced, by the week (Monday, UTC) we first forecast them; lower Brier is better.
export type ProgressWeek = { start: string; questions: number; ours: number | null; market: number | null; claude?: number | null; right: number; wrong: number };
export type ProgressSplit = { questions: number; ours: number | null; market: number | null; claude?: number | null };
export type Progress = { weeks: ProgressWeek[]; changes: { date: string; label: string }[];
  since_last_change: { date: string; before: ProgressSplit; after: ProgressSplit } };
// A tested edge: a rule for moving the call further toward Claude on some questions. It joins the call only after
// it beats the market on enough new results; until then its score is tracked on the side ("counted").
export type EdgeBlock = { questions: number; dates: number; rule: number | null; market: number | null; difference: number | null;
  interval: [number, number] | null; meets_bar?: boolean };
export type EdgeTest = { id: string; label: string; status: "testing" | "promoted"; registered: string; counted: EdgeBlock; before: EdgeBlock };
export type CallPolicy = { default_move: number; promoted: string[]; bar: { questions: number; dates: number; interval: number } };
export type TrackRecord = { generated_on: string; summary: Tally & { pending: number; cancelled: number; unscored?: number; total: number; vs_market?: VersusMarket; progress?: Progress;
  call_policy?: CallPolicy; edge_tests?: EdgeTest[] };
  calibration?: { slope: number; intercept: number; results: number; as_of: string } | null;
  forecasters: Forecaster[]; questions: Question[] };

// Questions on one indicator (other thresholds or days) shown together, in first-seen order.
export type QuestionGroup = { key: string; label: string; items: Question[] };

export function groupBySeries(questions: Question[]): QuestionGroup[] {
  const groups = new Map<string, QuestionGroup>();
  for (const q of questions) {
    const key = q.series ?? q.id;
    const group = groups.get(key) ?? { key, label: q.series_label ?? q.title, items: [] };
    group.items.push(q);
    groups.set(key, group);
  }
  return [...groups.values()];
}

// Milestones for the progress box: each says what that many decided questions buys us.
const MILESTONES = [
  { at: 30, why: "At about 30 decided questions the score starts to show skill rather than luck." },
  { at: 50, why: "Past 30, so the score now means something. At 50 we can check whether our 70% calls come true about 70% of the time." },
  { at: 100, why: "Past 50. At 100 the score is solid enough to tell which forecasters are really better." },
];

// The next milestone ahead of `resolved`, or null once every milestone is passed.
export function nextMilestone(resolved: number) {
  return MILESTONES.find(m => resolved < m.at) ?? null;
}

const noon = (iso: string) => new Date(`${iso.slice(0, 10)}T12:00:00Z`);

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

// The latest deadline a market's rules name, if it is more than a month after the listed date:
// such a market can stay unsettled long after it closes (an election runoff, a new government).
export function lateDeadline(rules: string, listed: string): string | null {
  const dates = [...rules.matchAll(new RegExp(`\\b(${MONTHS.join("|")}) (\\d{1,2}),? (\\d{4})\\b`, "g"))]
    .map(([, month, day, year]) => new Date(Date.UTC(+year, MONTHS.indexOf(month), +day, 12)).toISOString().slice(0, 10));
  const latest = dates.sort().at(-1);
  return latest && (noon(latest).getTime() - noon(listed).getTime()) / 86_400_000 > 30 ? latest : null;
}

export function shortDate(iso: string, withYear = false) {
  return noon(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: withYear ? "numeric" : undefined, timeZone: "UTC" });
}

// Today's date in UTC, from the clock: the record itself may be days old (weekends, skipped runs).
export const todayUTC = () => new Date().toISOString().slice(0, 10);

// "Today", "Tomorrow", "Yesterday", or "Oct 14" relative to today.
export function relativeDay(iso: string, today: string) {
  const days = Math.round((noon(iso).getTime() - noon(today).getTime()) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days === -1) return "Yesterday";
  return shortDate(iso, iso.slice(0, 4) !== today.slice(0, 4));
}

export function callText(p: number) {
  const c = call(p);
  return c.answer === "Toss-up" ? "Toss-up" : `${c.answer}, ${Math.round(c.sure * 100)}% sure`;
}

// The accuracy (Brier) score in words; always answering 50% scores 0.25.
export function versusGuessing(brier: number | null, coinFlip: number) {
  if (brier == null) return null;
  const skill = 1 - brier / coinFlip;
  return skill > 0.1 ? "Better than guessing" : skill < -0.1 ? "Worse than guessing" : "About the same as guessing";
}

// How far our call may move from the market's price. Records built before the policy was published moved it at most 10 points.
export const DEFAULT_POLICY: CallPolicy = { default_move: 0.1, promoted: [], bar: { questions: 40, dates: 10, interval: 0.9 } };
export const points = (move: number) => `${Math.round(move * 100)} point${Math.round(move * 100) === 1 ? "" : "s"}`;

// One question's call, in words: what the market said and what, if anything, moved the call off it.
export function anchoredCallText(call: Forecast, marketPct: string, edges: EdgeTest[] | undefined) {
  if (call.held) return `held at the market’s ${marketPct} because the research behind Claude’s forecast was thin`;
  const move = call.max_move ?? DEFAULT_POLICY.default_move;
  if (call.rule) return `the market’s ${marketPct}, moved toward Claude’s forecast under the tested edge “${edges?.find(e => e.id === call.rule)?.label ?? call.rule}”`;
  if (move === 0) return `the market’s ${marketPct}; no tested edge applies to this question`;
  return `the market’s ${marketPct}, moved toward Claude’s forecast by at most ${points(move)}`;
}

export const percent = (p: number) => `${Math.round(p * 100)}%`;

export const sourceUrl = (source: Source) => (typeof source === "string" ? source : source.url);

export function sourceLabel(source: Source) {
  const url = sourceUrl(source);
  let host = url;
  try { host = new URL(url).hostname.replace(/^www\./, ""); } catch { /* keep the raw text */ }
  return typeof source === "string" || !source.published ? host : `${host}, ${shortDate(source.published, true)}`;
}

// One week of decided questions (weeks start on Monday, by result date) and the score so far at its end.
export type Week = { start: string; right: number; wrong: number; brier: number; runningBrier: number };

// Scored results by the week they were decided, oldest first; toss-ups count in the score but not as right or wrong.
export function byWeek(questions: Question[]): Week[] {
  const rows = questions.filter(q => q.status === "resolved" && q.scored !== false && q.call && q.outcome != null)
    .map(q => {
      const day = noon(q.resolves_on);
      day.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7));
      return { start: day.toISOString().slice(0, 10), verdict: q.verdict, brier: (q.call!.probability - q.outcome!) ** 2 };
    }).sort((a, b) => a.start.localeCompare(b.start));
  const weeks: Week[] = [];
  let total = 0, count = 0;
  for (const start of [...new Set(rows.map(r => r.start))]) {
    const these = rows.filter(r => r.start === start);
    const sum = these.reduce((s, r) => s + r.brier, 0);
    total += sum; count += these.length;
    weeks.push({ start, right: these.filter(r => r.verdict === "right").length, wrong: these.filter(r => r.verdict === "wrong").length,
      brier: sum / these.length, runningBrier: total / count });
  }
  return weeks;
}

// Straight-line fit through the weeks in order, each week weighted by how many calls it decided.
export function trend(weeks: Week[], value: (wk: Week) => number): { start: number; end: number } | null {
  if (weeks.length < 2) return null;
  const pts = weeks.map((wk, x) => ({ x, y: value(wk), n: wk.right + wk.wrong }));
  const n = pts.reduce((s, p) => s + p.n, 0);
  const mx = pts.reduce((s, p) => s + p.x * p.n, 0) / n, my = pts.reduce((s, p) => s + p.y * p.n, 0) / n;
  const sxx = pts.reduce((s, p) => s + p.n * (p.x - mx) ** 2, 0);
  const slope = sxx ? pts.reduce((s, p) => s + p.n * (p.x - mx) * (p.y - my), 0) / sxx : 0;
  return { start: my - slope * mx, end: my + slope * (pts.length - 1 - mx) };
}
