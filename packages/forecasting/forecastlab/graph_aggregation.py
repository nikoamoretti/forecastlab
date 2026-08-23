from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from forecastlab.aggregation import clip_aggregated_probability, clip_track_probability
from forecastlab.schemas import (
    ForecastAggregation,
    ForecastGraph,
    ForecastNodeContribution,
    ForecastNodeRun,
)
from forecastlab.timeutil import utcnow

METHOD = "dependency_discounted_weighted_mean_v1"
LOG_ODDS_METHOD = "importance_weighted_log_odds_v1"
LOG_ODDS_FORMULA = (
    "Normalize graph importance weights, convert each node probability to log odds, "
    "sum normalized_weight × log_odds, then apply the logistic function."
)


class ForecastAggregationError(ValueError):
    """Raised when a graph and its node runs cannot form a complete aggregation."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = list(dict.fromkeys(reasons))
        super().__init__(f"Forecast aggregation failed: {', '.join(self.reasons)}")


@dataclass(frozen=True)
class NodeContribution:
    node_id: str
    raw_probability: float | None
    clipped_probability: float | None
    raw_importance_weight: float
    dependency_ids: list[str]
    parent_node_id: str | None
    missing_dependency_ids: list[str]
    dependency_factor: float
    adjusted_weight: float
    normalized_weight: float
    probability_contribution: float
    included: bool
    failure_reason: str | None = None


@dataclass(frozen=True)
class GraphAggregationBreakdown:
    method: str
    included_node_ids: list[str]
    missing_node_ids: list[str]
    contributions: list[NodeContribution]
    normalization_denominator: float
    unbounded_probability: float | None
    final_probability: float | None
    ensemble_probability: float | None
    track_spread: float | None
    calculation_trace: list[dict[str, Any]]
    shrinkage: float = 0.0
    formula: str = (
        "For each available node: adjusted_weight = importance_weight / "
        "(1 + distinct_dependency_and_parent_relationship_count). Normalize adjusted weights, "
        "multiply each by its clipped node probability, sum contributions, and clip final output to [0.02, 0.98]."
    )


class GraphAggregator:
    """Build a standalone, deterministic weighted-log-odds aggregation audit record.

    This service intentionally has no execution-engine call site yet. Graph execution keeps
    using its existing aggregation policy until a later integration task explicitly replaces it.
    """

    def __init__(
        self,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._clock = clock or utcnow

    @staticmethod
    def _validate(
        graph: ForecastGraph,
        node_runs: list[ForecastNodeRun],
    ) -> tuple[str, dict[str, ForecastNodeRun], dict[str, float]]:
        reasons: list[str] = []
        graph_node_ids = [node.id for node in graph.nodes]
        known_node_ids = set(graph_node_ids)
        if not graph_node_ids:
            reasons.append("graph_nodes_required")
        if len(known_node_ids) != len(graph_node_ids):
            reasons.append("duplicate_graph_node_id")

        runs_by_node: dict[str, ForecastNodeRun] = {}
        for node_run in node_runs:
            if node_run.node_id in runs_by_node:
                reasons.append(f"duplicate_node_forecast:{node_run.node_id}")
            runs_by_node[node_run.node_id] = node_run
        unknown_node_ids = sorted(set(runs_by_node) - known_node_ids)
        if unknown_node_ids:
            reasons.append(f"node_forecasts_not_in_graph:{','.join(unknown_node_ids)}")
        missing_node_ids = sorted(known_node_ids - set(runs_by_node))
        if missing_node_ids:
            reasons.append(f"missing_node_forecasts:{','.join(missing_node_ids)}")

        run_ids = {node_run.run_id for node_run in node_runs if node_run.run_id}
        if not node_runs or any(not node_run.run_id for node_run in node_runs):
            reasons.append("forecast_run_id_required")
        elif len(run_ids) > 1:
            reasons.append("node_forecasts_must_share_run_id")

        weights: dict[str, float] = {}
        for node in graph.nodes:
            weight = getattr(node, "importance_weight", None)
            if weight is None:
                reasons.append(f"missing_node_weight:{node.id}")
            elif isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight):
                reasons.append(f"invalid_node_weight:{node.id}")
            elif weight < 0:
                reasons.append(f"invalid_node_weight:{node.id}")
            else:
                weights[node.id] = float(weight)

        for node_id, node_run in runs_by_node.items():
            probability = getattr(node_run, "probability", None)
            if (
                isinstance(probability, bool)
                or not isinstance(probability, (int, float))
                or not math.isfinite(probability)
                or not 0.0 < probability < 1.0
            ):
                reasons.append(f"invalid_node_probability:{node_id}")

        if len(weights) == len(graph.nodes) and math.fsum(weights.values()) <= 0.0:
            reasons.append("zero_total_importance_weight")
        if reasons:
            raise ForecastAggregationError(reasons)
        return next(iter(run_ids)), runs_by_node, weights

    def aggregate(
        self,
        graph: ForecastGraph,
        node_runs: list[ForecastNodeRun],
        *,
        aggregation_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ForecastAggregation:
        forecast_run_id, runs_by_node, weights = self._validate(graph, node_runs)
        total_weight = math.fsum(weights.values())
        contributions: list[ForecastNodeContribution] = []

        for node in sorted(graph.nodes, key=lambda item: item.id):
            probability = float(runs_by_node[node.id].probability)
            normalized_weight = round(weights[node.id] / total_weight, 12)
            log_odds = round(math.log(probability / (1.0 - probability)), 12)
            contribution = round(normalized_weight * log_odds, 12)
            contributions.append(
                ForecastNodeContribution(
                    node_id=node.id,
                    node_question=node.question,
                    input_probability=probability,
                    raw_importance_weight=weights[node.id],
                    normalized_weight=normalized_weight,
                    log_odds=log_odds,
                    weighted_log_odds_contribution=contribution,
                )
            )

        combined_log_odds = round(
            math.fsum(item.weighted_log_odds_contribution for item in contributions),
            12,
        )
        if combined_log_odds >= 0:
            final_probability = 1.0 / (1.0 + math.exp(-combined_log_odds))
        else:
            exponential = math.exp(combined_log_odds)
            final_probability = exponential / (1.0 + exponential)
        final_probability = round(final_probability, 12)

        trace: list[dict[str, Any]] = [
            {
                "step": "weight_normalization",
                "method": LOG_ODDS_METHOD,
                "total_importance_weight": round(total_weight, 12),
                "normalized_weight_sum": round(
                    math.fsum(item.normalized_weight for item in contributions),
                    12,
                ),
            }
        ]
        trace.extend(
            {
                "step": "node_contribution",
                "node_id": item.node_id,
                "node": item.node_question,
                "input_probability": item.input_probability,
                "raw_importance_weight": item.raw_importance_weight,
                "normalized_weight": item.normalized_weight,
                "log_odds": item.log_odds,
                "contribution": item.weighted_log_odds_contribution,
            }
            for item in contributions
        )
        trace.append(
            {
                "step": "final",
                "combined_log_odds": combined_log_odds,
                "final_probability": final_probability,
            }
        )
        return ForecastAggregation(
            id=aggregation_id or self._id_factory(),
            forecast_run_id=forecast_run_id,
            method=LOG_ODDS_METHOD,
            final_probability=final_probability,
            calculation_trace=trace,
            node_contributions=contributions,
            created_at=created_at or self._clock(),
        )


def _spread(probabilities: list[float]) -> float | None:
    if not probabilities:
        return None
    if len(probabilities) == 1:
        return 0.0
    return round(max(probabilities) - min(probabilities), 12)


def aggregate_graph_probabilities(
    graph: ForecastGraph,
    node_probabilities: dict[str, float | None],
    *,
    failed_nodes: dict[str, str] | None = None,
) -> GraphAggregationBreakdown:
    """Aggregate node probabilities with deterministic, dependency-aware normalized weights."""

    failed_nodes = failed_nodes or {}
    nodes_by_id = {node.id: node for node in graph.nodes}
    unknown_ids = sorted(set(node_probabilities) - set(nodes_by_id))
    if unknown_ids:
        raise ValueError(f"node_probabilities_not_in_graph:{','.join(unknown_ids)}")

    prepared: list[dict[str, Any]] = []
    missing_node_ids: list[str] = []
    for node in sorted(graph.nodes, key=lambda item: item.id):
        raw_probability = node_probabilities.get(node.id)
        failure_reason = failed_nodes.get(node.id)
        dependency_ids = sorted(set(node.dependencies))
        relationships = set(dependency_ids)
        if node.parent_node_id is not None:
            relationships.add(node.parent_node_id)
        dependency_factor = 1.0 / (1.0 + len(relationships))
        missing_dependencies = sorted(
            dependency_id
            for dependency_id in relationships
            if dependency_id not in node_probabilities
            or node_probabilities.get(dependency_id) is None
            or dependency_id in failed_nodes
        )
        included = raw_probability is not None and failure_reason is None
        if not included:
            missing_node_ids.append(node.id)
        prepared.append(
            {
                "node": node,
                "raw_probability": raw_probability,
                "clipped_probability": (
                    clip_aggregated_probability(clip_track_probability(raw_probability)) if included else None
                ),
                "dependency_ids": dependency_ids,
                "missing_dependency_ids": missing_dependencies,
                "dependency_factor": dependency_factor,
                "included": included,
                "failure_reason": failure_reason or ("node_missing" if raw_probability is None else None),
            }
        )

    included = [item for item in prepared if item["included"]]
    use_equal_fallback = bool(included) and not any(float(item["node"].importance_weight) > 0 for item in included)
    adjusted_weights: dict[str, float] = {}
    for item in included:
        raw_weight = 1.0 if use_equal_fallback else float(item["node"].importance_weight)
        adjusted_weights[item["node"].id] = raw_weight * float(item["dependency_factor"])
    denominator = math.fsum(adjusted_weights.values())

    trace: list[dict[str, Any]] = [
        {
            "step": "weight_policy",
            "method": METHOD,
            "zero_importance_fallback": use_equal_fallback,
            "normalization_denominator": round(denominator, 12),
        }
    ]
    contributions: list[NodeContribution] = []
    contribution_values: list[float] = []
    included_node_ids: list[str] = []
    for item in prepared:
        node = item["node"]
        adjusted_weight = adjusted_weights.get(node.id, 0.0)
        normalized_weight = adjusted_weight / denominator if item["included"] and denominator > 0 else 0.0
        probability_contribution = (
            normalized_weight * float(item["clipped_probability"]) if item["included"] else 0.0
        )
        normalized_weight = round(normalized_weight, 12)
        probability_contribution = round(probability_contribution, 12)
        if item["included"]:
            included_node_ids.append(node.id)
            contribution_values.append(probability_contribution)
        contribution = NodeContribution(
            node_id=node.id,
            raw_probability=float(item["raw_probability"]) if item["raw_probability"] is not None else None,
            clipped_probability=item["clipped_probability"],
            raw_importance_weight=float(node.importance_weight),
            dependency_ids=item["dependency_ids"],
            parent_node_id=node.parent_node_id,
            missing_dependency_ids=item["missing_dependency_ids"],
            dependency_factor=round(float(item["dependency_factor"]), 12),
            adjusted_weight=round(adjusted_weight, 12),
            normalized_weight=normalized_weight,
            probability_contribution=probability_contribution,
            included=item["included"],
            failure_reason=item["failure_reason"],
        )
        contributions.append(contribution)
        trace.append(
            {
                "step": "node_contribution",
                "node_id": node.id,
                "included": item["included"],
                "importance_weight": float(node.importance_weight),
                "dependency_factor": contribution.dependency_factor,
                "normalized_weight": normalized_weight,
                "probability": item["clipped_probability"],
                "contribution": probability_contribution,
                "failure_reason": item["failure_reason"],
            }
        )

    unbounded = round(math.fsum(contribution_values), 12) if contribution_values else None
    final = clip_aggregated_probability(unbounded) if unbounded is not None else None
    final = round(final, 12) if final is not None else None
    trace.append(
        {
            "step": "final",
            "sum_of_contributions": unbounded,
            "clip_range": [0.02, 0.98],
            "final_probability": final,
        }
    )
    return GraphAggregationBreakdown(
        method=METHOD,
        included_node_ids=included_node_ids,
        missing_node_ids=missing_node_ids,
        contributions=contributions,
        normalization_denominator=round(denominator, 12),
        unbounded_probability=unbounded,
        final_probability=final,
        ensemble_probability=final,
        track_spread=_spread(
            [float(item["clipped_probability"]) for item in included if item["clipped_probability"] is not None]
        ),
        calculation_trace=trace,
    )
