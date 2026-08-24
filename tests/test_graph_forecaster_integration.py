from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import func, select

from forecastlab.errors import GraphForecastExecutionError
from forecastlab.graph_aggregation import ForecastAggregationError
from forecastlab.graph_execution import (
    ForecastNodeExecution,
    GraphNodeForecastResult,
)
from forecastlab.graph_research import NodeResearchPlan
from forecastlab.graphs import ForecastGraphError
from forecastlab.profiles import load_profile
from forecastlab.providers.mock import MockModelProvider, MockSearchProvider
from forecastlab.schemas import ForecastNodeRun
from forecastlab.timeutil import utcnow
from forecastlab_api.contracts import forecast_contract_from_row
from forecastlab_api.graph_executor import GraphForecastExecutor
from forecastlab_api.models import (
    ForecastAggregationRow,
    ForecastNodeRunRow,
    ForecastVersion,
    GraphExecutionFailureRow,
    Question,
)
from forecastlab_api.pipeline import apply_execution_limits, create_run_record, resolve_for_question
from forecastlab_api.v1_execution import approved_contract_for_question


def _approved_forecast(client) -> dict:
    draft = client.post(
        "/api/contracts/generate",
        json={"question": "Will the US unemployment rate exceed 5% before 30 June 2027?"},
    ).json()
    client.post(f"/api/contracts/{draft['id']}/approve").raise_for_status()
    return draft


def _executor(session, question_id: str, **overrides) -> tuple[GraphForecastExecutor, str]:
    question = session.get(Question, question_id)
    assert question is not None
    context = resolve_for_question(
        question,
        profile_id="graph_forecaster_v1",
        mode="demo",
        settings_data={
            "model_provider": "mock",
            "model_name": "mock-forecast-v1",
            "search_provider": "mock",
            "max_cost_usd": 5.0,
        },
    )
    run = create_run_record(
        session,
        question=question,
        context=context,
        as_of=None,
        enqueue=False,
    )
    run.started_at = utcnow()
    run.status = "running"
    session.commit()
    kwargs = {
        "run": run,
        "profile": apply_execution_limits(load_profile("graph_forecaster_v1"), context),
        "execution": context,
        "model": MockModelProvider(),
        "search": MockSearchProvider(),
        "allow_local_fixtures": True,
    }
    kwargs.update(overrides)
    return GraphForecastExecutor(session, **kwargs), run.id


def _node_result(**kwargs) -> GraphNodeForecastResult:
    missing_node_id = kwargs.pop("missing_node_id", None)
    invalid_node_id = kwargs.pop("invalid_node_id", None)
    contract = kwargs["contract"]
    graph = kwargs["graph"]
    run_id = kwargs["run_id"]
    executions: list[ForecastNodeExecution] = []
    for node in graph.nodes:
        if node.id == missing_node_id:
            executions.append(
                ForecastNodeExecution(
                    node=node,
                    node_run=None,
                    error="no_matching_source",
                    error_detail="No eligible evidence was returned by the stubbed research path.",
                    error_stage="node_research",
                    research_plan=NodeResearchPlan(
                        primary_research_question=node.question,
                        supporting_search_queries=[node.question],
                        preferred_sources=node.preferred_sources,
                        required_evidence_types=[node.required_output_type],
                    ),
                    queries_attempted=[node.question],
                    sources_checked=[
                        {
                            "url": "https://fixtures.forecastlab.local/missing",
                            "outcome": "no_matching_source",
                        }
                    ],
                )
            )
            continue
        executions.append(
            ForecastNodeExecution(
                node=node,
                node_run=ForecastNodeRun(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    node_id=node.id,
                    probability=0.0 if node.id == invalid_node_id else 0.55,
                    confidence=0.8,
                    reasoning="Stubbed node forecast for integration failure testing.",
                    supporting_claim_ids=[],
                    opposing_claim_ids=[],
                    uncertainty_notes=["Stubbed uncertainty."],
                    model_used="stub:node-v1",
                    uncertainty=0.2,
                    created_at=utcnow(),
                ),
            )
        )
    return GraphNodeForecastResult(
        contract=contract,
        graph=graph,
        nodes=executions,
        prompt_versions={"forecast_node": "v3"},
        budget={},
        stopped_early=False,
        stop_reason=None,
        stop_stage=None,
    )


