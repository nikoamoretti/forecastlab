"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";

type Contract = {
  exact_yes: string;
  exact_no: string;
  resolution_deadline: string;
  authoritative_source: string;
  fallback_sources?: string[];
  fallback_sources_json?: string;
  geography?: string;
  units?: string;
  ambiguity_notes?: string;
  cancellation_conditions?: string;
  resolver_risk_notes?: string;
};

export default function NewQuestionPage() {
  const router = useRouter();
  const [question, setQuestion] = useState(
    "Will the US unemployment rate exceed 5% before 30 June 2027?"
  );
  const [notes, setNotes] = useState("");
  const [deadline, setDeadline] = useState("2027-06-30");
  const [resolutionSource, setResolutionSource] = useState(
    "https://www.bls.gov/news.release/empsit.nr0.htm"
  );
  const [mode, setMode] = useState("demo");
  const [profileId, setProfileId] = useState("three_track_ensemble");
  const [asOf, setAsOf] = useState("");
  const [step, setStep] = useState<"ask" | "contract">("ask");
  const [questionId, setQuestionId] = useState<string | null>(null);
  const [contract, setContract] = useState<Contract | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function createAndOperationalize(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api<{ id: string }>("/api/questions", {
        method: "POST",
        body: JSON.stringify({
          question,
          notes: notes || undefined,
          forecast_deadline: deadline ? `${deadline}T23:59:59Z` : undefined,
          resolution_source: resolutionSource || undefined,
          profile_id: profileId,
          mode,
          as_of: asOf || undefined,
          start: false
        })
      });
      const operationalized = await api<{ id: string; contract: Contract }>(
        `/api/questions/${created.id}/operationalize`,
        { method: "POST" }
      );
      setQuestionId(created.id);
      const next = operationalized.contract;
      if (resolutionSource) next.authoritative_source = resolutionSource;
      setContract(next);
      setStep("contract");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not operationalize");
    } finally {
      setBusy(false);
    }
  }

  async function saveAndRun(event: React.FormEvent) {
    event.preventDefault();
    if (!questionId || !contract) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/api/questions/${questionId}/contract`, {
        method: "PUT",
        body: JSON.stringify({
          exact_yes: contract.exact_yes,
          exact_no: contract.exact_no,
          resolution_deadline: contract.resolution_deadline,
          authoritative_source: contract.authoritative_source,
          fallback_sources: Array.isArray(contract.fallback_sources)
            ? contract.fallback_sources
            : JSON.parse(contract.fallback_sources_json || "[]"),
          geography: contract.geography,
          units: contract.units,
          ambiguity_notes: contract.ambiguity_notes || "",
          cancellation_conditions: contract.cancellation_conditions || "",
          resolver_risk_notes: contract.resolver_risk_notes || ""
        })
      });
      await api(`/api/questions/${questionId}/runs`, {
        method: "POST",
        body: JSON.stringify({
          profile_id: profileId,
          mode,
          as_of: asOf || undefined
        })
      });
      router.push(`/forecasts/${questionId}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start run");
      setBusy(false);
    }
  }

  if (step === "contract" && contract) {
    return (
      <form onSubmit={saveAndRun} className="mx-auto max-w-3xl space-y-6">
        <p className="font-mono text-xs uppercase tracking-[0.25em] text-copper">Resolution contract</p>
        <h2 className="font-serif text-4xl">Edit the yes/no rules before research starts.</h2>
        {error ? <p className="text-copper">{error}</p> : null}
        {(
          [
            ["exact_yes", "Exact yes"],
            ["exact_no", "Exact no"],
            ["resolution_deadline", "Resolution deadline"],
            ["authoritative_source", "Authoritative source"],
            ["ambiguity_notes", "Ambiguity notes"],
            ["resolver_risk_notes", "Resolver-risk notes"]
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="block">
            <span className="text-sm">{label}</span>
            <textarea
              className="mt-2 w-full border border-rule bg-white p-3"
              rows={key.includes("notes") || key.startsWith("exact") ? 3 : 2}
              value={String(contract[key] || "")}
              onChange={(event) => setContract({ ...contract, [key]: event.target.value })}
            />
          </label>
        ))}
        <button disabled={busy} className="border border-ink bg-ink px-5 py-2 text-paper disabled:opacity-50">
          {busy ? "Starting…" : "Save contract and launch mock run"}
        </button>
      </form>
    );
  }

  return (
    <form onSubmit={createAndOperationalize} className="mx-auto max-w-3xl space-y-6">
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
          <span className="text-sm">Deadline</span>
          <input
            className="mt-2 w-full border border-rule bg-white p-3"
            type="date"
            value={deadline}
            onChange={(event) => setDeadline(event.target.value)}
          />
        </label>
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
            <option value="three_track_ensemble">three_track_ensemble</option>
            <option value="single_agent_baseline">single_agent_baseline</option>
          </select>
        </label>
        <label className="block">
          <span className="text-sm">as_of (optional, ISO)</span>
          <input
            className="mt-2 w-full border border-rule bg-white p-3"
            placeholder="2024-06-01T00:00:00Z"
            value={asOf}
            onChange={(event) => setAsOf(event.target.value)}
          />
        </label>
      </div>
      <label className="block">
        <span className="text-sm">Preferred resolution source</span>
        <input
          className="mt-2 w-full border border-rule bg-white p-3"
          value={resolutionSource}
          onChange={(event) => setResolutionSource(event.target.value)}
        />
      </label>
      <label className="block">
        <span className="text-sm">Notes</span>
        <textarea
          className="mt-2 w-full border border-rule bg-white p-3"
          rows={3}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
        />
      </label>
      <button disabled={busy} className="border border-ink bg-ink px-5 py-2 text-paper disabled:opacity-50">
        {busy ? "Operationalizing…" : "Review resolution contract"}
      </button>
    </form>
  );
}
