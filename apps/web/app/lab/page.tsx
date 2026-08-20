"use client";

import { useEffect, useState } from "react";
import { api, pct } from "@/lib/api";

const SYNTHETIC_NOTICE = "Software-verification fixtures only. Not evidence of real-world forecasting quality.";

export default function LabPage() {
  const [datasets, setDatasets] = useState<any[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [profiles, setProfiles] = useState<any[]>([]);
  const [selected, setSelected] = useState<string[]>([
    "single_agent_equal_budget_v1",
    "three_track_equal_budget_v1",
  ]);
  const [experimentId, setExperimentId] = useState<string | null>(null);
  const [progress, setProgress] = useState<any>(null);
  const [summary, setSummary] = useState<any>(null);
  const [message, setMessage] = useState("");

  async function loadMeta() {
    const ds = await api<{ datasets: any[] }>("/api/datasets");
    setDatasets(ds.datasets || []);
    if (!datasetId && ds.datasets?.[0]) setDatasetId(ds.datasets[0].id);
    const plist = await api<any[]>("/api/profiles");
    setProfiles(plist);
  }

  useEffect(() => {
    loadMeta().catch(() => setMessage("Lab metadata unavailable"));
  }, []);

  useEffect(() => {
    if (!experimentId) return;
    let cancelled = false;
    const terminal = new Set(["completed", "completed_with_failures", "failed"]);
    const timer = setInterval(async () => {
      try {
        const next = await api<any>(`/api/experiments/${experimentId}`);
        if (cancelled) return;
        setProgress(next);
        if (terminal.has(next.status)) {
          const full = await api<any>(`/api/experiments/${experimentId}/summary`);
          if (!cancelled) setSummary(full);
          clearInterval(timer);
        }
      } catch {
        /* keep polling until a terminal status is observed */
      }
    }, 1200);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [experimentId]);

  async function create() {
    setMessage("Creating experiment…");
    const created = await api<{ id: string }>("/api/experiments", {
      method: "POST",
      body: JSON.stringify({ dataset_id: datasetId, profile_ids: selected })
    });
    setExperimentId(created.id);
    setSummary(null);
    setMessage("Experiment queued. Polling progress.");
  }

  async function onImport(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    body.append("name", file.name.replace(/\.(csv|json)$/i, ""));
    setMessage("Importing…");
    await api("/api/benchmarks/import", { method: "POST", body });
    await loadMeta();
    setMessage("Import finished. Mixed synthetic/real files are rejected.");
  }

  const dataset = datasets.find((item) => item.id === datasetId);
  const rows = summary?.rows || [];
  const comparisons = summary?.paired_comparisons_all_valid || summary?.paired_comparisons || [];
  const fullComparisons = summary?.paired_comparisons_full_only || [];
  const reliability = summary?.reliability_by_profile || {};

  return (
    <div className="space-y-6">
      <p className="font-mono text-xs uppercase tracking-[0.25em] text-copper">Evaluation lab</p>
      <h2 className="font-serif text-4xl">Benchmark experiments</h2>
      <p className="max-w-2xl text-ink/80">
        Create an asynchronous experiment against one dataset. Real tasks use backtest mode. Results stay inside that
        experiment.
      </p>
      <section className="grid gap-4 md:grid-cols-2">
        <label className="block">
          <span className="text-sm">Dataset</span>
          <select className="mt-2 w-full border border-rule bg-white p-3" value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
            {datasets.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} ({item.is_synthetic ? "synthetic" : "real"})
              </option>
            ))}
          </select>
        </label>
        <div className="border border-rule p-3 text-sm">
          <p>{dataset?.description || "Select a dataset."}</p>
          <p className="mt-2">
            Badge: {dataset?.is_synthetic ? "synthetic" : "real"} · {dataset?.question_count || 0} questions · hash{" "}
            <span className="font-mono text-xs">{dataset?.dataset_hash || "—"}</span>
          </p>
        </div>
      </section>
      <fieldset>
        <legend className="text-sm">Profiles</legend>
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          {profiles.map((profile) => (
            <label key={profile.id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={selected.includes(profile.id)}
                onChange={(event) => {
                  setSelected((current) =>
                    event.target.checked ? [...current, profile.id] : current.filter((id) => id !== profile.id)
                  );
                }}
              />
              {profile.label || profile.id}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="flex flex-wrap items-center gap-3">
        <button className="border border-ink bg-ink px-4 py-2 text-paper" onClick={create} disabled={!datasetId || selected.length < 1}>
          Create experiment
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
      {progress ? (
        <section className="border border-rule p-4">
          <h3 className="font-serif text-2xl">Progress</h3>
          <p className="mt-2 text-sm">
            {progress.status} · {progress.completed_tasks}/{progress.total_tasks} completed · {progress.failed_tasks} failed ·{" "}
            {progress.percent}%
          </p>
        </section>
      ) : null}
      {summary?.is_synthetic || summary?.synthetic ? <p className="border border-copper px-4 py-3">{SYNTHETIC_NOTICE}</p> : null}
      {summary ? (
        <>
          <section>
            <h3 className="font-serif text-2xl">Experiment spend</h3>
            <p className="mt-2 text-sm text-ink/70">
              Totals include successful, partial, and failed tasks. Failed-attempt and search charges stay in commercial
              cost reporting.
            </p>
            <dl className="mt-3 grid gap-2 text-sm md:grid-cols-4">
              <div>
                <dt className="text-ink/60">Total experiment cost</dt>
                <dd>${Number(summary.spend?.total_cost_usd || 0).toFixed(4)}</dd>
              </div>
              <div>
                <dt className="text-ink/60">Full / partial / failed-task</dt>
                <dd>
                  ${Number(summary.spend?.total_full_cost_usd || 0).toFixed(4)} / $
                  {Number(summary.spend?.total_partial_cost_usd || 0).toFixed(4)} / $
                  {Number(summary.spend?.total_failed_task_cost_usd || 0).toFixed(4)}
                </dd>
              </div>
              <div>
                <dt className="text-ink/60">Mean cost per started task</dt>
                <dd>${Number(summary.spend?.mean_cost_per_started_task || 0).toFixed(4)}</dd>
              </div>
              <div>
                <dt className="text-ink/60">Model / search / failed-attempt</dt>
                <dd>
                  ${Number(summary.spend?.model_cost_usd || 0).toFixed(4)} / $
                  {Number(summary.spend?.search_cost_usd || 0).toFixed(4)} / $
                  {Number(summary.spend?.failed_attempt_cost_usd || 0).toFixed(4)}
                </dd>
              </div>
            </dl>
          </section>
          <section>
            <h3 className="font-serif text-2xl">Outcome mix</h3>
            <p className="mt-2 text-sm text-ink/70">
              Full forecasts, partial forecasts, and failed tasks are counted separately. Headline scores below do not
              hide partials inside ordinary success rates.
            </p>
            <table className="mt-3 w-full text-left text-sm">
              <thead>
                <tr className="border-b border-rule">
                  <th className="py-2">Profile</th>
                  <th>Total</th>
                  <th>Full</th>
                  <th>Partial</th>
                  <th>Failed</th>
                  <th>Completion rate</th>
                  <th>Partial rate</th>
                  <th>Failure rate</th>
                </tr>
              </thead>
              <tbody>
                {(summary.profiles || []).map((profile: any) => (
                  <tr key={`${profile.profile_id}-mix`} className="border-b border-rule/70">
                    <td className="py-2">{profile.profile_id}</td>
                    <td>{profile.total_count ?? profile.n}</td>
                    <td>{profile.full_count ?? "—"}</td>
                    <td>{profile.partial_count ?? "—"}</td>
                    <td>{profile.failed_count ?? "—"}</td>
                    <td>{pct(profile.completion_rate)}</td>
                    <td>{pct(profile.partial_rate)}</td>
                    <td>{pct(profile.failure_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section>
            <h3 className="font-serif text-2xl">All-valid metrics</h3>
            <p className="mt-2 text-sm text-ink/70">Includes full and partial forecasts that produced a probability.</p>
            <table className="mt-3 w-full text-left text-sm">
              <thead>
                <tr className="border-b border-rule">
                  <th className="py-2">Profile</th>
                  <th>n</th>
                  <th>Brier</th>
                  <th>Log loss</th>
                  <th>Mean total cost</th>
                  <th>Median total cost</th>
                  <th>Mean latency</th>
                  <th>Brier per dollar</th>
                </tr>
              </thead>
              <tbody>
                {(summary.profiles || []).map((profile: any) => {
                  const block = profile.all_valid || profile;
                  return (
                    <tr key={`${profile.profile_id}-all-valid`} className="border-b border-rule/70">
                      <td className="py-2">{profile.profile_id}</td>
                      <td>{block.n}</td>
                      <td>{block.brier?.toFixed?.(4) ?? "—"}</td>
                      <td>{block.log_loss?.toFixed?.(4) ?? "—"}</td>
                      <td>${Number(block.mean_cost_usd || 0).toFixed(4)}</td>
                      <td>${Number(block.median_cost_usd || 0).toFixed(4)}</td>
                      <td>{Math.round(block.mean_latency_ms || 0)} ms</td>
                      <td>{block.brier_per_dollar?.toFixed?.(4) ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
          <section>
            <h3 className="font-serif text-2xl">Full-run-only metrics</h3>
            <p className="mt-2 text-sm text-ink/70">Excludes partial and failed forecasts.</p>
            <table className="mt-3 w-full text-left text-sm">
              <thead>
                <tr className="border-b border-rule">
                  <th className="py-2">Profile</th>
                  <th>n</th>
                  <th>Brier</th>
                  <th>Log loss</th>
                  <th>Mean total cost</th>
                  <th>Median total cost</th>
                  <th>Mean latency</th>
                  <th>Brier per dollar</th>
                </tr>
              </thead>
              <tbody>
                {(summary.profiles || []).map((profile: any) => {
                  const block = profile.full_only || {};
                  return (
                    <tr key={`${profile.profile_id}-full-only`} className="border-b border-rule/70">
                      <td className="py-2">{profile.profile_id}</td>
                      <td>{block.n ?? "—"}</td>
                      <td>{block.brier?.toFixed?.(4) ?? "—"}</td>
                      <td>{block.log_loss?.toFixed?.(4) ?? "—"}</td>
                      <td>${Number(block.mean_cost_usd || 0).toFixed(4)}</td>
                      <td>${Number(block.median_cost_usd || 0).toFixed(4)}</td>
                      <td>{Math.round(block.mean_latency_ms || 0)} ms</td>
                      <td>{block.brier_per_dollar?.toFixed?.(4) ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
          <section>
            <h3 className="font-serif text-2xl">Paired comparisons (all valid)</h3>
            <table className="mt-3 w-full text-left text-sm">
              <thead>
                <tr className="border-b border-rule">
                  <th className="py-2">Pair</th>
                  <th>n</th>
                  <th>Δ Brier</th>
                  <th>Wins/ties/losses</th>
                  <th>95% interval</th>
                </tr>
              </thead>
              <tbody>
                {comparisons.map((row: any) => (
                  <tr key={`${row.left_profile_id}-${row.right_profile_id}`} className="border-b border-rule/70">
                    <td className="py-2">
                      {row.left_profile_id} vs {row.right_profile_id}
                    </td>
                    <td>{row.n}</td>
                    <td>{row.mean_paired_brier_difference?.toFixed?.(4) ?? "—"}</td>
                    <td>
                      {row.wins_left}/{row.ties}/{row.losses_left}
                    </td>
                    <td>
                      {row.paired_brier_bootstrap?.available
                        ? `${row.paired_brier_bootstrap.low.toFixed(4)} to ${row.paired_brier_bootstrap.high.toFixed(4)}`
                        : row.paired_brier_bootstrap?.message || "unavailable"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section>
            <h3 className="font-serif text-2xl">Paired comparisons (full-run-only)</h3>
            <table className="mt-3 w-full text-left text-sm">
              <thead>
                <tr className="border-b border-rule">
                  <th className="py-2">Pair</th>
                  <th>n</th>
                  <th>Δ Brier</th>
                  <th>Wins/ties/losses</th>
                  <th>95% interval</th>
                </tr>
              </thead>
              <tbody>
                {fullComparisons.map((row: any) => (
                  <tr key={`full-${row.left_profile_id}-${row.right_profile_id}`} className="border-b border-rule/70">
                    <td className="py-2">
                      {row.left_profile_id} vs {row.right_profile_id}
                    </td>
                    <td>{row.n}</td>
                    <td>{row.mean_paired_brier_difference?.toFixed?.(4) ?? "—"}</td>
                    <td>
                      {row.wins_left}/{row.ties}/{row.losses_left}
                    </td>
                    <td>
                      {row.paired_brier_bootstrap?.available
                        ? `${row.paired_brier_bootstrap.low.toFixed(4)} to ${row.paired_brier_bootstrap.high.toFixed(4)}`
                        : row.paired_brier_bootstrap?.message || "unavailable"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section>
            <h3 className="font-serif text-2xl">Reliability by profile</h3>
            <ul className="mt-3 space-y-1 text-sm">
              {Object.entries(reliability).map(([profileId, item]: [string, any]) => (
                <li key={profileId}>
                  {profileId}: {item.available ? `displayable n=${item.sample_count}` : item.message}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-sm text-ink/70">
              Twenty observations are a display threshold only and are not enough for a calibration claim.
            </p>
          </section>
          <p>
            <a className="border border-rule px-4 py-2" href={`/api/experiments/${summary.experiment_id}/export.csv`}>
              Export CSV
            </a>
          </p>
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
                  <th>Status</th>
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
                    <td>{row.status || (row.failed ? "failed" : row.partial ? "partial" : "full")}</td>
                    <td>{row.failed ? "yes" : "no"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section>
            <h3 className="font-serif text-2xl">Experiment configuration</h3>
            <pre className="mt-3 overflow-x-auto border border-rule bg-white/70 p-4 text-sm">
{JSON.stringify(
  {
    experiment_id: summary.experiment_id,
    experiment_hash: summary.experiment_hash,
    dataset_name: summary.dataset_name,
    dataset_hash: summary.dataset_hash,
    synthetic: summary.synthetic,
    profile_hashes: summary.profile_hashes,
    model: `${summary.model_provider}/${summary.model_name}`,
    search: summary.search_provider,
    evidence_policy: summary.evidence_policy,
    commit: summary.code_commit
  },
  null,
  2
)}
            </pre>
          </section>
        </>
      ) : null}
    </div>
  );
}
