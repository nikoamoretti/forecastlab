"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

type ForecastContract = {
  id: string;
  question_id: string;
  original_question: string;
  normalized_question: string;
  yes_condition: string;
  no_condition: string;
  resolution_date: string | null;
  authoritative_source: string;
  fallback_sources: string[];
  resolution_method: string;
  ambiguity_notes: string;
  cancellation_conditions: string;
  resolver_risk_notes: string;
  initial_reference_class: string;
  suggested_drivers: string[];
  known_dependencies: string[];
  status: "draft" | "approved" | "superseded";
};

type ForecastNode = {
  id: string;
  graph_id: string;
  parent_node_id: string | null;
  question: string;
  node_type: "base_rate" | "trend" | "driver" | "dependency" | "scenario" | "adversarial" | "resolver";
  importance_weight: number;
  dependencies: string[];
  preferred_sources: string[];
  required_output_type: string;
  status: "pending" | "completed" | "failed";
};

type ForecastGraph = {
  id: string;
  contract_id: string;
  version: number;
  status: "draft" | "approved" | "superseded";
  created_at: string;
  generation_model: string;
  root_question: string;
  nodes: ForecastNode[];
};

type Preview = {
  ready: boolean;
  reasons?: string[];
  detail?: string;
  context?: {
    effective_mode: string;
    model_provider: string;
    model_name: string;
    search_provider: string;
    evidence_policy: string;
    effective_max_cost_usd: number;
    planned_model_calls: number;
    planned_search_calls: number;
    planned_fetches: number;
    estimated_upper_bound_cost_usd: number | null;
    estimate_exceeds_ceiling?: boolean;
  } | null;
};

