import type { Question } from "@/lib/trackRecord";
import { callText } from "@/lib/trackRecord";

// Right/wrong use green/red; calls use a neutral pill so a "No" never reads as a failure.
export function ResultMark({ verdict, large = false }: { verdict: Question["verdict"]; large?: boolean }) {
  const size = large ? "px-4 py-1.5 text-base" : "px-2.5 py-0.5 text-sm";
  if (verdict === "right") return <span className={`inline-block whitespace-nowrap rounded-full bg-pine/10 font-medium text-pine ${size}`}>✓ Right</span>;
  if (verdict === "wrong") return <span className={`inline-block whitespace-nowrap rounded-full bg-brick/10 font-medium text-brick ${size}`}>✗ Wrong</span>;
  return <span className={`inline-block whitespace-nowrap rounded-full bg-ink/5 text-ink/70 ${size}`}>Toss-up</span>;
}

export function CallPill({ probability }: { probability: number }) {
  return <span className="inline-block whitespace-nowrap rounded-full border border-ink/20 bg-white/60 px-2.5 py-0.5 text-sm font-medium tabular-nums">{callText(probability)}</span>;
}
