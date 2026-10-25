import { call } from "@/lib/verdict";

export type Source = string | { url: string; published?: string };
export type Forecast = { method: string; probability: number; rationale?: string; sources?: Source[]; made_at?: string; members?: number; weights?: Record<string, number> };
export type Question = {
  id: string; title: string; detail: string; topic: string; resolves_on: string;
  status: "pending" | "resolved" | "cancelled"; outcome: 0 | 1 | null; actual: string | null;
  source_url: string | null; report_url: string | null; note?: string | null; scored?: boolean;
  series?: string; series_label?: string; source: string;
  forecasts: Forecast[]; call: Forecast | null; verdict: "right" | "wrong" | "toss_up" | null;
};
export type Tally = { resolved: number; right: number; wrong: number; toss_ups: number; brier: number | null; coin_flip_brier: number };
export type Forecaster = Tally & { method: string; label: string; about: string; forecasts: number };
export type TrackRecord = { generated_on: string; summary: Tally & { pending: number; cancelled: number; unscored?: number; total: number };
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

// Results this many decided questions in, a score starts to reflect skill more than luck.
export const MEANINGFUL_RESULTS = 30;

const noon = (iso: string) => new Date(`${iso.slice(0, 10)}T12:00:00Z`);

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
