"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";

type ForecastNode = {
  id: string;
  question: string;
  node_type: string;
  importance_weight: number;
  preferred_sources: string[];
};

type EvidenceClaim = {
  id: string;
  claim: string;
  excerpt: string;
  source_url: string;
  source_title: string;
  publisher: string;
  publication_date: string | null;
  publication_date_source: string | null;
  publication_date_verified: boolean;
  retrieval_date: string;
  source_available_at: string;
  temporal_basis: "publication_date" | "snapshot_date" | "retrieval_date";
  supports_or_refutes: "supports" | "refutes";
  confidence: number;
  source_quality: number;
  primary_source: boolean;
  as_of_eligible: boolean;
  cutoff_verified: boolean;
};

type NodeEvidence = {
  node: ForecastNode;
  claims: EvidenceClaim[];
};

function dateLabel(value: string | null) {
  if (!value) return "unavailable";
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric"
  });
}

function temporalLabel(claim: EvidenceClaim) {
  if (claim.temporal_basis === "snapshot_date" && claim.cutoff_verified) {
    return "Historical snapshot verified";
  }
  if (claim.publication_date && claim.publication_date_verified) {
    return "Published date verified";
  }
  if (claim.temporal_basis === "retrieval_date" && !claim.publication_date) {
    return "Publication date unavailable; page observed during live run";
  }
  return "Publication date available but not independently verified";
}

export default function NodeEvidencePage() {
  const params = useParams<{ id: string }>();
  const [data, setData] = useState<NodeEvidence | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<NodeEvidence>(`/api/nodes/${params.id}/evidence`)
      .then(setData)
      .catch((reason: Error) => setError(reason.message));
  }, [params.id]);

  if (error) return <p className="text-copper">{error}</p>;
  if (!data) return <p>Loading evidence claims…</p>;

  return (
    <article className="mx-auto max-w-4xl space-y-6">
      <header>
        <p className="font-mono text-xs uppercase tracking-[0.25em] text-copper">Forecast Node</p>
        <h2 className="mt-2 font-serif text-4xl leading-tight">{data.node.question}</h2>
        <p className="mt-3 text-sm text-ink/60">
          {data.node.node_type.replace("_", " ")} · Importance {Math.round(data.node.importance_weight * 100)}%
        </p>
      </header>

      <p aria-hidden="true" className="text-center text-2xl text-copper">↓</p>

      <section>
        <div className="flex items-baseline justify-between gap-4">
          <h3 className="font-serif text-3xl">Claims</h3>
          <p className="font-mono text-xs uppercase tracking-[0.16em]">
            {data.claims.length} claim{data.claims.length === 1 ? "" : "s"}
          </p>
        </div>
        {data.claims.length ? (
          <div className="mt-4 space-y-5">
            {data.claims.map((claim) => (
              <article key={claim.id} className="border border-rule bg-white/70 p-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="font-mono text-xs uppercase tracking-[0.16em] text-copper">
                    {claim.supports_or_refutes}
                  </p>
                  <p className="text-xs text-ink/60">
                    Claim confidence {Math.round(claim.confidence * 100)}% · Source quality{" "}
                    {Math.round(claim.source_quality * 100)}%
                  </p>
                </div>
                <p className="mt-3 font-serif text-2xl">{claim.claim}</p>
                <blockquote className="mt-4 border-l-2 border-copper pl-4 text-sm leading-6 text-ink/75">
                  “{claim.excerpt}”
                </blockquote>
                <p aria-hidden="true" className="my-4 text-center text-xl text-copper">↓</p>
                <div className="text-sm">
                  <a
                    className="font-medium text-copper underline underline-offset-4"
                    href={claim.source_url}
                    rel="noreferrer"
                    target="_blank"
                  >
                    {claim.source_title}
                  </a>
                  <p className="mt-1 text-ink/60">
                    {claim.publisher} · Published {dateLabel(claim.publication_date)} · Available{" "}
                    {dateLabel(claim.source_available_at)} · Retrieved {dateLabel(claim.retrieval_date)}
                  </p>
                  <p className="mt-1 text-xs font-medium text-ink/70">{temporalLabel(claim)}</p>
                  <p className="mt-1 text-xs text-ink/60">
                    {claim.primary_source ? "Primary source" : "Secondary source"} ·{" "}
                    {claim.cutoff_verified && claim.as_of_eligible ? "Cutoff verified" : "Not eligible for cutoff"} ·{" "}
                    Basis {claim.temporal_basis.replaceAll("_", " ")}
                  </p>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="mt-4 border border-rule bg-white/70 p-5 text-sm text-ink/70">
            No Evidence Claims have been extracted for this node.
          </div>
        )}
      </section>
    </article>
  );
}
