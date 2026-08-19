"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, pct } from "@/lib/api";

const STAGES = [
  { id: "operationalize", label: "Operationalizing question" },
  { id: "research", label: "Planning independent research" },
  { id: "evidence", label: "Collecting evidence" },
  { id: "forecast", label: "Producing track estimates" },
  { id: "aggregate", label: "Aggregating probabilities" },
  { id: "report", label: "Preparing report" }
];

export default function ForecastPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [openTrack, setOpenTrack] = useState<string | null>(null);
  const [watchUrl, setWatchUrl] = useState("http://127.0.0.1:8765/demo/indicators/inflation");

  async function load() {
    const payload = await api<any>(`/api/questions/${id}/report`);
    setData(payload);
  }

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
    const timer = setInterval(() => {
      load().catch(() => undefined);
    }, 1500);
    return () => clearInterval(timer);
  }, [id]);

  async function simulateWatch() {
    const watch = data?.watches?.[0];
    if (!watch) return;
    await api(`/api/watches/${watch.id}/check`, { method: "POST" });
    await api("/demo/indicators/unemployment/simulate", {
      method: "POST",
      body: JSON.stringify({ value: 5.4 })
    });
    await api(`/api/watches/${watch.id}/check`, { method: "POST" });
    await load();
  }

  async function rerun() {
    await api(`/api/questions/${id}/runs`, {
      method: "POST",
      body: JSON.stringify({
        profile_id: data?.requested_profile_id || data?.latest_run?.profile_id || "three_track_ensemble",
        mode: data?.requested_mode || data?.latest_run?.mode || "demo",
        as_of: data?.requested_as_of || undefined
      })
    });
    await load();
  }

  async function addWatch(event: React.FormEvent) {
    event.preventDefault();
    await api(`/api/questions/${id}/watches`, {
      method: "POST",
      body: JSON.stringify({
        endpoint_url: watchUrl,
        endpoint_type: watchUrl.includes("json") || watchUrl.includes("/demo/") ? "json" : "html",
        json_path: "$.value",
        auto_rerun: false
      })
    });
    await load();
  }

  if (error) return <p>{error}</p>;
  if (!data) return <p>Loading forecast…</p>;
  const run = data.latest_run || {};
  const tracks = run.tracks || [];
  const evidence = run.evidence || [];
  const aggregation = run.aggregation || {};
  const versions = data.versions || [];
  const previous = data.previous_probability ?? versions[1]?.ensemble_probability;
  const stage = run.progress_stage || "queued";
  const done = stage === "report" || run.status === "completed";
  const context = run.execution_context || {};
  const modeLabel = String(context.effective_mode || run.mode || "demo").toUpperCase();
  const missingTracks = (aggregation.missing_track_types || []).length > 0;
  const rejectedEvidence = evidence.filter((item: any) => item.rejected);

  return (
    <article className="space-y-10">
      <header className="grid gap-6 md:grid-cols-[1.3fr_0.7fr]">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.25em] text-copper">Forecast report</p>
          <h2 className="mt-2 font-serif text-4xl leading-tight">{data.original_text}</h2>
          <p className="mt-3 text-sm text-ink/70">
            {data.status} {data.stale ? "· stale after a watch change" : ""} · {data.version_count} version
            {data.version_count === 1 ? "" : "s"}
            {previous != null ? ` · previous ${pct(previous)}` : ""}
          </p>
          <p className="mt-2 text-sm">
            Deadline {data.contract?.resolution_deadline || "—"} · sources {evidence.length} · cost $
            {Number(run.cost_usd || 0).toFixed(4)} · {run.latency_ms || 0} ms · {run.mode || "demo"}
          </p>
        </div>
        <div className="border border-rule bg-white/60 p-5">
          <p className="font-mono text-xs uppercase tracking-[0.2em]">Ensemble estimate</p>
          <p className="font-serif text-6xl leading-none">{pct(data.latest_probability)}</p>
          <p className="mt-3 text-sm">Coded logit mean with shrinkage. Not a calibrated probability.</p>
          <p className="mt-2 text-sm">
            Track spread:{" "}
            {aggregation.track_spread == null ? "—" : Number(aggregation.track_spread).toFixed(3)}
          </p>
        </div>
      </header>

      <section className="border border-ink bg-white/70 p-4" aria-label="Execution identity">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-copper">{modeLabel}</p>
        <dl className="mt-3 grid gap-2 text-sm md:grid-cols-3">
          <div>
            <dt className="text-ink/60">Model</dt>
            <dd>
              {context.model_provider || run.providers?.model_provider || "—"} / {context.model_name || "—"}
              {context.model_is_mock ? " (mock)" : ""}
            </dd>
          </div>
          <div>
            <dt className="text-ink/60">Search</dt>
            <dd>
              {context.search_provider || "—"}
              {context.search_is_mock ? " (mock)" : ""}
            </dd>
          </div>
          <div>
            <dt className="text-ink/60">Evidence policy</dt>
            <dd>{context.evidence_policy || run.evidence_policy || "—"}</dd>
          </div>
          <div>
            <dt className="text-ink/60">Fixture evidence used</dt>
            <dd>{context.fixture_evidence_used || run.fixture_evidence_used ? "yes" : "no"}</dd>
          </div>
          <div>
            <dt className="text-ink/60">Profile</dt>
            <dd>
              {context.profile_id || run.profile_id} v{context.profile_version || 1}
            </dd>
          </div>
          <div>
            <dt className="text-ink/60">Configuration hash</dt>
            <dd className="font-mono text-xs">{context.configuration_hash || run.configuration_hash || "—"}</dd>
          </div>
          <div>
            <dt className="text-ink/60">Git commit</dt>
            <dd className="font-mono text-xs">{context.code_commit || run.code_commit || "unavailable"}</dd>
          </div>
          <div>
            <dt className="text-ink/60">Cost ceiling / actual</dt>
            <dd>
              ${Number(context.effective_max_cost_usd ?? 0).toFixed(2)} / ${Number(run.cost_usd || 0).toFixed(4)}
            </dd>
          </div>
          <div>
            <dt className="text-ink/60">Tokens / as_of</dt>
            <dd>
              {run.tokens || 0} · {run.as_of || "live clock"}
            </dd>
          </div>
        </dl>
      </section>
      {context.synthetic_fixture_run || context.fixture_evidence_used ? (
        <p className="border border-copper px-4 py-3 text-sm">
          Mixed or synthetic execution. Fixture evidence is for software verification, not real-world quality.
        </p>
      ) : null}
      {missingTracks ? (
        <p className="border border-copper px-4 py-3 text-sm">
          Partial tracks: {aggregation.missing_track_types.join(", ")}
        </p>
      ) : null}
      {run.error_stage === "budget" || String(run.error_message || "").includes("Budget") ? (
        <p className="border border-copper px-4 py-3 text-sm">Budget stop. Partial results were preserved.</p>
      ) : null}
      {rejectedEvidence.length ? (
        <p className="border border-rule px-4 py-3 text-sm">
          {rejectedEvidence.length} evidence rejection{rejectedEvidence.length === 1 ? "" : "s"} recorded.
        </p>
      ) : null}
      {run.status === "failed" ? (
        <p className="border border-copper px-4 py-3 text-sm">Provider or run failure: {run.error_message}</p>
      ) : null}

      <ol className="grid gap-2 md:grid-cols-6" aria-label="Forecast stages">
        {STAGES.map((item) => {
          const active = done || stage.includes(item.id);
          return (
            <li
              key={item.id}
              className={`border px-3 py-2 text-xs uppercase tracking-[0.12em] ${
                active ? "border-copper text-copper" : "border-rule text-ink/50"
              }`}
            >
              {item.label}
            </li>
          );
        })}
      </ol>
      <p className="text-sm text-ink/70">{run.progress_message || "Waiting for worker"}</p>

      <section className="flex flex-wrap gap-3">
        <button className="border border-ink px-4 py-2" onClick={simulateWatch}>
          Simulate watch change
        </button>
        <button className="border border-copper px-4 py-2 text-copper" onClick={rerun}>
          Rerun
        </button>
        <a className="border border-rule px-4 py-2" href={`/api/questions/${id}/export.md`}>
          Markdown
        </a>
        <a className="border border-rule px-4 py-2" href={`/api/questions/${id}/export.json`}>
          JSON
        </a>
      </section>

      <section>
        <h3 className="font-serif text-2xl">Key drivers</h3>
        <ul className="mt-3 list-disc space-y-2 pl-5">
          {tracks.flatMap((track: any) => track.key_drivers || []).map((driver: any, index: number) => (
            <li key={`${driver.factor}-${index}`}>
              {driver.factor} ({driver.direction}, {driver.importance})
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3 className="font-serif text-2xl">Main counterarguments</h3>
        <ul className="mt-3 list-disc space-y-2 pl-5">
          {tracks.flatMap((track: any) => track.counterarguments || []).map((item: string, index: number) => (
            <li key={`${item}-${index}`}>{item}</li>
          ))}
        </ul>
      </section>

      {run.disagreement_summary ? (
        <section>
          <h3 className="font-serif text-2xl">Disagreement summary</h3>
          <p className="mt-3 max-w-3xl leading-relaxed">{run.disagreement_summary}</p>
        </section>
      ) : null}

      <section>
        <h3 className="font-serif text-2xl">Independent tracks</h3>
        <div className="mt-4 grid gap-4 md:grid-cols-3">
          {tracks.map((track: any) => (
            <button
              key={track.id}
              className="border border-rule p-4 text-left"
              onClick={() => setOpenTrack(openTrack === track.id ? null : track.id)}
            >
              <p className="font-mono text-xs uppercase tracking-[0.2em]">{track.track_type}</p>
              <p className="mt-2 font-serif text-4xl">{pct(track.probability)}</p>
              <p className="mt-3 text-sm leading-relaxed">{track.reasoning_summary}</p>
              {openTrack === track.id ? (
                <p className="mt-3 text-xs text-ink/70">
                  Resolver risk {track.resolver_risk} · quality {track.evidence_quality}
                </p>
              ) : null}
            </button>
          ))}
        </div>
      </section>

      <details className="border border-rule p-4">
        <summary className="cursor-pointer font-serif text-2xl">How this was calculated</summary>
        <pre className="mt-3 overflow-x-auto bg-white/70 p-4 text-sm">{JSON.stringify(aggregation, null, 2)}</pre>
      </details>

      <section>
        <h3 className="font-serif text-2xl">Evidence ledger</h3>
        <table className="mt-3 w-full text-left text-sm">
          <thead>
            <tr className="border-b border-rule">
              <th className="py-2">Source</th>
              <th>Class</th>
              <th>Eligible</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {evidence.map((item: any) => (
              <tr key={item.id} className="border-b border-rule/70">
                <td className="py-2">
                  <a href={item.url} className="underline decoration-copper" target="_blank" rel="noreferrer">
                    {item.title || item.url}
                  </a>
                </td>
                <td>{item.source_class}</td>
                <td>{item.as_of_eligible ? "yes" : "no"}</td>
                <td>{item.rejected ? item.rejection_reason : "kept"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h3 className="font-serif text-2xl">Resolution contract</h3>
        <pre className="mt-3 overflow-x-auto border border-rule bg-white/70 p-4 text-sm">
{JSON.stringify(data.contract, null, 2)}
        </pre>
      </section>

      <section>
        <h3 className="font-serif text-2xl">Version timeline</h3>
        <ol className="mt-3 space-y-2">
          {versions.map((version: any, index: number) => (
            <li key={version.id} className="border-b border-rule py-2 text-sm">
              Version {versions.length - index}: {pct(version.ensemble_probability)} · {version.trigger_event} ·{" "}
              {version.created_at}
            </li>
          ))}
        </ol>
      </section>

      <section>
        <h3 className="font-serif text-2xl">Watchers</h3>
        <p className="mt-2 text-sm text-ink/70">Changes mark the forecast stale. Reruns require user action.</p>
        <ul className="mt-3 space-y-2 text-sm">
          {(data.watches || []).map((watch: any) => (
            <li key={watch.id}>
              {watch.endpoint_url} ({watch.endpoint_type}) · last value {watch.previous_value || "—"}
            </li>
          ))}
        </ul>
        <form onSubmit={addWatch} className="mt-4 flex flex-wrap gap-2">
          <label className="sr-only" htmlFor="watch-url">
            Watch URL
          </label>
          <input
            id="watch-url"
            className="min-w-80 flex-1 border border-rule p-2"
            value={watchUrl}
            onChange={(event) => setWatchUrl(event.target.value)}
          />
          <button className="border border-ink px-3 py-2" type="submit">
            Attach watch
          </button>
        </form>
      </section>

      <section>
        <h3 className="font-serif text-2xl">Run audit log</h3>
        <dl className="mt-3 grid gap-2 text-sm md:grid-cols-2">
          <div>
            <dt className="text-ink/60">Mode</dt>
            <dd>{run.mode || "—"}</dd>
          </div>
          <div>
            <dt className="text-ink/60">as_of</dt>
            <dd>{run.as_of || "live clock"}</dd>
          </div>
          <div>
            <dt className="text-ink/60">Prompt versions</dt>
            <dd className="font-mono text-xs">{run.prompt_versions_json || "—"}</dd>
          </div>
          <div>
            <dt className="text-ink/60">Error</dt>
            <dd>{run.error_message || "none"}</dd>
          </div>
        </dl>
      </section>
    </article>
  );
}
