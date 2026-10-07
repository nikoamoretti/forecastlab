import { VERDICT_SCALE, call, verdict } from "@/lib/verdict";

// Leads with the call (yes or no) and how sure we are; the exact probability follows.
export default function VerdictHeadline({ probability, className = "font-serif text-5xl" }: { probability: number; className?: string }) {
  const c = call(probability);
  return <div>
    <p className="font-mono text-xs uppercase tracking-widest text-copper">Our call</p>
    <p className={className}>{c.answer === "Toss-up" ? "Toss-up" : `${c.answer}, ${Math.round(c.sure * 100)}% sure`}</p>
    <p className="mt-2 text-xl">{c.sentence}</p>
    <details className="mt-2 text-sm"><summary className="cursor-pointer text-ink/70">How to read this</summary>
      <p className="mt-2">The number is the chance we give to “yes”. Below 50% we call no, above 50% we call yes, and “sure” is the chance we give to our side. In intelligence-community wording this is “{verdict(probability).label.toLowerCase()}” ({VERDICT_SCALE.map(s => `${s.label.toLowerCase()} ${s.range}`).join(", ")}). Only resolved questions show how often calls like this come true.</p></details>
  </div>;
}
