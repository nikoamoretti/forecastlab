from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from forecastlab.aggregation import clip_aggregated_probability, clip_track_probability
from forecastlab.schemas import ForecastGraph

METHOD = "dependency_discounted_weighted_mean_v1"


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
