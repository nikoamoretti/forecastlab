"use client";

import { useEffect, useState } from "react";
import { api, pct } from "@/lib/api";

export default function LabPage() {
  const [data, setData] = useState<any>(null);
  const [message, setMessage] = useState("");

  async function load() {
    const summary = await api("/api/benchmarks/summary");
    setData(summary);
  }

  useEffect(() => {
    load().catch(() => setData({ error: "unavailable" }));
  }, []);

  async function run() {
    setMessage("Running synthetic comparison…");
    await api("/api/benchmarks/run", { method: "POST" });
    await load();
    setMessage("Finished. Scores are for synthetic fixtures only.");
  }

  async function onImport(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    setMessage("Importing…");
    await api("/api/benchmarks/import", { method: "POST", body });
    await load();
    setMessage("Import finished. Duplicates are skipped by immutable hash.");
  }

  if (!data) return <p>Loading lab…</p>;
  const profiles = data.profiles || [];
  const reliability = data.reliability || {};
  const rows = data.rows || [];
  const categories = data.by_category || [];

  return (
    <div className="space-y-6">
      <p className="font-mono text-xs uppercase tracking-[0.25em] text-copper">Evaluation lab</p>
      <h2 className="font-serif text-4xl">Synthetic benchmarks only</h2>
      <p className="max-w-2xl text-ink/80">
        These rows are labeled fixtures. They exist to exercise scoring code. They are not a public leaderboard and
        they are not evidence of calibration.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <button className="border border-ink bg-ink px-4 py-2 text-paper" onClick={run}>
          Run baseline vs ensemble
        </button>
        <a className="border border-rule px-4 py-2" href="/api/benchmarks/template.csv">
          CSV template
        </a>
        <label className="border border-rule px-4 py-2">
          Import CSV or JSON
          <input className="sr-only" type="file" accept=".csv,.json,text/csv,application/json" onChange={onImport} />
        </label>
      </div>
      {message ? <p>{message}</p> : null}
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-rule">
            <th className="py-2">Profile</th>
            <th>n</th>
            <th>Brier</th>
            <th>Log loss</th>
            <th>Failure rate</th>
            <th>Mean cost</th>
            <th>Mean latency</th>
            <th>Brier / $</th>
          </tr>
        </thead>
        <tbody>
          {profiles.map((profile: any) => (
            <tr key={profile.profile_id} className="border-b border-rule/70">
              <td className="py-2">{profile.profile_id}</td>
              <td>{profile.n}</td>
              <td>{profile.brier?.toFixed?.(4) ?? "—"}</td>
              <td>{profile.log_loss?.toFixed?.(4) ?? "—"}</td>
              <td>{pct(profile.failure_rate)}</td>
              <td>${Number(profile.mean_cost_usd || 0).toFixed(4)}</td>
              <td>{Math.round(profile.mean_latency_ms || 0)} ms</td>
              <td>{profile.brier_per_dollar?.toFixed?.(2) ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-sm text-ink/70">
        {reliability.available
          ? "Reliability diagram available."
          : reliability.message || "Calibration cannot yet be estimated reliably."}
      </p>
      <section>
        <h3 className="font-serif text-2xl">By category</h3>
        <ul className="mt-3 space-y-1 text-sm">
          {categories.length === 0 ? <li>No category scores yet.</li> : null}
          {categories.map((row: any) => (
            <li key={row.key}>
              {row.key}: Brier {row.brier?.toFixed?.(4)} (n={row.n})
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h3 className="font-serif text-2xl">Question-level results</h3>
        <table className="mt-3 w-full text-left text-sm">
          <thead>
            <tr className="border-b border-rule">
              <th className="py-2">Question</th>
              <th>Profile</th>
              <th>p</th>
              <th>y</th>
              <th>Brier</th>
              <th>Failed</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row: any) => (
              <tr key={row.id} className="border-b border-rule/70">
                <td className="py-2">{row.question}</td>
                <td>{row.profile_id}</td>
                <td>{pct(row.probability)}</td>
                <td>{row.outcome}</td>
                <td>{row.brier?.toFixed?.(4) ?? "—"}</td>
                <td>{row.failed ? "yes" : "no"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section>
        <h3 className="font-serif text-2xl">Profile configurations</h3>
        <pre className="mt-3 overflow-x-auto border border-rule bg-white/70 p-4 text-sm">
{JSON.stringify(data.profile_configs || [], null, 2)}
        </pre>
      </section>
    </div>
  );
}
