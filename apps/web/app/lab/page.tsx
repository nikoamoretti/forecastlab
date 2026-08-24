"use client";

import { useEffect, useState } from "react";
import { api, pct } from "@/lib/api";

const SYNTHETIC_NOTICE = "Software-verification fixtures only. Not evidence of real-world forecasting quality.";

const PROFILE_LABELS: Record<string, string> = {
  single_model_forecaster_v1: "Single model V1",
  three_track_forecaster: "Three track",
  graph_forecaster_v1: "Graph forecast V1"
};

function profileLabel(profileId: string) {
  return PROFILE_LABELS[profileId] || profileId;
}

function metric(value: number | null | undefined, digits = 4) {
  if (value == null) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function intervalLabel(interval: number[] | null | undefined) {
  if (!interval || interval.length !== 2) return "insufficient evidence";
  return `${metric(interval[0])} to ${metric(interval[1])}`;
}

function IntervalRail({ comparison, metricKey }: { comparison: any; metricKey: "brier" | "log_loss" }) {
  const interval = comparison[`${metricKey}_confidence_interval`] as number[] | null;
  const observed = comparison[`mean_${metricKey}_difference`] as number | null;
  const label = metricKey === "brier" ? "Brier" : "Log loss";
  if (!interval || observed == null) {
    return (
      <div className="border border-rule bg-paper/60 p-3 text-sm" data-testid={`${metricKey}-insufficient`}>
        <p className="font-medium">{label} observed difference</p>
        <p className="mt-1 font-mono text-xs text-copper">insufficient evidence</p>
      </div>
    );
  }
  const scale = Math.max(Math.abs(interval[0]), Math.abs(interval[1]), Math.abs(observed), 0.0001);
  const position = (value: number) => 50 + (value / scale) * 45;
  const lower = Math.min(position(interval[0]), position(interval[1]));
  const upper = Math.max(position(interval[0]), position(interval[1]));
  const point = position(observed);
  return (
    <div className="border border-rule bg-paper/60 p-3">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm font-medium">{label} observed difference</p>
        <p className="font-mono text-sm">{metric(observed)}</p>
      </div>
      <div
        className="relative mt-4 h-8"
        role="img"
        aria-label={`${label} observed difference ${metric(observed)}, 95 percent interval ${intervalLabel(interval)}`}
      >
        <div className="absolute left-[5%] right-[5%] top-3 h-px bg-rule" />
        <div className="absolute bottom-0 top-0 left-1/2 w-px bg-ink/40" />
        <div
          className="absolute top-[9px] h-2 bg-pine"
          style={{ left: `${lower}%`, width: `${Math.max(upper - lower, 1)}%` }}
        />
        <div
          className="absolute top-[6px] h-3.5 w-3.5 -translate-x-1/2 rounded-full border-2 border-ink bg-copper"
          style={{ left: `${point}%` }}
        />
      </div>
      <div className="flex justify-between font-mono text-[10px] uppercase tracking-wide text-ink/60">
        <span>A records lower loss</span>
        <span>zero</span>
        <span>B records lower loss</span>
      </div>
      <p className="mt-2 font-mono text-xs text-ink/70">95% interval {intervalLabel(interval)}</p>
    </div>
  );
}

function CalibrationChart({ profile }: { profile: any }) {
  const calibration = profile.performance?.calibration_buckets || {};
  const buckets = calibration.buckets || [];
  return (
    <article className="border border-rule bg-white/50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h4 className="font-mono text-xs uppercase tracking-[0.16em]" title={profile.profile_id}>
          {profileLabel(profile.profile_id)}
        </h4>
        <span className="text-xs text-ink/60">n={calibration.sample_count ?? 0}</span>
      </div>
      {calibration.evidence_status === "insufficient evidence" ? (
        <p className="mt-2 text-xs font-medium text-copper">insufficient evidence</p>
      ) : null}
      <div
        className="mt-4 grid h-44 grid-cols-5 items-end gap-2 border-b border-l border-rule px-2 pt-3"
        role="img"
        aria-label={`${profile.profile_id} calibration chart with five probability buckets`}
      >
        {buckets.map((bucket: any) => {
          const predicted = Number(bucket.average_predicted_probability || 0);
          const actual = Number(bucket.actual_outcome_frequency || 0);
          return (
            <div key={bucket.label} className="flex h-full min-w-0 flex-col justify-end">
              <div className="flex h-32 items-end justify-center gap-1">
                <div
                  className="w-2.5 bg-copper"
                  style={{ height: `${predicted * 100}%` }}
                  title={`Average prediction ${pct(bucket.average_predicted_probability)}`}
                />
                <div
                  className="w-2.5 bg-pine"
                  style={{ height: `${actual * 100}%` }}
                  title={`Outcome frequency ${pct(bucket.actual_outcome_frequency)}`}
                />
              </div>
              <p className="mt-2 truncate text-center font-mono text-[9px]">{bucket.label}</p>
              <p className="text-center text-[9px] text-ink/55">n={bucket.forecast_count}</p>
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex gap-4 text-xs text-ink/70">
        <span className="flex items-center gap-1.5"><i className="h-2 w-2 bg-copper" />Prediction</span>
        <span className="flex items-center gap-1.5"><i className="h-2 w-2 bg-pine" />Outcome frequency</span>
      </div>
    </article>
  );
}

export default function LabPage() {
  const [datasets, setDatasets] = useState<any[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [profiles, setProfiles] = useState<any[]>([]);
  const [selected, setSelected] = useState<string[]>([
    "single_agent_equal_budget_v1",
    "three_track_equal_budget_v1",
  ]);
  const [v1Workflow, setV1Workflow] = useState<any>(null);
  const [experimentId, setExperimentId] = useState<string | null>(null);
  const [progress, setProgress] = useState<any>(null);
  const [summary, setSummary] = useState<any>(null);
  const [message, setMessage] = useState("");
  const [controlledExperiments, setControlledExperiments] = useState<any[]>([]);
  const [statisticalExperimentId, setStatisticalExperimentId] = useState("");
  const [statisticalAnalysis, setStatisticalAnalysis] = useState<any>(null);
  const [statisticalMessage, setStatisticalMessage] = useState("");

  async function loadMeta() {
    const ds = await api<{ datasets: any[] }>("/api/datasets");
    setDatasets(ds.datasets || []);
    if (!datasetId && ds.datasets?.[0]) setDatasetId(ds.datasets[0].id);
    const plist = await api<any[]>("/api/profiles");
    setProfiles(plist);
    const workflow = await api<any>("/api/evaluations/v1");
    setV1Workflow(workflow);
    try {
      const controlled = await api<{ experiments: any[] }>("/api/forecast-experiments");
      const available = controlled.experiments || [];
      setControlledExperiments(available);
      setStatisticalExperimentId((current) => current || available[0]?.id || "");
    } catch {
      setControlledExperiments([]);
    }
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

  useEffect(() => {
    if (!statisticalExperimentId) {
      setStatisticalAnalysis(null);
      return;
    }
    let cancelled = false;
    setStatisticalMessage("Loading paired analysis…");
    api<any>(`/api/forecast-experiments/${statisticalExperimentId}/analysis`)
      .then((analysis) => {
        if (cancelled) return;
        setStatisticalAnalysis(analysis);
        setStatisticalMessage("");
      })
      .catch(() => {
        if (cancelled) return;
        setStatisticalAnalysis(null);
        setStatisticalMessage("Statistical analysis unavailable for this experiment.");
      });
    return () => {
      cancelled = true;
    };
  }, [statisticalExperimentId]);

  async function refreshStatisticalAnalysis() {
    if (!statisticalExperimentId) return;
    setStatisticalMessage("Refreshing paired analysis…");
    try {
      const analysis = await api<any>(`/api/forecast-experiments/${statisticalExperimentId}/analysis`);
      setStatisticalAnalysis(analysis);
      setStatisticalMessage("");
    } catch {
      setStatisticalMessage("Statistical analysis unavailable for this experiment.");
    }
  }

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

  async function createV1() {
    setMessage("Creating the V1 10-question comparison…");
    const created = await api<{ id: string; workflow: any }>("/api/evaluations/v1", { method: "POST" });
    setExperimentId(created.id);
    setSummary(null);
    if (created.workflow?.dataset?.id) setDatasetId(created.workflow.dataset.id);
    setMessage(
      `V1 experiment queued. Polling ${created.workflow?.task_count || v1Workflow?.task_count || 0} profile-question tasks.`
    );
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
      <section className="border border-ink bg-white/65 p-5 md:p-6" data-testid="statistical-evaluation">
        <div className="flex flex-wrap items-start justify-between gap-4 border-b border-rule pb-5">
          <div>
            <p className="font-mono text-xs uppercase tracking-[0.2em] text-copper">Controlled experiment analysis</p>
            <h3 className="mt-2 font-serif text-3xl">Paired statistical evaluation</h3>
            <p className="mt-2 max-w-3xl text-sm text-ink/75">
              Each observed difference uses completed results for the same questions. Negative loss differences mean
              profile A recorded lower loss; intervals quantify uncertainty without assigning a system rank.
            </p>
          </div>
          <div className="flex min-w-64 flex-col gap-2 sm:flex-row sm:items-end">
            <label className="text-sm">
              <span className="block text-ink/65">Controlled experiment</span>
              <select
                className="mt-1 w-full border border-rule bg-paper px-3 py-2"
                value={statisticalExperimentId}
                onChange={(event) => setStatisticalExperimentId(event.target.value)}
              >
                {controlledExperiments.length ? null : <option value="">No experiment available</option>}
                {controlledExperiments.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.dataset_id} · {item.status}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="border border-ink px-3 py-2 text-sm disabled:opacity-40"
              onClick={refreshStatisticalAnalysis}
              disabled={!statisticalExperimentId}
            >
              Refresh analysis
            </button>
          </div>
        </div>
        {statisticalMessage ? <p className="mt-4 text-sm" aria-live="polite">{statisticalMessage}</p> : null}
        {!controlledExperiments.length && !statisticalMessage ? (
          <p className="mt-5 border border-rule bg-paper/60 p-4 text-sm">
            No controlled experiment is available. Run the three-profile comparison on a frozen evaluation dataset to
            populate this analysis.
          </p>
        ) : null}
        {statisticalAnalysis ? (
          <div className="mt-6 space-y-7">
            <dl className="grid gap-px border border-rule bg-rule text-sm sm:grid-cols-2 lg:grid-cols-4">
              <div className="bg-paper p-3">
                <dt className="text-ink/60">Paired population</dt>
                <dd className="mt-1">Completed, identical questions</dd>
              </div>
              <div className="bg-paper p-3">
                <dt className="text-ink/60">Bootstrap</dt>
                <dd className="mt-1">{statisticalAnalysis.methodology?.bootstrap_samples?.toLocaleString()} samples</dd>
              </div>
              <div className="bg-paper p-3">
                <dt className="text-ink/60">Random seed</dt>
                <dd className="mt-1 font-mono">{statisticalAnalysis.methodology?.random_seed}</dd>
              </div>
              <div className="bg-paper p-3">
                <dt className="text-ink/60">Minimum paired sample</dt>
                <dd className="mt-1">{statisticalAnalysis.methodology?.minimum_paired_questions} questions</dd>
              </div>
            </dl>
            {(statisticalAnalysis.comparisons || []).some((item: any) => item.evidence_status === "insufficient evidence") ? (
              <p className="border border-copper bg-paper px-4 py-3 text-sm" data-testid="insufficient-evidence">
                insufficient evidence — observed means are shown, but confidence intervals require at least {statisticalAnalysis.methodology?.minimum_paired_questions} paired questions.
              </p>
            ) : null}
            <section>
              <div className="flex flex-wrap items-end justify-between gap-2">
                <div>
                  <p className="font-mono text-xs uppercase tracking-[0.18em] text-copper">Profile A vs profile B</p>
                  <h4 className="mt-1 font-serif text-2xl">Observed differences</h4>
                </div>
                <p className="text-xs text-ink/60">Difference = A − B · 95% paired percentile interval</p>
              </div>
              <div className="mt-4 grid gap-4 xl:grid-cols-3">
                {(statisticalAnalysis.comparisons || []).map((comparison: any) => (
                  <article
                    key={`${comparison.profile_a}-${comparison.profile_b}`}
                    className="border border-rule bg-white/60 p-4"
                    data-testid="statistical-comparison"
                  >
                    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 border-b border-rule pb-3 text-sm">
                      <p className="font-medium" title={comparison.profile_a}>{profileLabel(comparison.profile_a)}</p>
                      <span className="font-serif text-xl text-copper">vs</span>
                      <p className="text-right font-medium" title={comparison.profile_b}>{profileLabel(comparison.profile_b)}</p>
                    </div>
                    <p className="mt-3 text-xs text-ink/60">
                      {comparison.question_count} paired questions · {comparison.evidence_status}
                    </p>
                    <div className="mt-3 space-y-3">
                      <IntervalRail comparison={comparison} metricKey="brier" />
                      <IntervalRail comparison={comparison} metricKey="log_loss" />
                    </div>
                    <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-rule pt-3 text-sm">
                      <div>
                        <dt className="text-ink/60">Cost difference</dt>
                        <dd className="mt-1 font-mono">${metric(comparison.mean_cost_difference)}</dd>
                      </div>
                      <div>
                        <dt className="text-ink/60">Latency difference</dt>
                        <dd className="mt-1 font-mono">{metric(comparison.mean_latency_difference, 1)} ms</dd>
                      </div>
                    </dl>
                  </article>
                ))}
              </div>
            </section>
            <section>
              <p className="font-mono text-xs uppercase tracking-[0.18em] text-copper">Calibration</p>
              <h4 className="mt-1 font-serif text-2xl">Prediction and outcome frequency</h4>
              <p className="mt-2 text-sm text-ink/70">
                Five fixed buckets are displayed as recorded. No calibration adjustment is applied.
              </p>
              <div className="mt-4 grid gap-4 xl:grid-cols-3">
                {(statisticalAnalysis.profiles || []).map((profile: any) => (
                  <CalibrationChart key={`calibration-${profile.profile_id}`} profile={profile} />
                ))}
              </div>
            </section>
            <section>
              <p className="font-mono text-xs uppercase tracking-[0.18em] text-copper">Cost efficiency</p>
              <h4 className="mt-1 font-serif text-2xl">Recorded resource ratios</h4>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[760px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-rule">
                      <th className="py-2">Profile</th>
                      <th>Total cost</th>
                      <th>Cost/question</th>
                      <th>Brier/$</th>
                      <th>Log loss/$</th>
                      <th>Latency/question</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(statisticalAnalysis.cost_efficiency || []).map((item: any) => (
                      <tr key={`efficiency-${item.profile_id}`} className="border-b border-rule/70">
                        <td className="py-2" title={item.profile_id}>{profileLabel(item.profile_id)}</td>
                        <td>${Number(item.total_cost || 0).toFixed(4)}</td>
                        <td>{item.cost_per_question == null ? "—" : `$${item.cost_per_question.toFixed(4)}`}</td>
                        <td>{item.brier_per_dollar?.toFixed?.(4) ?? "—"}</td>
                        <td>{item.log_loss_per_dollar?.toFixed?.(4) ?? "—"}</td>
                        <td>{item.latency_per_question?.toFixed?.(1) ?? "—"} ms</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
            <p className="border-t border-rule pt-4 text-xs text-ink/60">{statisticalAnalysis.notice}</p>
          </div>
        ) : null}
      </section>
      <section className="border border-copper bg-white/60 p-5">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-copper">First V1 experiment</p>
        <h3 className="mt-2 font-serif text-2xl">10-question profile comparison</h3>
        <p className="mt-2 text-sm text-ink/80">
          {v1Workflow
            ? `${v1Workflow.profiles.join(" vs ")} · ${v1Workflow.question_count} synthetic questions · ${v1Workflow.task_count} tasks`
            : "Loading the fixed V1 workflow…"}
        </p>
        <p className="mt-2 text-sm text-ink/70">
          Measures Brier score, log loss, cost, latency, completion rate, and evidence coverage. This framework reports
          observed differences without assigning a system rank.
        </p>
        <button
          className="mt-4 border border-ink bg-ink px-4 py-2 text-paper disabled:opacity-50"
          onClick={createV1}
          disabled={!v1Workflow}
        >
          Run V1 10-question comparison
        </button>
      </section>
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
          <p className="border border-rule px-4 py-3 text-sm">
            Observed differences are descriptive measurements and do not assign a system rank.
          </p>
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
                  <th>Evidence coverage</th>
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
                      <td>{pct(block.mean_evidence_coverage)} (n={block.evidence_coverage_n ?? 0})</td>
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
                  <th>Evidence coverage</th>
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
                      <td>{pct(block.mean_evidence_coverage)} (n={block.evidence_coverage_n ?? 0})</td>
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
                  <th>Δ log loss</th>
                  <th>Δ cost</th>
                  <th>Δ latency</th>
                  <th>Δ evidence coverage</th>
                  <th>A lower / equal / A higher Brier</th>
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
                    <td>{row.mean_log_loss_difference?.toFixed?.(4) ?? "—"}</td>
                    <td>${row.mean_cost_difference?.toFixed?.(4) ?? "—"}</td>
                    <td>{row.mean_latency_difference?.toFixed?.(1) ?? "—"} ms</td>
                    <td>
                      {pct(row.mean_evidence_coverage_difference)} (n={row.evidence_coverage_pair_count ?? 0})
                    </td>
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
                  <th>Δ log loss</th>
                  <th>Δ cost</th>
                  <th>Δ latency</th>
                  <th>Δ evidence coverage</th>
                  <th>A lower / equal / A higher Brier</th>
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
                    <td>{row.mean_log_loss_difference?.toFixed?.(4) ?? "—"}</td>
                    <td>${row.mean_cost_difference?.toFixed?.(4) ?? "—"}</td>
                    <td>{row.mean_latency_difference?.toFixed?.(1) ?? "—"} ms</td>
                    <td>
                      {pct(row.mean_evidence_coverage_difference)} (n={row.evidence_coverage_pair_count ?? 0})
                    </td>
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
                  <th>Log loss</th>
                  <th>Cost</th>
                  <th>Latency</th>
                  <th>Evidence coverage</th>
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
                    <td>{row.log_loss_value?.toFixed?.(4) ?? "—"}</td>
                    <td>${Number(row.cost_usd || 0).toFixed(4)}</td>
                    <td>{row.latency_ms || 0} ms</td>
                    <td>
                      {pct(row.evidence_coverage)} ({row.evidence_covered_units || 0}/{row.evidence_total_units || 0})
                    </td>
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
