from __future__ import annotations

import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from forecastlab.budget import Budget
from forecastlab.budgeted_provider import BudgetedModelProvider
from forecastlab.engine import ProgressFn
from forecastlab.errors import BudgetExceeded, PermanentProviderError, StructuredOutputError
from forecastlab.execution import ExecutionContext, assert_no_fixture_evidence
from forecastlab.graph_aggregation import METHOD as GRAPH_AGGREGATION_METHOD
from forecastlab.graph_aggregation import GraphAggregationBreakdown, aggregate_graph_probabilities
from forecastlab.graph_research import (
    GraphResearchExecutor,
    GraphResearchResult,
    NodeResearchPlan,
)
from forecastlab.ledger import RunUsageTotals, UsageLedger
from forecastlab.node_forecasting import NodeForecaster
from forecastlab.profiles import load_profile
from forecastlab.prompts import PromptBundle
from forecastlab.providers.base import ModelProvider, SearchProvider
from forecastlab.research_planning import (
    PARALLEL_RESEARCH_WORKERS,
    ResearchPlan,
    ResearchPlanner,
)
from forecastlab.run_cache import RunCache
from forecastlab.schemas import (
    EvidenceClaim,
    ForecastContract,
    ForecastGraph,
    ForecastNode,
    ForecastNodeRun,
    ForecastProfile,
    NodeForecast,
)
from forecastlab.timeutil import as_utc, utcnow

ResearchPersistFn = Callable[
    [ForecastNode, list[dict[str, Any]], list[dict[str, Any]], list[EvidenceClaim]],
    None,
]
ResearchPlanPersistFn = Callable[[ResearchPlan], None]


@dataclass
class ForecastNodeExecution:
    node: ForecastNode
    node_run: ForecastNodeRun | None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    claims: list[EvidenceClaim] = field(default_factory=list)
    claim_extraction_errors: list[str] = field(default_factory=list)
    research_plan: NodeResearchPlan | None = None
    queries_attempted: list[str] = field(default_factory=list)
    sources_checked: list[dict[str, Any]] = field(default_factory=list)
    research_warnings: list[str] = field(default_factory=list)
    error: str | None = None
    error_detail: str | None = None
    error_stage: str | None = None
    research_selected: bool = True
    skip_reason: str | None = None


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
    research_plan: ResearchPlan | None = None


@dataclass
class GraphNodeForecastResult:
    """Research and node-level forecasts before any graph aggregation policy is applied."""

    contract: ForecastContract
    graph: ForecastGraph
    nodes: list[ForecastNodeExecution]
    prompt_versions: dict[str, str]
    budget: dict[str, Any]
    stopped_early: bool
    stop_reason: str | None
    stop_stage: str | None
    fixture_evidence_used: bool = False
    research_plan: ResearchPlan | None = None


@dataclass
class ParallelResearchResult:
    node: ForecastNode
    result: GraphResearchResult | None = None
    error: Exception | None = None


def _emit(
    progress: ProgressFn | None,
    stage: str,
    message: str,
    pct: float,
    extra: dict[str, Any] | None = None,
) -> None:
    if progress:
        progress(stage, message, pct, extra)


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


def _selected_graph(
    graph: ForecastGraph,
    selected_node_ids: set[str],
) -> ForecastGraph:
    return graph.model_copy(
        update={
            "nodes": [
                node.model_copy(
                    update={
                        "parent_node_id": (
                            node.parent_node_id
                            if node.parent_node_id in selected_node_ids
                            else None
                        ),
                        "dependencies": [
                            dependency
                            for dependency in node.dependencies
                            if dependency in selected_node_ids
                        ],
                    }
                )
                for node in graph.nodes
                if node.id in selected_node_ids
            ]
        }
    )


def _forecast_node(
    *,
    contract: ForecastContract,
    node: ForecastNode,
    claims: list[EvidenceClaim],
    model: ModelProvider,
    budget: Budget,
    prompt_versions: dict[str, str],
    prompt_bundle: PromptBundle | None = None,
    max_output_tokens: int | None = None,
) -> NodeForecast:
    forecaster = NodeForecaster(
        BudgetedModelProvider(
            model,
            budget,
            stage=f"forecast_node:{node.id}",
            max_output_tokens_cap=max_output_tokens,
        ),
        prompt_bundle=prompt_bundle,
        prompt_versions=prompt_versions,
    )
    return forecaster.forecast(contract=contract, node=node, claims=claims)


