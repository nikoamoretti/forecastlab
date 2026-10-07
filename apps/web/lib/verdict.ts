import { pct } from "@/lib/api";

// Words of estimative probability on the U.S. intelligence community scale
// (ICD 203). Bands are symmetric around 50%, so p and 1 - p mirror each other.
// The words translate the estimate; they do not make it calibrated.
export const VERDICT_SCALE = [
  { label: "Almost no chance", range: "under 5%" },
  { label: "Very unlikely", range: "5–20%" },
  { label: "Unlikely", range: "20–45%" },
  { label: "Roughly even chance", range: "45–55%" },
  { label: "Likely", range: "55–80%" },
  { label: "Very likely", range: "80–95%" },
  { label: "Almost certain", range: "over 95%" },
] as const;

export type Verdict = { label: string; lean: "yes" | "no" | "even"; odds: string; sentence: string };

function band(p: number) {
  if (p < 0.05) return 0;
  if (p < 0.2) return 1;
  if (p < 0.45) return 2;
  if (p <= 0.55) return 3;
  if (p <= 0.8) return 4;
  if (p <= 0.95) return 5;
  return 6;
}

// Natural frequencies: "about 4 in 10", with "1 in N" near the extremes.
export function odds(p: number) {
  if (p < 0.095) return `about 1 in ${Math.round(1 / Math.max(p, 0.001))}`;
  if (p > 0.905) {
    const n = Math.round(1 / Math.max(1 - p, 0.001));
    return `about ${n - 1} in ${n}`;
  }
  return `about ${Math.round(p * 10)} in 10`;
}

export function verdict(p: number): Verdict {
  const index = band(p);
  const lean = index < 3 ? "no" : index > 3 ? "yes" : "even";
  const lead = lean === "no" ? "Most likely no" : lean === "yes" ? "Most likely yes" : "Too close to call";
  const chance = odds(p);
  return { label: VERDICT_SCALE[index].label, lean, odds: chance, sentence: `${lead}: ${chance} chance it happens (${pct(p)}).` };
}

export type Call = { answer: "Yes" | "No" | "Toss-up"; sure: number; sentence: string };

// The forecast as a decision: which way we lean and how sure we are of that side.
// 37% becomes "No, 63% sure". Exactly 50% makes no call.
export function call(p: number): Call {
  if (p === 0.5) return { answer: "Toss-up", sure: 0.5, sentence: "Toss-up: we make no call (50%)." };
  const answer = p > 0.5 ? "Yes" : "No";
  const sure = Math.max(p, 1 - p);
  const event = answer === "Yes" ? "it happens" : "it doesn't happen";
  return { answer, sure, sentence: `We think ${event}, ${Math.round(sure * 100)}% sure (${pct(p)} chance of yes).` };
}

// Compact form for lists, e.g. "No · 63% sure".
export function callShort(p?: number | null) {
  if (p == null) return pct(p);
  const c = call(p);
  return c.answer === "Toss-up" ? "Toss-up · 50%" : `${c.answer} · ${Math.round(c.sure * 100)}% sure`;
}