def test_execute_graph_api_creates_complete_auditable_report(client) -> None:
    draft = _approved_forecast(client)

    response = client.post(
        f"/api/forecasts/{draft['question_id']}/execute-graph",
        json={"mode": "demo"},
    )

    assert response.status_code == 200
    run = response.json()
    assert run["status"] == "completed"
    assert run["profile_id"] == "graph_forecaster_v1"
    stored_run = client.get(f"/api/runs/{run['id']}").json()
    assert stored_run["prompt_versions"]["graph_research"] == "v1"

    report_response = client.get(f"/api/forecasts/{draft['question_id']}/graph-report")
    assert report_response.status_code == 200
    payload = report_response.json()
    report = payload["report"]
    assert payload["status"] == "completed"
    assert payload["failures"] == []
    assert payload["final_probability"] == report["final_probability"]
    assert report["forecast_contract"]["yes_condition"]
    assert report["forecast_contract"]["no_condition"]
    assert report["forecast_contract"]["authoritative_source"]
    assert len(report["nodes"]) == 7
    assert all(node["dependencies"] is not None for node in report["nodes"])
    assert all(node["supporting_evidence"] for node in report["nodes"])
    assert report["calculation"]["method"] == "importance_weighted_log_odds_v1"
    assert report["calculation"]["trace"][-1]["step"] == "final"
    assert report["final_answer"]["status"] == "completed"

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        assert session.scalar(
            select(func.count()).select_from(ForecastAggregationRow).where(
                ForecastAggregationRow.forecast_run_id == run["id"]
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(GraphExecutionFailureRow).where(
                GraphExecutionFailureRow.forecast_run_id == run["id"]
            )
        ) == 0


def test_execute_graph_requires_an_approved_contract(client) -> None:
    question = client.post(
        "/api/questions",
        json={"question": "Will US unemployment exceed 5% before June 2027?"},
    ).json()

    response = client.post(f"/api/forecasts/{question['id']}/execute-graph", json={"mode": "demo"})

    assert response.status_code == 422
    assert "approved_forecast_contract_required" in response.json()["reasons"]


def test_missing_graph_is_recorded_and_never_creates_probability(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        contract = forecast_contract_from_row(
            approved_contract_for_question(session, draft["question_id"])
        )
        executor, run_id = _executor(
            session,
            draft["question_id"],
            graph_resolver=lambda *_args, **_kwargs: (contract, None),
        )

        with pytest.raises(GraphForecastExecutionError, match="approved Forecast Graph"):
            executor.execute()

        assert session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run_id)) is None
        failure = session.scalar(
            select(GraphExecutionFailureRow).where(
                GraphExecutionFailureRow.forecast_run_id == run_id
            )
        )
        assert failure is not None
        assert failure.stage == "graph"
        assert failure.error_code == "approved_forecast_graph_required"


def test_graph_generation_failure_is_recorded(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    def fail_graph(*_args, **_kwargs):
        raise ForecastGraphError(["forced_graph_generation_failure"])

    with main_mod.SessionLocal() as session:
        executor, run_id = _executor(session, draft["question_id"], graph_resolver=fail_graph)

        with pytest.raises(GraphForecastExecutionError, match="Forecast Graph could not be generated"):
            executor.execute()

        failure = session.scalar(
            select(GraphExecutionFailureRow).where(
                GraphExecutionFailureRow.forecast_run_id == run_id
            )
        )
        assert failure is not None
        assert failure.error_code == "forced_graph_generation_failure"


def test_no_eligible_evidence_records_every_node_failure_without_aggregation(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    def no_evidence_for_all(**kwargs):
        result = _node_result(**kwargs)
        result.nodes = [
            ForecastNodeExecution(
                node=execution.node,
                node_run=None,
                error="node_forecast_evidence_required",
            )
            for execution in result.nodes
        ]
        return result

    with main_mod.SessionLocal() as session:
        executor, run_id = _executor(session, draft["question_id"], node_runner=no_evidence_for_all)

        with pytest.raises(GraphForecastExecutionError, match="critical node failed"):
            executor.execute()

        assert session.scalar(
            select(func.count()).select_from(GraphExecutionFailureRow).where(
                GraphExecutionFailureRow.forecast_run_id == run_id,
                GraphExecutionFailureRow.error_code == "no_eligible_evidence",
            )
        ) == 7
        assert session.scalar(
            select(ForecastAggregationRow).where(ForecastAggregationRow.forecast_run_id == run_id)
        ) is None
        assert session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run_id)) is None


