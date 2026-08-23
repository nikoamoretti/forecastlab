from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from forecastlab.budget import Budget, estimate_prompt_tokens
from forecastlab.engine import ProgressFn
from forecastlab.errors import BudgetExceeded, StructuredOutputError
from forecastlab.evidence_claims import EvidenceClaimError, EvidenceExtractor, eligible_claims_for_forecasting
from forecastlab.execution import ExecutionContext, assert_no_fixture_evidence
from forecastlab.graph_aggregation import METHOD as GRAPH_AGGREGATION_METHOD
from forecastlab.graph_aggregation import GraphAggregationBreakdown, aggregate_graph_probabilities
from forecastlab.ledger import RunUsageTotals, UsageLedger
from forecastlab.node_forecasting import NodeForecaster
from forecastlab.profiles import load_profile
from forecastlab.prompts import PromptBundle
from forecastlab.providers.base import ChatResult, ModelProvider, SearchProvider
from forecastlab.ranking import rank_hits
from forecastlab.run_cache import RunCache
from forecastlab.schemas import (
    EvidenceClaim,
    FetchedDocument,
    ForecastContract,
    ForecastGraph,
    ForecastNode,
    ForecastNodeRun,
    ForecastProfile,
    NodeForecast,
)
from forecastlab.timeutil import as_utc, utcnow
from forecastlab.wayback import discover_snapshots, mock_snapshots, nearest_eligible_snapshot

ResearchPersistFn = Callable[
    [ForecastNode, list[dict[str, Any]], list[dict[str, Any]], list[EvidenceClaim]],
    None,
]


@dataclass
class ForecastNodeExecution:
    node: ForecastNode
    node_run: ForecastNodeRun | None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    claims: list[EvidenceClaim] = field(default_factory=list)
    claim_extraction_errors: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class GraphEngineResult:
    contract: ForecastContract
    graph: ForecastGraph
    nodes: list[ForecastNodeExecution]
    aggregation: GraphAggregationBreakdown
    prompt_versions: dict[str, str]
    budget: dict[str, Any]
    stopped_early: bool
    stop_reason: str | None
    stop_stage: str | None
    fixture_evidence_used: bool = False
    partial: bool = False


class _BudgetedModel:
    """Apply the run budget to a structured model service's provider seam."""

    def __init__(self, delegate: ModelProvider, budget: Budget, *, stage: str) -> None:
        self.delegate = delegate
        self.budget = budget
        self.stage = stage
        self.name = delegate.name
        self.model = str(getattr(delegate, "model", delegate.name))

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        temperature: float = 0.2,
        timeout: float | None = None,
        max_output_tokens: int | None = None,
        estimated_input_tokens: int | None = None,
    ) -> ChatResult:
        estimated_input = estimated_input_tokens or estimate_prompt_tokens(system, user)
        allowed_output = self.budget.max_output_tokens_for_call(estimated_input)
        if max_output_tokens is not None:
            allowed_output = min(allowed_output, max_output_tokens)
        reservation = self.budget.reserve_model_call(
            self.stage,
            estimated_input_tokens=estimated_input,
            max_output_tokens=allowed_output,
        )
        try:
            result = self.delegate.complete_json(
                system=system,
                user=user,
                schema_name=schema_name,
                temperature=temperature,
                timeout=timeout,
                max_output_tokens=allowed_output,
                estimated_input_tokens=estimated_input,
            )
        except Exception:
            self.budget.release_reservation(reservation)
            raise
        self.budget.reconcile_model_call(reservation, result.usage)
        return result


def _emit(
    progress: ProgressFn | None,
    stage: str,
    message: str,
    pct: float,
    extra: dict[str, Any] | None = None,
) -> None:
    if progress:
        progress(stage, message, pct, extra)


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
            document.requested_snapshot_at.isoformat() if document.requested_snapshot_at else None
        ),
        "final_snapshot_url": document.final_snapshot_url,
        "final_snapshot_at": document.final_snapshot_at.isoformat() if document.final_snapshot_at else None,
        "archived_original_url": document.archived_original_url,
        "snapshot_verification_status": document.snapshot_verification_status,
        "status_code": document.status_code,
        "published_at_unknown": document.published_at_unknown,
    }


