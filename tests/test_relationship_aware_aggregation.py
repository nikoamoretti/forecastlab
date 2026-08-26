from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from inspect import signature

import pytest

from forecastlab.graph_aggregation import (
    RELATIONSHIP_MASS_CONSERVING_LOG_ODDS_METHOD,
    ForecastAggregationError,
    GraphAggregator,
    RelationshipMassConservingLogOddsAggregator,
)
from forecastlab.schemas import ForecastGraph, ForecastNode, ForecastNodeRun

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _node(
    node_id: str,
    weight: float,
    *,
    parent: str | None = None,
    dependencies: list[str] | None = None,
) -> ForecastNode:
    return ForecastNode(
        id=node_id,
        graph_id="graph-relationship",
        parent_node_id=parent,
        question=f"Question for {node_id}",
        node_type="driver",
        importance_weight=weight,
        dependencies=dependencies or [],
        required_output_type="probability",
    )


def _graph(nodes: list[ForecastNode]) -> ForecastGraph:
    return ForecastGraph(
        id="graph-relationship",
        contract_id="contract-1",
        status="approved",
        created_at=NOW,
        generation_model="test",
        root_question="Will the event happen?",
        nodes=nodes,
    )


def _run(
    node_id: str,
    probability: float,
    *,
    run_id: str = "run-1",
    reasoning: str | None = None,
) -> ForecastNodeRun:
    return ForecastNodeRun(
        id=f"node-run-{node_id}",
        node_id=node_id,
        run_id=run_id,
        probability=probability,
        confidence=0.2,
        reasoning=reasoning or f"Reasoning for {node_id}",
        supporting_claim_ids=[f"claim-{node_id}"],
        uncertainty_notes=["Uncertainty."],
        model_used="mock:test",
        uncertainty=0.8,
        created_at=NOW,
    )


def _aggregate(
    graph: ForecastGraph,
    runs: list[ForecastNodeRun],
):
    return RelationshipMassConservingLogOddsAggregator().aggregate(
        graph,
        runs,
        aggregation_id="aggregation-1",
        created_at=NOW,
    )


def _step(aggregation, name: str) -> dict:
    return next(item for item in aggregation.calculation_trace if item["step"] == name)


def _allocation_hash(aggregation) -> str:
    return _step(aggregation, "relationship_aggregation_policy")["allocation_hash"]


def test_no_relationships_exactly_match_importance_only_method() -> None:
    graph = _graph([_node("a", 0.3), _node("b", 0.7)])
    runs = [_run("a", 0.35), _run("b", 0.65)]

    relationship = _aggregate(graph, runs)
    legacy = GraphAggregator().aggregate(
        graph,
        runs,
        aggregation_id="aggregation-1",
        created_at=NOW,
    )

    assert relationship.method == RELATIONSHIP_MASS_CONSERVING_LOG_ODDS_METHOD
    assert relationship.final_probability == legacy.final_probability
    assert [item.normalized_weight for item in relationship.node_contributions] == [
        item.normalized_weight for item in legacy.node_contributions
    ]
    mass = _step(relationship, "graph_mass")
    assert mass["neutral_residual_weight"] == "0"
    assert mass["conservation_check"] is True


def test_simple_parent_splits_child_mass_between_self_and_parent() -> None:
    aggregation = _aggregate(
        _graph([_node("a", 0.6), _node("b", 0.4, parent="a")]),
        [_run("a", 0.4), _run("b", 0.7)],
    )

    by_node = {item.node_id: item for item in aggregation.node_contributions}
    assert by_node["a"].effective_importance_weight == 0.8
    assert by_node["a"].self_allocated_weight == 0.6
    assert by_node["a"].relationship_received_weight == 0.2
    assert by_node["a"].relationship_source_node_ids == ["b"]
    assert by_node["b"].effective_importance_weight == 0.2
    assert by_node["b"].self_allocated_weight == 0.2
    assert _step(aggregation, "graph_mass")["neutral_residual_weight"] == "0"


