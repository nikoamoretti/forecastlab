from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

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
from forecastlab.ranking import (
    normalized_candidate_host,
    rank_hits,
    schedule_ranked_hits,
)
from forecastlab.run_cache import RunCache
from forecastlab.schemas import EvidenceClaim, FetchedDocument, ForecastNode, ForecastProfile, SearchHit
from forecastlab.timeutil import as_utc, utcnow
from forecastlab.wayback import (
    WaybackSnapshot,
    canonicalize_url,
    discover_snapshots,
    mock_snapshots,
    nearest_eligible_snapshot,
)

GraphResearchFailureCode = Literal[
    "retrieval_failure",
    "no_matching_source",
    "cutoff_rejection",
    "extraction_failure",
]


@runtime_checkable
class SyntheticHistoricalEvidenceAdapter(Protocol):
    """Optional fail-closed fixture boundary used only by explicit synthetic backtests."""

    synthetic_workflow_only: bool

    def discover_fixture_snapshots(
        self,
        url: str,
        *,
        as_of: datetime,
    ) -> list[WaybackSnapshot]: ...

    def fetch_fixture_document(
        self,
        url: str,
        *,
        as_of: datetime | None,
        allow_local_fixtures: bool,
        snapshot_url: str | None,
        snapshot_at: datetime | None,
        mode: str,
    ) -> FetchedDocument: ...