export default function NewQuestionPage() {
  const router = useRouter();
  const [question, setQuestion] = useState(
    "Will the US unemployment rate exceed 5% before 30 June 2027?"
  );
  const [mode, setMode] = useState("demo");
  const [profileId, setProfileId] = useState("three_track_ensemble");
  const [profiles, setProfiles] = useState<Array<{ id: string; label: string }>>([]);
  const [asOf, setAsOf] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [step, setStep] = useState<"ask" | "contract" | "graph">("ask");
  const [contract, setContract] = useState<ForecastContract | null>(null);
  const [graph, setGraph] = useState<ForecastGraph | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Array<{ id: string; label: string }>>("/api/profiles")
      .then(setProfiles)
      .catch(() => setProfiles([]));
  }, []);

  useEffect(() => {
    const params = new URLSearchParams({ profile_id: profileId, mode });
    if (asOf) params.set("as_of", asOf);
    api<Preview>(`/api/execution/preview?${params.toString()}`)
      .then(setPreview)
      .catch(() => setPreview({ ready: mode === "demo", reasons: ["preview_unavailable"] }));
  }, [mode, profileId, asOf]);

  async function createAndGenerate(event: React.FormEvent) {
    event.preventDefault();
    if (preview && !preview.ready) {
      setError(preview.detail || preview.reasons?.join(", ") || "Selected mode is not ready");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const generated = await api<ForecastContract>("/api/contracts/generate", {
        method: "POST",
        body: JSON.stringify({
          question,
          profile_id: profileId,
          mode,
          as_of: asOf || undefined
        })
      });
      setContract(generated);
      setStep("contract");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not generate Forecast Contract");
    } finally {
      setBusy(false);
    }
  }

  async function approveAndGenerateGraph(event: React.FormEvent) {
    event.preventDefault();
    if (!contract) return;
    if (preview && !preview.ready) {
      setError(preview.detail || "Selected mode is not ready");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const approved = await api<ForecastContract>(`/api/contracts/${contract.id}/approve`, {
        method: "POST",
        body: JSON.stringify({
          normalized_question: contract.normalized_question,
          yes_condition: contract.yes_condition,
          no_condition: contract.no_condition,
          resolution_date: contract.resolution_date,
          authoritative_source: contract.authoritative_source,
          fallback_sources: contract.fallback_sources,
          resolution_method: contract.resolution_method,
          ambiguity_notes: contract.ambiguity_notes,
          cancellation_conditions: contract.cancellation_conditions,
          resolver_risk_notes: contract.resolver_risk_notes,
          initial_reference_class: contract.initial_reference_class,
          suggested_drivers: contract.suggested_drivers,
          known_dependencies: contract.known_dependencies
        })
      });
      setContract(approved);
      const generatedGraph = await api<ForecastGraph>(`/api/contracts/${contract.id}/graph`, {
        method: "POST"
      });
      setGraph(generatedGraph);
      setStep("graph");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not approve contract and generate Research Graph");
    } finally {
      setBusy(false);
    }
  }

  async function launchRun(event: React.FormEvent) {
    event.preventDefault();
    if (!contract || !graph) return;
    if (preview && !preview.ready) {
      setError(preview.detail || "Selected mode is not ready");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api(`/api/questions/${contract.question_id}/runs`, {
        method: "POST",
        body: JSON.stringify({
          profile_id: profileId,
          mode,
          as_of: asOf || undefined
        })
      });
      router.push(`/forecasts/${contract.question_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start run");
      setBusy(false);
    }
  }

  const launchLabel =
    mode === "live"
      ? "Launch live run"
      : mode === "backtest"
        ? "Launch backtest"
        : "Launch mock run";

  if (step === "graph" && contract && graph) {
    const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
    return (
      <form onSubmit={launchRun} className="mx-auto max-w-4xl space-y-6">
        <p className="font-mono text-xs uppercase tracking-[0.25em] text-copper">Research Graph</p>
        <h2 className="font-serif text-4xl">Review the research plan before forecasting starts.</h2>
        {error ? <p className="text-copper">{error}</p> : null}
        <section className="border border-rule bg-white p-5">
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-copper">Forecast Contract</p>
          <p className="mt-2 font-serif text-2xl">{graph.root_question}</p>
          <p className="mt-3 text-sm text-ink/70">Yes: {contract.yes_condition}</p>
          <p className="mt-1 text-sm text-ink/70">No: {contract.no_condition}</p>
        </section>
        <p aria-hidden="true" className="text-center text-2xl text-copper">↓</p>
        <section>
          <div className="flex items-baseline justify-between gap-4">
            <h3 className="font-serif text-3xl">Research Graph</h3>
            <p className="font-mono text-xs uppercase tracking-[0.16em]">{graph.nodes.length} nodes</p>
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            {graph.nodes.map((node) => {
              const relationships = [
                ...(node.parent_node_id ? [`Parent: ${nodesById.get(node.parent_node_id)?.node_type || "node"}`] : []),
                ...node.dependencies.map(
                  (dependencyId) => `Depends on: ${nodesById.get(dependencyId)?.node_type || "node"}`
                )
              ];
              return (
                <article key={node.id} className="border border-rule bg-white/70 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <p className="font-mono text-xs uppercase tracking-[0.16em] text-copper">
                      {node.node_type.replace("_", " ")}
                    </p>
                    <p className="text-xs">Importance {Math.round(node.importance_weight * 100)}%</p>
                  </div>
                  <p className="mt-3 text-sm leading-6">{node.question}</p>
                  <p className="mt-3 text-xs text-ink/60">
                    {relationships.length ? relationships.join(" · ") : "Directly beneath the forecast outcome"}
                  </p>
                  <Link
                    className="mt-4 inline-block text-xs font-medium text-copper underline underline-offset-4"
                    href={`/nodes/${node.id}`}
                  >
                    View evidence claims
                  </Link>
                </article>
              );
            })}
          </div>
        </section>
        <button
          disabled={busy || Boolean(preview && !preview.ready)}
          className="border border-ink bg-ink px-5 py-2 text-paper disabled:opacity-50"
        >
          {busy ? "Starting…" : launchLabel}
        </button>
      </form>
    );
  }

  if (step === "contract" && contract) {
    return (
      <form onSubmit={approveAndGenerateGraph} className="mx-auto max-w-3xl space-y-6">
        <p className="font-mono text-xs uppercase tracking-[0.25em] text-copper">Forecast Contract</p>
        <h2 className="font-serif text-4xl">Review the contract before research starts.</h2>
        {error ? <p className="text-copper">{error}</p> : null}
        {(
          [
            ["normalized_question", "Question"],
            ["yes_condition", "Yes means"],
            ["no_condition", "No means"],
            ["authoritative_source", "Resolution source"],
            ["resolution_date", "Deadline"],
            ["ambiguity_notes", "Ambiguities"],
            ["resolver_risk_notes", "Resolver risks"]
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="block">
            <span className="text-sm">{label}</span>
            <textarea
              className="mt-2 w-full border border-rule bg-white p-3"
              rows={key.includes("condition") || key.includes("notes") ? 3 : 2}
              value={String(contract[key] || "")}
              onChange={(event) => setContract({ ...contract, [key]: event.target.value })}
            />
          </label>
        ))}
        <section className="border border-rule bg-white/70 p-4 text-sm">
          <p className="font-mono text-xs uppercase tracking-[0.2em]">Initial reference class</p>
          <p className="mt-2">{contract.initial_reference_class || "Not identified"}</p>
        </section>
        <button
          disabled={busy || Boolean(preview && !preview.ready)}
          className="border border-ink bg-ink px-5 py-2 text-paper disabled:opacity-50"
        >
          {busy ? "Generating graph…" : "Approve and generate Research Graph"}
        </button>
      </form>
    );
  }

  return (
    <form onSubmit={createAndGenerate} className="mx-auto max-w-3xl space-y-6">
      <p className="font-mono text-xs uppercase tracking-[0.25em] text-copper">New question</p>
      <h2 className="font-serif text-4xl">State a binary claim, then inspect the contract.</h2>
      {error ? <p className="text-copper">{error}</p> : null}
      <label className="block">
        <span className="text-sm">Question</span>
        <textarea
          className="mt-2 w-full border border-rule bg-white p-3"
          rows={5}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
        />
      </label>
      <div className="grid gap-4 md:grid-cols-2">
        <label className="block">
          <span className="text-sm">Run mode</span>
          <select
            className="mt-2 w-full border border-rule bg-white p-3"
            value={mode}
            onChange={(event) => setMode(event.target.value)}
          >
            <option value="demo">demo</option>
            <option value="live">live</option>
            <option value="backtest">evidence-cutoff backtest</option>
          </select>
        </label>
        <label className="block">
          <span className="text-sm">Forecast profile</span>
          <select
            className="mt-2 w-full border border-rule bg-white p-3"
            value={profileId}
            onChange={(event) => setProfileId(event.target.value)}
          >
            {(profiles.length ? profiles : [{ id: "three_track_ensemble", label: "three_track_ensemble" }]).map(
              (item) => (
                <option key={item.id} value={item.id}>
                  {item.label || item.id}
                </option>
              )
            )}
          </select>
        </label>
        <label className="block">
          <span className="text-sm">as_of (required for backtest)</span>
          <input
            className="mt-2 w-full border border-rule bg-white p-3"
            placeholder="2024-06-01T00:00:00Z"
            value={asOf}
            onChange={(event) => setAsOf(event.target.value)}
          />
        </label>
      </div>
      <section className="border border-rule bg-white/70 p-4 text-sm">
        <p className="font-mono text-xs uppercase tracking-[0.2em]">Mode readiness</p>
        <p className="mt-2">{preview?.ready ? `${mode} is ready` : `${mode} is not ready`}</p>
        {preview?.reasons?.length ? <p className="mt-1">Missing: {preview.reasons.join(", ")}</p> : null}
        {preview?.context ? (
          <ul className="mt-2 space-y-1">
            <li>
              Workload estimate: {preview.context.planned_model_calls} model calls,{" "}
              {preview.context.planned_search_calls} searches, {preview.context.planned_fetches} fetches
            </li>
            <li>Effective cost ceiling: ${Number(preview.context.effective_max_cost_usd).toFixed(2)}</li>
            <li>
              Upper-bound cost:{" "}
              {preview.context.estimated_upper_bound_cost_usd == null
                ? "unavailable / conservative token tracking only"
                : `$${preview.context.estimated_upper_bound_cost_usd.toFixed(4)} (estimated)`}
            </li>
          </ul>
        ) : null}
      </section>
      <button
        disabled={busy || Boolean(preview && !preview.ready && mode !== "demo")}
        className="border border-ink bg-ink px-5 py-2 text-paper disabled:opacity-50"
      >
        {busy ? "Generating…" : "Generate Forecast Contract"}
      </button>
    </form>
  );
}
