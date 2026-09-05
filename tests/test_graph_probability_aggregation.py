from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from forecastlab.graph_aggregation import (
    LOG_ODDS_METHOD,
    ForecastAggregationError,
    GraphAggregator,
)
from forecastlab.schemas import ForecastGraph, ForecastNode, ForecastNodeRun
from forecastlab_api.aggregations import forecast_aggregation_from_row, store_forecast_aggregation
from forecastlab_api.db import Base
from forecastlab_api.models import ForecastRun, Question

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _node(node_id: str, weight: float) -> ForecastNode:
    return ForecastNode(
        id=node_id,
        graph_id="graph-1",
        question=f"Question for {node_id}",
        node_type="base_rate" if node_id == "base-rate" else "driver",
        importance_weight=weight,
        required_output_type="probability",
    )


def _graph(nodes: list[ForecastNode]) -> ForecastGraph:
    return ForecastGraph(
        id="graph-1",
        contract_id="contract-1",
        status="approved",
        created_at=NOW,
        generation_model="test",
        root_question="Will the event happen?",
        nodes=nodes,
    )


def _run(node_id: str, probability: float, *, run_id: str = "run-1") -> ForecastNodeRun:
    return ForecastNodeRun(
        id=f"node-run-{node_id}",
        node_id=node_id,
        run_id=run_id,
        probability=probability,
        confidence=0.7,
        reasoning=f"Reasoning for {node_id}",
        supporting_claim_ids=[f"claim-{node_id}"],
        uncertainty_notes=["Some uncertainty remains."],
        model_used="mock:test",
        uncertainty=0.3,
        created_at=NOW,
    )


def _aggregate(
    graph: ForecastGraph,
    node_runs: list[ForecastNodeRun],
):
    return GraphAggregator().aggregate(
        graph,
        node_runs,
        aggregation_id="aggregation-1",
        created_at=NOW,
    )


def test_graph_aggregator_uses_deterministic_weighted_log_odds() -> None:
    graph = _graph([_node("base-rate", 0.3), _node("driver", 0.7)])

    aggregation = _aggregate(graph, [_run("base-rate", 0.35), _run("driver", 0.65)])

    expected_log_odds = 0.3 * math.log(0.35 / 0.65) + 0.7 * math.log(0.65 / 0.35)
    expected_probability = 1.0 / (1.0 + math.exp(-expected_log_odds))
    assert aggregation.method == LOG_ODDS_METHOD
    assert aggregation.final_probability == pytest.approx(expected_probability, abs=1e-12)
    assert aggregation.calculation_trace[-1] == {
        "step": "final",
        "combined_log_odds": round(expected_log_odds, 12),
        "final_probability": aggregation.final_probability,
    }
    assert aggregation.calculation_trace[1]["node"] == "Question for base-rate"
    assert aggregation.calculation_trace[1]["input_probability"] == 0.35


def test_graph_aggregator_normalizes_importance_weights() -> None:
    aggregation = _aggregate(
        _graph([_node("base-rate", 0.2), _node("driver", 0.3)]),
        [_run("base-rate", 0.4), _run("driver", 0.6)],
    )

    assert [item.normalized_weight for item in aggregation.node_contributions] == [0.4, 0.6]
    assert sum(item.normalized_weight for item in aggregation.node_contributions) == pytest.approx(1.0)


def test_graph_aggregator_rejects_invalid_probability() -> None:
    invalid = _run("base-rate", 0.5).model_copy(update={"probability": 1.0})

    with pytest.raises(ForecastAggregationError) as exc_info:
        _aggregate(_graph([_node("base-rate", 1.0)]), [invalid])

    assert exc_info.value.reasons == ["invalid_node_probability:base-rate"]


def test_graph_aggregator_rejects_missing_node_forecast() -> None:
    graph = _graph([_node("base-rate", 0.4), _node("driver", 0.6)])

    with pytest.raises(ForecastAggregationError) as exc_info:
        _aggregate(graph, [_run("base-rate", 0.4)])

    assert exc_info.value.reasons == ["missing_node_forecasts:driver"]


def test_graph_aggregator_rejects_missing_weight() -> None:
    node = _node("base-rate", 1.0).model_copy(update={"importance_weight": None})

    with pytest.raises(ForecastAggregationError) as exc_info:
        _aggregate(_graph([node]), [_run("base-rate", 0.4)])

    assert exc_info.value.reasons == ["missing_node_weight:base-rate"]


def test_graph_aggregator_rejects_zero_total_weight() -> None:
    graph = _graph([_node("base-rate", 0.0), _node("driver", 0.0)])

    with pytest.raises(ForecastAggregationError) as exc_info:
        _aggregate(graph, [_run("base-rate", 0.4), _run("driver", 0.6)])

    assert exc_info.value.reasons == ["zero_total_importance_weight"]


def test_graph_aggregator_trace_is_reproducible_across_input_order() -> None:
    base_rate = _node("base-rate", 0.3)
    driver = _node("driver", 0.7)
    base_run = _run("base-rate", 0.35)
    driver_run = _run("driver", 0.65)

    first = _aggregate(_graph([base_rate, driver]), [base_run, driver_run])
    second = _aggregate(_graph([driver, base_rate]), [driver_run, base_run])

    assert first == second


def test_same_inputs_produce_same_probability_contributions_and_trace() -> None:
    graph = _graph([_node("base-rate", 0.3), _node("driver", 0.7)])
    node_runs = [_run("base-rate", 0.35), _run("driver", 0.65)]

    first = GraphAggregator().aggregate(graph, node_runs)
    second = GraphAggregator().aggregate(graph, node_runs)

    assert first.final_probability == second.final_probability
    assert first.node_contributions == second.node_contributions
    assert first.calculation_trace == second.calculation_trace


def test_forecast_aggregation_persists_and_round_trips() -> None:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    aggregation = _aggregate(
        _graph([_node("base-rate", 0.4), _node("driver", 0.6)]),
        [_run("base-rate", 0.4), _run("driver", 0.6)],
    )

    with Session(engine) as session:
        session.add(Question(id="question-1", original_text="Will the event happen?"))
        session.add(
            ForecastRun(
                id="run-1",
                question_id="question-1",
                profile_id="graph_forecaster_v1",
                mode="demo",
            )
        )
        session.flush()
        stored = store_forecast_aggregation(session, aggregation)
        session.commit()
        loaded = forecast_aggregation_from_row(stored)

        assert loaded == aggregation
        assert loaded.node_contributions[0].effective_importance_weight is None
        assert loaded.node_contributions[0].relationship_source_node_ids is None
        assert store_forecast_aggregation(session, aggregation).id == "aggregation-1"


def test_run_api_exposes_persisted_forecast_aggregation(client) -> None:
    from forecastlab_api.db import SessionLocal

    aggregation = _aggregate(
        _graph([_node("base-rate", 0.4), _node("driver", 0.6)]),
        [_run("base-rate", 0.4), _run("driver", 0.6)],
    )
    with SessionLocal() as session:
        session.add(Question(id="question-1", original_text="Will the event happen?"))
        session.add(
            ForecastRun(
                id="run-1",
                question_id="question-1",
                profile_id="graph_forecaster_v1",
                mode="demo",
            )
        )
        session.flush()
        store_forecast_aggregation(session, aggregation)
        session.commit()

    response = client.get("/api/runs/run-1")

    assert response.status_code == 200
    payload = response.json()["forecast_aggregation"]
    assert payload["method"] == LOG_ODDS_METHOD
    assert payload["final_probability"] == aggregation.final_probability
    assert payload["calculation_trace"] == aggregation.calculation_trace
