"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export type SuggestedMacro = { indicator: string; observation_period: string; threshold: number; comparison: string;
  release_at: string; revision_policy: string };
export type QuestionSuggestion = { id: string; question: string; macro: SuggestedMacro; reason: string;
  baseline: { period: string; source_url: string; available_at: string; revision_basis: string };
  schedule: { source_url: string; checked_at: string }; release_event: string; related_question_ids: string[];
  existing_question_id: string | null; existing_draft_run_id: string | null };
type Suggestions = { items: QuestionSuggestion[]; gaps: string[]; checked_at: string; policy: string };

function dateTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export default function QuestionSuggestions({ busy, onSelect, onCustomize }: {
  busy: boolean; onSelect: (suggestion: QuestionSuggestion) => void; onCustomize: (macro: SuggestedMacro) => void;
}) {
  const [data, setData] = useState<Suggestions | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    api<Suggestions>("/api/question-suggestions").then(result => {
      if (active) { setData(result); setError(""); }
    }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [revision]);
  return <div className="space-y-5" aria-label="Suggested questions">
    <div><h3 className="font-serif text-3xl">Let ForecastLab pick.</h3>
      <p className="mt-2 text-sm text-ink/70">Upcoming U.S. macro releases with a clear answer date. We pick the event, threshold, and resolver; you review once. Choosing questions is free.</p></div>
    {error && <p role="alert">Could not load suggested questions: {error}</p>}
    {!data && !error && <p role="status">Checking the BLS calendar and recent observations…</p>}
    {data && <>
      {!data.items.length && <p role="status" className="border border-rule p-5">No source-backed questions are available right now. Try again later or write your own question.</p>}
      <ol className="space-y-4">{data.items.map((item, index) => <li key={item.id} className={`border p-5 md:p-6 ${index === 0 ? "border-copper bg-white" : "border-rule"}`}>
        <p className="font-mono text-xs uppercase tracking-wider text-copper">{item.existing_question_id ? "Already on your board" : index === 0 ? "Recommended next" : "Another upcoming question"}</p>
        <h4 className="mt-3 font-serif text-2xl leading-snug">{item.question}</h4>
        <p className="mt-3 text-sm">{item.reason}</p>
        <p className="mt-3 text-sm text-ink/70">Scheduled for {dateTime(item.macro.release_at)} (your local time). Resolves on the first published value.</p>
        {item.related_question_ids.length > 0 && <p className="mt-2 text-sm text-copper">{item.related_question_ids.length} existing {item.related_question_ids.length === 1 ? "question shares" : "questions share"} this release event. Their outcomes may move together.</p>}
        <div className="mt-5 flex flex-wrap items-center gap-4">
          {item.existing_question_id ? <Link className="border border-ink bg-ink px-4 py-2 text-sm text-paper"
            href={item.existing_draft_run_id ? `/new?profile=root_event_ensemble_v1&draft=${item.existing_draft_run_id}` : `/forecasts/${item.existing_question_id}`}>
            {item.existing_draft_run_id ? "Continue reviewing" : "Open existing forecast"}</Link> :
            <button disabled={busy} onClick={() => onSelect(item)} className="border border-ink bg-ink px-4 py-2 text-sm text-paper disabled:opacity-50">
              {busy ? "Preparing…" : index === 0 ? "Review recommended question" : "Review this question"}</button>}
          <button disabled={busy} onClick={() => onCustomize(item.macro)} className="text-sm underline">Adjust inputs</button>
        </div>
        <details className="mt-4 text-xs text-ink/70"><summary className="cursor-pointer">Sources and selection details</summary>
          <div className="mt-2 space-y-2">
            <p><a className="underline" href={item.schedule.source_url} target="_blank" rel="noreferrer">Official BLS release calendar</a> · checked {dateTime(item.schedule.checked_at)}</p>
            <p><a className="underline" href={item.baseline.source_url} target="_blank" rel="noreferrer">BLS observations</a> · checked {dateTime(item.baseline.available_at)}. Historical readings can include revisions.</p>
            <p>Shared event: {item.release_event}</p>
          </div></details>
      </li>)}</ol>
      {data.gaps.length > 0 && <details className="text-sm" open={!data.items.length}><summary className="cursor-pointer">Why some questions are unavailable</summary>
        <ul className="mt-2 list-disc space-y-1 pl-5">{data.gaps.map((gap, i) => <li key={i}>{gap}</li>)}</ul></details>}
      <p className="text-xs text-ink/60">{data.policy} Sources refresh twice daily and after a scheduled release. These picks are not predictions.</p>
    </>}
    <button disabled={busy} onClick={() => { setData(null); setError(""); setRevision(value => value + 1); }} className="text-sm underline">Check suggestions again</button>
  </div>;
}
