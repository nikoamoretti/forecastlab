from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import func, select

from forecastlab.budget import Budget
from forecastlab.errors import GraphForecastExecutionError
from forecastlab.graph_aggregation import (
    ForecastAggregationError,
    RelationshipMassConservingLogOddsAggregator,
)
from forecastlab.graph_execution import (
    ForecastNodeExecution,
    GraphNodeForecastResult,
    run_graph_node_forecasts,
)
from forecastlab.graph_research import NodeResearchPlan
from forecastlab.graphs import ForecastGraphError
from forecastlab.material_node_coverage import assess_material_node_plan
from forecastlab.profiles import load_profile
from forecastlab.providers.mock import MockModelProvider, MockSearchProvider
from forecastlab.research_planning import ResearchPlan
from forecastlab.scenario_synthesis import SCENARIO_SYNTHESIS_CALL_KIND
from forecastlab.schemas import EvidenceClaim, ForecastGraph, ForecastNode, ForecastNodeRun
from forecastlab.timeutil import utcnow
from forecastlab_api.contracts import forecast_contract_from_row
from forecastlab_api.graph_executor import GraphForecastExecutor
from forecastlab_api.graphs import store_forecast_graph
from forecastlab_api.material_node_coverage import (
    MaterialNodeCoverageStoreError,
    material_node_coverage_from_row,
    store_material_node_coverage_assessment,
)
from forecastlab_api.models import (
    EvidenceClaimRow,
    EvidenceItem,
    EvidenceSufficiencyAssessmentRow,
    ForecastAggregationRow,
    ForecastNodeRunRow,
    ForecastVersion,
    GraphExecutionFailureRow,
    MaterialNodeCoverageAssessmentRow,
    Question,
    ResearchPlanRow,
    ScenarioSynthesisRow,
)
from forecastlab_api.pipeline import apply_execution_limits, create_run_record, resolve_for_question
from forecastlab_api.scenario_synthesis import (
    ScenarioSynthesisStoreError,
    scenario_synthesis_from_row,
    store_scenario_synthesis,
)
from forecastlab_api.v1_execution import approved_contract_for_question


class CountingScenarioModel(MockModelProvider):
    def __init__(self, *, invalid_scenario: bool = False) -> None:
        super().__init__()
        self.invalid_scenario = invalid_scenario
        self.scenario_calls = 0

    def _payload(self, prompt_id: str, user: str, schema_name: str) -> dict:
        if schema_name == "scenario_synthesis":
            self.scenario_calls += 1
            if self.invalid_scenario:
                return {"scenarios": []}
        return super()._payload(prompt_id, user, schema_name)


def _approved_forecast(client) -> dict:
    draft = client.post(
        "/api/contracts/generate",
        json={"question": "Will the US unemployment rate exceed 5% before 30 June 2027?"},
    ).json()
    client.post(f"/api/contracts/{draft['id']}/approve").raise_for_status()
    return draft


