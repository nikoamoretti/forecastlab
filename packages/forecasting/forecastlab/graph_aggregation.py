from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from forecastlab.aggregation import clip_aggregated_probability, clip_track_probability
from forecastlab.hashing import canonical_json, sha256_text
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
RELATIONSHIP_MASS_CONSERVING_LOG_ODDS_METHOD = (
    "relationship_mass_conserving_log_odds_v1"
)
RELATIONSHIP_MASS_CONSERVING_POLICY_VERSION = (
    "relationship_mass_conserving_log_odds_v1"
)
RELATIONSHIP_MASS_CONSERVING_FORMULA = (
    "Split each included node's canonical importance weight equally across itself, "
    "its direct parent, and its direct dependencies. Included recipients retain their "
    "shares; excluded recipients and excluded nodes send their mass to neutral log odds "
    "at probability 0.5. Normalize all retained mass by total graph importance, combine "
    "weighted node log odds, and apply the logistic function."
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


def _decimal_text(value: Decimal) -> str:
    """Return a stable, non-scientific representation for persisted mass audits."""

    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _decimal_number(value: Decimal) -> float:
    return round(float(value), 12)


class RelationshipMassConservingLogOddsAggregator:
    """Deterministically deconflict direct relationship mass without imputation.

    The full approved graph remains available to this service. Only supplied node runs
    contribute authored probabilities; every excluded or excluded-recipient share is
    retained as neutral residual mass.
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
    ) -> tuple[
        str,
        dict[str, Any],
        dict[str, ForecastNodeRun],
        dict[str, Decimal],
    ]:
        reasons: list[str] = []
        graph_node_ids = [node.id for node in graph.nodes]
        nodes_by_id = {node.id: node for node in graph.nodes}
        known_node_ids = set(graph_node_ids)
        if not graph_node_ids:
            reasons.append("graph_nodes_required")
        if len(known_node_ids) != len(graph_node_ids):
            reasons.append("duplicate_graph_node_id")

        for node in sorted(graph.nodes, key=lambda item: item.id):
            if node.parent_node_id == node.id or node.id in node.dependencies:
                reasons.append(f"self_relationship:{node.id}")
            if (
                node.parent_node_id is not None
                and node.parent_node_id not in known_node_ids
            ):
                reasons.append(
                    f"unknown_parent:{node.id}:{node.parent_node_id}"
                )
            for dependency_id in sorted(set(node.dependencies)):
                if dependency_id not in known_node_ids:
                    reasons.append(
                        f"unknown_dependency:{node.id}:{dependency_id}"
                    )

        runs_by_node: dict[str, ForecastNodeRun] = {}
        for node_run in node_runs:
            if node_run.node_id in runs_by_node:
                reasons.append(f"duplicate_node_forecast:{node_run.node_id}")
            runs_by_node[node_run.node_id] = node_run
        unknown_node_ids = sorted(set(runs_by_node) - known_node_ids)
        if unknown_node_ids:
            reasons.append(
                f"node_forecasts_not_in_graph:{','.join(unknown_node_ids)}"
            )

        run_ids = {node_run.run_id for node_run in node_runs if node_run.run_id}
        if not node_runs or any(not node_run.run_id for node_run in node_runs):
            reasons.append("forecast_run_id_required")
        elif len(run_ids) > 1:
            reasons.append("node_forecasts_must_share_run_id")

        weights: dict[str, Decimal] = {}
        for node in sorted(graph.nodes, key=lambda item: item.id):
            raw_weight = getattr(node, "importance_weight", None)
            if raw_weight is None:
                reasons.append(f"missing_node_weight:{node.id}")
                continue
            if isinstance(raw_weight, bool):
                reasons.append(f"invalid_node_weight:{node.id}")
                continue
            try:
                weight = Decimal(str(raw_weight))
            except (InvalidOperation, ValueError):
                reasons.append(f"invalid_node_weight:{node.id}")
                continue
            if not weight.is_finite() or weight < 0:
                reasons.append(f"invalid_node_weight:{node.id}")
                continue
            weights[node.id] = weight

        for node_id, node_run in sorted(runs_by_node.items()):
            probability = getattr(node_run, "probability", None)
            if (
                isinstance(probability, bool)
                or not isinstance(probability, (int, float))
                or not math.isfinite(probability)
                or not 0.0 < probability < 1.0
            ):
                reasons.append(f"invalid_node_probability:{node_id}")

        if len(weights) == len(graph.nodes) and sum(
            weights.values(), start=Decimal("0")
        ) <= 0:
            reasons.append("zero_total_importance_weight")
        if reasons:
            raise ForecastAggregationError(reasons)
        return next(iter(run_ids)), nodes_by_id, runs_by_node, weights

    def aggregate(
        self,
        graph: ForecastGraph,
        node_runs: list[ForecastNodeRun],
        *,
        exclusion_origins: dict[str, str] | None = None,
        aggregation_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ForecastAggregation:
        (
            forecast_run_id,
            nodes_by_id,
            runs_by_node,
            weights,
        ) = self._validate(graph, node_runs)
        exclusion_origins = exclusion_origins or {}
        included_node_ids = set(runs_by_node)
        excluded_node_ids = set(nodes_by_id) - included_node_ids

        with localcontext() as context:
            context.prec = 80
            total_weight = sum(weights.values(), start=Decimal("0"))
            included_raw_weight = sum(
                (weights[node_id] for node_id in included_node_ids),
                start=Decimal("0"),
            )
            excluded_raw_weight = sum(
                (weights[node_id] for node_id in excluded_node_ids),
                start=Decimal("0"),
            )
            effective_weights = {
                node_id: Decimal("0") for node_id in included_node_ids
            }
            self_allocations = {
                node_id: Decimal("0") for node_id in included_node_ids
            }
            relationship_received = {
                node_id: Decimal("0") for node_id in included_node_ids
            }
            relationship_sources: dict[str, set[str]] = {
                node_id: set() for node_id in included_node_ids
            }
            neutral_from_relationships = Decimal("0")
            source_allocations: list[dict[str, Any]] = []

            for source_id in sorted(included_node_ids):
                node = nodes_by_id[source_id]
                recipient_ids = {source_id, *node.dependencies}
                if node.parent_node_id is not None:
                    recipient_ids.add(node.parent_node_id)
                ordered_recipients = sorted(recipient_ids)
                share = weights[source_id] / Decimal(len(ordered_recipients))
                included_recipients = [
                    node_id
                    for node_id in ordered_recipients
                    if node_id in included_node_ids
                ]
                excluded_recipients = [
                    node_id
                    for node_id in ordered_recipients
                    if node_id in excluded_node_ids
                ]
                for recipient_id in included_recipients:
                    effective_weights[recipient_id] += share
                    if recipient_id == source_id:
                        self_allocations[recipient_id] += share
                    else:
                        relationship_received[recipient_id] += share
                        relationship_sources[recipient_id].add(source_id)
                relationship_residual = share * Decimal(
                    len(excluded_recipients)
                )
                neutral_from_relationships += relationship_residual
                allocated_to_included = share * Decimal(
                    len(included_recipients)
                )
                source_allocations.append(
                    {
                        "step": "source_node_allocation",
                        "source_node_id": source_id,
                        "source_raw_importance_weight": _decimal_text(
                            weights[source_id]
                        ),
                        "parent_node_id": node.parent_node_id,
                        "dependency_ids": sorted(set(node.dependencies)),
                        "recipient_ids": ordered_recipients,
                        "recipient_count": len(ordered_recipients),
                        "equal_share": _decimal_text(share),
                        "included_recipient_ids": included_recipients,
                        "excluded_recipient_ids": excluded_recipients,
                        "mass_allocated_to_included_recipients": _decimal_text(
                            allocated_to_included
                        ),
                        "mass_sent_to_neutral_residual": _decimal_text(
                            relationship_residual
                        ),
                    }
                )

            neutral_residual_weight = (
                excluded_raw_weight + neutral_from_relationships
            )
            effective_included_weight = sum(
                effective_weights.values(), start=Decimal("0")
            )
            decimal_rounding_adjustment = total_weight - (
                effective_included_weight + neutral_residual_weight
            )
            # Recurring Decimal divisions can leave an infinitesimal remainder.
            # When real unrepresented mass exists, preserve that remainder as
            # neutral uncertainty. When every recipient is represented, assign it
            # deterministically to the last included node so a fully represented
            # graph does not fabricate neutral residual mass.
            rounding_adjustment_destination = "none"
            if decimal_rounding_adjustment != 0:
                if excluded_node_ids or neutral_from_relationships != 0:
                    neutral_residual_weight += decimal_rounding_adjustment
                    rounding_adjustment_destination = "neutral_residual"
                else:
                    adjustment_node_id = sorted(included_node_ids)[-1]
                    effective_weights[adjustment_node_id] += (
                        decimal_rounding_adjustment
                    )
                    self_allocations[adjustment_node_id] += (
                        decimal_rounding_adjustment
                    )
                    effective_included_weight += decimal_rounding_adjustment
                    rounding_adjustment_destination = adjustment_node_id
            normalized_effective_sum = effective_included_weight / total_weight
            neutral_residual_fraction = neutral_residual_weight / total_weight
            conservation_check = (
                effective_included_weight + neutral_residual_weight
                == total_weight
            )
            if not conservation_check or neutral_residual_weight < 0:
                raise ForecastAggregationError(
                    ["relationship_mass_conservation_failed"]
                )

            allocation_payload = {
                "method": RELATIONSHIP_MASS_CONSERVING_LOG_ODDS_METHOD,
                "graph_id": graph.id,
                "graph_version": graph.version,
                "included_node_ids": sorted(included_node_ids),
                "excluded_node_ids": sorted(excluded_node_ids),
                "weights": {
                    node_id: _decimal_text(weights[node_id])
                    for node_id in sorted(weights)
                },
                "source_allocations": source_allocations,
                "effective_weights": {
                    node_id: _decimal_text(effective_weights[node_id])
                    for node_id in sorted(effective_weights)
                },
                "neutral_residual_weight": _decimal_text(
                    neutral_residual_weight
                ),
                "decimal_rounding_adjustment": _decimal_text(
                    decimal_rounding_adjustment
                ),
                "rounding_adjustment_destination": (
                    rounding_adjustment_destination
                ),
            }
            allocation_hash = sha256_text(canonical_json(allocation_payload))

            contributions: list[ForecastNodeContribution] = []
            for node_id in sorted(included_node_ids):
                node = nodes_by_id[node_id]
                probability = float(runs_by_node[node_id].probability)
                normalized_weight = round(
                    float(effective_weights[node_id] / total_weight),
                    12,
                )
                log_odds = round(
                    math.log(probability / (1.0 - probability)),
                    12,
                )
                contribution = round(normalized_weight * log_odds, 12)
                contributions.append(
                    ForecastNodeContribution(
                        node_id=node_id,
                        node_question=node.question,
                        input_probability=probability,
                        raw_importance_weight=float(weights[node_id]),
                        normalized_weight=normalized_weight,
                        log_odds=log_odds,
                        weighted_log_odds_contribution=contribution,
                        effective_importance_weight=_decimal_number(
                            effective_weights[node_id]
                        ),
                        self_allocated_weight=_decimal_number(
                            self_allocations[node_id]
                        ),
                        relationship_received_weight=_decimal_number(
                            relationship_received[node_id]
                        ),
                        relationship_source_node_ids=sorted(
                            relationship_sources[node_id]
                        ),
                        direct_parent_id=node.parent_node_id,
                        direct_dependency_ids=sorted(set(node.dependencies)),
                    )
                )

            combined_log_odds = round(
                math.fsum(
                    item.weighted_log_odds_contribution
                    for item in contributions
                ),
                12,
            )
            if not math.isfinite(combined_log_odds):
                raise ForecastAggregationError(
                    ["nonfinite_combined_log_odds"]
                )
            if combined_log_odds >= 0:
                final_probability = 1.0 / (
                    1.0 + math.exp(-combined_log_odds)
                )
            else:
                exponential = math.exp(combined_log_odds)
                final_probability = exponential / (1.0 + exponential)
            rounded_probability = round(final_probability, 12)
            if 0.0 < rounded_probability < 1.0:
                final_probability = rounded_probability
            if (
                not math.isfinite(final_probability)
                or not 0.0 < final_probability < 1.0
            ):
                raise ForecastAggregationError(
                    ["invalid_final_probability"]
                )

            trace: list[dict[str, Any]] = [
                {
                    "step": "relationship_aggregation_policy",
                    "method": RELATIONSHIP_MASS_CONSERVING_LOG_ODDS_METHOD,
                    "policy_version": RELATIONSHIP_MASS_CONSERVING_POLICY_VERSION,
                    "direct_relationships_only": True,
                    "allocation_rule": (
                        "equal split across source, direct parent, and direct "
                        "dependencies"
                    ),
                    "neutral_probability": 0.5,
                    "neutral_log_odds": 0.0,
                    "no_imputation": True,
                    "allocation_hash": allocation_hash,
                },
                {
                    "step": "graph_mass",
                    "total_graph_raw_weight": _decimal_text(total_weight),
                    "included_node_raw_weight": _decimal_text(
                        included_raw_weight
                    ),
                    "excluded_node_raw_weight": _decimal_text(
                        excluded_raw_weight
                    ),
                    "effective_included_weight": _decimal_text(
                        effective_included_weight
                    ),
                    "neutral_residual_weight": _decimal_text(
                        neutral_residual_weight
                    ),
                    "normalized_effective_weight_sum": _decimal_text(
                        normalized_effective_sum
                    ),
                    "neutral_residual_fraction": _decimal_text(
                        neutral_residual_fraction
                    ),
                    "decimal_rounding_adjustment": _decimal_text(
                        decimal_rounding_adjustment
                    ),
                    "rounding_adjustment_destination": (
                        rounding_adjustment_destination
                    ),
                    "conservation_check": conservation_check,
                    "allocation_hash": allocation_hash,
                },
                *source_allocations,
            ]
            trace.extend(
                {
                    "step": "excluded_node_mass",
                    "node_id": node_id,
                    "raw_importance_weight": _decimal_text(weights[node_id]),
                    "exclusion_origin": exclusion_origins.get(
                        node_id, "not_included"
                    ),
                    "mass_sent_to_neutral_residual": _decimal_text(
                        weights[node_id]
                    ),
                }
                for node_id in sorted(excluded_node_ids)
            )
            trace.extend(
                {
                    "step": "node_contribution",
                    "node_id": item.node_id,
                    "node": item.node_question,
                    "raw_importance_weight": _decimal_text(
                        weights[item.node_id]
                    ),
                    "self_allocated_weight": _decimal_text(
                        self_allocations[item.node_id]
                    ),
                    "relationship_received_weight": _decimal_text(
                        relationship_received[item.node_id]
                    ),
                    "relationship_source_node_ids": (
                        item.relationship_source_node_ids or []
                    ),
                    "effective_importance_weight": _decimal_text(
                        effective_weights[item.node_id]
                    ),
                    "normalized_effective_weight": item.normalized_weight,
                    "input_probability": item.input_probability,
                    "log_odds": item.log_odds,
                    "weighted_log_odds_contribution": (
                        item.weighted_log_odds_contribution
                    ),
                }
                for item in contributions
            )
            trace.append(
                {
                    "step": "final",
                    "combined_log_odds": combined_log_odds,
                    "neutral_residual_contribution": 0.0,
                    "neutral_residual_probability": 0.5,
                    "final_probability": final_probability,
                }
            )
            return ForecastAggregation(
                id=aggregation_id or self._id_factory(),
                forecast_run_id=forecast_run_id,
                method=RELATIONSHIP_MASS_CONSERVING_LOG_ODDS_METHOD,
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
