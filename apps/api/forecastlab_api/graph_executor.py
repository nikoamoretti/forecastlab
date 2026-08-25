from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any, NoReturn

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.contracts import ForecastContractError
from forecastlab.engine import ProgressFn
from forecastlab.errors import (
    GraphForecastExecutionError,
    PermanentProviderError,
    StructuredOutputError,
)
from forecastlab.execution import ExecutionContext
from forecastlab.graph_aggregation import (
    LOG_ODDS_METHOD,
    ForecastAggregationError,
    GraphAggregator,
)
from forecastlab.graph_execution import (
    GraphNodeForecastResult,
    run_graph_node_forecasts,
)
from forecastlab.graphs import ForecastGraphError
from forecastlab.ledger import UsageLedger
from forecastlab.prompts import PromptBundle
from forecastlab.providers.base import ModelProvider, SearchProvider
from forecastlab.research_planning import ResearchPlan, ResearchPlanningError
from forecastlab.run_cache import RunCache
from forecastlab.schemas import (
    ForecastAggregation,
    ForecastGraph,
    ForecastNodeRun,
    ForecastProfile,
)
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.aggregations import store_forecast_aggregation
from forecastlab_api.models import (
    ForecastNodeRunRow,
    ForecastRun,
    ForecastVersion,
    GraphExecutionFailureRow,
)
from forecastlab_api.persist import jsonable
from forecastlab_api.research_plans import store_research_plan
from forecastlab_api.v1_execution import (
    ExecutionGraphResolution,
    ensure_execution_graph,
    persist_node_research,
    record_execution_graph_resolution,
)

GraphResolver = Callable[..., tuple[Any, Any]]
NodeRunner = Callable[..., GraphNodeForecastResult]
CRITICAL_IMPORTANCE_THRESHOLD = 0.8
MINIMUM_SUCCESSFUL_NODES = 3