def test_multiple_relationships_split_equally_and_ignore_recipient_order() -> None:
    first = _aggregate(
        _graph(
            [
                _node("a", 0.4),
                _node("b", 0.3),
                _node("c", 0.3, parent="a", dependencies=["b"]),
            ]
        ),
        [_run("a", 0.4), _run("b", 0.5), _run("c", 0.7)],
    )
    second = _aggregate(
        _graph(
            [
                _node("c", 0.3, parent="a", dependencies=["b"]),
                _node("b", 0.3),
                _node("a", 0.4),
            ]
        ),
        [_run("c", 0.7), _run("a", 0.4), _run("b", 0.5)],
    )

    allocation = next(
        item
        for item in first.calculation_trace
        if item.get("step") == "source_node_allocation"
        and item.get("source_node_id") == "c"
    )
    assert allocation["recipient_ids"] == ["a", "b", "c"]
    assert allocation["equal_share"] == "0.1"
    assert first.final_probability == second.final_probability
    assert first.node_contributions == second.node_contributions
    assert _allocation_hash(first) == _allocation_hash(second)
    mass = _step(first, "graph_mass")
    assert mass["effective_included_weight"] == "1"
    assert mass["neutral_residual_weight"] == "0"
    assert mass["conservation_check"] is True


def test_parent_duplicated_as_dependency_is_counted_once() -> None:
    aggregation = _aggregate(
        _graph(
            [
                _node("a", 0.6),
                _node("b", 0.4, parent="a", dependencies=["a", "a"]),
            ]
        ),
        [_run("a", 0.4), _run("b", 0.7)],
    )

    allocation = next(
        item
        for item in aggregation.calculation_trace
        if item.get("source_node_id") == "b"
    )
    assert allocation["recipient_ids"] == ["a", "b"]
    assert allocation["recipient_count"] == 2
    assert allocation["equal_share"] == "0.2"


def test_excluded_node_raw_mass_becomes_neutral_without_imputation() -> None:
    aggregation = _aggregate(
        _graph([_node("a", 0.4), _node("b", 0.3), _node("c", 0.3)]),
        [_run("a", 0.3), _run("b", 0.7)],
    )

    assert {item.node_id for item in aggregation.node_contributions} == {"a", "b"}
    mass = _step(aggregation, "graph_mass")
    assert mass["effective_included_weight"] == "0.7"
    assert mass["neutral_residual_weight"] == "0.3"
    assert mass["neutral_residual_fraction"] == "0.3"
    excluded = next(
        item
        for item in aggregation.calculation_trace
        if item.get("step") == "excluded_node_mass"
    )
    assert excluded["node_id"] == "c"
    assert excluded["mass_sent_to_neutral_residual"] == "0.3"


def test_included_node_share_to_excluded_dependency_is_also_neutral() -> None:
    aggregation = _aggregate(
        _graph([_node("a", 0.6, dependencies=["x"]), _node("x", 0.4)]),
        [_run("a", 0.8)],
    )

    mass = _step(aggregation, "graph_mass")
    assert mass["effective_included_weight"] == "0.3"
    assert mass["neutral_residual_weight"] == "0.7"
    assert mass["neutral_residual_fraction"] == "0.7"
    assert [item.node_id for item in aggregation.node_contributions] == ["a"]
    assert aggregation.node_contributions[0].normalized_weight == 0.3


@pytest.mark.parametrize(
    ("nodes", "included"),
    [
        (
            [
                _node("a", 0.41),
                _node("b", 0.29, parent="a"),
                _node("c", 0.2, dependencies=["a", "b"]),
                _node("d", 0.1, parent="c"),
            ],
            ["a", "b", "c"],
        ),
        (
            [
                _node("a", 0.35),
                _node("b", 0.25, dependencies=["a"]),
                _node("c", 0.25, dependencies=["a"]),
                _node("d", 0.15, dependencies=["b", "c"]),
            ],
            ["a", "b", "c", "d"],
        ),
    ],
)
def test_decimal_mass_conservation_for_varied_graphs(
    nodes: list[ForecastNode],
    included: list[str],
) -> None:
    aggregation = _aggregate(
        _graph(nodes),
        [_run(node_id, 0.45 + index * 0.05) for index, node_id in enumerate(included)],
    )
    mass = _step(aggregation, "graph_mass")

    assert mass["conservation_check"] is True
    assert Decimal(mass["effective_included_weight"]) + Decimal(
        mass["neutral_residual_weight"]
    ) == Decimal(mass["total_graph_raw_weight"])


