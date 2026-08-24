from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from forecastlab.budget import Budget
from forecastlab.budgeted_provider import BudgetedModelProvider
from forecastlab.errors import (
    BudgetExceeded,
    PermanentProviderError,
    TransientProviderError,
)
from forecastlab.evidence_claims import (
    EvidenceClaimError,
    EvidenceExtractor,
    eligible_claims_for_forecasting,
)
from forecastlab.prompts import PromptBundle, load_prompt
from forecastlab.providers.base import ModelProvider, SearchProvider
from forecastlab.ranking import rank_hits
from forecastlab.run_cache import RunCache
from forecastlab.schemas import EvidenceClaim, FetchedDocument, ForecastNode, ForecastProfile, SearchHit
from forecastlab.timeutil import as_utc, utcnow
from forecastlab.wayback import discover_snapshots, mock_snapshots, nearest_eligible_snapshot

GraphResearchFailureCode = Literal[
    "retrieval_failure",
    "no_matching_source",
    "cutoff_rejection",
    "extraction_failure",
]

_CUTOFF_REASONS = {
    "claim_after_cutoff",
    "final_snapshot_after_as_of",
    "no_eligible_historical_snapshot",
    "published_after_as_of",
    "snapshot_after_as_of",
    "unverifiable_as_of",
}

_EVIDENCE_TYPES_BY_NODE = {
    "base_rate": ["historical frequency", "quantitative reference class"],
    "trend": ["dated official series", "directional indicator"],
    "driver": ["primary causal record", "empirical driver estimate"],
    "dependency": ["official methodology", "dependency indicator"],
    "scenario": ["dated scenario analysis", "leading indicator"],
    "adversarial": ["contradictory primary evidence", "methodological critique"],
    "resolver": ["official resolution rule", "publication or revision methodology"],
}


def _normalize_text(value: str) -> str:
    return " ".join(value.split()).strip()


def _deduplicate_text(values: list[str], *, limit: int | None = None) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_text(value)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        output.append(normalized)
        if limit is not None and len(output) >= limit:
            break
    return output


class NodeResearchPlan(BaseModel):
    """Structured, node-specific instructions for evidence retrieval."""

    model_config = ConfigDict(extra="forbid")

    primary_research_question: str = Field(min_length=1)
    supporting_search_queries: list[str] = Field(min_length=1, max_length=4)
    preferred_sources: list[str] = Field(default_factory=list, max_length=8)
    required_evidence_types: list[str] = Field(min_length=1, max_length=8)

    @field_validator("primary_research_question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = _normalize_text(value)
        if not normalized:
            raise ValueError("primary_research_question_required")
        return normalized

    @field_validator(
        "supporting_search_queries",
        "preferred_sources",
        "required_evidence_types",
    )
    @classmethod
    def normalize_lists(cls, values: list[str]) -> list[str]:
        return _deduplicate_text(values)


@dataclass(frozen=True)
class GraphResearchFailure:
    code: GraphResearchFailureCode
    reason: str


@dataclass
class GraphResearchResult:
    node_id: str
    plan: NodeResearchPlan
    queries_attempted: list[str]
    sources_checked: list[dict[str, Any]]
    evidence: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    claims: list[EvidenceClaim] = field(default_factory=list)
    extraction_errors: list[str] = field(default_factory=list)
    planning_warnings: list[str] = field(default_factory=list)
    failure: GraphResearchFailure | None = None


def _stable_id(kind: str, *parts: str) -> str:
    identity = ":".join(("forecastlab", "graph_forecaster_v1", kind, *parts))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))


def _document_record(
    document: FetchedDocument,
    *,
    node: ForecastNode,
    evidence_item_id: str,
    source_class: str,
) -> dict[str, Any]:
    return {
        "id": evidence_item_id,
        "forecast_node_id": node.id,
        "subquestion": node.question,
        "url": document.url,
        "title": document.title,
        "publisher": document.publisher,
        "published_at": document.published_at.isoformat() if document.published_at else None,
        "retrieved_at": document.retrieved_at.isoformat(),
        "excerpt": (document.text or "")[:800],
        "content_hash": document.content_hash,
        "source_class": source_class,
        "as_of_eligible": document.as_of_eligible,
        "rejected": document.rejected,
        "rejection_reason": document.rejection_reason,
        "snapshot_url": document.snapshot_url,
        "snapshot_at": document.snapshot_at.isoformat() if document.snapshot_at else None,
        "requested_snapshot_url": document.requested_snapshot_url,
        "requested_snapshot_at": (
            document.requested_snapshot_at.isoformat()
            if document.requested_snapshot_at
            else None
        ),
        "final_snapshot_url": document.final_snapshot_url,
        "final_snapshot_at": (
            document.final_snapshot_at.isoformat() if document.final_snapshot_at else None
        ),
        "archived_original_url": document.archived_original_url,
        "snapshot_verification_status": document.snapshot_verification_status,
        "status_code": document.status_code,
        "published_at_unknown": document.published_at_unknown,
    }


