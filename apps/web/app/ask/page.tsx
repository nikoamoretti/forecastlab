"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/trackRecord";

type Request = { id: string; text: string; asked_at: string; status: "waiting" | "answered"; answered_at?: string };

const EXAMPLES = [
  "Will NVIDIA announce an RTX 50 Super graphics card before March 31, 2027?",
  "Will the Fed cut interest rates at its December 2026 meeting?",
  "Will Bitcoin trade above $150,000 on December 31, 2026?",
];

// The daily Claude run starts at 10:52 UTC on weekdays.
function nextRun(now = new Date()) {
  const run = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 10, 52));
  while (run <= now || run.getUTCDay() === 0 || run.getUTCDay() === 6) run.setUTCDate(run.getUTCDate() + 1);
  return run.toLocaleString("en-US", { weekday: "long", hour: "numeric", minute: "2-digit" });
}

export default function AskPage() {
  const [text, setText] = useState("");
  const [requests, setRequests] = useState<Request[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const load = () => api<Request[]>("/api/question-requests").then(setRequests).catch(e => setError(e.message));
  useEffect(() => { load(); }, []);
  const waiting = (requests || []).filter(r => r.status === "waiting");
  const answered = (requests || []).filter(r => r.status === "answered");
  const tooShort = text.trim().length < 10;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setError(""); setNotice("");
    try {
      await api<Request>("/api/question-requests", { method: "POST", body: JSON.stringify({ text: text.trim() }) });
      setText(""); setNotice(`Got it. Claude will research it and answer by ${nextRun()}.`); await load();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save the question"); }
    finally { setBusy(false); }
  }

  async function withdraw(id: string) {
    try { await api(`/api/question-requests/${id}`, { method: "DELETE" }); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not withdraw the question"); }
  }

  return <div className="mx-auto max-w-2xl space-y-10">
    <header>
      <h2 className="font-serif text-5xl">Ask a question</h2>
      <p className="mt-4 text-lg text-ink/80">Ask a yes-or-no question about the future. Claude researches it and gives you a call, like “No, 70% sure”, in the next daily run. It costs nothing.</p>
    </header>

    <form onSubmit={submit} className="space-y-4">
      <label className="block">
        <span className="font-medium">Your question</span>
        <textarea value={text} onChange={e => setText(e.target.value)} rows={3} maxLength={500} autoFocus
          placeholder="Will … happen by [date]?" className="mt-2 block w-full border border-rule bg-white/80 p-4 text-lg focus:border-ink focus:outline-none" />
      </label>
      <p className="text-sm text-ink/60">Include a date and something that can be checked. If anything is unclear, Claude writes down exactly how the question will be decided.</p>
      <div className="flex flex-wrap gap-2">{EXAMPLES.map(example => <button type="button" key={example} onClick={() => setText(example)}
        className="rounded-full border border-rule bg-white/50 px-3 py-1.5 text-left text-sm text-ink/80 hover:border-ink/40 hover:bg-white">{example}</button>)}</div>
      <button disabled={busy || tooShort} className="w-full bg-ink px-6 py-4 text-lg text-paper hover:bg-ink/85 disabled:opacity-40 sm:w-auto">
        {busy ? "Saving…" : "Ask Claude"}</button>
      {notice && <p role="status" className="border border-pine/30 bg-pine/10 p-4 text-pine">{notice}</p>}
      {error && <p role="alert" className="text-brick">{error}</p>}
    </form>

    {waiting.length > 0 && <section>
      <h3 className="font-serif text-2xl">Waiting for Claude <span className="text-ink/40">{waiting.length}</span></h3>
      <p className="mt-1 text-sm text-ink/60">Answers arrive by {nextRun()}.</p>
      <ul className="mt-3 divide-y divide-rule border-y border-rule">{waiting.map(r => <li key={r.id} className="flex items-start justify-between gap-4 py-3">
        <span>{r.text}<span className="mt-1 block text-sm text-ink/50">Asked {shortDate(r.asked_at)}</span></span>
        <button onClick={() => withdraw(r.id)} className="shrink-0 rounded border border-rule px-3 py-1.5 text-sm text-ink/70 hover:border-ink/40 hover:text-ink">Withdraw</button></li>)}</ul>
    </section>}

    {answered.length > 0 && <section>
      <h3 className="font-serif text-2xl">Answered</h3>
      <ul className="mt-3 divide-y divide-rule border-y border-rule">{answered.map(r => <li key={r.id}>
        <Link href={`/q/${r.id}`} className="flex items-center justify-between gap-4 py-3 hover:bg-white/50"><span>{r.text}</span><span aria-hidden className="text-ink/30">›</span></Link></li>)}</ul>
    </section>}

    <p className="border-t border-rule pt-6 text-sm text-ink/60">Need an answer right now? <Link className="underline" href="/new">Run the full research pipeline</Link>. It uses the paid OpenAI API (about $0.50 per question) and is meant for U.S. economic data.</p>
  </div>;
}
