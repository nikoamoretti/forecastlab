import { call } from "@/lib/verdict";

export type Source = string | { url: string; published?: string };
export type Forecast = { method: string; probability: number; rationale?: string; sources?: Source[]; made_at?: string; members?: number };
export type Question = {
  id: string; title: string; detail: string; topic: string; resolves_on: string;
  status: "pending" | "resolved" | "cancelled"; outcome: 0 | 1 | null; actual: string | null;
  source_url: string | null; report_url: string | null;
  forecasts: Forecast[]; call: Forecast | null; verdict: "right" | "wrong" | "toss_up" | null;
};
export type Tally = { resolved: number; right: number; wrong: number; toss_ups: number; brier: number | null; coin_flip_brier: number };
export type Forecaster = Tally & { method: string; label: string; about: string; forecasts: number };
export type TrackRecord = { generated_on: string; summary: Tally & { pending: number; cancelled: number; total: number };
  forecasters: Forecaster[]; questions: Question[] };

// Results this many decided questions in, a score starts to reflect skill more than luck.
export const MEANINGFUL_RESULTS = 30;

const noon = (iso: string) => new Date(`${iso.slice(0, 10)}T12:00:00Z`);

export function shortDate(iso: string, withYear = false) {
  return noon(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: withYear ? "numeric" : undefined, timeZone: "UTC" });
}

// "Today", "Tomorrow", "Yesterday", or "Oct 14" relative to the day the record was built.
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