class GraphForecastExecutor:
    """Persist the complete Contract -> Graph -> Claims -> Nodes -> Probability flow."""

    def __init__(
        self,
        session: Session,
        *,
        run: ForecastRun,
        profile: ForecastProfile,
        execution: ExecutionContext,
        model: ModelProvider,
        search: SearchProvider,
        allow_local_fixtures: bool,
        prompt_bundle: PromptBundle | None = None,
        progress: ProgressFn | None = None,
        ledger: UsageLedger | None = None,
        pricing_catalog: dict[str, Any] | None = None,
        prior_elapsed_seconds: float = 0.0,
        cache: RunCache | None = None,
        graph_resolver: GraphResolver = ensure_execution_graph,
        node_runner: NodeRunner = run_graph_node_forecasts,
        aggregator: GraphAggregator | None = None,
    ) -> None:
        self.session = session
        self.run = run
        self.profile = profile
        self.execution = execution
        self.model = model
        self.search = search
        self.allow_local_fixtures = allow_local_fixtures
        self.prompt_bundle = prompt_bundle
        self.progress = progress
        self.ledger = ledger
        self.pricing_catalog = pricing_catalog
        self.prior_elapsed_seconds = prior_elapsed_seconds
        self.cache = cache
        self.graph_resolver = graph_resolver
        self.node_runner = node_runner
        self.aggregator = aggregator or GraphAggregator()

    def execute(self) -> ForecastVersion:
        existing = self.session.scalar(
            select(ForecastVersion).where(ForecastVersion.run_id == self.run.id)
        )
        if existing is not None:
            return existing

        self._validate_profile()
        try:
            resolved_graph = self.graph_resolver(
                self.session,
                question=self.run.question,
                model=self.model,
                max_output_tokens=self.profile.max_output_tokens_per_call,
                prompt_bundle=self.prompt_bundle,
            )
        except ForecastContractError as exc:
            self._fail(stage="contract", reasons=exc.reasons, message=str(exc))
        except ForecastGraphError as exc:
            self._fail(
                stage="graph",
                reasons=exc.reasons,
                message=str(exc),
                audit=exc.audit,
            )
        except StructuredOutputError as exc:
            self._fail(stage="graph", reasons=[str(exc)], message=str(exc))
        except PermanentProviderError as exc:
            self._fail(
                stage="graph",
                reasons=["graph_generation_provider_failure"],
                message=str(exc),
            )

        resolution: ExecutionGraphResolution | None
        if isinstance(resolved_graph, ExecutionGraphResolution):
            resolution = resolved_graph
            contract = resolution.contract
            graph = resolution.graph
        else:
            contract, graph = resolved_graph
            resolution = None

        if contract is None:
            self._fail(
                stage="contract",
                reasons=["approved_forecast_contract_required"],
                message="An approved Forecast Contract is required",
            )
        if graph is None:
            self._fail(
                stage="graph",
                reasons=["approved_forecast_graph_required"],
                message="An approved Forecast Graph could not be loaded or generated",
            )

        if resolution is None:
            resolution = ExecutionGraphResolution(
                contract=contract,
                graph=graph,
                status="reused",
                model_request_issued=False,
                generation_audit=graph.generation_audit,
            )
        record_execution_graph_resolution(self.run, resolution)
        self.session.commit()

        self._emit("graph", "Executing the approved Forecast Graph", 0.12)

        def persist_plan(plan: ResearchPlan) -> None:
            store_research_plan(self.session, plan)
            self.session.commit()

        try:
            node_result = self.node_runner(
                contract=contract,
                graph=graph,
                profile_id=self.execution.profile_id,
                mode=self.execution.effective_mode,
                as_of=as_utc(self.run.as_of) if self.run.as_of else None,
                model=self.model,
                search=self.search,
                run_id=self.run.id,
                allow_local_fixtures=self.allow_local_fixtures,
                progress=self.progress,
                profile=self.profile,
                execution=self.execution,
                prompt_bundle=self.prompt_bundle,
                cache=self.cache,
                ledger=self.ledger,
                pricing_catalog=self.pricing_catalog,
                prior_elapsed_seconds=self.prior_elapsed_seconds,
                persist_research=lambda _node, evidence, rejected, claims: persist_node_research(
                    self.session,
                    run=self.run,
                    evidence=evidence,
                    rejected=rejected,
                    claims=claims,
                ),
                persist_research_plan=persist_plan,
                capture_node_failures=True,
            )
        except ResearchPlanningError as exc:
            self._fail(
                stage="research_planning",
                reasons=exc.reasons,
                message=str(exc),
                audit=exc.audit,
            )
        except StructuredOutputError as exc:
            self._fail(stage="node_research", reasons=[str(exc)], message=str(exc))

        failures = self._node_failures(node_result)
        fatal, failed_node_ids, reliability = self._apply_failure_policy(
            node_result,
            failures,
        )
        if fatal:
            self._persist_partial_result(node_result, failures, reliability=reliability)
            reasons = [failure["error_code"] for failure in failures]
            self._raise_failure(
                stage=str(failures[0]["stage"]),
                reasons=reasons,
                message=(
                    "Graph execution stopped because a critical node failed or too few "
                    "eligible node forecasts remained"
                ),
            )

        node_runs = [
            execution.node_run
            for execution in node_result.nodes
            if execution.node_run is not None
            and execution.node.id not in failed_node_ids
        ]
        aggregation_graph = self._aggregation_graph(
            node_result.graph,
            included_node_ids={node_run.node_id for node_run in node_runs},
        )
        if failures:
            self._persist_failures(failures)
            self._emit(
                "aggregate",
                "Aggregating eligible node forecasts with reduced research coverage",
                0.88,
            )
        else:
            self._emit("aggregate", "Aggregating complete node forecasts", 0.88)
        try:
            aggregation = self.aggregator.aggregate(aggregation_graph, node_runs)
        except ForecastAggregationError as exc:
            failure = {
                "node_id": None,
                "stage": "aggregation",
                "error_code": "aggregation_failed",
                "error_message": str(exc),
                "critical_node": True,
                "impact": "forecast_failed",
            }
            self._persist_partial_result(
                node_result,
                [failure],
                reliability=reliability,
            )
            self._raise_failure(stage="aggregation", reasons=exc.reasons, message=str(exc))

        if failures:
            aggregation = aggregation.model_copy(
                update={
                    "calculation_trace": [
                        {
                            "step": "node_failure_tolerance",
                            **reliability,
                        },
                        *aggregation.calculation_trace,
                    ]
                }
            )

        weighted_runs = self._apply_aggregation_weights(node_runs, aggregation)
        version = self._persist_success(
            node_result,
            weighted_runs,
            aggregation,
            failures=failures,
            reliability=reliability,
        )
        self.session.commit()
        return version

    def _validate_profile(self) -> None:
        reasons: list[str] = []
        if self.profile.execution_strategy != "graph_nodes":
            reasons.append("graph_execution_strategy_required")
        for enabled, reason in (
            (self.profile.graph_generation_enabled, "graph_generation_must_be_enabled"),
            (self.profile.evidence_claims_enabled, "evidence_claims_must_be_enabled"),
            (self.profile.node_forecasting_enabled, "node_forecasting_must_be_enabled"),
            (self.profile.graph_aggregation_enabled, "graph_aggregation_must_be_enabled"),
        ):
            if not enabled:
                reasons.append(reason)
        if self.profile.aggregation_method != LOG_ODDS_METHOD:
            reasons.append("graph_log_odds_aggregation_required")
        if reasons:
            self._fail(
                stage="profile",
                reasons=reasons,
                message=f"Invalid graph profile: {self.profile.id}",
            )

    def _node_failures(self, result: GraphNodeForecastResult) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        for execution in result.nodes:
            if not execution.research_selected:
                continue
            if execution.node_run is None:
                raw_error = execution.error or "node_forecast_missing"
                if raw_error == "node_forecast_evidence_required":
                    error_code = "no_eligible_evidence"
                elif raw_error.startswith("Budget exceeded"):
                    error_code = "budget_exceeded"
                else:
                    error_code = raw_error
                failures.append(
                    {
                        "node_id": execution.node.id,
                        "stage": execution.error_stage or "node_forecast",
                        "error_code": error_code,
                        "error_message": execution.error_detail or raw_error,
                        "research_plan": (
                            execution.research_plan.model_dump(mode="json")
                            if execution.research_plan is not None
                            else {}
                        ),
                        "queries_attempted": execution.queries_attempted,
                        "sources_checked": execution.sources_checked,
                    }
                )
                continue
            probability = execution.node_run.probability
            if not 0.0 < probability < 1.0:
                failures.append(
                    {
                        "node_id": execution.node.id,
                        "stage": "node_forecast",
                        "error_code": "invalid_node_probability",
                        "error_message": (
                            f"Node probability must be strictly between 0 and 1 for log-odds aggregation: "
                            f"{probability}"
                        ),
                        "research_plan": (
                            execution.research_plan.model_dump(mode="json")
                            if execution.research_plan is not None
                            else {}
                        ),
                        "queries_attempted": execution.queries_attempted,
                        "sources_checked": execution.sources_checked,
                    }
                )
        return failures

    @staticmethod
    def _critical_node(node: Any) -> bool:
        return float(node.importance_weight) >= CRITICAL_IMPORTANCE_THRESHOLD

    def _apply_failure_policy(
        self,
        result: GraphNodeForecastResult,
        failures: list[dict[str, Any]],
    ) -> tuple[bool, set[str], dict[str, Any]]:
        nodes_by_id = {node.id: node for node in result.graph.nodes}
        failed_node_ids = {
            str(failure["node_id"])
            for failure in failures
            if failure.get("node_id") is not None
        }
        critical_node_ids = {
            node.id for node in result.graph.nodes if self._critical_node(node)
        }
        successful_node_ids = {
            execution.node.id
            for execution in result.nodes
            if execution.node_run is not None
            and execution.node.id not in failed_node_ids
        }
        minimum_required = min(MINIMUM_SUCCESSFUL_NODES, len(result.graph.nodes))
        critical_failures = sorted(failed_node_ids & critical_node_ids)
        fatal = bool(critical_failures) or len(successful_node_ids) < minimum_required

        for failure in failures:
            node_id = failure.get("node_id")
            critical = node_id is None or str(node_id) in critical_node_ids
            failure["critical_node"] = critical
            failure["impact"] = (
                "forecast_failed" if fatal else "excluded_reduced_confidence"
            )

        total_weight = sum(float(node.importance_weight) for node in result.graph.nodes)
        included_weight = sum(
            float(nodes_by_id[node_id].importance_weight)
            for node_id in successful_node_ids
        )
        coverage_factor = included_weight / total_weight if total_weight > 0 else 0.0
        reliability = {
            "policy": "critical_node_gate_v1",
            "critical_importance_threshold": CRITICAL_IMPORTANCE_THRESHOLD,
            "minimum_successful_nodes": minimum_required,
            "critical_node_ids": sorted(critical_node_ids),
            "failed_node_ids": sorted(failed_node_ids),
            "included_node_ids": sorted(successful_node_ids),
            "research_plan_id": (
                result.research_plan.id
                if result.research_plan is not None
                else None
            ),
            "planned_selected_node_ids": (
                result.research_plan.selected_nodes
                if result.research_plan is not None
                else [node.id for node in result.graph.nodes]
            ),
            "planned_skipped_node_ids": (
                result.research_plan.skipped_nodes
                if result.research_plan is not None
                else []
            ),
            "critical_failure_ids": critical_failures,
            "research_coverage_factor": round(coverage_factor, 12),
            "impact": "forecast_failed" if fatal else (
                "reduced_confidence" if failures else "none"
            ),
        }
        return fatal, failed_node_ids, reliability

    @staticmethod
    def _aggregation_graph(
        graph: ForecastGraph,
        *,
        included_node_ids: set[str],
    ) -> ForecastGraph:
        nodes = [
            node.model_copy(
                update={
                    "parent_node_id": (
                        node.parent_node_id
                        if node.parent_node_id in included_node_ids
                        else None
                    ),
                    "dependencies": [
                        dependency
                        for dependency in node.dependencies
                        if dependency in included_node_ids
                    ],
                }
            )
            for node in graph.nodes
            if node.id in included_node_ids
        ]
        return graph.model_copy(update={"nodes": nodes})

    @staticmethod
    def _apply_aggregation_weights(
        node_runs: list[ForecastNodeRun],
        aggregation: ForecastAggregation,
    ) -> list[ForecastNodeRun]:
        by_node = {item.node_id: item for item in aggregation.node_contributions}
        return [
            node_run.model_copy(
                update={
                    "raw_importance_weight": by_node[node_run.node_id].raw_importance_weight,
                    "dependency_factor": 1.0,
                    "normalized_weight": by_node[node_run.node_id].normalized_weight,
                    # Signed log-odds contributions live on ForecastAggregation. The legacy
                    # non-negative probability-contribution field remains a compatibility value.
                    "probability_contribution": 0.0,
                }
            )
            for node_run in node_runs
        ]

    def _persist_node_runs(self, node_runs: list[ForecastNodeRun]) -> None:
        for node_run in node_runs:
            existing = self.session.scalar(
                select(ForecastNodeRunRow).where(
                    ForecastNodeRunRow.forecast_run_id == self.run.id,
                    ForecastNodeRunRow.node_id == node_run.node_id,
                )
            )
            if existing is not None:
                if (
                    existing.probability != node_run.probability
                    or existing.reasoning != node_run.reasoning
                    or existing.supporting_claim_ids_json != json.dumps(node_run.supporting_claim_ids)
                    or existing.opposing_claim_ids_json != json.dumps(node_run.opposing_claim_ids)
                ):
                    self._fail(
                        stage="persistence",
                        reasons=[f"conflicting_node_run:{node_run.node_id}"],
                        message="A different node forecast is already stored for this run",
                    )
                existing.confidence = node_run.confidence
                existing.uncertainty = node_run.uncertainty
                existing.uncertainty_notes_json = json.dumps(node_run.uncertainty_notes)
                existing.model_used = node_run.model_used
                existing.raw_importance_weight = node_run.raw_importance_weight
                existing.dependency_factor = node_run.dependency_factor
                existing.normalized_weight = node_run.normalized_weight
                existing.probability_contribution = node_run.probability_contribution
                continue
            self.session.add(
                ForecastNodeRunRow(
                    id=node_run.id,
                    forecast_run_id=self.run.id,
                    node_id=node_run.node_id,
                    probability=node_run.probability,
                    confidence=node_run.confidence,
                    reasoning=node_run.reasoning,
                    supporting_claim_ids_json=json.dumps(node_run.supporting_claim_ids),
                    opposing_claim_ids_json=json.dumps(node_run.opposing_claim_ids),
                    uncertainty=node_run.uncertainty,
                    uncertainty_notes_json=json.dumps(node_run.uncertainty_notes),
                    model_used=node_run.model_used,
                    raw_importance_weight=node_run.raw_importance_weight,
                    dependency_factor=node_run.dependency_factor,
                    normalized_weight=node_run.normalized_weight,
                    probability_contribution=node_run.probability_contribution,
                    created_at=node_run.created_at,
                )
            )

    def _persist_failures(self, failures: list[dict[str, Any]]) -> None:
        for failure in failures:
            node_id = failure.get("node_id")
            stage = str(failure["stage"])
            error_code = str(failure["error_code"])
            existing = self.session.scalar(
                select(GraphExecutionFailureRow).where(
                    GraphExecutionFailureRow.forecast_run_id == self.run.id,
                    GraphExecutionFailureRow.node_id == node_id,
                    GraphExecutionFailureRow.stage == stage,
                    GraphExecutionFailureRow.error_code == error_code,
                )
            )
            research_plan_json = json.dumps(
                failure.get("research_plan") or {},
                sort_keys=True,
            )
            queries_attempted_json = json.dumps(
                failure.get("queries_attempted") or [],
            )
            sources_checked_json = json.dumps(
                failure.get("sources_checked") or [],
                sort_keys=True,
            )
            if existing is None:
                self.session.add(
                    GraphExecutionFailureRow(
                        id=str(uuid.uuid4()),
                        forecast_run_id=self.run.id,
                        node_id=node_id,
                        stage=stage,
                        error_code=error_code[:128],
                        error_message=str(failure["error_message"]),
                        research_plan_json=research_plan_json,
                        queries_attempted_json=queries_attempted_json,
                        sources_checked_json=sources_checked_json,
                        critical_node=bool(failure.get("critical_node")),
                        impact=str(failure.get("impact") or "forecast_failed"),
                    )
                )
            else:
                existing.error_message = str(failure["error_message"])
                existing.research_plan_json = research_plan_json
                existing.queries_attempted_json = queries_attempted_json
                existing.sources_checked_json = sources_checked_json
                existing.critical_node = bool(failure.get("critical_node"))
                existing.impact = str(failure.get("impact") or "forecast_failed")

    def _persist_partial_result(
        self,
        result: GraphNodeForecastResult,
        failures: list[dict[str, Any]],
        *,
        reliability: dict[str, Any] | None = None,
    ) -> None:
        self._persist_node_runs(
            [execution.node_run for execution in result.nodes if execution.node_run is not None]
        )
        self._persist_failures(failures)
        self._apply_result_metadata(result)
        self._store_artifact_ids(result, reliability=reliability)
        self.run.status = "failed"
        self.run.error_stage = str(failures[0]["stage"])
        self.run.error_message = "; ".join(str(item["error_code"]) for item in failures)
        self.run.progress_stage = "failed"
        self.run.progress_message = "Graph forecast stopped before aggregation"
        self._finish_failed_run()
        if not self.run.question.is_benchmark:
            self.run.question.status = "failed"
        self.session.commit()

    def _persist_success(
        self,
        result: GraphNodeForecastResult,
        node_runs: list[ForecastNodeRun],
        aggregation: ForecastAggregation,
        *,
        failures: list[dict[str, Any]],
        reliability: dict[str, Any],
    ) -> ForecastVersion:
        self._persist_node_runs(node_runs)
        self._persist_failures(failures)
        store_forecast_aggregation(self.session, aggregation)
        self._apply_result_metadata(result)
        self._store_artifact_ids(result, reliability=reliability)
        self.run.aggregation_json = json.dumps(jsonable(aggregation))
        self.run.status = "completed"
        self.run.error_stage = None
        self.run.error_message = None
        self.run.progress_pct = 100
        self.run.progress_stage = "report"
        self.run.progress_message = (
            "Forecast ready with reduced research coverage"
            if failures
            else "Forecast ready"
        )
        self.run.finished_at = utcnow()

        question = self.run.question
        if not question.is_benchmark:
            question.status = "complete"
            question.stale = False

        prior = self.session.scalar(
            select(ForecastVersion)
            .where(ForecastVersion.question_id == question.id)
            .order_by(ForecastVersion.created_at.desc())
            .limit(1)
        )
        probabilities = {node_run.node_id: node_run.probability for node_run in node_runs}
        included_node_ids = {node_run.node_id for node_run in node_runs}
        drivers = [
            {
                "factor": execution.node.question,
                "direction": "up" if execution.node_run and execution.node_run.probability >= 0.5 else "down",
                "importance": execution.node.importance_weight,
                "evidence_ids": (
                    [
                        *execution.node_run.supporting_claim_ids,
                        *execution.node_run.opposing_claim_ids,
                    ]
                    if execution.node_run is not None
                    else []
                ),
                "inference": False,
            }
            for execution in result.nodes
            if execution.node_run is not None
            and execution.node.id in included_node_ids
        ]
        counterarguments = [
            execution.node_run.reasoning
            for execution in result.nodes
            if execution.node_run is not None
            and execution.node.id in included_node_ids
            and (execution.node.node_type == "adversarial" or execution.node_run.opposing_claim_ids)
        ]
        claim_ids = list(
            dict.fromkeys(
                claim.id
                for execution in result.nodes
                if execution.node.id in included_node_ids
                for claim in execution.claims
            )
        )
        probability_values = list(probabilities.values())
        version = ForecastVersion(
            id=str(uuid.uuid4()),
            question_id=question.id,
            run_id=self.run.id,
            raw_track_probabilities_json=json.dumps(probabilities, sort_keys=True),
            ensemble_probability=aggregation.final_probability,
            aggregation_json=json.dumps(jsonable(aggregation)),
            shrinkage=0.0,
            track_spread=(
                round(max(probability_values) - min(probability_values), 12)
                if probability_values
                else None
            ),
            key_drivers_json=json.dumps(drivers),
            counterarguments_json=json.dumps(counterarguments),
            evidence_ids_json=json.dumps(claim_ids),
            trigger_event="run",
            previous_version_id=prior.id if prior else None,
        )
        self.session.add(version)
        self.session.flush()
        self._emit("report", "Graph forecast report is ready", 1.0)
        return version

    def _apply_result_metadata(self, result: GraphNodeForecastResult) -> None:
        budget = result.budget
        self.run.cost_usd = float(budget.get("total_cost_usd") or budget.get("cost_usd") or 0)
        self.run.total_cost_usd = self.run.cost_usd
        self.run.model_cost_usd = float(budget.get("model_cost_usd") or 0)
        self.run.search_cost_usd = float(budget.get("search_cost_usd") or 0)
        self.run.failed_attempt_cost_usd = float(budget.get("failed_attempt_cost_usd") or 0)
        self.run.tokens = int(budget.get("tokens") or budget.get("total_tokens") or 0)
        self.run.prompt_tokens = int(budget.get("prompt_tokens") or 0)
        self.run.completion_tokens = int(budget.get("completion_tokens") or 0)
        self.run.total_tokens = int(budget.get("total_tokens") or self.run.tokens)
        self.run.provider_request_count = int(budget.get("provider_request_count") or 0)
        self.run.cost_source = str(budget.get("cost_label") or "estimated")
        self.run.prompt_versions_json = json.dumps(result.prompt_versions)
        self.run.budget_json = json.dumps(jsonable(budget))
        self.run.disagreement_summary = None
        self.run.fixture_evidence_used = bool(result.fixture_evidence_used)

    def _store_artifact_ids(
        self,
        result: GraphNodeForecastResult,
        *,
        reliability: dict[str, Any] | None = None,
    ) -> None:
        try:
            snapshot = json.loads(self.run.execution_context_json or "{}")
        except json.JSONDecodeError:
            snapshot = {}
        snapshot["forecast_contract_id"] = result.contract.id
        snapshot["forecast_graph_id"] = result.graph.id
        if result.research_plan is not None:
            snapshot["research_plan_id"] = result.research_plan.id
        if reliability is not None:
            snapshot["graph_research_reliability"] = reliability
        self.run.execution_context_json = json.dumps(snapshot)

    def _fail(
        self,
        *,
        stage: str,
        reasons: list[str],
        message: str,
        audit: dict[str, Any] | None = None,
    ) -> NoReturn:
        failures = [
            {
                "node_id": None,
                "stage": stage,
                "error_code": reason,
                "error_message": message,
                "critical_node": True,
                "impact": "forecast_failed",
                "research_plan": audit or {},
            }
            for reason in reasons
        ]
        self._persist_failures(failures)
        self.run.status = "failed"
        self.run.error_stage = stage
        self.run.error_message = "; ".join(reasons)
        self.run.progress_stage = "failed"
        self.run.progress_message = message
        self._finish_failed_run()
        if not self.run.question.is_benchmark:
            self.run.question.status = "failed"
        self.session.commit()
        self._raise_failure(stage=stage, reasons=reasons, message=message)

    def _finish_failed_run(self) -> None:
        finished_at = utcnow()
        self.run.finished_at = finished_at
        if self.run.started_at is not None:
            elapsed_ms = int(
                (finished_at - as_utc(self.run.started_at)).total_seconds() * 1000
            )
            self.run.latency_ms = max(1, elapsed_ms)

    @staticmethod
    def _raise_failure(*, stage: str, reasons: list[str], message: str) -> NoReturn:
        raise GraphForecastExecutionError(reasons, stage=stage, message=message)

    def _emit(self, stage: str, message: str, pct: float) -> None:
        if self.progress is not None:
            self.progress(stage, message, pct, None)