def _executor(session, question_id: str, **overrides) -> tuple[GraphForecastExecutor, str]:
    enforce_evidence_gate = bool(overrides.pop("enforce_evidence_gate", False))
    enforce_material_gate = bool(overrides.pop("enforce_material_gate", False))
    enforce_scenario_synthesis = bool(
        overrides.pop("enforce_scenario_synthesis", False)
    )
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
    profile = apply_execution_limits(load_profile("graph_forecaster_v1"), context)
    if not enforce_evidence_gate:
        profile = profile.model_copy(update={"evidence_sufficiency_policy": None})
    if not enforce_material_gate:
        profile = profile.model_copy(update={"material_node_policy": "none"})
    if not enforce_scenario_synthesis:
        profile = profile.model_copy(update={"scenario_synthesis_policy": "none"})
    kwargs = {
        "run": run,
        "profile": profile,
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


def _gate_graph(
    contract_id: str,
    *,
    weights: list[float] | None = None,
) -> ForecastGraph:
    graph_id = str(uuid.uuid4())
    node_types = ["base_rate", "driver", "adversarial", "resolver", "trend"]
    weight_values = weights or [0.8, 0.1, 0.1, 0.0, 0.0]
    return ForecastGraph(
        id=graph_id,
        contract_id=contract_id,
        version=1,
        status="approved",
        created_at=utcnow(),
        generation_model="stub:gate-graph-v1",
        root_question="Will the exact outcome occur?",
        nodes=[
            ForecastNode(
                id=str(uuid.uuid4()),
                graph_id=graph_id,
                question=f"Gate node {index + 1}?",
                node_type=node_type,  # type: ignore[arg-type]
                importance_weight=weight,
                required_output_type="probability",
            )
            for index, (node_type, weight) in enumerate(
                zip(node_types, weight_values, strict=True)
            )
        ],
    )


def _gate_node_result(
    *,
    fallback_node_index: int | None = None,
    selected_count: int = 3,
    missing_node_index: int | None = None,
    primary_node_index: int = 0,
    critical_node_indices: list[int] | None = None,
    include_material_plan_audit: bool = False,
    include_scenario_budget: bool = False,
    node_reasoning_characters: int | None = None,
):
    def run(**kwargs) -> GraphNodeForecastResult:
        graph = kwargs["graph"]
        run_id = kwargs["run_id"]
        selected = graph.nodes[:selected_count]
        critical_indices = (
            critical_node_indices
            if critical_node_indices is not None
            else [0]
        )
        plan = ResearchPlan(
            id=str(uuid.uuid4()),
            forecast_run_id=run_id,
            selected_nodes=[node.id for node in selected],
            skipped_nodes=[node.id for node in graph.nodes[selected_count:]],
            priority_scores={
                node.id: float(len(graph.nodes) - index)
                for index, node in enumerate(graph.nodes)
            },
            budget_allocation={
                "critical_node_ids": [
                    selected[index].id
                    for index in critical_indices
                    if index < len(selected)
                ]
            },
            created_at=utcnow(),
        )
        if include_material_plan_audit:
            material_plan_audit = assess_material_node_plan(
                graph=graph,
                plan=plan,
            )
            plan = plan.model_copy(
                update={
                    "budget_allocation": {
                        **plan.budget_allocation,
                        "material_node_plan_audit": (
                            material_plan_audit.model_dump(mode="json")
                        ),
                    }
                }
            )
        kwargs["persist_research_plan"](plan)
        hosts = ["bls.gov", "reuters.com", "sec.gov", "census.gov", "bea.gov"]
        executions: list[ForecastNodeExecution] = []
        for index, node in enumerate(graph.nodes):
            if index >= selected_count:
                executions.append(
                    ForecastNodeExecution(
                        node=node,
                        node_run=None,
                        research_selected=False,
                        skip_reason="not_selected_by_frozen_plan",
                    )
                )
                continue
            if index == missing_node_index:
                executions.append(
                    ForecastNodeExecution(
                        node=node,
                        node_run=None,
                        error="no_matching_source",
                        error_detail="No eligible evidence for the material-node fixture.",
                        error_stage="node_research",
                        research_selected=True,
                        queries_attempted=[node.question],
                        sources_checked=[],
                    )
                )
                continue
            host = hosts[index]
            source_class = (
                "primary" if index == primary_node_index else "secondary"
            )
            item_id = f"item-{node.id}"
            claim_id = f"claim-{node.id}"
            url = f"https://{host}/source/{node.id}"
            observed_at = utcnow()
            extraction_method = (
                "document_fallback"
                if fallback_node_index == index
                else "mock_structured"
            )
            record = {
                "id": item_id,
                "forecast_node_id": node.id,
                "subquestion": node.question,
                "url": url,
                "title": f"Source for {node.question}",
                "publisher": host,
                "published_at": observed_at.isoformat(),
                "retrieved_at": observed_at.isoformat(),
                "source_available_at": observed_at.isoformat(),
                "temporal_basis": "publication_date",
                "publication_date_source": "test_fixture",
                "publication_date_verified": True,
                "excerpt": "Exact test excerpt.",
                "content_hash": "a" * 64,
                "source_class": source_class,
                "as_of_eligible": True,
                "rejected": False,
                "status_code": 200,
                "published_at_unknown": False,
                "snapshot_verification_status": "fixture",
            }
            claim = EvidenceClaim(
                id=claim_id,
                evidence_item_id=item_id,
                forecast_node_id=node.id,
                claim="Exact test claim.",
                excerpt="Exact test excerpt.",
                source_url=url,
                source_title=record["title"],
                publisher=host,
                publication_date=observed_at,
                publication_date_source="test_fixture",
                publication_date_verified=True,
                retrieval_date=observed_at,
                source_available_at=observed_at,
                temporal_basis="publication_date",
                supports_or_refutes="supports",
                confidence=0.1,
                source_quality=0.0,
                primary_source=False,
                as_of_eligible=True,
                cutoff_verified=True,
                source_class=source_class,  # type: ignore[arg-type]
                extraction_method=extraction_method,  # type: ignore[arg-type]
                source_host=host,
            )
            kwargs["persist_research"](node, [record], [], [claim])
            executions.append(
                ForecastNodeExecution(
                    node=node,
                    node_run=ForecastNodeRun(
                        id=str(uuid.uuid4()),
                        run_id=run_id,
                        node_id=node.id,
                        probability=0.45 + index * 0.05,
                        confidence=0.5,
                        reasoning=(
                            "x" * node_reasoning_characters
                            if node_reasoning_characters is not None
                            else "Gate integration stub."
                        ),
                        supporting_claim_ids=[claim_id],
                        opposing_claim_ids=[],
                        uncertainty_notes=[],
                        model_used="stub:node-v1",
                        uncertainty=0.5,
                        created_at=utcnow(),
                    ),
                    evidence=[record],
                    claims=[claim],
                )
            )
        budget_controller = None
        budget_snapshot: dict = {}
        if include_scenario_budget:
            budget_controller = Budget(
                kwargs["profile"],
                provider="mock",
                model="mock-forecast-v1",
                search_provider="mock",
            )
            budget_controller.freeze_model_call_envelope(
                planner_version="scenario-test-envelope-v1",
                planned_calls_by_kind={SCENARIO_SYNTHESIS_CALL_KIND: 1},
            )
            budget_snapshot = budget_controller.snapshot()
        return GraphNodeForecastResult(
            contract=kwargs["contract"],
            graph=graph,
            nodes=executions,
            prompt_versions={"forecast_node": "v3"},
            budget=budget_snapshot,
            stopped_early=False,
            stop_reason=None,
            stop_stage=None,
            research_plan=plan,
            budget_controller=budget_controller,
        )

    return run


def test_execute_graph_api_creates_complete_auditable_report(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        question = session.get(Question, draft["question_id"])
        assert question is not None
        question.requested_profile_id = "graph_live_smoke_v1"
        session.commit()

    response = client.post(
        f"/api/questions/{draft['question_id']}/runs",
        json={"mode": "demo", "profile_id": "graph_live_smoke_v1"},
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "completed"
    assert run["profile_id"] == "graph_live_smoke_v1"
    stored_run = client.get(f"/api/runs/{run['id']}").json()
    assert stored_run["prompt_versions"]["graph_research"] == "v1"
    assert stored_run["research_plan"]["forecast_run_id"] == run["id"]
    assert len(stored_run["research_plan"]["selected_nodes"]) == 3

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
    selected_nodes = [node for node in report["nodes"] if node["research_selected"]]
    assert len(selected_nodes) == 3
    assert all(node["supporting_evidence"] for node in selected_nodes)
    assert report["evidence_sufficiency"] is None
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
        assert session.scalar(
            select(func.count()).select_from(ResearchPlanRow).where(
                ResearchPlanRow.forecast_run_id == run["id"]
            )
        ) == 1


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
            relationship_aggregator=FailingAggregator(),
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
        assert failure.error_code == "relationship_aggregation_failed"
        assert session.scalar(
            select(ForecastAggregationRow).where(ForecastAggregationRow.forecast_run_id == run_id)
        ) is None
        assert session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run_id)) is None


def test_unknown_graph_aggregation_method_fails_before_execution(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    model = _NoCallModel()
    search = _NoCallSearch()
    with main_mod.SessionLocal() as session:
        executor, run_id = _executor(
            session,
            draft["question_id"],
            model=model,
            search=search,
        )
        executor.profile = executor.profile.model_copy(
            update={"aggregation_method": "unregistered_graph_method"}
        )

        with pytest.raises(
            GraphForecastExecutionError,
            match="Invalid graph profile",
        ) as raised:
            executor.execute()
        assert "unsupported_graph_aggregation_method" in raised.value.reasons

        failure = session.scalar(
            select(GraphExecutionFailureRow).where(
                GraphExecutionFailureRow.forecast_run_id == run_id,
                GraphExecutionFailureRow.error_code
                == "unsupported_graph_aggregation_method",
            )
        )
        assert failure is not None
        assert failure.stage == "profile"
        assert session.scalar(
            select(ForecastAggregationRow).where(
                ForecastAggregationRow.forecast_run_id == run_id
            )
        ) is None
        assert session.scalar(
            select(ForecastVersion).where(ForecastVersion.run_id == run_id)
        ) is None

    assert model.calls == 0
    assert search.calls == 0


def test_private_v1_evidence_gate_passes_before_unchanged_aggregation(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        contract = forecast_contract_from_row(
            approved_contract_for_question(session, draft["question_id"])
        )
        graph = _gate_graph(contract.id)
        store_forecast_graph(session, graph)
        session.commit()
        executor, run_id = _executor(
            session,
            draft["question_id"],
            enforce_evidence_gate=True,
            graph_resolver=lambda *_args, **_kwargs: (contract, graph),
            node_runner=_gate_node_result(),
        )

        version = executor.execute()

        assessment = session.scalar(
            select(EvidenceSufficiencyAssessmentRow).where(
                EvidenceSufficiencyAssessmentRow.forecast_run_id == run_id
            )
        )
        assert assessment is not None
        assert assessment.status == "passed"
        assert assessment.policy_version == "private_v1_evidence_gate_v1"
        assert assessment.included_node_count == 3
        assert assessment.graph_weight_coverage == pytest.approx(1.0)
        aggregation = session.scalar(
            select(ForecastAggregationRow).where(
                ForecastAggregationRow.forecast_run_id == run_id
            )
        )
        assert aggregation is not None
        trace = json.loads(aggregation.calculation_trace_json)
        assert trace[0] == {
            "assessment_id": assessment.id,
            "assessment_input_hash": assessment.assessment_input_hash,
            "policy_version": "private_v1_evidence_gate_v1",
            "status": "passed",
            "step": "evidence_sufficiency_gate",
        }
        context = json.loads(version.run.execution_context_json)
        assert context["evidence_sufficiency_assessment_id"] == assessment.id
        assert context["evidence_sufficiency_assessment_input_hash"] == assessment.assessment_input_hash
        assert version.ensemble_probability is not None

    report = client.get(f"/api/forecasts/{draft['question_id']}/graph-report").json()["report"]
    assert report["evidence_sufficiency"]["status"] == "passed"
    assert report["final_probability"] is not None


def test_private_v1_evidence_gate_fails_closed_and_preserves_node_artifacts(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        contract = forecast_contract_from_row(
            approved_contract_for_question(session, draft["question_id"])
        )
        graph = _gate_graph(contract.id)
        store_forecast_graph(session, graph)
        session.commit()
        executor, run_id = _executor(
            session,
            draft["question_id"],
            enforce_evidence_gate=True,
            graph_resolver=lambda *_args, **_kwargs: (contract, graph),
            node_runner=_gate_node_result(fallback_node_index=1),
        )

        with pytest.raises(
            GraphForecastExecutionError,
            match="included_node_evidence_insufficient",
        ):
            executor.execute()

        assessment = session.scalar(
            select(EvidenceSufficiencyAssessmentRow).where(
                EvidenceSufficiencyAssessmentRow.forecast_run_id == run_id
            )
        )
        assert assessment is not None
        assert assessment.status == "failed"
        assert session.scalar(
            select(func.count()).select_from(ForecastNodeRunRow).where(
                ForecastNodeRunRow.forecast_run_id == run_id
            )
        ) == 3
        assert session.scalar(
            select(ForecastAggregationRow).where(
                ForecastAggregationRow.forecast_run_id == run_id
            )
        ) is None
        assert session.scalar(
            select(ForecastVersion).where(ForecastVersion.run_id == run_id)
        ) is None
        failure = session.scalar(
            select(GraphExecutionFailureRow).where(
                GraphExecutionFailureRow.forecast_run_id == run_id,
                GraphExecutionFailureRow.stage == "evidence_sufficiency",
            )
        )
        assert failure is not None
        assert failure.error_code == "evidence_sufficiency_gate_failed"

    report = client.get(f"/api/forecasts/{draft['question_id']}/graph-report").json()["report"]
    assert report["final_probability"] is None
    assert report["evidence_sufficiency"]["status"] == "failed"
    assert report["final_answer"]["statement"] == (
        "No private-V1 probability was produced because deterministic evidence "
        "sufficiency was not met."
    )


class _NoCallModel(MockModelProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def complete_json(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("material plan failure reached a model provider")


class _NoCallSearch(MockSearchProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def search(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("material plan failure reached a search provider")


class _HigherWeightOmittingPlanner:
    def plan(self, graph, *, forecast_run_id, budget):
        del budget
        selected = graph.nodes[1:4]
        skipped = [graph.nodes[0], graph.nodes[4]]
        return ResearchPlan(
            id=str(uuid.uuid4()),
            forecast_run_id=forecast_run_id,
            selected_nodes=[node.id for node in selected],
            skipped_nodes=[node.id for node in skipped],
            priority_scores={node.id: 1.0 for node in graph.nodes},
            budget_allocation={
                "planner_version": "graph_research_planner_v3",
                "planned_calls_by_kind": {},
                "per_node": {},
            },
            created_at=utcnow(),
        )


def test_private_v1_plan_materiality_fails_before_all_research_activity(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    model = _NoCallModel()
    search = _NoCallSearch()

    def node_runner(**kwargs):
        return run_graph_node_forecasts(
            **kwargs,
            research_planner=_HigherWeightOmittingPlanner(),
        )

    with main_mod.SessionLocal() as session:
        contract = forecast_contract_from_row(
            approved_contract_for_question(session, draft["question_id"])
        )
        graph = _gate_graph(
            contract.id,
            weights=[0.30, 0.25, 0.20, 0.15, 0.10],
        )
        store_forecast_graph(session, graph)
        session.commit()
        executor, run_id = _executor(
            session,
            draft["question_id"],
            enforce_evidence_gate=True,
            enforce_material_gate=True,
            graph_resolver=lambda *_args, **_kwargs: (contract, graph),
            node_runner=node_runner,
            model=model,
            search=search,
        )

        with pytest.raises(GraphForecastExecutionError) as raised:
            executor.execute()
        assert "private_v1_plan_omits_higher_importance_node" in raised.value.reasons

        plan_row = session.scalar(
            select(ResearchPlanRow).where(
                ResearchPlanRow.forecast_run_id == run_id
            )
        )
        assert plan_row is not None
        audit = json.loads(plan_row.budget_allocation_json)[
            "material_node_plan_audit"
        ]
        assert audit["status"] == "failed"
        assert audit["higher_importance_skipped_node_ids"] == [graph.nodes[0].id]
        assert session.scalar(
            select(func.count()).select_from(EvidenceItem).where(
                EvidenceItem.run_id == run_id
            )
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(EvidenceClaimRow).join(
                EvidenceItem,
                EvidenceClaimRow.evidence_item_id == EvidenceItem.id,
            ).where(EvidenceItem.run_id == run_id)
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(ForecastNodeRunRow).where(
                ForecastNodeRunRow.forecast_run_id == run_id
            )
        ) == 0
        assert session.scalar(
            select(EvidenceSufficiencyAssessmentRow).where(
                EvidenceSufficiencyAssessmentRow.forecast_run_id == run_id
            )
        ) is None
        assert session.scalar(
            select(MaterialNodeCoverageAssessmentRow).where(
                MaterialNodeCoverageAssessmentRow.forecast_run_id == run_id
            )
        ) is None
        failure = session.scalar(
            select(GraphExecutionFailureRow).where(
                GraphExecutionFailureRow.forecast_run_id == run_id,
                GraphExecutionFailureRow.error_code
                == "private_v1_plan_omits_higher_importance_node",
            )
        )
        assert failure is not None
        assert failure.stage == "research_planning"

    assert model.calls == 0
    assert search.calls == 0


def test_private_v1_material_execution_gate_passes_before_unchanged_aggregation(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        contract = forecast_contract_from_row(
            approved_contract_for_question(session, draft["question_id"])
        )
        graph = _gate_graph(
            contract.id,
            weights=[0.30, 0.25, 0.20, 0.15, 0.10],
        )
        # Preserve the full approved DAG for the relationship-aware aggregator:
        # one included parent, one included dependency, and one lower-weight
        # excluded dependency whose allocation remains neutral.
        graph = graph.model_copy(
            update={
                "nodes": [
                    graph.nodes[0].model_copy(
                        update={"dependencies": [graph.nodes[3].id]}
                    ),
                    graph.nodes[1].model_copy(
                        update={"parent_node_id": graph.nodes[0].id}
                    ),
                    graph.nodes[2].model_copy(
                        update={"dependencies": [graph.nodes[1].id]}
                    ),
                    graph.nodes[3],
                    graph.nodes[4],
                ]
            }
        )
        store_forecast_graph(session, graph)
        session.commit()
        executor, run_id = _executor(
            session,
            draft["question_id"],
            enforce_evidence_gate=True,
            enforce_material_gate=True,
            graph_resolver=lambda *_args, **_kwargs: (contract, graph),
            node_runner=_gate_node_result(
                selected_count=4,
                missing_node_index=3,
                critical_node_indices=[],
                include_material_plan_audit=True,
            ),
        )

        version = executor.execute()
        evidence = session.scalar(
            select(EvidenceSufficiencyAssessmentRow).where(
                EvidenceSufficiencyAssessmentRow.forecast_run_id == run_id
            )
        )
        material = session.scalar(
            select(MaterialNodeCoverageAssessmentRow).where(
                MaterialNodeCoverageAssessmentRow.forecast_run_id == run_id
            )
        )
        aggregation = session.scalar(
            select(ForecastAggregationRow).where(
                ForecastAggregationRow.forecast_run_id == run_id
            )
        )
        assert evidence is not None and evidence.status == "passed"
        assert material is not None and material.status == "passed"
        assert material.included_frontier_weight == "0.2"
        assert material.maximum_excluded_weight == "0.15"
        assert aggregation is not None
        assert (
            aggregation.method
            == "relationship_mass_conserving_log_odds_v1"
        )
        trace = json.loads(aggregation.calculation_trace_json)
        material_trace = next(
            item for item in trace if item["step"] == "material_node_coverage_gate"
        )
        assert material_trace["assessment_id"] == material.id
        assert any(item["step"] == "evidence_sufficiency_gate" for item in trace)
        mass_trace = next(item for item in trace if item["step"] == "graph_mass")
        assert mass_trace["conservation_check"] is True
        assert float(mass_trace["neutral_residual_weight"]) > 0
        assert any(
            item["step"] == "source_node_allocation"
            and item["excluded_recipient_ids"]
            for item in trace
        )
        assert {
            item["node_id"]
            for item in trace
            if item["step"] == "excluded_node_mass"
        } == {graph.nodes[3].id, graph.nodes[4].id}
        context = json.loads(version.run.execution_context_json)
        assert context["material_node_coverage_assessment_id"] == material.id
        assert context["material_node_coverage_assessment_input_hash"] == (
            material.assessment_input_hash
        )
        assert version.ensemble_probability is not None

        domain_assessment = material_node_coverage_from_row(material)
        assert store_material_node_coverage_assessment(
            session,
            domain_assessment,
        ).id == material.id
        with pytest.raises(
            MaterialNodeCoverageStoreError,
            match="conflicting_immutable_material_node_coverage_assessment",
        ):
            store_material_node_coverage_assessment(
                session,
                domain_assessment.model_copy(
                    update={"assessment_input_hash": "f" * 64}
                ),
            )

    report = client.get(
        f"/api/forecasts/{draft['question_id']}/graph-report"
    ).json()["report"]
    assert report["material_node_completeness"]["plan_audit"]["status"] == "passed"
    assert report["material_node_completeness"]["execution_assessment"]["status"] == "passed"
    assert report["calculation"]["method"] == (
        "relationship_mass_conserving_log_odds_v1"
    )
    relationship = report["calculation"]["relationship_aggregation"]
    assert relationship["graph_mass"]["conservation_check"] is True
    assert float(relationship["graph_mass"]["neutral_residual_weight"]) > 0
    assert relationship["source_allocations"]
    assert relationship["excluded_nodes"]
    assert report["final_probability"] is not None


def test_private_v1_material_execution_gate_fails_closed_and_preserves_artifacts(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        contract = forecast_contract_from_row(
            approved_contract_for_question(session, draft["question_id"])
        )
        graph = _gate_graph(
            contract.id,
            weights=[0.30, 0.25, 0.20, 0.15, 0.10],
        )
        store_forecast_graph(session, graph)
        session.commit()
        executor, run_id = _executor(
            session,
            draft["question_id"],
            enforce_evidence_gate=True,
            enforce_material_gate=True,
            graph_resolver=lambda *_args, **_kwargs: (contract, graph),
            node_runner=_gate_node_result(
                selected_count=4,
                missing_node_index=0,
                primary_node_index=1,
                critical_node_indices=[],
                include_material_plan_audit=True,
            ),
        )

        with pytest.raises(GraphForecastExecutionError) as raised:
            executor.execute()
        assert "material_node_coverage_gate_failed" in raised.value.reasons

        evidence = session.scalar(
            select(EvidenceSufficiencyAssessmentRow).where(
                EvidenceSufficiencyAssessmentRow.forecast_run_id == run_id
            )
        )
        material = session.scalar(
            select(MaterialNodeCoverageAssessmentRow).where(
                MaterialNodeCoverageAssessmentRow.forecast_run_id == run_id
            )
        )
        assert evidence is not None and evidence.status == "passed"
        assert material is not None and material.status == "failed"
        assert json.loads(material.higher_importance_excluded_node_ids_json) == [
            graph.nodes[0].id
        ]
        assert session.scalar(
            select(func.count()).select_from(ForecastNodeRunRow).where(
                ForecastNodeRunRow.forecast_run_id == run_id
            )
        ) == 3
        assert session.scalar(
            select(ForecastAggregationRow).where(
                ForecastAggregationRow.forecast_run_id == run_id
            )
        ) is None
        assert session.scalar(
            select(ForecastVersion).where(ForecastVersion.run_id == run_id)
        ) is None
        failure = session.scalar(
            select(GraphExecutionFailureRow).where(
                GraphExecutionFailureRow.forecast_run_id == run_id,
                GraphExecutionFailureRow.stage == "material_node_coverage",
            )
        )
        assert failure is not None
        assert failure.error_code == "material_node_coverage_gate_failed"

    report = client.get(
        f"/api/forecasts/{draft['question_id']}/graph-report"
    ).json()["report"]
    assert report["final_probability"] is None
    assert report["material_node_completeness"]["execution_assessment"]["status"] == "failed"
    assert report["final_answer"]["statement"] == (
        "No private-V1 probability was produced because a higher-importance "
        "graph uncertainty was omitted while lower-importance nodes were retained."
    )


def test_private_v1_preserves_evidence_and_material_failures_together(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        contract = forecast_contract_from_row(
            approved_contract_for_question(session, draft["question_id"])
        )
        graph = _gate_graph(
            contract.id,
            weights=[0.30, 0.25, 0.20, 0.15, 0.10],
        )
        store_forecast_graph(session, graph)
        session.commit()
        executor, run_id = _executor(
            session,
            draft["question_id"],
            enforce_evidence_gate=True,
            enforce_material_gate=True,
            graph_resolver=lambda *_args, **_kwargs: (contract, graph),
            node_runner=_gate_node_result(
                fallback_node_index=1,
                selected_count=4,
                missing_node_index=0,
                primary_node_index=1,
                critical_node_indices=[],
                include_material_plan_audit=True,
            ),
        )

        with pytest.raises(GraphForecastExecutionError) as raised:
            executor.execute()
        assert "evidence_sufficiency_gate_failed" in raised.value.reasons
        assert "material_node_coverage_gate_failed" in raised.value.reasons

        evidence = session.scalar(
            select(EvidenceSufficiencyAssessmentRow).where(
                EvidenceSufficiencyAssessmentRow.forecast_run_id == run_id
            )
        )
        material = session.scalar(
            select(MaterialNodeCoverageAssessmentRow).where(
                MaterialNodeCoverageAssessmentRow.forecast_run_id == run_id
            )
        )
        assert evidence is not None and evidence.status == "failed"
        assert material is not None and material.status == "failed"
        failure_codes = set(
            session.scalars(
                select(GraphExecutionFailureRow.error_code).where(
                    GraphExecutionFailureRow.forecast_run_id == run_id,
                    GraphExecutionFailureRow.error_code.in_(
                        [
                            "evidence_sufficiency_gate_failed",
                            "material_node_coverage_gate_failed",
                        ]
                    ),
                )
            ).all()
        )
        assert failure_codes == {
            "evidence_sufficiency_gate_failed",
            "material_node_coverage_gate_failed",
        }
        assert session.scalar(
            select(ForecastAggregationRow).where(
                ForecastAggregationRow.forecast_run_id == run_id
            )
        ) is None
        assert session.scalar(
            select(ForecastVersion).where(ForecastVersion.run_id == run_id)
        ) is None


def test_private_v1_scenario_synthesis_precedes_unchanged_aggregation(client) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        contract = forecast_contract_from_row(
            approved_contract_for_question(session, draft["question_id"])
        )
        graph = _gate_graph(
            contract.id,
            weights=[0.30, 0.25, 0.20, 0.15, 0.10],
        )
        store_forecast_graph(session, graph)
        session.commit()
        executor, run_id = _executor(
            session,
            draft["question_id"],
            enforce_evidence_gate=True,
            enforce_material_gate=True,
            enforce_scenario_synthesis=True,
            graph_resolver=lambda *_args, **_kwargs: (contract, graph),
            node_runner=_gate_node_result(
                selected_count=3,
                critical_node_indices=[],
                include_material_plan_audit=True,
                include_scenario_budget=True,
            ),
        )

        version = executor.execute()
        scenario = session.scalar(
            select(ScenarioSynthesisRow).where(
                ScenarioSynthesisRow.forecast_run_id == run_id
            )
        )
        assert scenario is not None
        assert scenario.status == "passed"
        assert len(json.loads(scenario.scenarios_json)) == 3
        assert json.loads(scenario.coverage_audit_json)["errors"] == []
        domain_scenario = scenario_synthesis_from_row(scenario)
        assert store_scenario_synthesis(session, domain_scenario).id == scenario.id
        with pytest.raises(
            ScenarioSynthesisStoreError,
            match="conflicting_immutable_scenario_synthesis",
        ):
            store_scenario_synthesis(
                session,
                domain_scenario.model_copy(update={"input_hash": "f" * 64}),
            )
        session.expire(scenario)
        assert scenario.input_hash == domain_scenario.input_hash

        stored_node_runs = [
            item
            for item in session.scalars(
                select(ForecastNodeRunRow).where(
                    ForecastNodeRunRow.forecast_run_id == run_id
                )
            ).all()
        ]
        domain_runs = [
            ForecastNodeRun(
                id=item.id,
                run_id=item.forecast_run_id,
                node_id=item.node_id,
                probability=item.probability,
                confidence=item.confidence,
                reasoning=item.reasoning,
                supporting_claim_ids=json.loads(item.supporting_claim_ids_json),
                opposing_claim_ids=json.loads(item.opposing_claim_ids_json),
                uncertainty_notes=json.loads(item.uncertainty_notes_json),
                model_used=item.model_used,
                uncertainty=item.uncertainty,
                raw_importance_weight=item.raw_importance_weight,
                dependency_factor=item.dependency_factor,
                normalized_weight=item.normalized_weight,
                probability_contribution=item.probability_contribution,
                created_at=item.created_at,
            )
            for item in stored_node_runs
        ]
        expected = RelationshipMassConservingLogOddsAggregator().aggregate(
            graph,
            domain_runs,
            exclusion_origins={
                node.id: "research_plan"
                for node in graph.nodes
                if node.id not in {run.node_id for run in domain_runs}
            },
        )
        assert version.ensemble_probability == expected.final_probability
        aggregation = session.scalar(
            select(ForecastAggregationRow).where(
                ForecastAggregationRow.forecast_run_id == run_id
            )
        )
        assert aggregation is not None
        trace = json.loads(aggregation.calculation_trace_json)
        scenario_step = next(
            item for item in trace if item.get("step") == "scenario_synthesis"
        )
        assert scenario_step["scenario_synthesis_id"] == scenario.id
        assert scenario_step["numerical_effect"] == "none"
        assert trace[3:] == expected.calculation_trace
        assert json.loads(aggregation.node_contributions_json) == [
            contribution.model_dump(mode="json")
            for contribution in expected.node_contributions
        ]

    report = client.get(
        f"/api/forecasts/{draft['question_id']}/graph-report"
    ).json()["report"]
    assert report["scenario_synthesis"]["status"] == "passed"
    assert len(report["scenario_synthesis"]["scenarios"]) == 3
    assert report["final_probability"] == version.ensemble_probability


def test_failed_scenario_synthesis_preserves_prior_artifacts_and_no_probability(
    client,
) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    model = CountingScenarioModel(invalid_scenario=True)
    with main_mod.SessionLocal() as session:
        contract = forecast_contract_from_row(
            approved_contract_for_question(session, draft["question_id"])
        )
        graph = _gate_graph(
            contract.id,
            weights=[0.30, 0.25, 0.20, 0.15, 0.10],
        )
        store_forecast_graph(session, graph)
        session.commit()
        executor, run_id = _executor(
            session,
            draft["question_id"],
            enforce_evidence_gate=True,
            enforce_material_gate=True,
            enforce_scenario_synthesis=True,
            model=model,
            graph_resolver=lambda *_args, **_kwargs: (contract, graph),
            node_runner=_gate_node_result(
                selected_count=3,
                critical_node_indices=[],
                include_material_plan_audit=True,
                include_scenario_budget=True,
            ),
        )

        with pytest.raises(GraphForecastExecutionError) as exc_info:
            executor.execute()

        assert exc_info.value.stage == "scenario_synthesis"
        assert exc_info.value.reasons == ["scenario_synthesis_failed"]
        scenario = session.scalar(
            select(ScenarioSynthesisRow).where(
                ScenarioSynthesisRow.forecast_run_id == run_id
            )
        )
        assert scenario is not None and scenario.status == "failed"
        assert json.loads(scenario.failure_reasons_json) == [
            "structured_output_schema_invalid"
        ]
        assert session.scalar(
            select(func.count(ForecastNodeRunRow.id)).where(
                ForecastNodeRunRow.forecast_run_id == run_id
            )
        ) == 3
        assert session.scalar(
            select(ForecastAggregationRow).where(
                ForecastAggregationRow.forecast_run_id == run_id
            )
        ) is None
        assert session.scalar(
            select(ForecastVersion).where(ForecastVersion.run_id == run_id)
        ) is None
        failure = session.scalar(
            select(GraphExecutionFailureRow).where(
                GraphExecutionFailureRow.forecast_run_id == run_id,
                GraphExecutionFailureRow.stage == "scenario_synthesis",
            )
        )
        assert failure is not None
        assert failure.error_code == "scenario_synthesis_failed"
    assert model.scenario_calls == 1

    report = client.get(
        f"/api/forecasts/{draft['question_id']}/graph-report"
    ).json()["report"]
    assert report["scenario_synthesis"]["status"] == "failed"
    assert report["final_probability"] is None


def test_oversize_scenario_packet_fails_and_persists_before_model_call(
    client,
) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    model = CountingScenarioModel()
    with main_mod.SessionLocal() as session:
        contract = forecast_contract_from_row(
            approved_contract_for_question(session, draft["question_id"])
        )
        graph = _gate_graph(
            contract.id,
            weights=[0.30, 0.25, 0.20, 0.15, 0.10],
        )
        store_forecast_graph(session, graph)
        session.commit()
        executor, run_id = _executor(
            session,
            draft["question_id"],
            enforce_evidence_gate=True,
            enforce_material_gate=True,
            enforce_scenario_synthesis=True,
            model=model,
            graph_resolver=lambda *_args, **_kwargs: (contract, graph),
            node_runner=_gate_node_result(
                selected_count=3,
                critical_node_indices=[],
                include_material_plan_audit=True,
                include_scenario_budget=True,
                node_reasoning_characters=11_000,
            ),
        )

        with pytest.raises(GraphForecastExecutionError):
            executor.execute()

        scenario = session.scalar(
            select(ScenarioSynthesisRow).where(
                ScenarioSynthesisRow.forecast_run_id == run_id
            )
        )
        assert scenario is not None and scenario.status == "failed"
        assert json.loads(scenario.failure_reasons_json) == [
            "scenario_synthesis_input_character_limit_exceeded"
        ]
        assert session.scalar(
            select(ForecastAggregationRow).where(
                ForecastAggregationRow.forecast_run_id == run_id
            )
        ) is None
        assert session.scalar(
            select(ForecastVersion).where(ForecastVersion.run_id == run_id)
        ) is None
    assert model.scenario_calls == 0


@pytest.mark.parametrize(
    ("fallback_node_index", "missing_node_index", "selected_count"),
    [
        (1, None, 3),
        (None, 0, 4),
    ],
)
def test_prior_private_v1_gates_prevent_scenario_call(
    client,
    fallback_node_index: int | None,
    missing_node_index: int | None,
    selected_count: int,
) -> None:
    draft = _approved_forecast(client)
    from forecastlab_api import main as main_mod

    model = CountingScenarioModel()
    with main_mod.SessionLocal() as session:
        contract = forecast_contract_from_row(
            approved_contract_for_question(session, draft["question_id"])
        )
        graph = _gate_graph(
            contract.id,
            weights=[0.30, 0.25, 0.20, 0.15, 0.10],
        )
        store_forecast_graph(session, graph)
        session.commit()
        executor, run_id = _executor(
            session,
            draft["question_id"],
            enforce_evidence_gate=True,
            enforce_material_gate=True,
            enforce_scenario_synthesis=True,
            model=model,
            graph_resolver=lambda *_args, **_kwargs: (contract, graph),
            node_runner=_gate_node_result(
                fallback_node_index=fallback_node_index,
                selected_count=selected_count,
                missing_node_index=missing_node_index,
                primary_node_index=1 if missing_node_index == 0 else 0,
                critical_node_indices=[],
                include_material_plan_audit=True,
                include_scenario_budget=True,
            ),
        )

        with pytest.raises(GraphForecastExecutionError):
            executor.execute()

        assert session.scalar(
            select(ScenarioSynthesisRow).where(
                ScenarioSynthesisRow.forecast_run_id == run_id
            )
        ) is None
    assert model.scenario_calls == 0