def _stable_id(kind: str, *parts: str) -> str:
    identity = ":".join(("forecastlab", "graph_forecaster_v1", kind, *parts))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))


def _no_snapshot_record(
    node: ForecastNode,
    *,
    run_id: str,
    url: str,
    title: str,
    source_class: str,
) -> dict[str, Any]:
    return {
        "id": _stable_id("evidence", run_id, node.id, url),
        "forecast_node_id": node.id,
        "subquestion": node.question,
        "url": url,
        "title": title,
        "publisher": None,
        "published_at": None,
        "retrieved_at": utcnow().isoformat(),
        "excerpt": "",
        "content_hash": "",
        "source_class": source_class,
        "as_of_eligible": False,
        "rejected": True,
        "rejection_reason": "no_eligible_historical_snapshot",
        "snapshot_url": None,
        "snapshot_at": None,
        "status_code": 0,
        "published_at_unknown": True,
    }


def _topological_nodes(graph: ForecastGraph) -> list[ForecastNode]:
    by_id = {node.id: node for node in graph.nodes}
    dependencies = {
        node.id: set(node.dependencies) | ({node.parent_node_id} if node.parent_node_id else set())
        for node in graph.nodes
    }
    ordered: list[ForecastNode] = []
    ready = [node.id for node in graph.nodes if not dependencies[node.id]]
    while ready:
        node_id = ready.pop(0)
        ordered.append(by_id[node_id])
        for candidate in graph.nodes:
            if node_id in dependencies[candidate.id]:
                dependencies[candidate.id].remove(node_id)
                if not dependencies[candidate.id] and candidate.id not in {item.id for item in ordered}:
                    if candidate.id not in ready:
                        ready.append(candidate.id)
    if len(ordered) != len(graph.nodes):
        raise StructuredOutputError("forecast_graph_dependency_cycle")
    return ordered


def _research_node(
    *,
    node: ForecastNode,
    run_id: str,
    profile: ForecastProfile,
    model: ModelProvider,
    search: SearchProvider,
    budget: Budget,
    cache: RunCache,
    mode: str,
    as_of: datetime | None,
    allow_local_fixtures: bool,
    prompt_bundle: PromptBundle | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[EvidenceClaim], list[str]]:
    evidence: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    claims: list[EvidenceClaim] = []
    extraction_errors: list[str] = []
    if profile.max_search_calls <= 0 or profile.max_fetched_documents <= 0:
        return evidence, rejected, claims, extraction_errors

    source_hint = " ".join(node.preferred_sources[:2])
    query = " ".join(part for part in (node.question, source_hint) if part).strip()
    budget.add_search(f"search_node:{node.id}")
    hits = rank_hits(cache.search(search, query, profile.search_results_per_subquestion))
    for hit in hits[: profile.fetches_per_subquestion]:
        snapshot_url = None
        snapshot_at = None
        if mode == "backtest" and as_of is not None:
            snapshots = (
                mock_snapshots(hit.url)
                if allow_local_fixtures
                else discover_snapshots(hit.url, as_of=as_of)
            )
            nearest = nearest_eligible_snapshot(snapshots, as_of)
            if nearest is None:
                rejected.append(
                    _no_snapshot_record(
                        node,
                        run_id=run_id,
                        url=hit.url,
                        title=hit.title,
                        source_class=hit.source_class,
                    )
                )
                continue
            snapshot_url = nearest.snapshot_url
            snapshot_at = nearest.timestamp

        budget.add_fetch(f"fetch_node:{node.id}")
        document = cache.fetch(
            hit.url,
            as_of=as_of if mode == "backtest" else None,
            allow_local_fixtures=allow_local_fixtures,
            snapshot_url=snapshot_url,
            snapshot_at=snapshot_at,
            mode=mode,
        )
        evidence_item_id = _stable_id("evidence", run_id, node.id, hit.url)
        record = _document_record(
            document,
            node=node,
            evidence_item_id=evidence_item_id,
            source_class=hit.source_class,
        )
        if document.rejected or not document.as_of_eligible:
            rejected.append(record)
            continue
        evidence.append(record)

        extractor = EvidenceExtractor(
            _BudgetedModel(model, budget, stage=f"extract_claims:{node.id}"),
            prompt_bundle=prompt_bundle,
        )
        try:
            extracted = extractor.extract(
                document,
                evidence_item_id=evidence_item_id,
                forecast_node_id=node.id,
                forecast_node_question=node.question,
                as_of=as_of if mode == "backtest" else None,
            )
        except EvidenceClaimError as exc:
            extraction_errors.extend(exc.reasons)
            continue
        claims.extend(
            claim.model_copy(
                update={
                    "id": _stable_id(
                        "claim",
                        run_id,
                        node.id,
                        evidence_item_id,
                        claim.supports_or_refutes,
                        claim.claim,
                        claim.excerpt,
                    )
                }
            )
            for claim in extracted
        )
    return evidence, rejected, eligible_claims_for_forecasting(claims, cutoff=as_of), extraction_errors