@pytest.mark.parametrize(
    ("graph", "runs", "reason"),
    [
        (_graph([]), [], "graph_nodes_required"),
        (
            _graph([_node("a", 0.5), _node("a", 0.5)]),
            [_run("a", 0.5)],
            "duplicate_graph_node_id",
        ),
        (
            _graph([_node("a", 1.0, parent="missing")]),
            [_run("a", 0.5)],
            "unknown_parent:a:missing",
        ),
        (
            _graph([_node("a", 1.0, dependencies=["missing"])]),
            [_run("a", 0.5)],
            "unknown_dependency:a:missing",
        ),
        (
            _graph([_node("a", 1.0, parent="a")]),
            [_run("a", 0.5)],
            "self_relationship:a",
        ),
        (
            _graph([_node("a", 0.0), _node("b", 0.0)]),
            [_run("a", 0.4)],
            "zero_total_importance_weight",
        ),
        (
            _graph([_node("a", 1.0)]),
            [_run("a", 0.4), _run("a", 0.6)],
            "duplicate_node_forecast:a",
        ),
        (
            _graph([_node("a", 1.0)]),
            [_run("outside", 0.4)],
            "node_forecasts_not_in_graph:outside",
        ),
        (
            _graph([_node("a", 0.5), _node("b", 0.5)]),
            [_run("a", 0.4), _run("b", 0.6, run_id="run-2")],
            "node_forecasts_must_share_run_id",
        ),
        (
            _graph([_node("a", 1.0)]),
            [_run("a", 0.5).model_copy(update={"probability": 0.0})],
            "invalid_node_probability:a",
        ),
        (
            _graph([_node("a", 1.0)]),
            [_run("a", 0.5).model_copy(update={"probability": 1.0})],
            "invalid_node_probability:a",
        ),
        (
            _graph([_node("a", 1.0)]),
            [_run("a", 0.5).model_copy(update={"probability": math.nan})],
            "invalid_node_probability:a",
        ),
    ],
)
def test_invalid_inputs_fail_closed(
    graph: ForecastGraph,
    runs: list[ForecastNodeRun],
    reason: str,
) -> None:
    with pytest.raises(ForecastAggregationError) as exc_info:
        _aggregate(graph, runs)

    assert reason in exc_info.value.reasons


def test_allocation_is_immutable_to_non_policy_inputs() -> None:
    graph = _graph([_node("a", 0.6), _node("b", 0.4, parent="a")])
    baseline = _aggregate(graph, [_run("a", 0.4), _run("b", 0.7)])
    probability_change = _aggregate(graph, [_run("a", 0.2), _run("b", 0.8)])
    reasoning_change = _aggregate(
        graph,
        [
            _run("a", 0.4, reasoning="Changed model reasoning"),
            _run("b", 0.7, reasoning="Changed model reasoning"),
        ],
    )

    assert _allocation_hash(probability_change) == _allocation_hash(baseline)
    assert probability_change.final_probability != baseline.final_probability
    assert reasoning_change == baseline

    changed_weight = _aggregate(
        _graph([_node("a", 0.7), _node("b", 0.3, parent="a")]),
        [_run("a", 0.4), _run("b", 0.7)],
    )
    changed_relationship = _aggregate(
        _graph([_node("a", 0.6), _node("b", 0.4)]),
        [_run("a", 0.4), _run("b", 0.7)],
    )
    assert _allocation_hash(changed_weight) != _allocation_hash(baseline)
    assert _allocation_hash(changed_relationship) != _allocation_hash(baseline)


def test_relationship_allocation_has_no_outcome_or_evidence_quality_inputs() -> None:
    parameter_names = set(
        signature(RelationshipMassConservingLogOddsAggregator.aggregate).parameters
    )

    assert parameter_names == {
        "self",
        "graph",
        "node_runs",
        "exclusion_origins",
        "aggregation_id",
        "created_at",
    }
    assert "outcome" not in parameter_names
    assert "evidence_confidence" not in parameter_names
    assert "source_quality" not in parameter_names


def test_result_and_trace_are_reproducible() -> None:
    graph = _graph([_node("a", 0.6), _node("b", 0.4, parent="a")])
    runs = [_run("a", 0.4), _run("b", 0.7)]

    assert _aggregate(graph, runs) == _aggregate(graph, runs)