def _no_snapshot_record(
    node: ForecastNode,
    *,
    run_id: str,
    hit: SearchHit,
) -> dict[str, Any]:
    return {
        "id": _stable_id("evidence", run_id, node.id, hit.url),
        "forecast_node_id": node.id,
        "subquestion": node.question,
        "url": hit.url,
        "title": hit.title,
        "publisher": None,
        "published_at": None,
        "retrieved_at": utcnow().isoformat(),
        "excerpt": "",
        "content_hash": "",
        "source_class": hit.source_class,
        "as_of_eligible": False,
        "rejected": True,
        "rejection_reason": "no_eligible_historical_snapshot",
        "snapshot_url": None,
        "snapshot_at": None,
        "status_code": 0,
        "published_at_unknown": True,
    }


def _default_plan(node: ForecastNode) -> NodeResearchPlan:
    evidence_types = [
        *_EVIDENCE_TYPES_BY_NODE[node.node_type],
        node.required_output_type.replace("_", " "),
    ]
    queries = [node.question]
    queries.extend(f"{node.question} {source}" for source in node.preferred_sources[:2])
    queries.append(f"{node.question} official source {evidence_types[0]}")
    return NodeResearchPlan(
        primary_research_question=node.question,
        supporting_search_queries=_deduplicate_text(queries, limit=4),
        preferred_sources=_deduplicate_text(node.preferred_sources, limit=8),
        required_evidence_types=_deduplicate_text(evidence_types, limit=8),
    )


def _cutoff_query(query: str, *, mode: str, as_of: datetime | None) -> str:
    if mode != "backtest" or as_of is None:
        return query
    return f"{query} published on or before {as_utc(as_of).date().isoformat()}"


def _is_cutoff_reason(reason: str | None) -> bool:
    if not reason:
        return False
    return reason in _CUTOFF_REASONS or reason.startswith("final_snapshot_after_as_of")


