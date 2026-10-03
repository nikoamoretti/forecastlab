import { VERDICT_SCALE, verdict } from "@/lib/verdict";

// Leads with the verdict in words; the exact percentage stays in the sentence.
export default function VerdictHeadline({ probability, className = "font-serif text-5xl" }: { probability: number; className?: string }) {
  const v = verdict(probability);
  return <div>
    <p className={className}>{v.label}</p>
    <p className="mt-2 text-xl">{v.sentence}</p>
    <details className="mt-2 text-sm"><summary className="cursor-pointer text-ink/70">How to read this</summary>
      <p className="mt-2">The words follow the standard intelligence-community scale: {VERDICT_SCALE.map(s => `${s.label.toLowerCase()} (${s.range})`).join(", ")}. They restate the estimate in plain language and do not make it calibrated. Only resolved questions can show how often estimates like this come true.</p></details>
  </div>;
}
