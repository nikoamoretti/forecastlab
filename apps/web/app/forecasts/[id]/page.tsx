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
  const [evidenceUrl, setEvidenceUrl] = useState("");
  const [evidenceNote, setEvidenceNote] = useState("");
  const [evidenceNodeId, setEvidenceNodeId] = useState("");
  const [evidenceMessage, setEvidenceMessage] = useState<string | null>(null);
  const [evidenceSubmitting, setEvidenceSubmitting] = useState(false);

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

  async function addEvidenceUrl(event: React.FormEvent) {
    event.preventDefault();
    setEvidenceSubmitting(true);
    setEvidenceMessage(null);
    try {
      const requestedMode = data?.requested_mode || data?.latest_run?.mode || "live";
      if (requestedMode !== "live" && requestedMode !== "backtest") {
        throw new Error("Manual external evidence is available for live and backtest questions; demo runs remain fixture-only.");
      }
      const mode = requestedMode;
      const result = await api<any>(`/api/questions/${id}/evidence-urls`, {
        method: "POST",
        body: JSON.stringify({
          url: evidenceUrl,
          note: evidenceNote || undefined,
          intended_use: evidenceNodeId ? "forecast_node" : "general_question_evidence",
          forecast_node_id: evidenceNodeId || undefined,
          mode,
          as_of: mode === "backtest" ? data?.requested_as_of || data?.latest_run?.as_of : undefined
        })
      });
      setEvidenceMessage(
        result.accepted
          ? "Evidence accepted. A fresh explicit rerun is required before ordinary claim extraction can use it."
          : `Evidence rejected: ${result.rejection_reason || "document rejected"}`
      );
      setEvidenceUrl("");
      setEvidenceNote("");
      await load();
    } catch (err) {
      setEvidenceMessage(err instanceof Error ? err.message : "Evidence intake failed");
    } finally {
      setEvidenceSubmitting(false);
    }
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
  const rejectedEvidence = evidence.filter((item: any) => item.rejected);
  const v1Report = data.v1_report || run.v1_report || null;
  const reportNodes = v1Report?.nodes || [];
  const reportCalculation = v1Report?.calculation || {};
  const calculationTrace = v1Report?.calculation?.trace || [];
  const aggregationMethod = reportCalculation.method || aggregation.method;
  const directModelProbability = aggregationMethod === "direct_model_probability_v1";
  const graphAggregation = [
    "dependency_discounted_weighted_mean_v1",
    "importance_weighted_log_odds_v1",
    "relationship_mass_conserving_log_odds_v1"
  ].includes(aggregationMethod);
  const relationshipAggregation = aggregationMethod === "relationship_mass_conserving_log_odds_v1";
  const logOddsAggregation = [
    "importance_weighted_log_odds_v1",
    "relationship_mass_conserving_log_odds_v1"
  ].includes(aggregationMethod);
  const relationshipAudit = reportCalculation.relationship_aggregation || null;
  const graphExecutionFailed = v1Report?.execution_status === "failed";
  const evidenceSufficiency = v1Report?.evidence_sufficiency || null;
  const materialCompleteness = v1Report?.material_node_completeness || null;
  const materialPlanAudit = materialCompleteness?.plan_audit || null;
  const materialAssessment = materialCompleteness?.execution_assessment || null;
  const materialGateFailed = materialPlanAudit?.status === "failed" || materialAssessment?.status === "failed";
  const scenarioSynthesis = v1Report?.scenario_synthesis || null;
  const manualEvidence = data.manual_evidence_urls || [];
  const manualEvidenceMode = data?.requested_mode || run.mode || "demo";
  const manualEvidenceModeSupported = manualEvidenceMode === "live" || manualEvidenceMode === "backtest";
  const displayedProbability = graphExecutionFailed
    ? null
    : v1Report?.final_probability ?? data.latest_probability;
  const finalCalculationStep = [...calculationTrace].reverse().find((item: any) => item.step === "final") || {};

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
          <p className="font-mono text-xs uppercase tracking-[0.2em]">
            {directModelProbability ? "Single-model estimate" : "Ensemble estimate"}
          </p>
          <p className="font-serif text-6xl leading-none">
            {pct(displayedProbability)}
          </p>
          <p className="mt-3 text-sm">
            {graphExecutionFailed
              ? materialGateFailed
                ? "No private-V1 probability was produced because a higher-importance graph uncertainty was omitted while lower-importance nodes were retained."
                : evidenceSufficiency?.status === "failed"
                ? "No private-V1 probability was produced because deterministic evidence sufficiency was not met."
                : scenarioSynthesis?.status === "failed"
                ? "No private-V1 probability was produced because grounded scenario synthesis did not pass deterministic validation."
                : "No probability was produced because graph execution was incomplete."
              : directModelProbability
              ? "Direct structured model probability. No probability aggregation."
              : logOddsAggregation
              ? "Deterministic importance-weighted log odds. Not a calibrated probability."
              : graphAggregation
              ? "Deterministic dependency-aware weighted mean. Not a calibrated probability."
              : "Coded logit mean with shrinkage. Not a calibrated probability."}
          </p>
          {!logOddsAggregation && !directModelProbability ? (
            <p className="mt-2 text-sm">
              {graphAggregation ? "Node spread" : "Track spread"}:{" "}
              {aggregation.track_spread == null ? "—" : Number(aggregation.track_spread).toFixed(3)}
            </p>
          ) : null}
          {evidenceSufficiency ? (
            <div className="border border-rule bg-white/60 p-4 text-sm" aria-label="Evidence Sufficiency">
              <h4 className="font-serif text-xl">Evidence Sufficiency</h4>
              <p className="mt-2">
                {evidenceSufficiency.status} · policy {evidenceSufficiency.policy_version} · assessment {evidenceSufficiency.id}
              </p>
              <p className="mt-1 font-mono text-xs text-ink/60">
                selected {evidenceSufficiency.selected_coverage_numerator}/{evidenceSufficiency.selected_coverage_denominator}
                {" · "}graph {evidenceSufficiency.graph_coverage_numerator}/{evidenceSufficiency.graph_coverage_denominator}
                {" · "}weight {Number(evidenceSufficiency.graph_weight_coverage || 0).toFixed(3)}
                {" · "}hosts {evidenceSufficiency.distinct_host_count}
                {" · "}primary nodes {evidenceSufficiency.primary_node_count}
                {" · "}structured/fallback {evidenceSufficiency.structured_claim_count}/{evidenceSufficiency.fallback_claim_count}
              </p>
              <p className="mt-1 break-all font-mono text-xs text-ink/60">
                input {evidenceSufficiency.assessment_input_hash}
              </p>
              {evidenceSufficiency.reasons?.length ? (
                <ul className="mt-3 list-disc space-y-1 pl-5 text-red-800">
                  {evidenceSufficiency.reasons.map((reason: string) => <li key={reason}>{reason}</li>)}
                </ul>
              ) : null}
              <div className="mt-3 grid gap-2 md:grid-cols-2">
                {(evidenceSufficiency.per_node || []).map((item: any) => (
                  <div key={item.node_id} className="border border-rule/70 p-2">
                    <p className="font-medium">{item.node_id}: {String(item.grade).replaceAll("_", " ")}</p>
                    <p className="text-xs text-ink/60">
                      {item.critical ? "critical" : "noncritical"} · {item.passed ? "passed" : "failed"}
                      {item.reasons?.length ? ` · ${item.reasons.join(", ")}` : ""}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {materialPlanAudit || materialAssessment ? (
            <div className="mt-4 border border-rule bg-white/60 p-4 text-sm" aria-label="Material Node Completeness">
              <h4 className="font-serif text-xl">Material Node Completeness</h4>
              {materialPlanAudit ? (
                <div className="mt-2">
                  <p>
                    Plan {materialPlanAudit.status} · policy {materialPlanAudit.policy_version}
                  </p>
                  <p className="mt-1 font-mono text-xs text-ink/60">
                    selected frontier {materialPlanAudit.selected_frontier_weight ?? "—"}
                    {" · "}maximum skipped {materialPlanAudit.maximum_skipped_weight ?? "—"}
                    {" · "}higher skipped {(materialPlanAudit.higher_importance_skipped_node_ids || []).join(", ") || "none"}
                    {" · "}frontier ties {(materialPlanAudit.frontier_tie_skipped_node_ids || []).join(", ") || "none"}
                  </p>
                  {materialPlanAudit.reasons?.length ? (
                    <p className="mt-1 text-red-800">{materialPlanAudit.reasons.join(", ")}</p>
                  ) : null}
                </div>
              ) : null}
              {materialAssessment ? (
                <div className="mt-3 border-t border-rule pt-3">
                  <p>
                    Execution {materialAssessment.status} · assessment {materialAssessment.id}
                  </p>
                  <p className="mt-1 font-mono text-xs text-ink/60">
                    included frontier {materialAssessment.included_frontier_weight ?? "—"}
                    {" · "}maximum excluded {materialAssessment.maximum_excluded_weight ?? "—"}
                    {" · "}included/excluded weight {materialAssessment.included_graph_weight}/{materialAssessment.excluded_graph_weight}
                    {" · "}higher excluded {(materialAssessment.higher_importance_excluded_node_ids || []).join(", ") || "none"}
                  </p>
                  <p className="mt-1 break-all font-mono text-xs text-ink/60">
                    input {materialAssessment.assessment_input_hash}
                  </p>
                  {materialAssessment.reasons?.length ? (
                    <p className="mt-1 text-red-800">{materialAssessment.reasons.join(", ")}</p>
                  ) : null}
                  {materialAssessment.warnings?.length ? (
                    <p className="mt-1 text-amber-800">{materialAssessment.warnings.join(", ")}</p>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}
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

      {v1Report && (graphAggregation || graphExecutionFailed) ? (
        <section className="space-y-5" aria-label="V1 Forecast Graph report">
          <div>
            <h3 className="font-serif text-2xl">Forecast Graph report</h3>
            <p className="mt-2 text-sm text-ink/70">
              profile {v1Report.profile_id || run.profile_id || "unknown"} · {" "}
              {v1Report.graph?.node_count || reportNodes.length} research nodes · evidence coverage {" "}
              {v1Report.evidence_coverage?.covered_units || 0}/{v1Report.evidence_coverage?.total_units || 0} · final {" "}
              {pct(v1Report.final_probability)}
            </p>
          </div>
          {v1Report.graph?.generation_audit || v1Report.graph_resolution ? (
            <div className="border border-rule bg-white/60 p-4 text-sm" aria-label="Graph generation audit">
              <h4 className="font-serif text-xl">Graph generation audit</h4>
              <p className="mt-2 text-ink/70">
                {v1Report.graph_resolution?.status || "legacy graph"} · model request for this run {" "}
                {v1Report.graph_resolution?.model_request_issued ? "yes" : "no"} · {" "}
                {v1Report.graph?.generation_audit?.provider || "provider unavailable"} / {" "}
                {v1Report.graph?.generation_audit?.model || "model unavailable"}
              </p>
              <p className="mt-1 font-mono text-xs text-ink/60">
                schema {v1Report.graph?.generation_audit?.schema_name || "unavailable"} · completion/visible caps {" "}
                {v1Report.graph?.generation_audit?.requested_max_completion_tokens ?? v1Report.graph?.generation_audit?.requested_max_output_tokens ?? "unavailable"} / {" "}
                {v1Report.graph?.generation_audit?.requested_max_visible_output_tokens ?? v1Report.graph?.generation_audit?.requested_max_output_tokens ?? "unavailable"} · finish {" "}
                {v1Report.graph?.generation_audit?.finish_reason || "unavailable"} · request {" "}
                {v1Report.graph?.generation_audit?.provider_request_id || "unavailable"}
              </p>
              <p className="mt-1 font-mono text-xs text-ink/60">
                refusal {String(v1Report.graph?.generation_audit?.refusal_present ?? false)} · tokens completion/reasoning/visible {" "}
                {v1Report.graph?.generation_audit?.completion_tokens ?? "unavailable"} / {" "}
                {v1Report.graph?.generation_audit?.reasoning_tokens ?? "unavailable"} / {" "}
                {v1Report.graph?.generation_audit?.visible_output_tokens ?? "unavailable"} · characters {" "}
                {v1Report.graph?.generation_audit?.content_character_count ?? "unavailable"}
              </p>
              <p className="mt-1 font-mono text-xs text-ink/60">
                reasoning/verbosity {v1Report.graph?.generation_audit?.reasoning_effort || "unavailable"} / {" "}
                {v1Report.graph?.generation_audit?.verbosity || "unavailable"} · token split {" "}
                {String(v1Report.graph?.generation_audit?.token_split_available ?? false)} ({" "}
                {v1Report.graph?.generation_audit?.token_split_interpretation || "unavailable"})
              </p>
              <p className="mt-1 font-mono text-xs text-ink/60">
                parsed/schema/domain {String(v1Report.graph?.generation_audit?.json_parsing_succeeded)} / {" "}
                {String(v1Report.graph?.generation_audit?.schema_validation_succeeded)} / {" "}
                {String(v1Report.graph?.generation_audit?.domain_validation_succeeded)} · prompt {" "}
                {v1Report.graph?.generation_audit?.prompt_version || "unavailable"} · generated {" "}
                {v1Report.graph?.generation_audit?.generated_at || "unavailable"}
              </p>
              {v1Report.graph?.generation_audit?.errors?.length ||
              v1Report.graph?.generation_audit?.schema_validation_errors?.length ||
              v1Report.graph?.generation_audit?.domain_validation_errors?.length ? (
                <pre className="mt-2 overflow-x-auto text-xs">
{JSON.stringify({
  errors: v1Report.graph?.generation_audit?.errors || [],
  schema_errors: v1Report.graph?.generation_audit?.schema_validation_errors || [],
  domain_errors: v1Report.graph?.generation_audit?.domain_validation_errors || []
}, null, 2)}
                </pre>
              ) : null}
            </div>
          ) : null}
          <p className="mt-2 text-sm text-ink/70">
            {graphExecutionFailed
              ? "Final calculation unavailable because the graph run was incomplete."
              : `Final calculation: ${
                  reportCalculation.formula ||
                  aggregation.formula ||
                  "Weighted node contributions are summed deterministically."
                }`}
          </p>
          {scenarioSynthesis ? (
            <div className="border border-rule bg-white/60 p-4 text-sm" aria-label="Scenario Synthesis">
              <h4 className="font-serif text-xl">Scenario Synthesis</h4>
              <p className="mt-2 text-ink/70">
                Grounded explanatory pathways have no assigned probabilities and do not alter the deterministic calculation.
              </p>
              <p className="mt-2 font-mono text-xs text-ink/60">
                {scenarioSynthesis.status} · {scenarioSynthesis.policy_version} · artifact {scenarioSynthesis.id}
              </p>
              <p className="mt-1 break-all font-mono text-xs text-ink/60">
                input {scenarioSynthesis.input_hash} · output {scenarioSynthesis.output_hash || "unavailable"}
              </p>
              <p className="mt-1 font-mono text-xs text-ink/60">
                provider/model/prompt {scenarioSynthesis.provider} / {scenarioSynthesis.model} / {scenarioSynthesis.prompt_version}
              </p>
              <div className="mt-4 grid gap-3 md:grid-cols-3">
                {(scenarioSynthesis.scenarios || []).map((pathway: any) => (
                  <article key={pathway.id || pathway.local_id} className="border border-rule p-3">
                    <p className="font-mono text-xs uppercase tracking-[0.14em] text-copper">
                      {String(pathway.kind || "pathway").replaceAll("_", " ")}
                    </p>
                    <h5 className="mt-1 font-serif text-lg">{pathway.title}</h5>
                    <p className="mt-2 leading-relaxed">{pathway.summary}</p>
                    <p className="mt-2 font-mono text-xs text-ink/60">
                      nodes {(pathway.node_ids || []).join(", ")} · claims {(pathway.claim_ids || []).join(", ")}
                    </p>
                    <details className="mt-2">
                      <summary className="cursor-pointer">Mechanisms and uncertainties</summary>
                      <pre className="mt-2 overflow-x-auto text-xs">
{JSON.stringify({
  mechanisms: pathway.mechanisms || [],
  triggers: pathway.triggers || [],
  invalidators: pathway.invalidators || [],
  unresolved_uncertainties: pathway.unresolved_uncertainties || []
}, null, 2)}
                      </pre>
                    </details>
                  </article>
                ))}
              </div>
              <details className="mt-3">
                <summary className="cursor-pointer">Grounding and sanitized diagnostics</summary>
                <pre className="mt-2 overflow-x-auto text-xs">
{JSON.stringify({
  coverage_audit: scenarioSynthesis.coverage_audit || {},
  failure_reasons: scenarioSynthesis.failure_reasons || [],
  diagnostics: scenarioSynthesis.diagnostics || {}
}, null, 2)}
                </pre>
              </details>
            </div>
          ) : null}
          {relationshipAggregation && relationshipAudit ? (
            <div className="border border-rule bg-white/60 p-4 text-sm" aria-label="Relationship-aware aggregation">
              <h4 className="font-serif text-xl">Relationship-aware aggregation</h4>
              <p className="mt-2 text-ink/70">{relationshipAudit.heuristic_notice}</p>
              <p className="mt-2 text-ink/70">{relationshipAudit.neutral_residual_notice}</p>
              <dl className="mt-3 grid gap-2 md:grid-cols-3">
                <div>
                  <dt className="text-ink/60">Total raw graph weight</dt>
                  <dd>{relationshipAudit.graph_mass?.total_graph_raw_weight ?? "unavailable"}</dd>
                </div>
                <div>
                  <dt className="text-ink/60">Effective included weight</dt>
                  <dd>{relationshipAudit.graph_mass?.effective_included_weight ?? "unavailable"}</dd>
                </div>
                <div>
                  <dt className="text-ink/60">Neutral residual weight / fraction</dt>
                  <dd>
                    {relationshipAudit.graph_mass?.neutral_residual_weight ?? "unavailable"} / {" "}
                    {relationshipAudit.graph_mass?.neutral_residual_fraction ?? "unavailable"}
                  </dd>
                </div>
                <div>
                  <dt className="text-ink/60">Mass conserved</dt>
                  <dd>{String(relationshipAudit.graph_mass?.conservation_check ?? false)}</dd>
                </div>
                <div>
                  <dt className="text-ink/60">Allocation hash</dt>
                  <dd className="break-all font-mono text-xs">{relationshipAudit.graph_mass?.allocation_hash || "unavailable"}</dd>
                </div>
              </dl>
              <details className="mt-3">
                <summary className="cursor-pointer">Direct allocations and excluded mass</summary>
                <pre className="mt-2 overflow-x-auto text-xs">
{JSON.stringify({
  source_allocations: relationshipAudit.source_allocations || [],
  excluded_nodes: relationshipAudit.excluded_nodes || []
}, null, 2)}
                </pre>
              </details>
            </div>
          ) : null}
          <div className="grid gap-4">
            {reportNodes.map((node: any) => (
              <article key={node.id} className="border border-rule bg-white/60 p-5">
                <div className="grid gap-4 md:grid-cols-[1fr_auto]">
                  <div>
                    <p className="font-mono text-xs uppercase tracking-[0.16em] text-copper">
                      {String(node.node_type || "node").replace("_", " ")}
                    </p>
                    <h4 className="mt-2 font-serif text-xl">{node.question}</h4>
                    {node.failures?.length ? (
                      <ul className="mt-3 space-y-1 text-sm text-red-800">
                        {node.failures.map((failure: any) => (
                          <li key={failure.id || `${failure.stage}-${failure.error_code}`}>
                            {failure.stage}: {failure.error_code}. {failure.error_message}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    <p className="mt-3 text-sm leading-relaxed">{node.reasoning || "No node reasoning was produced."}</p>
                    <p className="mt-2 font-mono text-xs text-ink/60">Model: {node.model_used || "not recorded"}</p>
                    <p className="mt-1 font-mono text-xs text-ink/60">
                      importance {node.canonical_importance_weight ?? node.importance_weight}
                      {" · "}{node.material_selected ? "selected" : "skipped"}
                      {" · "}{node.material_included ? "included" : `excluded (${node.material_exclusion_origin || "unknown"})`}
                      {" · "}{String(node.material_frontier_position || "frontier unavailable").replaceAll("_", " ")}
                    </p>
                    {relationshipAggregation && node.material_included ? (
                      <p className="mt-1 font-mono text-xs text-ink/60">
                        effective/self/received {node.effective_importance_weight ?? "unavailable"} / {" "}
                        {node.self_allocated_weight ?? "unavailable"} / {" "}
                        {node.relationship_received_weight ?? "unavailable"} · sources {" "}
                        {(node.relationship_source_node_ids || []).join(", ") || "none"}
                      </p>
                    ) : null}
                    {node.missing_parent_relationships?.length || node.missing_dependency_relationships?.length ? (
                      <p className="mt-1 text-xs text-amber-800">
                        Relationship warning: excluded parents {node.missing_parent_relationships?.length || 0}; excluded dependencies {node.missing_dependency_relationships?.length || 0}
                      </p>
                    ) : null}
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
                      <dt className="text-ink/60">
                        {logOddsAggregation ? "Log-odds contribution" : "Contribution"}
                      </dt>
                      <dd>
                        {Number(
                          node.weighted_log_odds_contribution ?? node.probability_contribution ?? 0
                        ).toFixed(4)}
                      </dd>
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
                              <p className="mt-1 text-xs font-medium text-ink/70">
                                {claim.temporal_quality_label || "Temporal provenance unavailable"}
                              </p>
                              <p className="mt-1 text-xs text-ink/60">
                                Published {claim.publication_date ? new Date(claim.publication_date).toLocaleDateString() : "unavailable"}
                                {" · "}Available {claim.source_available_at ? new Date(claim.source_available_at).toLocaleDateString() : "unavailable"}
                                {" · "}Retrieved {claim.retrieval_date ? new Date(claim.retrieval_date).toLocaleDateString() : "unavailable"}
                                {" · "}Basis {String(claim.temporal_basis || "unavailable").replaceAll("_", " ")}
                                {" · "}Publication verified {claim.publication_date_verified ? "yes" : "no"}
                                {" · "}Cutoff verified {claim.cutoff_verified ? "yes" : "no"}
                                {" · "}Source class {claim.source_class || "unknown legacy"}
                                {" · "}Extraction {String(claim.extraction_method || "unknown_legacy").replaceAll("_", " ")}
                                {" · "}Host {claim.source_host || "unavailable"}
                              </p>
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
          {v1Report.final_probability != null ? (
            <p className="mt-3 text-sm">
              {logOddsAggregation ? (
              <>
                Combined log odds {Number(finalCalculationStep.combined_log_odds).toFixed(6)} → final probability{" "}
                {Number(v1Report.final_probability).toFixed(6)}
              </>
              ) : (
              <>
                Sum of contributions {Number(aggregation.unbounded_probability).toFixed(6)} → final probability{" "}
                {Number(aggregation.final_probability).toFixed(6)}
              </>
              )}
            </p>
          ) : null}
          {calculationTrace.length ? <div>
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
                      <td>
                        {trace.normalized_effective_weight == null && trace.normalized_weight == null
                          ? "—"
                          : Number(trace.normalized_effective_weight ?? trace.normalized_weight).toFixed(4)}
                      </td>
                      <td>
                        {trace.input_probability == null && trace.probability == null
                          ? "—"
                          : Number(trace.input_probability ?? trace.probability).toFixed(4)}
                      </td>
                      <td>
                        {trace.weighted_log_odds_contribution == null && trace.contribution == null
                          ? trace.final_probability == null
                            ? trace.normalization_denominator == null &&
                              trace.total_importance_weight == null &&
                              trace.total_graph_raw_weight == null
                              ? "—"
                              : `total weight ${Number(
                                  trace.total_graph_raw_weight ??
                                  trace.total_importance_weight ??
                                  trace.normalization_denominator
                                ).toFixed(4)}`
                            : `final ${Number(trace.final_probability).toFixed(4)}`
                          : Number(
                              trace.weighted_log_odds_contribution ?? trace.contribution
                            ).toFixed(4)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div> : null}
          <div>
            <h4 className="font-serif text-xl">Final answer</h4>
            <p className="mt-2 text-sm">
              {v1Report.final_answer?.statement || "No final answer was produced."}
            </p>
          </div>
        </section>
      ) : null}

      {tracks.length ? (
        <section>
          <h3 className="font-serif text-2xl">
            {directModelProbability ? "Single-model forecast" : "Independent tracks"}
          </h3>
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
                {directModelProbability && track.unresolved_uncertainties?.length ? (
                  <div className="mt-3 text-xs text-ink/70">
                    <p className="font-medium">Uncertainty</p>
                    <ul className="mt-1 list-disc space-y-1 pl-4">
                      {track.unresolved_uncertainties.map((item: string) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
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

      <section className="border border-rule bg-white/70 p-4" aria-label="Manual evidence URL intake">
        <h3 className="font-serif text-2xl">Add evidence URL</h3>
        <p className="mt-2 text-sm text-ink/70">
          Adding a URL fetches and audits the document. It creates no claim and never starts a forecast.
          Accepted evidence can enter the normal extraction path only on a fresh explicit rerun with matching mode and cutoff.
        </p>
        {!manualEvidenceModeSupported ? (
          <p className="mt-2 text-sm text-amber-800">
            Manual external evidence is available for live and backtest questions. Demo runs remain fixture-only.
          </p>
        ) : null}
        <form onSubmit={addEvidenceUrl} className="mt-4 grid gap-3 md:grid-cols-2">
          <label className="text-sm md:col-span-2">
            Evidence URL
            <input
              className="mt-1 w-full border border-rule p-2"
              type="url"
              required
              value={evidenceUrl}
              onChange={(event) => setEvidenceUrl(event.target.value)}
              placeholder="https://www.example.org/source"
            />
          </label>
          <label className="text-sm">
            Intended use
            <select
              className="mt-1 w-full border border-rule p-2"
              value={evidenceNodeId}
              onChange={(event) => setEvidenceNodeId(event.target.value)}
            >
              <option value="">General question evidence</option>
              {reportNodes.map((node: any) => (
                <option key={node.id} value={node.id}>{node.question}</option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            Optional note
            <input
              className="mt-1 w-full border border-rule p-2"
              value={evidenceNote}
              onChange={(event) => setEvidenceNote(event.target.value)}
              placeholder="Why this source matters"
            />
          </label>
          <button
            className="border border-ink px-4 py-2 md:col-span-2 md:w-fit"
            type="submit"
            disabled={evidenceSubmitting || !manualEvidenceModeSupported}
          >
            {evidenceSubmitting ? "Checking evidence…" : "Add evidence URL"}
          </button>
        </form>
        {evidenceMessage ? <p className="mt-3 text-sm" role="status">{evidenceMessage}</p> : null}
        {manualEvidence.length ? (
          <div className="mt-5 space-y-3">
            {manualEvidence.map((item: any) => (
              <article key={item.id} className="border border-rule p-3 text-sm">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    {item.accepted ? (
                      <a className="font-medium underline decoration-copper" href={item.canonical_url} target="_blank" rel="noreferrer">
                        {item.title || item.canonical_url}
                      </a>
                    ) : (
                      <p className="font-medium">{item.title || item.canonical_url}</p>
                    )}
                    <p className="text-xs text-ink/60">{item.publisher || "Publisher unavailable"}</p>
                  </div>
                  <p className={item.accepted ? "text-emerald-800" : "text-red-800"}>
                    {item.accepted ? "accepted" : "rejected"}
                  </p>
                </div>
                <p className="mt-2 break-all font-mono text-xs text-ink/60">
                  submitted {item.submitted_url}
                  {item.final_url && item.final_url !== item.canonical_url ? ` · final ${item.final_url}` : ""}
                </p>
                <p className="mt-2 font-mono text-xs text-ink/60">
                  Published {item.publication_date || "unavailable"} · retrieved {item.retrieval_date} · available {item.source_available_at}
                  {" · "}basis {String(item.temporal_basis || "unavailable").replaceAll("_", " ")}
                  {" · "}publication verified {item.publication_date_verified ? "yes" : "no"}
                </p>
                <p className="mt-1 break-all font-mono text-xs text-ink/60">
                  content {item.content_hash || "unavailable"} · extracted text {item.extracted_text_hash || "unavailable"}
                  {" · "}{item.content_type || "content type unavailable"} · {item.byte_length ?? 0} bytes
                </p>
                <p className="mt-1 text-xs text-ink/70">
                  Target {item.forecast_node_id || "general question"}
                  {item.rejection_reason ? ` · ${item.rejection_reason}` : ""}
                  {item.fresh_explicit_rerun_required ? " · fresh explicit rerun required" : ""}
                </p>
              </article>
            ))}
          </div>
        ) : (
          <p className="mt-4 text-sm text-ink/60">No manual evidence URLs have been submitted.</p>
        )}
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
              Version {versions.length - index}: {pct(version.ensemble_probability)} · {version.profile_id || "unknown profile"} ·{" "}
              {version.trigger_event} ·{" "}
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