class GraphResearchExecutor:
    """Turn one ForecastNode into cutoff-safe, provenance-bearing Evidence Claims."""

    def __init__(
        self,
        *,
        model: ModelProvider,
        search: SearchProvider,
        profile: ForecastProfile,
        budget: Budget,
        cache: RunCache,
        run_id: str,
        mode: str,
        as_of: datetime | None,
        allow_local_fixtures: bool,
        max_queries_per_node: int | None = None,
        max_fetches_per_node: int | None = None,
        prompt_bundle: PromptBundle | None = None,
        prompt_versions: dict[str, str] | None = None,
    ) -> None:
        self.model = model
        self.search = search
        self.profile = profile
        self.budget = budget
        self.cache = cache
        self.run_id = run_id
        self.mode = mode
        self.as_of = as_utc(as_of) if as_of is not None else None
        self.allow_local_fixtures = allow_local_fixtures
        self.max_queries_per_node = max(
            1,
            min(
                4,
                max_queries_per_node
                if max_queries_per_node is not None
                else profile.max_search_calls,
            ),
        )
        self.max_fetches_per_node = max(
            1,
            min(
                profile.max_fetched_documents,
                max_fetches_per_node
                if max_fetches_per_node is not None
                else profile.max_fetched_documents,
            ),
        )
        self.prompt_bundle = prompt_bundle
        self.prompt_versions = prompt_versions if prompt_versions is not None else {}

    def _prompt(self) -> tuple[str, str]:
        if self.prompt_bundle is not None:
            return self.prompt_bundle.get("graph_research")
        return load_prompt("graph_research")

    def _generate_plan(self, node: ForecastNode) -> tuple[NodeResearchPlan, list[str]]:
        fallback = _default_plan(node)
        try:
            system, version = self._prompt()
            self.prompt_versions["graph_research"] = version
            result = BudgetedModelProvider(
                self.model,
                self.budget,
                stage=f"plan_node_research:{node.id}",
            ).complete_json(
                system=system,
                user=json.dumps(
                    {
                        "node": {
                            "id": node.id,
                            "question": node.question,
                            "node_type": node.node_type,
                            "preferred_sources": node.preferred_sources,
                            "required_output_type": node.required_output_type,
                        },
                        "historical_cutoff": self.as_of.isoformat() if self.as_of else None,
                    }
                ),
                schema_name="graph_research_plan",
            )
            payload = result.parsed if result.parsed is not None else json.loads(result.content)
            generated = NodeResearchPlan.model_validate(payload)
        except BudgetExceeded:
            raise
        except (
            FileNotFoundError,
            json.JSONDecodeError,
            PermanentProviderError,
            TransientProviderError,
            TypeError,
            ValidationError,
        ) as exc:
            return fallback, [f"research_plan_fallback:{exc.__class__.__name__}"]

        return (
            NodeResearchPlan(
                primary_research_question=generated.primary_research_question,
                supporting_search_queries=_deduplicate_text(
                    [
                        generated.primary_research_question,
                        *generated.supporting_search_queries,
                        *fallback.supporting_search_queries,
                    ],
                    limit=4,
                ),
                preferred_sources=_deduplicate_text(
                    [*generated.preferred_sources, *fallback.preferred_sources],
                    limit=8,
                ),
                required_evidence_types=_deduplicate_text(
                    [
                        *generated.required_evidence_types,
                        *fallback.required_evidence_types,
                    ],
                    limit=8,
                ),
            ),
            [],
        )

    def execute(self, node: ForecastNode) -> GraphResearchResult:
        plan, planning_warnings = self._generate_plan(node)
        queries = _deduplicate_text(
            [
                _cutoff_query(query, mode=self.mode, as_of=self.as_of)
                for query in plan.supporting_search_queries
            ],
            limit=self.max_queries_per_node,
        )
        sources_checked: list[dict[str, Any]] = []
        retrieval_errors: list[str] = []
        hits_by_url: dict[str, SearchHit] = {}
        queries_by_url: dict[str, list[str]] = {}

        for query in queries:
            self.budget.add_search(f"search_node:{node.id}")
            try:
                hits = self.cache.search(
                    self.search,
                    query,
                    self.profile.search_results_per_subquestion,
                )
            except (PermanentProviderError, TransientProviderError) as exc:
                retrieval_errors.append(f"search:{exc.__class__.__name__}:{exc}")
                continue
            for hit in hits:
                hits_by_url.setdefault(hit.url, hit)
                queries_by_url.setdefault(hit.url, []).append(query)

        if not hits_by_url:
            code: GraphResearchFailureCode = (
                "retrieval_failure" if retrieval_errors else "no_matching_source"
            )
            reason = "; ".join(retrieval_errors) or "No search result matched the node research queries"
            return GraphResearchResult(
                node_id=node.id,
                plan=plan,
                queries_attempted=queries,
                sources_checked=sources_checked,
                planning_warnings=planning_warnings,
                failure=GraphResearchFailure(code=code, reason=reason),
            )

        evidence: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        claims: list[EvidenceClaim] = []
        extraction_errors: list[str] = []
        successful_documents = 0
        fetch_attempts = 0
        target_documents = max(1, self.profile.fetches_per_subquestion)

        for hit in rank_hits(list(hits_by_url.values())):
            snapshot_url = None
            snapshot_at = None
            source_audit: dict[str, Any] = {
                "url": hit.url,
                "title": hit.title,
                "queries": _deduplicate_text(queries_by_url.get(hit.url, [])),
                "source_class": hit.source_class,
            }
            if self.mode == "backtest" and self.as_of is not None:
                try:
                    snapshots = (
                        mock_snapshots(hit.url)
                        if self.allow_local_fixtures
                        else discover_snapshots(hit.url, as_of=self.as_of)
                    )
                except Exception as exc:
                    source_audit.update(
                        outcome="retrieval_failure",
                        reason=f"snapshot_discovery:{exc.__class__.__name__}",
                    )
                    sources_checked.append(source_audit)
                    retrieval_errors.append(str(source_audit["reason"]))
                    continue
                nearest = nearest_eligible_snapshot(snapshots, self.as_of)
                if nearest is None:
                    record = _no_snapshot_record(node, run_id=self.run_id, hit=hit)
                    rejected.append(record)
                    source_audit.update(
                        outcome="cutoff_rejection",
                        reason="no_eligible_historical_snapshot",
                    )
                    sources_checked.append(source_audit)
                    continue
                snapshot_url = nearest.snapshot_url
                snapshot_at = nearest.timestamp

            if fetch_attempts >= self.max_fetches_per_node:
                break
            self.budget.add_fetch(f"fetch_node:{node.id}")
            fetch_attempts += 1
            try:
                document = self.cache.fetch(
                    hit.url,
                    as_of=self.as_of if self.mode == "backtest" else None,
                    allow_local_fixtures=self.allow_local_fixtures,
                    snapshot_url=snapshot_url,
                    snapshot_at=snapshot_at,
                    mode=self.mode,
                )
            except BudgetExceeded:
                raise
            except Exception as exc:
                source_audit.update(
                    outcome="retrieval_failure",
                    reason=f"fetch:{exc.__class__.__name__}",
                )
                sources_checked.append(source_audit)
                retrieval_errors.append(str(source_audit["reason"]))
                continue

            evidence_item_id = _stable_id("evidence", self.run_id, node.id, hit.url)
            record = _document_record(
                document,
                node=node,
                evidence_item_id=evidence_item_id,
                source_class=hit.source_class,
            )
            if document.rejected or not document.as_of_eligible:
                rejected.append(record)
                source_audit.update(
                    outcome=(
                        "cutoff_rejection"
                        if _is_cutoff_reason(document.rejection_reason)
                        else "retrieval_failure"
                    ),
                    reason=document.rejection_reason or "document_not_as_of_eligible",
                )
                sources_checked.append(source_audit)
                if source_audit["outcome"] == "retrieval_failure":
                    retrieval_errors.append(str(source_audit["reason"]))
                continue

            evidence.append(record)
            extractor = EvidenceExtractor(
                BudgetedModelProvider(
                    self.model,
                    self.budget,
                    stage=f"extract_claims:{node.id}",
                ),
                prompt_bundle=self.prompt_bundle,
            )
            try:
                extracted = extractor.extract(
                    document,
                    evidence_item_id=evidence_item_id,
                    forecast_node_id=node.id,
                    forecast_node_question=node.question,
                    as_of=self.as_of if self.mode == "backtest" else None,
                )
            except BudgetExceeded:
                raise
            except (EvidenceClaimError, PermanentProviderError, TransientProviderError) as exc:
                reasons = (
                    exc.reasons
                    if isinstance(exc, EvidenceClaimError)
                    else [f"provider:{exc.__class__.__name__}:{exc}"]
                )
                extraction_errors.extend(reasons)
                source_audit.update(
                    outcome="extraction_failure",
                    reason=",".join(reasons),
                )
                sources_checked.append(source_audit)
                continue
            eligible = eligible_claims_for_forecasting(extracted, cutoff=self.as_of)
            if not eligible:
                extraction_errors.append("no_eligible_claims_extracted")
                source_audit.update(
                    outcome="extraction_failure",
                    reason="no_eligible_claims_extracted",
                )
                sources_checked.append(source_audit)
                continue
            claims.extend(
                claim.model_copy(
                    update={
                        "id": _stable_id(
                            "claim",
                            self.run_id,
                            node.id,
                            evidence_item_id,
                            claim.supports_or_refutes,
                            claim.claim,
                            claim.excerpt,
                        )
                    }
                )
                for claim in eligible
            )
            successful_documents += 1
            source_audit.update(outcome="claims_created", reason=None)
            sources_checked.append(source_audit)
            if successful_documents >= target_documents:
                break

        claims = eligible_claims_for_forecasting(claims, cutoff=self.as_of)
        failure: GraphResearchFailure | None = None
        if not claims:
            outcomes = {str(item.get("outcome")) for item in sources_checked}
            if "extraction_failure" in outcomes:
                failure = GraphResearchFailure(
                    code="extraction_failure",
                    reason=",".join(_deduplicate_text(extraction_errors))
                    or "Eligible documents produced no usable Evidence Claims",
                )
            elif outcomes and outcomes <= {"cutoff_rejection"}:
                failure = GraphResearchFailure(
                    code="cutoff_rejection",
                    reason="Every discovered source failed the historical cutoff policy",
                )
            elif retrieval_errors:
                failure = GraphResearchFailure(
                    code="retrieval_failure",
                    reason="; ".join(_deduplicate_text(retrieval_errors)),
                )
            else:
                failure = GraphResearchFailure(
                    code="no_matching_source",
                    reason="Search results did not yield a source usable for this node",
                )

        return GraphResearchResult(
            node_id=node.id,
            plan=plan,
            queries_attempted=queries,
            sources_checked=sources_checked,
            evidence=evidence,
            rejected=rejected,
            claims=claims,
            extraction_errors=_deduplicate_text(extraction_errors),
            planning_warnings=planning_warnings,
            failure=failure,
        )
