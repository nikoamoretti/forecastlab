"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { call } from "@/lib/verdict";

type Forecast = { method: string; probability: number };
type Question = {
  id: string; title: string; detail: string; topic: string; resolves_on: string;
  status: "pending" | "resolved" | "cancelled"; outcome: 0 | 1 | null; actual: string | null;
  forecasts: Forecast[]; call: Forecast | null; verdict: "right" | "wrong" | "toss_up" | null;
};
type Tally = { resolved: number; right: number; wrong: number; toss_ups: number; brier: number | null; coin_flip_brier: number };
type Forecaster = Tally & { method: string; label: string; about: string; forecasts: number };
type TrackRecord = { generated_on: string; summary: Tally & { pending: number; cancelled: number; total: number };
  forecasters: Forecaster[]; questions: Question[] };

const DAY = 86_400_000;

function date(iso: string, withYear = false) {
  return new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-US",
    { month: "short", day: "numeric", year: withYear ? "numeric" : undefined, timeZone: "UTC" });
}

function callText(p: number) {
  const c = call(p);
  return c.answer === "Toss-up" ? "Toss-up" : `${c.answer}, ${Math.round(c.sure * 100)}% sure`;
}

// Brier score in words: 0.25 is what always answering 50% earns.
function versusGuessing(brier: number | null, coinFlip: number) {
  if (brier == null) return "No results yet";
  const skill = 1 - brier / coinFlip;
  return skill > 0.1 ? "Better than guessing" : skill < -0.1 ? "Worse than guessing" : "Same as guessing";
}

function Mark({ verdict }: { verdict: Question["verdict"] }) {
  if (verdict === "right") return <span className="inline-flex items-center gap-1 rounded-full bg-pine/10 px-3 py-1 text-sm font-medium text-pine">✓ Right</span>;
  if (verdict === "wrong") return <span className="inline-flex items-center gap-1 rounded-full bg-brick/10 px-3 py-1 text-sm font-medium text-brick">✗ Wrong</span>;
  return <span className="inline-flex rounded-full bg-ink/5 px-3 py-1 text-sm text-ink/70">Toss-up</span>;
}

function Others({ question, labels }: { question: Question; labels: Record<string, string> }) {
  return <details className="mt-3 text-sm">
    <summary className="cursor-pointer text-ink/60 hover:text-ink">What each forecaster said ({question.forecasts.length})</summary>
    <ul className="mt-2 space-y-1">{question.forecasts.map(f => <li key={f.method} className="flex justify-between gap-4 border-b border-rule/60 py-1">
      <span>{labels[f.method] || f.method}{f.method === question.call?.method ? " · our call" : ""}</span>
      <span className="tabular-nums">{callText(f.probability)}</span></li>)}</ul>
    <p className="mt-2 text-ink/60">Exact rule: {question.detail}</p>
  </details>;
}

function Stat({ value, label, tone = "" }: { value: string | number; label: string; tone?: string }) {
  return <div className="border border-rule bg-white/40 p-5">
    <p className={`font-serif ${typeof value === "number" ? "text-4xl" : "text-2xl"} ${tone}`}>{value}</p>
    <p className="mt-1 text-sm text-ink/70">{label}</p>
  </div>;
}