_CUTOFF_REASONS = {
    "claim_after_cutoff",
    "final_snapshot_after_as_of",
    "no_eligible_historical_snapshot",
    "published_after_as_of",
    "snapshot_after_as_of",
    "unverifiable_as_of",
}
_SMALL_EXTRACTION_CHUNK_CHARS = 4000

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
        "publication_date_source": document.publication_date_source,
        "publication_date_verified": document.publication_date_verified,
        "publication_date_hint": (
            document.publication_date_hint.isoformat()
            if document.publication_date_hint
            else None
        ),
        "publication_date_hint_source": document.publication_date_hint_source,
        "modified_at": document.modified_at.isoformat() if document.modified_at else None,
        "modified_date_source": document.modified_date_source,
        "retrieved_at": document.retrieved_at.isoformat(),
        "source_available_at": document.source_available_at.isoformat(),
        "temporal_basis": document.temporal_basis,
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
    observed_at = utcnow().isoformat()
    return {
        "id": _stable_id("evidence", run_id, node.id, hit.url),
        "forecast_node_id": node.id,
        "subquestion": node.question,
        "url": hit.url,
        "title": hit.title,
        "publisher": None,
        "published_at": None,
        "publication_date_source": None,
        "publication_date_verified": False,
        "publication_date_hint": hit.published_at.isoformat() if hit.published_at else None,
        "publication_date_hint_source": (
            hit.published_at_source or "search_provider_hint"
            if hit.published_at
            else None
        ),
        "modified_at": None,
        "modified_date_source": None,
        "retrieved_at": observed_at,
        "source_available_at": observed_at,
        "temporal_basis": "retrieval_date",
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
        target_successful_documents_per_node: int | None = None,
        max_candidate_fetch_attempts_per_node: int | None = None,
        search_candidate_pool_per_node: int | None = None,
        prefer_distinct_candidate_hosts: bool | None = None,
        max_evidence_claims: int = 20,
        max_extraction_chars: int = 8000,
        research_plan_output_tokens: int = 1024,
        evidence_extraction_output_tokens: int = 1536,
        enable_extraction_fallbacks: bool = True,
        max_research_plan_model_calls: int = 1,
        max_primary_extraction_calls: int | None = None,
        max_extraction_retry_calls: int | None = None,
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
        legacy_fetch_limit = max(
            1,
            min(
                profile.max_fetched_documents,
                max_fetches_per_node
                if max_fetches_per_node is not None
                else profile.max_fetched_documents,
            ),
        )
        self.target_successful_documents_per_node = max(
            1,
            min(
                profile.fetches_per_subquestion,
                target_successful_documents_per_node
                if target_successful_documents_per_node is not None
                else legacy_fetch_limit,
            ),
        )
        self.max_candidate_fetch_attempts_per_node = max(
            self.target_successful_documents_per_node,
            min(
                profile.max_fetched_documents,
                max_candidate_fetch_attempts_per_node
                if max_candidate_fetch_attempts_per_node is not None
                else legacy_fetch_limit,
            ),
        )
        # Compatibility alias for existing audits and direct helpers. It now
        # means the candidate URL attempt ceiling, not extraction documents.
        self.max_fetches_per_node = self.max_candidate_fetch_attempts_per_node
        self.search_candidate_pool_per_node = max(
            self.max_candidate_fetch_attempts_per_node,
            search_candidate_pool_per_node
            if search_candidate_pool_per_node is not None
            else profile.search_candidate_pool_per_node
            or profile.search_results_per_subquestion,
        )
        self.prefer_distinct_candidate_hosts = (
            prefer_distinct_candidate_hosts
            if prefer_distinct_candidate_hosts is not None
            else profile.prefer_distinct_candidate_hosts
        )
        self.max_evidence_claims = max(1, max_evidence_claims)
        self.max_extraction_chars = max(512, max_extraction_chars)
        self.research_plan_output_tokens = max(1, research_plan_output_tokens)
        self.evidence_extraction_output_tokens = max(
            1,
            evidence_extraction_output_tokens,
        )
        self.enable_extraction_fallbacks = enable_extraction_fallbacks
        default_extraction_documents = (
            self.target_successful_documents_per_node
            if target_successful_documents_per_node is not None
            or profile.max_candidate_fetch_attempts_per_node is not None
            else self.max_candidate_fetch_attempts_per_node
        )
        self._planned_model_calls = {
            "research_plan": max(0, max_research_plan_model_calls),
            "primary_extraction": max(
                0,
                default_extraction_documents
                if max_primary_extraction_calls is None
                else max_primary_extraction_calls,
            ),
            "extraction_retry": max(
                0,
                (
                    default_extraction_documents
                    if enable_extraction_fallbacks
                    else 0
                )
                if max_extraction_retry_calls is None
                else max_extraction_retry_calls,
            ),
        }
        self._used_model_calls = {
            kind: 0 for kind in self._planned_model_calls
        }
        self.prompt_bundle = prompt_bundle
        self.prompt_versions = prompt_versions if prompt_versions is not None else {}

    def _take_planned_model_call(self, *, call_kind: str, stage: str) -> bool:
        planned = self._planned_model_calls[call_kind]
        used = self._used_model_calls[call_kind]
        if used >= planned:
            return False
        self._used_model_calls[call_kind] = used + 1
        return True

    def _prompt(self) -> tuple[str, str]:
        if self.prompt_bundle is not None:
            return self.prompt_bundle.get("graph_research")
        return load_prompt("graph_research")

    def _generate_plan(self, node: ForecastNode) -> tuple[NodeResearchPlan, list[str]]:
        fallback = _default_plan(node)
        try:
            stage = f"plan_node_research:{node.id}"
            if not self._take_planned_model_call(
                call_kind="research_plan",
                stage=stage,
            ):
                raise BudgetExceeded(stage, "unplanned_research_plan_call")
            system, version = self._prompt()
            self.prompt_versions["graph_research"] = version
            result = BudgetedModelProvider(
                self.model,
                self.budget,
                stage=stage,
                call_kind="research_plan",
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
                max_output_tokens=self.research_plan_output_tokens,
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

    def _extract_with_fallbacks(
        self,
        *,
        document: FetchedDocument,
        evidence_item_id: str,
        node: ForecastNode,
    ) -> tuple[list[EvidenceClaim], list[str], str]:
        errors: list[str] = []

        def attempt(
            candidate: FetchedDocument,
            *,
            label: str,
            call_kind: str,
        ) -> list[EvidenceClaim]:
            stage = f"extract_claims:{node.id}:{label}"
            if not self._take_planned_model_call(
                call_kind=call_kind,
                stage=stage,
            ):
                raise BudgetExceeded(stage, f"unplanned_{call_kind}_call")
            extractor = EvidenceExtractor(
                BudgetedModelProvider(
                    self.model,
                    self.budget,
                    stage=stage,
                    call_kind=call_kind,
                ),
                prompt_bundle=self.prompt_bundle,
                max_output_tokens=self.evidence_extraction_output_tokens,
            )
            try:
                return extractor.extract(
                    candidate,
                    evidence_item_id=evidence_item_id,
                    forecast_node_id=node.id,
                    forecast_node_question=node.question,
                    as_of=self.as_of if self.mode == "backtest" else None,
                    mode=self.mode,  # type: ignore[arg-type]
                )
            except BudgetExceeded:
                raise
            except (
                EvidenceClaimError,
                PermanentProviderError,
                TransientProviderError,
            ) as exc:
                reasons = (
                    exc.reasons
                    if isinstance(exc, EvidenceClaimError)
                    else [f"provider:{exc.__class__.__name__}:{exc}"]
                )
                errors.extend(f"{label}:{reason}" for reason in reasons)
                return []

        extracted = attempt(
            document,
            label="full_document",
            call_kind="primary_extraction",
        )
        if extracted or not self.enable_extraction_fallbacks:
            return extracted, errors, (
                "claims_created" if extracted else "extraction_failure"
            )

        source_text = document.text.strip()
        chunk_size = min(
            _SMALL_EXTRACTION_CHUNK_CHARS,
            max(512, len(source_text) // 2),
        )
        smaller_text = source_text[:chunk_size]
        if smaller_text and smaller_text != source_text:
            if (
                self._used_model_calls["extraction_retry"]
                < self._planned_model_calls["extraction_retry"]
            ):
                extracted = attempt(
                    document.model_copy(update={"text": smaller_text}),
                    label="smaller_chunk",
                    call_kind="extraction_retry",
                )
                if extracted:
                    return extracted, errors, "claims_created_smaller_chunk"
            else:
                errors.append("smaller_chunk:retry_not_planned")

        try:
            fallback = EvidenceExtractor(
                self.model,
                prompt_bundle=self.prompt_bundle,
                max_output_tokens=self.evidence_extraction_output_tokens,
            ).document_fallback_claim(
                document,
                evidence_item_id=evidence_item_id,
                forecast_node_id=node.id,
                as_of=self.as_of if self.mode == "backtest" else None,
                mode=self.mode,  # type: ignore[arg-type]
            )
        except EvidenceClaimError as exc:
            errors.extend(
                f"document_fallback:{reason}"
                for reason in exc.reasons
            )
            return [], errors, "extraction_failure"
        return [fallback], errors, "claims_created_document_fallback"

    def execute(self, node: ForecastNode) -> GraphResearchResult:
        fixture_adapter = (
            self.search
            if self.allow_local_fixtures
            and isinstance(self.search, SyntheticHistoricalEvidenceAdapter)
            and self.search.synthetic_workflow_only
            else None
        )
        plan, planning_warnings = self._generate_plan(node)
        preferred_source = plan.preferred_sources[0] if plan.preferred_sources else ""
        qualified_primary_query = plan.primary_research_question
        if (
            self.profile.max_candidate_fetch_attempts_per_node is not None
            and preferred_source
            and preferred_source.casefold()
            not in qualified_primary_query.casefold()
        ):
            qualified_primary_query = (
                f"{qualified_primary_query} {preferred_source}"
            )
        queries = _deduplicate_text(
            [
                _cutoff_query(query, mode=self.mode, as_of=self.as_of)
                for query in [
                    qualified_primary_query,
                    *plan.supporting_search_queries,
                ]
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
                    self.search_candidate_pool_per_node,
                )
            except (PermanentProviderError, TransientProviderError) as exc:
                retrieval_errors.append(f"search:{exc.__class__.__name__}:{exc}")
                continue
            for hit in hits:
                canonical_url = canonicalize_url(hit.url)
                hits_by_url.setdefault(canonical_url, hit)
                queries_by_url.setdefault(canonical_url, []).append(query)

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
        target_documents = self.target_successful_documents_per_node
        single_extraction_document_path = bool(
            self.profile.max_candidate_fetch_attempts_per_node is not None
            and target_documents == 1
        )

        ranked_hits = rank_hits(
            list(hits_by_url.values()),
            preferred_sources=(
                plan.preferred_sources
                if self.profile.max_candidate_fetch_attempts_per_node
                is not None
                else None
            ),
            node_question=(
                node.question
                if self.profile.max_candidate_fetch_attempts_per_node
                is not None
                else None
            ),
        )
        scheduled_hits = schedule_ranked_hits(
            ranked_hits,
            prefer_distinct_hosts=self.prefer_distinct_candidate_hosts,
        )
        returned_positions = {
            canonical_url: position
            for position, canonical_url in enumerate(hits_by_url, start=1)
        }
        ranked_positions = {
            canonicalize_url(hit.url): position
            for position, hit in enumerate(ranked_hits, start=1)
        }
        scheduled_positions = {
            canonicalize_url(hit.url): position
            for position, hit in enumerate(scheduled_hits, start=1)
        }
        blocked_hosts: set[str] = set()
        skipped_blocked_urls: set[str] = set()
        attempted_urls: set[str] = set()

        def source_audit_for(hit: SearchHit) -> dict[str, Any]:
            canonical_url = canonicalize_url(hit.url)
            ranked_position = ranked_positions[canonical_url]
            scheduled_position = scheduled_positions[canonical_url]
            return {
                "url": hit.url,
                "canonical_url": canonical_url,
                "normalized_host": normalized_candidate_host(hit.url),
                "title": hit.title,
                "returned_position": returned_positions[canonical_url],
                "candidate_rank": ranked_position,
                "ranked_position": ranked_position,
                "scheduled_position": scheduled_position,
                "diversity_reordered": scheduled_position != ranked_position,
                "candidate_pool_size": len(scheduled_hits),
                "queries": _deduplicate_text(
                    queries_by_url.get(canonical_url, [])
                ),
                "selected_query": next(
                    iter(queries_by_url.get(canonical_url, [])),
                    None,
                ),
                "source_class": hit.source_class,
                "backup_used": scheduled_position > 1,
                "actual_fetch_attempt": False,
                "http_status": None,
                "access_blocked_host": False,
                "skipped_access_blocked_host": False,
                "extraction_entered": False,
                "claim_created": False,
                "fetch_elapsed_ms": None,
                "publication_date_hint": (
                    hit.published_at.isoformat() if hit.published_at else None
                ),
                "publication_date_hint_source": (
                    hit.published_at_source or f"{self.search.name}_search_hit"
                    if hit.published_at
                    else None
                ),
            }

        def audit_blocked_host_alternates(
            *,
            blocked_host: str,
            after_position: int,
        ) -> None:
            for alternate in scheduled_hits:
                alternate_url = canonicalize_url(alternate.url)
                if (
                    scheduled_positions[alternate_url] <= after_position
                    or alternate_url in attempted_urls
                    or alternate_url in skipped_blocked_urls
                    or normalized_candidate_host(alternate.url) != blocked_host
                ):
                    continue
                skipped_audit = source_audit_for(alternate)
                skipped_audit.update(
                    outcome="skipped_access_blocked_host",
                    failure_category="skipped_access_blocked_host",
                    reason="skipped_access_blocked_host",
                    access_blocked_host=True,
                    skipped_access_blocked_host=True,
                )
                sources_checked.append(skipped_audit)
                skipped_blocked_urls.add(alternate_url)

        for hit in scheduled_hits:
            canonical_url = canonicalize_url(hit.url)
            if canonical_url in skipped_blocked_urls:
                continue
            snapshot_url = None
            snapshot_at = None
            source_audit = source_audit_for(hit)
            candidate_host = str(source_audit["normalized_host"])
            if candidate_host in blocked_hosts:
                source_audit.update(
                    outcome="skipped_access_blocked_host",
                    failure_category="skipped_access_blocked_host",
                    reason="skipped_access_blocked_host",
                    access_blocked_host=True,
                    skipped_access_blocked_host=True,
                )
                sources_checked.append(source_audit)
                skipped_blocked_urls.add(canonical_url)
                continue
            if fetch_attempts >= self.max_fetches_per_node:
                break
            fetch_started = time.monotonic()
            self.budget.add_fetch(f"fetch_node:{node.id}")
            fetch_attempts += 1
            attempted_urls.add(canonical_url)
            source_audit["actual_fetch_attempt"] = True
            if self.mode == "backtest" and self.as_of is not None:
                try:
                    if fixture_adapter is not None:
                        snapshots = fixture_adapter.discover_fixture_snapshots(
                            hit.url,
                            as_of=self.as_of,
                        )
                    else:
                        snapshots = (
                            mock_snapshots(hit.url)
                            if self.allow_local_fixtures
                            else discover_snapshots(hit.url, as_of=self.as_of)
                        )
                except Exception as exc:
                    source_audit["fetch_elapsed_ms"] = round(
                        (time.monotonic() - fetch_started) * 1000,
                        3,
                    )
                    source_audit.update(
                        outcome="retrieval_failure",
                        failure_category=(
                            f"snapshot_discovery:{exc.__class__.__name__}"
                        ),
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
                        failure_category="no_eligible_historical_snapshot",
                        reason="no_eligible_historical_snapshot",
                        fetch_elapsed_ms=round(
                            (time.monotonic() - fetch_started) * 1000,
                            3,
                        ),
                    )
                    sources_checked.append(source_audit)
                    continue
                snapshot_url = nearest.snapshot_url
                snapshot_at = nearest.timestamp

            try:
                if fixture_adapter is not None:
                    document = fixture_adapter.fetch_fixture_document(
                        hit.url,
                        as_of=self.as_of if self.mode == "backtest" else None,
                        allow_local_fixtures=self.allow_local_fixtures,
                        snapshot_url=snapshot_url,
                        snapshot_at=snapshot_at,
                        mode=self.mode,
                    )
                else:
                    document = self.cache.fetch(
                        hit.url,
                        as_of=self.as_of if self.mode == "backtest" else None,
                        allow_local_fixtures=self.allow_local_fixtures,
                        snapshot_url=snapshot_url,
                        snapshot_at=snapshot_at,
                        mode=self.mode,
                        publication_date_hint=hit.published_at,
                        publication_date_hint_source=(
                            hit.published_at_source or f"{self.search.name}_search_hit"
                            if hit.published_at
                            else None
                        ),
                    )
            except BudgetExceeded:
                raise
            except Exception as exc:
                source_audit["fetch_elapsed_ms"] = round(
                    (time.monotonic() - fetch_started) * 1000,
                    3,
                )
                source_audit.update(
                    outcome="retrieval_failure",
                    failure_category=f"fetch:{exc.__class__.__name__}",
                    reason=f"fetch:{exc.__class__.__name__}",
                )
                sources_checked.append(source_audit)
                retrieval_errors.append(str(source_audit["reason"]))
                continue

            source_audit["fetch_elapsed_ms"] = round(
                (time.monotonic() - fetch_started) * 1000,
                3,
            )
            source_audit["http_status"] = document.status_code

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
                    failure_category=(
                        document.rejection_reason
                        or "document_not_as_of_eligible"
                    ),
                    reason=document.rejection_reason or "document_not_as_of_eligible",
                )
                sources_checked.append(source_audit)
                if document.status_code in {401, 403, 451}:
                    source_audit["access_blocked_host"] = True
                    blocked_hosts.add(candidate_host)
                    audit_blocked_host_alternates(
                        blocked_host=candidate_host,
                        after_position=int(source_audit["scheduled_position"]),
                    )
                if source_audit["outcome"] == "retrieval_failure":
                    retrieval_errors.append(str(source_audit["reason"]))
                continue

            evidence.append(record)
            source_audit["extraction_entered"] = True
            extraction_document = document.model_copy(
                update={"text": document.text[: self.max_extraction_chars]}
            )
            source_audit.update(
                document_chars=len(document.text),
                extraction_input_chars=len(extraction_document.text),
            )
            extracted, attempt_errors, extraction_outcome = (
                self._extract_with_fallbacks(
                    document=extraction_document,
                    evidence_item_id=evidence_item_id,
                    node=node,
                )
            )
            extraction_errors.extend(attempt_errors)
            if not extracted:
                source_audit.update(
                    outcome="extraction_failure",
                    failure_category="extraction_failure",
                    reason=",".join(attempt_errors),
                )
                sources_checked.append(source_audit)
                if single_extraction_document_path:
                    break
                continue
            remaining_claims = self.max_evidence_claims - len(claims)
            eligible = eligible_claims_for_forecasting(
                extracted,
                mode=self.mode,  # type: ignore[arg-type]
                cutoff=self.as_of,
            )[:remaining_claims]
            if not eligible:
                extraction_errors.append("no_eligible_claims_extracted")
                source_audit.update(
                    outcome="extraction_failure",
                    failure_category="no_eligible_claims_extracted",
                    reason="no_eligible_claims_extracted",
                )
                sources_checked.append(source_audit)
                if single_extraction_document_path:
                    break
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
            source_audit.update(
                outcome=extraction_outcome,
                claim_created=True,
                reason=(
                    ",".join(attempt_errors)
                    if extraction_outcome != "claims_created"
                    else None
                ),
            )
            sources_checked.append(source_audit)
            if (
                successful_documents >= target_documents
                or len(claims) >= self.max_evidence_claims
            ):
                break

        claims = eligible_claims_for_forecasting(
            claims,
            mode=self.mode,  # type: ignore[arg-type]
            cutoff=self.as_of,
        )
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
