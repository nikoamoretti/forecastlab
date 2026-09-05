from __future__ import annotations

from datetime import UTC, datetime

import pytest

from forecastlab.graph_aggregation import aggregate_graph_probabilities
from forecastlab.schemas import ForecastGraph, ForecastNode


def _node(
    node_id: str,
    weight: float,
    *,
    dependencies: list[str] | None = None,
    parent_node_id: str | None = None,
) -> ForecastNode:
    return ForecastNode(
        id=node_id,
        graph_id="graph-1",
        parent_node_id=parent_node_id,
        question=f"Question for {node_id}",
        node_type="driver" if dependencies else "base_rate",
        importance_weight=weight,
        dependencies=dependencies or [],
        required_output_type="probability",
    )


def _graph(nodes: list[ForecastNode]) -> ForecastGraph:
    return ForecastGraph(
        id="graph-1",
        contract_id="contract-1",
        status="approved",
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
        generation_model="test",
        root_question="Will the event happen?",
        nodes=nodes,
    )


def test_graph_aggregation_is_deterministic() -> None:
    graph = _graph([_node("a", 0.8), _node("b", 0.4, dependencies=["a"])])

    result = aggregate_graph_probabilities(graph, {"a": 0.2, "b": 0.8})

    assert result.method == "dependency_discounted_weighted_mean_v1"
    assert result.normalization_denominator == 1.0
    assert result.ensemble_probability == 0.32
    by_id = {item.node_id: item for item in result.contributions}
    assert by_id["a"].normalized_weight == 0.8
    assert by_id["a"].probability_contribution == 0.16
    assert by_id["b"].dependency_factor == 0.5
    assert by_id["b"].normalized_weight == 0.2
    assert by_id["b"].probability_contribution == 0.16


def test_graph_aggregation_normalizes_zero_weights_with_documented_fallback() -> None:
    graph = _graph([_node("a", 0.0), _node("b", 0.0, dependencies=["a"])])

    result = aggregate_graph_probabilities(graph, {"a": 0.3, "b": 0.9})

    assert sum(item.normalized_weight for item in result.contributions) == pytest.approx(1.0)
    assert result.calculation_trace[0]["zero_importance_fallback"] is True


def test_graph_aggregation_omits_missing_node_and_renormalizes() -> None:
    graph = _graph([_node("a", 0.8), _node("b", 0.4, dependencies=["a"])])

    result = aggregate_graph_probabilities(graph, {"a": None, "b": 0.8}, failed_nodes={"a": "budget"})

    assert result.missing_node_ids == ["a"]
    assert result.included_node_ids == ["b"]
    assert result.ensemble_probability == 0.8
    by_id = {item.node_id: item for item in result.contributions}
    assert by_id["a"].included is False
    assert by_id["a"].failure_reason == "budget"
    assert by_id["b"].missing_dependency_ids == ["a"]
    assert by_id["b"].normalized_weight == 1.0


def test_graph_aggregation_is_reproducible_across_input_order() -> None:
    a = _node("a", 0.8)
    b = _node("b", 0.4, dependencies=["a"])

    first = aggregate_graph_probabilities(_graph([a, b]), {"a": 0.2, "b": 0.8})
    second = aggregate_graph_probabilities(_graph([b, a]), {"b": 0.8, "a": 0.2})

    assert first == second


def test_graph_aggregation_rejects_probability_outside_graph() -> None:
    with pytest.raises(ValueError, match="node_probabilities_not_in_graph:unknown"):
        aggregate_graph_probabilities(_graph([_node("a", 0.8)]), {"a": 0.2, "unknown": 0.9})