def execute_parallel_research(
    nodes: list[ForecastNode],
    worker: Callable[[ForecastNode], GraphResearchResult],
    *,
    max_workers: int = PARALLEL_RESEARCH_WORKERS,
) -> list[ParallelResearchResult]:
    """Execute independent research concurrently and return graph order deterministically."""

    def protected(node: ForecastNode) -> ParallelResearchResult:
        try:
            return ParallelResearchResult(node=node, result=worker(node))
        except Exception as exc:  # retained as a node-level audit result
            return ParallelResearchResult(node=node, error=exc)

    workers = max(1, min(max_workers, len(nodes)))
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="forecastlab-graph-research",
    ) as executor:
        futures = {node.id: executor.submit(protected, node) for node in nodes}
        return [futures[node.id].result() for node in nodes]


def run_graph_node_forecasts(
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
    persist_research_plan: ResearchPlanPersistFn | None = None,
    capture_node_failures: bool = False,
    research_planner: ResearchPlanner | None = None,
) -> GraphNodeForecastResult:
    """Run Contract -> Graph -> Claims -> Node forecasts without aggregating them."""

    profile = profile or load_profile(profile_id)
    if profile.execution_strategy != "graph_nodes":
        raise ValueError("graph_execution_strategy_required")
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
    all_nodes = _topological_nodes(graph)
    planner = research_planner or ResearchPlanner()
    plan = planner.plan(
        graph,
        forecast_run_id=run_id,
        budget=budget,
    )
    if persist_research_plan is not None:
        persist_research_plan(plan)
    selected_ids = set(plan.selected_nodes)
    ordered_nodes = [node for node in all_nodes if node.id in selected_ids]
    allocation_by_node = plan.budget_allocation.get("per_node") or {}
    _emit(
        progress,
        "research_planning",
        (
            f"Selected {len(ordered_nodes)} of {len(all_nodes)} graph nodes "
            "within the frozen research budget"
        ),
        0.15,
        {
            "research_plan_id": plan.id,
            "selected_nodes": plan.selected_nodes,
            "skipped_nodes": plan.skipped_nodes,
        },
    )

    def research_worker(node: ForecastNode) -> GraphResearchResult:
        allocation = allocation_by_node[node.id]
        worker_cache = RunCache.create(
            run_id=cache.identity.run_id,
            model_provider=cache.identity.model_provider,
            search_provider=cache.identity.search_provider,
            mode=cache.identity.mode,
            as_of=as_of,
            configuration_hash=cache.identity.configuration_hash,
        )
        return GraphResearchExecutor(
            model=model,
            search=search,
            profile=profile,
            budget=budget,
            cache=worker_cache,
            run_id=run_id,
            mode=mode,
            as_of=as_of,
            allow_local_fixtures=allow_local_fixtures,
            max_queries_per_node=int(allocation["searches"]),
            max_fetches_per_node=int(allocation["fetches"]),
            max_evidence_claims=int(allocation["max_evidence_claims"]),
            max_extraction_chars=int(allocation["evidence_document_max_chars"]),
            research_plan_output_tokens=int(
                allocation["research_plan_output_tokens"]
            ),
            evidence_extraction_output_tokens=int(
                allocation["evidence_extraction_output_tokens"]
            ),
            prompt_bundle=prompt_bundle,
            prompt_versions=prompt_versions,
        ).execute(node)

    _emit(
        progress,
        "node_research",
        f"Researching {len(ordered_nodes)} selected graph nodes concurrently",
        0.2,
    )
    parallel_results = execute_parallel_research(
        ordered_nodes,
        research_worker,
        max_workers=int(
            plan.budget_allocation.get("parallel_research_workers")
            or PARALLEL_RESEARCH_WORKERS
        ),
    )
    research_by_node = {item.node.id: item for item in parallel_results}
    executions_by_node: dict[str, ForecastNodeExecution] = {}
    stopped = False

    for index, node in enumerate(ordered_nodes):
        pct = 0.45 + 0.35 * (index / max(1, len(ordered_nodes)))
        evidence: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        claims: list[EvidenceClaim] = []
        extraction_errors: list[str] = []
        research_plan: NodeResearchPlan | None = None
        queries_attempted: list[str] = []
        sources_checked: list[dict[str, Any]] = []
        research_warnings: list[str] = []
        stage = "node_research"
        try:
            parallel = research_by_node[node.id]
            if parallel.error is not None:
                raise parallel.error
            research = parallel.result
            assert research is not None
            evidence = research.evidence
            rejected = research.rejected
            claims = research.claims
            extraction_errors = research.extraction_errors
            research_plan = research.plan
            queries_attempted = research.queries_attempted
            sources_checked = research.sources_checked
            research_warnings = research.planning_warnings
            if persist_research is not None:
                persist_research(node, evidence, rejected, claims)
            if research.failure is not None:
                if not capture_node_failures:
                    raise StructuredOutputError(research.failure.code)
                executions_by_node[node.id] = ForecastNodeExecution(
                    node=node,
                    node_run=None,
                    evidence=evidence,
                    rejected=rejected,
                    claims=claims,
                    claim_extraction_errors=list(dict.fromkeys(extraction_errors)),
                    research_plan=research_plan,
                    queries_attempted=queries_attempted,
                    sources_checked=sources_checked,
                    research_warnings=research_warnings,
                    error=research.failure.code,
                    error_detail=research.failure.reason,
                    error_stage="node_research",
                )
                _emit(
                    progress,
                    "node_failed",
                    f"Graph node {index + 1} produced no eligible evidence",
                    pct + 0.05,
                    {
                        "node_id": node.id,
                        "error": research.failure.code,
                        "reason": research.failure.reason,
                    },
                )
                continue
            stage = "node_forecast"
            _emit(
                progress,
                "node_forecast",
                f"Forecasting selected graph node {index + 1} of {len(ordered_nodes)}",
                pct,
            )
            allocation = allocation_by_node[node.id]
            node_forecast = _forecast_node(
                contract=contract,
                node=node,
                claims=claims,
                model=model,
                budget=budget,
                prompt_versions=prompt_versions,
                prompt_bundle=prompt_bundle,
                max_output_tokens=int(allocation["node_forecast_output_tokens"]),
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
            executions_by_node[node.id] = ForecastNodeExecution(
                node=node,
                node_run=node_run,
                evidence=evidence,
                rejected=rejected,
                claims=claims,
                claim_extraction_errors=list(dict.fromkeys(extraction_errors)),
                research_plan=research_plan,
                queries_attempted=queries_attempted,
                sources_checked=sources_checked,
                research_warnings=research_warnings,
            )
        except BudgetExceeded as exc:
            executions_by_node[node.id] = ForecastNodeExecution(
                node=node,
                node_run=None,
                evidence=evidence,
                rejected=rejected,
                claims=claims,
                claim_extraction_errors=list(dict.fromkeys(extraction_errors)),
                research_plan=research_plan,
                queries_attempted=queries_attempted,
                sources_checked=sources_checked,
                research_warnings=research_warnings,
                error=str(exc),
                error_detail=str(exc),
                error_stage=exc.stage,
            )
            stopped = True
            _emit(
                progress,
                "budget",
                f"Selected node stopped early: {exc.reason}",
                0.82,
                {"node_id": node.id, "stage": exc.stage},
            )
        except StructuredOutputError as exc:
            if not capture_node_failures or stage != "node_forecast":
                raise
            executions_by_node[node.id] = ForecastNodeExecution(
                node=node,
                node_run=None,
                evidence=evidence,
                rejected=rejected,
                claims=claims,
                claim_extraction_errors=list(dict.fromkeys(extraction_errors)),
                research_plan=research_plan,
                queries_attempted=queries_attempted,
                sources_checked=sources_checked,
                research_warnings=research_warnings,
                error=str(exc),
                error_detail=str(exc),
                error_stage=stage,
            )
            _emit(
                progress,
                "node_failed",
                f"Node forecast {index + 1} failed validation",
                pct + 0.05,
                {"node_id": node.id, "error": str(exc)},
            )
        except PermanentProviderError as exc:
            if not capture_node_failures:
                raise
            executions_by_node[node.id] = ForecastNodeExecution(
                node=node,
                node_run=None,
                evidence=evidence,
                rejected=rejected,
                claims=claims,
                claim_extraction_errors=list(dict.fromkeys(extraction_errors)),
                research_plan=research_plan,
                queries_attempted=queries_attempted,
                sources_checked=sources_checked,
                research_warnings=research_warnings,
                error=str(exc),
                error_detail=str(exc),
                error_stage=stage,
            )
            _emit(
                progress,
                "node_failed",
                f"Graph node {index + 1} failed permanently",
                pct + 0.05,
                {"node_id": node.id, "stage": stage, "error": str(exc)},
            )

        except Exception as exc:
            if not capture_node_failures:
                raise
            executions_by_node[node.id] = ForecastNodeExecution(
                node=node,
                node_run=None,
                evidence=evidence,
                rejected=rejected,
                claims=claims,
                claim_extraction_errors=list(dict.fromkeys(extraction_errors)),
                research_plan=research_plan,
                queries_attempted=queries_attempted,
                sources_checked=sources_checked,
                research_warnings=research_warnings,
                error="research_execution_failure",
                error_detail=f"{exc.__class__.__name__}:{exc}",
                error_stage=stage,
            )

    skipped_reasons = plan.budget_allocation.get("skipped_reasons") or {}
    for node in all_nodes:
        if node.id not in selected_ids:
            executions_by_node[node.id] = ForecastNodeExecution(
                node=node,
                node_run=None,
                research_selected=False,
                skip_reason=str(
                    skipped_reasons.get(node.id)
                    or "lower_priority_or_budget_limited"
                ),
            )
    results = [executions_by_node[node.id] for node in all_nodes]
    all_urls = [
        item.get("url") or ""
        for result in results
        for item in result.evidence + result.rejected
    ]
    fixture_used = any("fixtures.forecastlab.local" in url for url in all_urls if url)
    if execution is not None and execution.effective_mode == "live":
        assert_no_fixture_evidence(all_urls, live=True)

    return GraphNodeForecastResult(
        contract=contract,
        graph=graph,
        nodes=results,
        prompt_versions=prompt_versions,
        budget=budget.snapshot(),
        stopped_early=stopped or budget.state.stopped,
        stop_reason=budget.state.stop_reason,
        stop_stage=budget.state.stop_stage,
        fixture_evidence_used=fixture_used,
        research_plan=plan,
    )


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
    persist_research_plan: ResearchPlanPersistFn | None = None,
) -> GraphEngineResult:
    """Run the original opt-in graph path with its existing aggregation policy."""

    effective_profile = profile or load_profile(profile_id)
    if effective_profile.aggregation_method != GRAPH_AGGREGATION_METHOD:
        raise ValueError("graph_aggregation_method_required")
    node_result = run_graph_node_forecasts(
        contract=contract,
        graph=graph,
        profile_id=profile_id,
        mode=mode,
        as_of=as_of,
        model=model,
        search=search,
        run_id=run_id,
        allow_local_fixtures=allow_local_fixtures,
        progress=progress,
        profile=effective_profile,
        execution=execution,
        prompt_bundle=prompt_bundle,
        cache=cache,
        ledger=ledger,
        pricing_catalog=pricing_catalog,
        prior_elapsed_seconds=prior_elapsed_seconds,
        persist_research=persist_research,
        persist_research_plan=persist_research_plan,
    )

    selected_node_ids = {
        item.node.id for item in node_result.nodes if item.research_selected
    }
    aggregation_graph = _selected_graph(node_result.graph, selected_node_ids)
    probabilities = {
        item.node.id: item.node_run.probability if item.node_run is not None else None
        for item in node_result.nodes
        if item.research_selected
    }
    failures = {
        item.node.id: item.error or "node_failed"
        for item in node_result.nodes
        if item.research_selected and item.node_run is None
    }
    _emit(progress, "aggregate", "Aggregating node probabilities with graph weights", 0.86)
    aggregation = aggregate_graph_probabilities(
        aggregation_graph,
        probabilities,
        failed_nodes=failures,
    )
    contribution_by_node = {item.node_id: item for item in aggregation.contributions}
    for item in node_result.nodes:
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
    return GraphEngineResult(
        contract=node_result.contract,
        graph=node_result.graph,
        nodes=node_result.nodes,
        aggregation=aggregation,
        prompt_versions=node_result.prompt_versions,
        budget=node_result.budget,
        stopped_early=node_result.stopped_early,
        stop_reason=node_result.stop_reason,
        stop_stage=node_result.stop_stage,
        fixture_evidence_used=node_result.fixture_evidence_used,
        partial=bool(failures) and aggregation.ensemble_probability is not None,
        research_plan=node_result.research_plan,
    )
