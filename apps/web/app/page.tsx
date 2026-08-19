"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, pct } from "@/lib/api";

type Dashboard = {
  sample_question: string;
  mode: string;
  synthetic_benchmarks: boolean;
  benchmark_count: number;
  benchmark_question_count?: number;
  questions: Array<{
    id: string;
    original_text: string;
    status: string;
    stale: boolean;
    latest_probability: number | null;
    previous_probability: number | null;
    version_count: number;
  }>;
  watch_events: Array<{ id: string; created_at: string; material: boolean }>;
};

export default function BoardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Dashboard>("/api/dashboard")
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, []);

  if (error) return <p>API unavailable. Start ForecastLab first. {error}</p>;
  if (!data) return <p>Loading board…</p>;

  const stale = data.questions.filter((item) => item.stale);
  const active = data.questions.filter((item) => item.status !== "draft");

  return (
    <div className="space-y-10">
      <section className="grid gap-8 md:grid-cols-[1.4fr_0.8fr]">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.25em] text-copper">Working board</p>
          <h2 className="mt-2 max-w-3xl font-serif text-5xl leading-tight">A desk for binary questions, not a dashboard of widgets.</h2>
          <p className="mt-4 max-w-2xl text-lg text-ink/80">
            Independent tracks gather evidence. Ordinary code aggregates the probabilities. Mock mode needs no keys.
          </p>
        </div>
        <aside className="border border-rule bg-white/50 p-5">
          <p className="font-mono text-xs uppercase tracking-[0.2em]">Mode</p>
          <p className="mt-2 font-serif text-3xl">{data.mode}</p>
          <p className="mt-3 text-sm text-ink/70">
            {data.synthetic_benchmarks ? "Benchmark fixtures are synthetic." : ""}{" "}
            {data.benchmark_question_count ?? 0} imported questions, {data.benchmark_count} scored results.
            Calibration is not claimed.
          </p>
          <Link className="mt-6 inline-block border border-ink bg-ink px-4 py-2 text-sm text-paper" href="/new">
            New forecast
          </Link>
        </aside>
      </section>
      {stale.length ? (
        <section>
          <h3 className="font-serif text-2xl">Stale after a watch change</h3>
          <ul className="mt-3 space-y-2">
            {stale.map((item) => (
              <li key={item.id}>
                <Link className="underline decoration-copper" href={`/forecasts/${item.id}`}>
                  {item.original_text}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <section>
        <h3 className="font-serif text-2xl">Questions</h3>
        <p className="mt-1 text-sm text-ink/60">{active.length} with runs · sample: {data.sample_question}</p>
        <ul className="mt-4 divide-y divide-rule border-y border-rule">
          {data.questions.map((item) => (
            <li key={item.id} className="grid grid-cols-[1fr_auto] gap-4 py-4">
              <div>
                <Link href={`/forecasts/${item.id}`} className="font-medium">
                  {item.original_text}
                </Link>
                <p className="mt-1 text-sm text-ink/60">
                  {item.status}
                  {item.stale ? " · stale" : ""} · {item.version_count} version{item.version_count === 1 ? "" : "s"}
                  {item.previous_probability != null
                    ? ` · was ${pct(item.previous_probability)}`
                    : ""}
                </p>
              </div>
              <p className="font-serif text-3xl">{pct(item.latest_probability)}</p>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h3 className="font-serif text-2xl">Recent watcher events</h3>
        <ul className="mt-3 space-y-2 text-sm">
          {data.watch_events.length === 0 ? <li>No watcher events yet.</li> : null}
          {data.watch_events.map((event) => (
            <li key={event.id}>
              {event.created_at} {event.material ? "· material change" : "· unchanged"}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