def test_critical_node_failure_preserves_successes_but_never_aggregates(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    def one_missing(**kwargs):
        return _node_result(**kwargs, missing_node_id=kwargs["graph"].nodes[0].id)

    with main_mod.SessionLocal() as session:
        executor, run_id = _executor(session, draft["question_id"], node_runner=one_missing)

        with pytest.raises(GraphForecastExecutionError):
            executor.execute()

        assert session.scalar(
            select(func.count()).select_from(ForecastNodeRunRow).where(
                ForecastNodeRunRow.forecast_run_id == run_id
            )
        ) == 6
        assert session.scalar(
            select(func.count()).select_from(GraphExecutionFailureRow).where(
                GraphExecutionFailureRow.forecast_run_id == run_id
            )
        ) == 1
        assert session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run_id)) is None


def test_noncritical_node_failure_is_excluded_and_graph_still_aggregates(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    def one_noncritical_missing(**kwargs):
        noncritical = next(
            node
            for node in kwargs["graph"].nodes
            if node.node_type == "scenario" and node.importance_weight < 0.8
        )
        return _node_result(**kwargs, missing_node_id=noncritical.id)

    with main_mod.SessionLocal() as session:
        executor, run_id = _executor(
            session,
            draft["question_id"],
            node_runner=one_noncritical_missing,
        )

        version = executor.execute()

        assert version.ensemble_probability is not None
        assert session.scalar(
            select(func.count()).select_from(ForecastNodeRunRow).where(
                ForecastNodeRunRow.forecast_run_id == run_id
            )
        ) == 6
        aggregation = session.scalar(
            select(ForecastAggregationRow).where(
                ForecastAggregationRow.forecast_run_id == run_id
            )
        )
        assert aggregation is not None
        contributions = json.loads(aggregation.node_contributions_json)
        assert len(contributions) == 6
        failure = session.scalar(
            select(GraphExecutionFailureRow).where(
                GraphExecutionFailureRow.forecast_run_id == run_id
            )
        )
        assert failure is not None
        assert failure.error_code == "no_matching_source"
        assert failure.critical_node is False
        assert failure.impact == "excluded_reduced_confidence"
        assert json.loads(failure.queries_attempted_json)
        assert json.loads(failure.sources_checked_json)[0]["outcome"] == "no_matching_source"
        stored_run = version.run
        reliability = json.loads(stored_run.execution_context_json)[
            "graph_research_reliability"
        ]
        assert reliability["impact"] == "reduced_confidence"
        assert len(reliability["failed_node_ids"]) == 1
        trace = json.loads(aggregation.calculation_trace_json)
        assert trace[0]["step"] == "node_failure_tolerance"

    report = client.get(
        f"/api/forecasts/{draft['question_id']}/graph-report"
    ).json()["report"]
    assert report["research_reliability"] == {
        "status": "reduced_confidence",
        "failed_node_count": 1,
        "excluded_node_count": 1,
        "critical_failure_count": 0,
    }
    failed_node = next(node for node in report["nodes"] if node["forecast_status"] == "failed")
    assert failed_node["failure_impact"] == "excluded_reduced_confidence"
    assert failed_node["queries_attempted"]
    assert failed_node["sources_checked"]


def test_invalid_node_probability_is_recorded_before_aggregation(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    def invalid_probability(**kwargs):
        return _node_result(**kwargs, invalid_node_id=kwargs["graph"].nodes[0].id)

    with main_mod.SessionLocal() as session:
        executor, run_id = _executor(
            session,
            draft["question_id"],
            node_runner=invalid_probability,
        )

        with pytest.raises(GraphForecastExecutionError, match="critical node failed"):
            executor.execute()

        failure = session.scalar(
            select(GraphExecutionFailureRow).where(
                GraphExecutionFailureRow.forecast_run_id == run_id,
                GraphExecutionFailureRow.error_code == "invalid_node_probability",
            )
        )
        assert failure is not None
        assert session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run_id)) is None


def test_aggregation_failure_is_recorded_without_forecast_version(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    class FailingAggregator:
        def aggregate(self, *_args, **_kwargs):
            raise ForecastAggregationError(["forced_aggregation_failure"])

    with main_mod.SessionLocal() as session:
        executor, run_id = _executor(
            session,
            draft["question_id"],
            node_runner=_node_result,
            aggregator=FailingAggregator(),
        )

        with pytest.raises(GraphForecastExecutionError, match="forced_aggregation_failure"):
            executor.execute()

        failure = session.scalar(
            select(GraphExecutionFailureRow).where(
                GraphExecutionFailureRow.forecast_run_id == run_id,
                GraphExecutionFailureRow.stage == "aggregation",
            )
        )
        assert failure is not None
        assert session.scalar(
            select(ForecastAggregationRow).where(ForecastAggregationRow.forecast_run_id == run_id)
        ) is None
        assert session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run_id)) is None