def _forecast_node(
    *,
    contract: ForecastContract,
    node: ForecastNode,
    claims: list[EvidenceClaim],
    model: ModelProvider,
    budget: Budget,
    prompt_versions: dict[str, str],
    prompt_bundle: PromptBundle | None = None,
) -> NodeForecast:
    forecaster = NodeForecaster(
        _BudgetedModel(model, budget, stage=f"forecast_node:{node.id}"),
        prompt_bundle=prompt_bundle,
        prompt_versions=prompt_versions,
    )
    return forecaster.forecast(contract=contract, node=node, claims=claims)


def run_graph_forecast_engine(
    *,
    contract: ForecastContract,
    graph: ForecastGraph,
    profile_id: str,
    mode: str,
    as_of: datetime | None,
    model: ModelProvider,
    search: SearchProvider,
    run_id: str,
    allow_local_fixtures: bool = True,
    progress: ProgressFn | None = None,
    profile: ForecastProfile | None = None,
    execution: ExecutionContext | None = None,
    prompt_bundle: PromptBundle | None = None,
    cache: RunCache | None = None,
    ledger: UsageLedger | None = None,
    pricing_catalog: dict[str, Any] | None = None,
    prior_elapsed_seconds: float = 0.0,
    persist_research: ResearchPersistFn | None = None,
) -> GraphEngineResult:
    """Run the opt-in Contract -> Graph -> Claims -> Node forecast pipeline."""

    profile = profile or load_profile(profile_id)
    if profile.execution_strategy != "graph_nodes":
        raise ValueError("graph_execution_strategy_required")
    if profile.aggregation_method != GRAPH_AGGREGATION_METHOD:
        raise ValueError("graph_aggregation_method_required")
    if contract.status != "approved":
        raise ValueError("approved_forecast_contract_required")
    if graph.status != "approved" or graph.contract_id != contract.id:
        raise ValueError("approved_forecast_graph_required")
    if as_of is not None:
        as_of = as_utc(as_of)
    if execution is not None:
        allow_local_fixtures = execution.fixture_evidence_allowed
        mode = execution.effective_mode

    model_provider = execution.model_provider if execution is not None else model.name
    search_provider = execution.search_provider if execution is not None else search.name
    configuration_hash = execution.configuration_hash if execution is not None else "none"
    cache = cache or RunCache.create(
        run_id=run_id,
        model_provider=model_provider,
        search_provider=search_provider,
        mode=mode,
        as_of=as_of,
        configuration_hash=configuration_hash,
    )
    totals = ledger.totals(run_id) if ledger is not None else RunUsageTotals()
    budget = Budget.from_persisted(
        profile,
        totals,
        provider=model_provider,
        model=(
            execution.model_name
            if execution is not None
            else str(getattr(model, "model", getattr(model, "name", "mock")))
        ),
        search_provider=search_provider,
        pricing_catalog=pricing_catalog,
        prior_elapsed_seconds=prior_elapsed_seconds,
    )
    prompt_versions = dict(profile.prompt_versions)
    ordered_nodes = _topological_nodes(graph)
    results: list[ForecastNodeExecution] = []
    stopped = False

    for index, node in enumerate(ordered_nodes):
        pct = 0.15 + 0.65 * (index / max(1, len(ordered_nodes)))
        _emit(progress, "node_research", f"Researching graph node {index + 1} of {len(ordered_nodes)}", pct)
        try:
            evidence, rejected, claims, extraction_errors = _research_node(
                node=node,
                run_id=run_id,
                profile=profile,
                model=model,
                search=search,
                budget=budget,
                cache=cache,
                mode=mode,
                as_of=as_of,
                allow_local_fixtures=allow_local_fixtures,
                prompt_bundle=prompt_bundle,
            )
            if persist_research is not None:
                persist_research(node, evidence, rejected, claims)
            _emit(progress, "node_forecast", f"Forecasting graph node {index + 1} of {len(ordered_nodes)}", pct + 0.05)
            node_forecast = _forecast_node(
                contract=contract,
                node=node,
                claims=claims,
                model=model,
                budget=budget,
                prompt_versions=prompt_versions,
                prompt_bundle=prompt_bundle,
            )
            node_run = ForecastNodeRun(
                id=str(uuid.uuid4()),
                run_id=run_id,
                node_id=node.id,
                probability=node_forecast.probability,
                confidence=node_forecast.confidence,
                reasoning=node_forecast.reasoning,
                supporting_claim_ids=node_forecast.supporting_claim_ids,
                opposing_claim_ids=node_forecast.opposing_claim_ids,
                uncertainty_notes=node_forecast.uncertainty_notes,
                model_used=node_forecast.model_used,
                uncertainty=node_forecast.uncertainty,
                created_at=utcnow(),
            )
            results.append(
                ForecastNodeExecution(
                    node=node,
                    node_run=node_run,
                    evidence=evidence,
                    rejected=rejected,
                    claims=claims,
                    claim_extraction_errors=list(dict.fromkeys(extraction_errors)),
                )
            )
        except BudgetExceeded as exc:
            results.append(ForecastNodeExecution(node=node, node_run=None, error=str(exc)))
            stopped = True
            _emit(progress, "budget", f"Stopped early: {exc.reason}", 0.82, {"stage": exc.stage})
            break

    completed_ids = {item.node.id for item in results}
    for node in ordered_nodes:
        if node.id not in completed_ids:
            results.append(ForecastNodeExecution(node=node, node_run=None, error="not_run_after_budget_stop"))

    probabilities = {
        item.node.id: item.node_run.probability if item.node_run is not None else None
        for item in results
    }
    failures = {item.node.id: item.error or "node_failed" for item in results if item.node_run is None}
    _emit(progress, "aggregate", "Aggregating node probabilities with graph weights", 0.86)
    aggregation = aggregate_graph_probabilities(graph, probabilities, failed_nodes=failures)
    contribution_by_node = {item.node_id: item for item in aggregation.contributions}
    for item in results:
        if item.node_run is None:
            continue
        contribution = contribution_by_node[item.node.id]
        item.node_run = item.node_run.model_copy(
            update={
                "raw_importance_weight": contribution.raw_importance_weight,
                "dependency_factor": contribution.dependency_factor,
                "normalized_weight": contribution.normalized_weight,
                "probability_contribution": contribution.probability_contribution,
            }
        )

    _emit(progress, "report", "Preparing the graph forecast report", 0.96)
    all_urls = [
        item.get("url") or ""
        for result in results
        for item in result.evidence + result.rejected
    ]
    fixture_used = any("fixtures.forecastlab.local" in url for url in all_urls if url)
    if execution is not None and execution.effective_mode == "live":
        assert_no_fixture_evidence(all_urls, live=True)

    return GraphEngineResult(
        contract=contract,
        graph=graph,
        nodes=results,
        aggregation=aggregation,
        prompt_versions=prompt_versions,
        budget=budget.snapshot(),
        stopped_early=stopped or budget.state.stopped,
        stop_reason=budget.state.stop_reason,
        stop_stage=budget.state.stop_stage,
        fixture_evidence_used=fixture_used,
        partial=bool(failures) and aggregation.ensemble_probability is not None,
    )
