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

// The daily Claude run starts at 12:37 UTC on weekdays and its answers are on the site about 40 minutes later,
// so the page promises 13:30 UTC. Keep this in step with the routine's start time (docs/CURSOR_RESEARCH.md).
function nextRun(now = new Date()) {
  const run = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 13, 30));
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

  return <div className="mx-auto max-w-2xl space-y-8 sm:space-y-10">
    <header>
      <p className="eyebrow">Your own question</p>
      <h2 className="mt-3 font-serif text-4xl sm:text-5xl">Ask a question</h2>
      <p className="mt-4 text-lg text-ink/75">Ask a yes-or-no question about the future. Claude researches it and gives you a call, like “No, 70% sure”, in the next daily run. It costs nothing.</p>
    </header>

    <form onSubmit={submit} className="card space-y-4 p-5 sm:p-6">
      <label className="block">
        <span className="font-medium">Your question</span>
        <textarea value={text} onChange={e => setText(e.target.value)} rows={3} maxLength={500} autoFocus
          placeholder="Will … happen by [date]?" className="mt-2 block w-full rounded-xl border border-rule bg-paper/40 p-4 text-lg placeholder:text-ink/35 focus:border-ink/60 focus:bg-white focus:outline-none" />
      </label>
      <p className="text-sm text-ink/60">Include a date and something that can be checked. If anything is unclear, Claude writes down exactly how the question will be decided.</p>
      <div><p className="text-[13px] font-medium text-ink/55">Or start from an example</p>
        <div className="mt-2 flex flex-wrap gap-2">{EXAMPLES.map(example => <button type="button" key={example} onClick={() => setText(example)}
          className="rounded-full border border-rule bg-white px-3 py-1.5 text-left text-sm text-ink/80 transition hover:border-ink/40">{example}</button>)}</div></div>
      <button disabled={busy || tooShort} className="btn-primary w-full px-7 py-3.5 text-lg sm:w-auto">
        {busy ? "Saving…" : "Ask Claude"}</button>
      {notice && <p role="status" className="rounded-xl border border-pine/30 bg-pine/10 p-4 text-pine">{notice}</p>}
      {error && <p role="alert" className="text-brick">{error}</p>}
    </form>

    {waiting.length > 0 && <section className="card overflow-hidden">
      <div className="px-5 pb-4 pt-5 sm:px-6">
        <h3 className="font-serif text-2xl">Waiting for Claude <span className="chip ml-1 align-middle font-sans">{waiting.length}</span></h3>
        <p className="mt-1.5 text-sm text-ink/60">Answers arrive by {nextRun()}.</p></div>
      <ul className="rows border-t border-rule/60">{waiting.map(r => <li key={r.id} className="flex items-start justify-between gap-4 px-5 py-4 sm:px-6">
        <span>{r.text}<span className="mt-1 block text-sm text-ink/50">Asked {shortDate(r.asked_at)}</span></span>
        <button onClick={() => withdraw(r.id)} className="btn-quiet shrink-0">Withdraw</button></li>)}</ul>
    </section>}

    {answered.length > 0 && <section className="card overflow-hidden">
      <h3 className="px-5 pb-4 pt-5 font-serif text-2xl sm:px-6">Answered</h3>
      <ul className="rows border-t border-rule/60">{answered.map(r => <li key={r.id}>
        <Link href={`/q/${r.id}`} className="row flex items-center justify-between gap-4"><span>{r.text}</span><span aria-hidden className="text-ink/30">›</span></Link></li>)}</ul>
    </section>}
  </div>;
}
