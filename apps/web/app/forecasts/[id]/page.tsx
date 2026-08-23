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
  const [watchUrl, setWatchUrl] = useState("https://example.com/status.json");

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
        endpoint_type: watchUrl.includes("json") ? "json" : "html",
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
  const budget = run.budget || {};
  const costKind = budget.cost_is_estimated ? "estimated" : "provider-reported";
  const modeLabel = String(context.effective_mode || run.mode || "demo").toUpperCase();
  const missingTracks = (aggregation.missing_track_types || []).length > 0;
  const missingNodes = (aggregation.missing_node_ids || []).length > 0;
  const graphAggregation = aggregation.method === "dependency_discounted_weighted_mean_v1";
  const rejectedEvidence = evidence.filter((item: any) => item.rejected);
  const v1Report = data.v1_report || run.v1_report || null;
  const reportNodes = v1Report?.nodes || [];
  const calculationTrace = v1Report?.calculation?.trace || [];

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
            {Number(run.cost_usd || 0).toFixed(4)} ({costKind}) · {run.latency_ms || 0} ms · {run.mode || "demo"}
          </p>
        </div>
        <div className="border border-rule bg-white/60 p-5">
          <p className="font-mono text-xs uppercase tracking-[0.2em]">Ensemble estimate</p>
          <p className="font-serif text-6xl leading-none">{pct(data.latest_probability)}</p>
          <p className="mt-3 text-sm">
            {graphAggregation
              ? "Deterministic dependency-aware weighted mean. Not a calibrated probability."
              : "Coded logit mean with shrinkage. Not a calibrated probability."}
          </p>
          <p className="mt-2 text-sm">
            {graphAggregation ? "Node spread" : "Track spread"}:{" "}
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
            <dt className="text-ink/60">Cost ceiling / used</dt>
            <dd>
              ${Number(context.effective_max_cost_usd ?? 0).toFixed(2)} / ${Number(run.cost_usd || 0).toFixed(4)}{" "}
              ({costKind})
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
      <section className="border border-rule bg-white/70 p-4" aria-label="Provider usage audit">
        <h3 className="font-serif text-2xl">Provider usage audit</h3>
        <p className="mt-2 text-sm text-ink/70">
          Lifetime totals include successful calls, failed attempts, provider retries, and search charges.
          The reported <code>cost_usd</code> field is total lifetime cost.
        </p>
        <dl className="mt-3 grid gap-2 text-sm md:grid-cols-3">
          <div>
            <dt className="text-ink/60">Model cost</dt>
            <dd>${Number(run.model_cost_usd || 0).toFixed(4)}</dd>
          </div>
          <div>
            <dt className="text-ink/60">Search cost</dt>
            <dd>${Number(run.search_cost_usd || 0).toFixed(4)}</dd>
          </div>
          <div>
            <dt className="text-ink/60">Failed-attempt cost</dt>
            <dd>${Number(run.failed_attempt_cost_usd || 0).toFixed(4)}</dd>
          </div>
          <div>
            <dt className="text-ink/60">Total cost</dt>
            <dd>
              ${Number(run.total_cost_usd || run.cost_usd || 0).toFixed(4)} ({costKind})
            </dd>
          </div>
          <div>
            <dt className="text-ink/60">Attempts / provider requests</dt>
            <dd>
              {run.run_attempt_count || (run.run_attempts || []).length} /{" "}
              {run.provider_request_count || (run.provider_call_ledger || []).length}
            </dd>
          </div>
        </dl>
        {(run.provider_call_ledger || []).length ? (
          <table className="mt-4 w-full text-left text-sm">
            <thead>
              <tr className="border-b border-rule">
                <th className="py-2">Stage</th>
                <th>Provider</th>
                <th>Physical</th>
                <th>Status</th>
                <th>Cost</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {(run.provider_call_ledger || []).map((entry: any) => (
                <tr key={entry.id} className="border-b border-rule/70">
                  <td className="py-2">{entry.stage}</td>
                  <td>
                    {entry.provider_type} / {entry.provider}
                  </td>
                  <td>{entry.physical_attempt_number}</td>
                  <td>{entry.status}</td>
                  <td>${Number(entry.actual_cost_usd ?? entry.reserved_cost_usd ?? 0).toFixed(4)}</td>
                  <td>{entry.cost_source || "estimated"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="mt-3 text-sm text-ink/70">No provider-call ledger rows for this run yet.</p>
        )}
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
      {missingNodes ? (
        <p className="border border-copper px-4 py-3 text-sm">
          Missing graph nodes: {aggregation.missing_node_ids.join(", ")}. Available node weights were renormalized.
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

      {tracks.length ? (
        <>
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
        </>
      ) : null}

      {run.disagreement_summary ? (
        <section>
          <h3 className="font-serif text-2xl">Disagreement summary</h3>
          <p className="mt-3 max-w-3xl leading-relaxed">{run.disagreement_summary}</p>
        </section>
      ) : null}

      {v1Report && graphAggregation ? (
        <section className="space-y-5" aria-label="V1 Forecast Graph report">
          <div>
            <h3 className="font-serif text-2xl">Forecast Graph report</h3>
            <p className="mt-2 text-sm text-ink/70">
              {v1Report.graph?.node_count || reportNodes.length} research nodes · evidence coverage {" "}
              {v1Report.evidence_coverage?.covered_units || 0}/{v1Report.evidence_coverage?.total_units || 0} · final {" "}
              {pct(v1Report.final_probability)}
            </p>
          </div>
          <p className="mt-2 text-sm text-ink/70">
            Final calculation: {aggregation.formula || "Weighted node contributions are summed deterministically."}
          </p>
          <div className="grid gap-4">
            {reportNodes.map((node: any) => (
              <article key={node.id} className="border border-rule bg-white/60 p-5">
                <div className="grid gap-4 md:grid-cols-[1fr_auto]">
                  <div>
                    <p className="font-mono text-xs uppercase tracking-[0.16em] text-copper">
                      {String(node.node_type || "node").replace("_", " ")}
                    </p>
                    <h4 className="mt-2 font-serif text-xl">{node.question}</h4>
                    <p className="mt-3 text-sm leading-relaxed">{node.reasoning || "No node reasoning was produced."}</p>
                    <p className="mt-2 font-mono text-xs text-ink/60">Model: {node.model_used || "not recorded"}</p>
                    <div className="mt-3 text-sm">
                      <p className="font-medium">Uncertainty</p>
                      {node.uncertainty_notes?.length ? (
                        <ul className="mt-1 list-disc space-y-1 pl-5 text-ink/70">
                          {node.uncertainty_notes.map((note: string) => <li key={note}>{note}</li>)}
                        </ul>
                      ) : (
                        <p className="mt-1 text-ink/60">No uncertainty notes were produced.</p>
                      )}
                    </div>
                  </div>
                  <dl className="grid min-w-52 grid-cols-2 gap-x-5 gap-y-2 text-sm">
                    <div>
                      <dt className="text-ink/60">Probability</dt>
                      <dd className="font-serif text-2xl">{pct(node.probability)}</dd>
                    </div>
                    <div>
                      <dt className="text-ink/60">Confidence</dt>
                      <dd>{pct(node.confidence)}</dd>
                    </div>
                    <div>
                      <dt className="text-ink/60">Weight</dt>
                      <dd>{Number(node.normalized_weight || 0).toFixed(4)}</dd>
                    </div>
                    <div>
                      <dt className="text-ink/60">Contribution</dt>
                      <dd>{Number(node.probability_contribution || 0).toFixed(4)}</dd>
                    </div>
                  </dl>
                </div>
                <div className="mt-5 grid gap-5 md:grid-cols-2">
                  {[
                    ["Supporting evidence", node.supporting_evidence || []],
                    ["Opposing evidence", node.opposing_evidence || []]
                  ].map(([label, claims]: any) => (
                    <div key={label}>
                      <h5 className="font-medium">{label}</h5>
                      {claims.length ? (
                        <ul className="mt-2 space-y-3 text-sm">
                          {claims.map((claim: any) => (
                            <li key={claim.id} className="border-l-2 border-copper pl-3">
                              <p>{claim.claim}</p>
                              <p className="mt-1 text-xs text-ink/60">Excerpt: “{claim.excerpt}”</p>
                              <a
                                className="mt-1 inline-block text-xs underline decoration-copper"
                                href={claim.source_url}
                                target="_blank"
                                rel="noreferrer"
                              >
                                {claim.source_title || claim.publisher || claim.source_url}
                              </a>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-2 text-sm text-ink/60">No {String(label).toLowerCase()} cited.</p>
                      )}
                    </div>
                  ))}
                </div>
                {node.uncited_evidence?.length ? (
                  <details className="mt-4 text-sm">
                    <summary className="cursor-pointer">
                      {node.uncited_evidence.length} additional provenance-backed claim
                      {node.uncited_evidence.length === 1 ? "" : "s"} not selected by this node forecast
                    </summary>
                    <ul className="mt-2 list-disc space-y-1 pl-5">
                      {node.uncited_evidence.map((claim: any) => <li key={claim.id}>{claim.claim}</li>)}
                    </ul>
                  </details>
                ) : null}
              </article>
            ))}
          </div>
          <p className="mt-3 text-sm">
            Sum of contributions {Number(aggregation.unbounded_probability).toFixed(6)} → final probability{" "}
            {Number(aggregation.final_probability).toFixed(6)}
          </p>
          <div>
            <h4 className="font-serif text-xl">Calculation trace</h4>
            <table className="mt-3 w-full text-left text-sm">
              <thead>
                <tr className="border-b border-rule">
                  <th className="py-2">Step</th>
                  <th>Node</th>
                  <th>Normalized weight</th>
                  <th>Probability</th>
                  <th>Contribution / result</th>
                </tr>
              </thead>
              <tbody>
                {calculationTrace.map((trace: any, index: number) => {
                  const traceNode = reportNodes.find((node: any) => node.id === trace.node_id);
                  return (
                    <tr key={`${trace.step}-${trace.node_id || index}`} className="border-b border-rule/70">
                      <td className="py-2">{String(trace.step || "").replace("_", " ")}</td>
                      <td>{traceNode?.question || trace.node_id || "—"}</td>
                      <td>{trace.normalized_weight == null ? "—" : Number(trace.normalized_weight).toFixed(4)}</td>
                      <td>{trace.probability == null ? "—" : Number(trace.probability).toFixed(4)}</td>
                      <td>
                        {trace.contribution == null
                          ? trace.final_probability == null
                            ? trace.normalization_denominator == null
                              ? "—"
                              : `denominator ${Number(trace.normalization_denominator).toFixed(4)}`
                            : `final ${Number(trace.final_probability).toFixed(4)}`
                          : Number(trace.contribution).toFixed(4)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {tracks.length ? (
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
      ) : null}

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
          <div>
            <dt className="text-ink/60">Reservations</dt>
            <dd>
              {(budget.reservations || []).length} model calls · {costKind} cost
            </dd>
          </div>
        </dl>
      </section>
    </article>
  );
}