export default function TrackRecordPage() {
  const [data, setData] = useState<TrackRecord | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api<TrackRecord>("/api/track-record").then(setData).catch(e => setError(e.message)); }, []);
  if (error) return <p role="alert">The track record could not be loaded: {error}</p>;
  if (!data) return <p>Loading your track record…</p>;

  const s = data.summary;
  const labels = Object.fromEntries(data.forecasters.map(f => [f.method, f.label]));
  const resolved = data.questions.filter(q => q.status === "resolved");
  const open = data.questions.filter(q => q.status === "pending");
  const cancelled = data.questions.filter(q => q.status === "cancelled");
  const soon = new Date(`${data.generated_on}T12:00:00Z`).getTime() + 7 * DAY;
  const thisWeek = open.filter(q => new Date(`${q.resolves_on}T12:00:00Z`).getTime() <= soon);
  const later = open.filter(q => !thisWeek.includes(q));
  const calls = s.right + s.wrong;

  return <div className="space-y-14">
    <header>
      <p className="font-mono text-xs uppercase tracking-widest text-copper">Track record</p>
      <h2 className="mt-3 font-serif text-5xl">{calls ? `${s.right} of ${calls} calls right` : "No results yet"}</h2>
      <p className="mt-4 max-w-2xl text-lg text-ink/80">{s.resolved} {s.resolved === 1 ? "question has" : "questions have"} been decided by official data. {s.pending} more {s.pending === 1 ? "is" : "are"} waiting for results.</p>
      <div className="mt-8 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat value={s.right} label="Calls right" tone="text-pine" />
        <Stat value={s.wrong} label="Calls wrong" tone="text-brick" />
        <Stat value={s.pending} label="Waiting for results" />
        <Stat value={versusGuessing(s.brier, s.coin_flip_brier)} label={s.brier == null ? "Accuracy appears after the first result" : `Accuracy score ${s.brier.toFixed(2)} vs 0.25 for always saying 50%`} />
      </div>
      {s.resolved > 0 && s.resolved < 30 && <p className="mt-4 text-sm text-ink/60">With only {s.resolved} {s.resolved === 1 ? "result" : "results"}, luck dominates. About 30 results are needed before these numbers say much about skill.</p>}
    </header>

    <section>
      <h3 className="font-serif text-3xl">Results</h3>
      {!resolved.length && <p className="mt-3 text-ink/70">Nothing has resolved yet.</p>}
      <ul className="mt-4 divide-y divide-rule border-y border-rule">{resolved.map(q => {
        const p = q.call!.probability;
        return <li key={q.id} className="py-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-ink/60">{q.topic} · decided {date(q.resolves_on, true)}</p><Mark verdict={q.verdict} /></div>
          <p className="mt-2 text-lg">{q.title}</p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <p><span className="text-ink/60">We said: </span><strong>{callText(p)}</strong></p>
            <p><span className="text-ink/60">What happened: </span><strong>{q.outcome ? "Yes" : "No"}</strong>{q.actual ? ` (actual: ${q.actual})` : ""}</p>
          </div>
          <Others question={q} labels={labels} />
        </li>; })}</ul>
    </section>

    {[["Results due this week", thisWeek], ["Later", later]].map(([title, list]) => (list as Question[]).length > 0 && <section key={title as string}>
      <h3 className="font-serif text-3xl">{title as string}</h3>
      <ul className="mt-4 divide-y divide-rule border-y border-rule">{(list as Question[]).map(q => <li key={q.id} className="py-5">
        <p className="text-sm text-ink/60">{q.topic} · result due {date(q.resolves_on, q.resolves_on.slice(0, 4) !== data.generated_on.slice(0, 4))}</p>
        <div className="mt-2 grid gap-2 md:grid-cols-[1fr_auto] md:items-baseline md:gap-8">
          <p className="text-lg">{q.title}</p>
          {q.call && <p className="whitespace-nowrap md:text-right"><span className="text-ink/60">Our call: </span><strong>{callText(q.call.probability)}</strong></p>}
        </div>
        <Others question={q} labels={labels} />
      </li>)}</ul>
    </section>)}

    {cancelled.length > 0 && <section>
      <h3 className="font-serif text-2xl">Cancelled</h3>
      <p className="mt-2 text-sm text-ink/70">No official value was published (market holiday), so these never count.</p>
      <ul className="mt-3 space-y-1 text-ink/70">{cancelled.map(q => <li key={q.id}>{q.title}</li>)}</ul>
    </section>}

    <section>
      <h3 className="font-serif text-3xl">Who forecasts best</h3>
      <p className="mt-2 max-w-3xl text-ink/70">Every question goes to several forecasters. “Our call” is Claude’s answer when Claude forecast the question, otherwise the next forecaster in this list. The accuracy score runs from 0 (perfect) upward; always answering 50% scores 0.25, so lower than 0.25 beats guessing.</p>
      <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[40rem] text-left">
        <thead className="text-sm text-ink/60"><tr className="border-b border-rule">
          <th className="py-2 pr-4 font-normal">Forecaster</th><th className="py-2 pr-4 font-normal">Questions</th>
          <th className="py-2 pr-4 font-normal">Decided</th><th className="py-2 pr-4 font-normal">Right</th><th className="py-2 font-normal">Accuracy score</th></tr></thead>
        <tbody>{data.forecasters.map(f => <tr key={f.method} className="border-b border-rule/60 align-top">
          <td className="py-3 pr-4"><p className="font-medium">{f.label}</p><p className="text-sm text-ink/60">{f.about}</p></td>
          <td className="py-3 pr-4 tabular-nums">{f.forecasts}</td>
          <td className="py-3 pr-4 tabular-nums">{f.resolved}</td>
          <td className="py-3 pr-4 tabular-nums">{f.resolved ? `${f.right} of ${f.right + f.wrong}` : "—"}</td>
          <td className="py-3 tabular-nums">{f.brier == null ? "—" : `${f.brier.toFixed(2)} · ${versusGuessing(f.brier, f.coin_flip_brier).toLowerCase()}`}</td>
        </tr>)}</tbody></table></div>
    </section>

    <p className="text-sm text-ink/60">Updated {date(data.generated_on, true)} from official first releases. Pipeline tests and drafts are not counted; they are under <Link className="underline" href="/runs">All runs</Link>.</p>
  </div>;
}
