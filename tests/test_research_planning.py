from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest

from forecastlab.budget import Budget
from forecastlab.graph_execution import execute_parallel_research, run_graph_forecast_engine
from forecastlab.graph_research import GraphResearchResult, NodeResearchPlan
from forecastlab.providers.mock import MockModelProvider, MockSearchProvider
from forecastlab.research_planning import ResearchPlanner, ResearchPlanningError
from forecastlab.scenario_synthesis import (
    SCENARIO_SYNTHESIS_CALL_KIND,
    SCENARIO_SYNTHESIS_MAX_OUTPUT_TOKENS,
    SCENARIO_SYNTHESIS_POLICY_VERSION,
    SCENARIO_SYNTHESIS_RESERVED_INPUT_TOKENS,
)
from forecastlab.schemas import ForecastContract, ForecastGraph, ForecastNode, ForecastProfile

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
NODE_TYPES = (
    "base_rate",
    "resolver",
    "driver",
    "trend",
    "dependency",
    "scenario",
    "adversarial",
)


def _graph(count: int = 36, *, critical_count: int = 2) -> ForecastGraph:
    nodes: list[ForecastNode] = []
    for index in range(count):
        node_id = f"node-{index:02d}"
        dependencies = [] if index < 2 else [f"node-{index % 2:02d}"]
        nodes.append(
            ForecastNode(
                id=node_id,
                graph_id="graph-research-planner",
                question=f"What evidence resolves research factor {index:02d}?",
                node_type=NODE_TYPES[index % len(NODE_TYPES)],
                importance_weight=(
                    max(0.8, 0.99 - index * 0.01)
                    if index < critical_count
                    else 0.6
                ),
                dependencies=dependencies,
                preferred_sources=["BLS"],
                required_output_type="probability",
            )
        )
    return ForecastGraph(
        id="graph-research-planner",
        contract_id="contract-research-planner",
        status="approved",
        created_at=NOW,
        generation_model="test",
        root_question="Will the measurable outcome occur?",
        nodes=nodes,
    )


def _profile(
    *,
    max_cost_usd: float = 5.0,
    max_model_calls: int = 100,
    max_search_calls: int = 100,
    max_fetches: int = 100,
) -> ForecastProfile:
    return ForecastProfile(
        id="research-planner-test",
        label="Research planner test",
        description="Bounded graph execution for tests.",
        execution_strategy="graph_nodes",
        aggregation_method="dependency_discounted_weighted_mean_v1",
        tracks=["single_agent"],
        search_results_per_subquestion=2,
        fetches_per_subquestion=1,
        max_model_calls=max_model_calls,
        max_search_calls=max_search_calls,
        max_fetched_documents=max_fetches,
        max_tokens=200_000,
        max_estimated_cost_usd=max_cost_usd,
        max_wall_clock_seconds=300,
    )


def _contract() -> ForecastContract:
    return ForecastContract(
        id="contract-research-planner",
        question_id="question-research-planner",
        created_at=NOW,
        created_by="test",
        original_question="Will US unemployment exceed 5% before June 2027?",
        normalized_question="Will US U-3 unemployment exceed 5% before 30 June 2027?",
        yes_condition="BLS publishes U-3 above 5.0% before the deadline.",
        no_condition="BLS does not publish U-3 above 5.0% before the deadline.",
        resolution_date=datetime(2027, 6, 30, tzinfo=UTC),
        authoritative_source="Bureau of Labor Statistics",
        resolution_method="Check official BLS releases through the deadline.",
        status="approved",
    )


def test_large_graph_selects_at_most_eight_nodes_and_twenty_claims() -> None:
    graph = _graph()

    plan = ResearchPlanner().plan(
        graph,
        forecast_run_id="run-large-graph",
        budget=Budget(_profile()),
        created_at=NOW,
    )

    assert len(plan.selected_nodes) == 8
    assert len(plan.skipped_nodes) == 28
    assert set(plan.selected_nodes) | set(plan.skipped_nodes) == {
        node.id for node in graph.nodes
    }
    assert plan.priority_scores["node-02"] == pytest.approx(0.6 + 1 + 0.75)
    per_node = plan.budget_allocation["per_node"]
    assert all(allocation["searches"] <= 5 for allocation in per_node.values())
    assert all(
        allocation["evidence_document_max_chars"] <= 8000
        for allocation in per_node.values()
    )
    assert sum(
        int(allocation["max_evidence_claims"])
        for allocation in per_node.values()
    ) <= 20
    assert set(plan.skipped_nodes) == set(
        plan.budget_allocation["skipped_reasons"]
    )


def test_critical_nodes_are_preserved_and_excess_critical_nodes_fail_preflight() -> None:
    plan = ResearchPlanner().plan(
        _graph(12, critical_count=3),
        forecast_run_id="run-critical",
        budget=Budget(_profile()),
        created_at=NOW,
    )

    assert {"node-00", "node-01", "node-02"} <= set(plan.selected_nodes)

    with pytest.raises(ResearchPlanningError) as exc_info:
        ResearchPlanner().plan(
            _graph(12, critical_count=9),
            forecast_run_id="run-too-many-critical",
            budget=Budget(_profile()),
            created_at=NOW,
        )
    assert exc_info.value.reasons == ["critical_nodes_exceed_research_node_limit"]


def test_duplicate_noncritical_research_questions_are_skipped_and_audited() -> None:
    graph = _graph(10)
    duplicate = graph.nodes[3].model_copy(
        update={"question": graph.nodes[2].question}
    )
    graph = graph.model_copy(
        update={"nodes": [*graph.nodes[:3], duplicate, *graph.nodes[4:]]}
    )

    plan = ResearchPlanner().plan(
        graph,
        forecast_run_id="run-deduplicated",
        budget=Budget(_profile()),
        created_at=NOW,
    )

    assert "node-03" in plan.skipped_nodes
    assert plan.budget_allocation["skipped_reasons"]["node-03"] == (
        "duplicate_research_question:node-02"
    )


def test_planner_reduces_nodes_to_fit_real_provider_cost_ceiling() -> None:
    profile = _profile(max_cost_usd=0.06)
    budget = Budget(
        profile,
        provider="openai",
        model="test-model",
        search_provider="tavily",
        pricing_catalog={
            "providers": {
                "openai": {
                    "test-model": {
                        "input_per_million": 1.0,
                        "output_per_million": 1.0,
                    }
                }
            },
            "search": {"tavily": {"estimated_per_request": 0.01}},
        },
    )

    plan = ResearchPlanner().plan(
        _graph(12),
        forecast_run_id="run-budget-aware",
        budget=budget,
        created_at=NOW,
    )

    assert len(plan.selected_nodes) == 3
    assert plan.budget_allocation["estimated_total_cost_usd"] <= 0.06
    assert all(
        allocation["searches"] == 1
        for allocation in plan.budget_allocation["per_node"].values()
    )


def test_planner_accounts_for_graph_generation_spend_before_research() -> None:
    profile = _profile(max_cost_usd=0.40)
    budget = Budget(
        profile,
        provider="openai",
        model="gpt-5-mini-2025-08-07",
        search_provider="tavily",
    )
    budget.state.model_calls = 1
    budget.state.tokens = 4500
    budget.state.cost_usd = 0.055
    budget.state.model_cost_usd = 0.055

    plan = ResearchPlanner().plan(
        _graph(12),
        forecast_run_id="run-after-graph-generation",
        budget=budget,
        created_at=NOW,
    )

    assert len(plan.selected_nodes) == 4
    assert (
        plan.budget_allocation["estimated_total_cost_usd"]
        + budget.state.cost_usd
        <= profile.max_estimated_cost_usd
    )


def test_critical_nodes_that_cannot_fit_budget_fail_before_execution() -> None:
    budget = Budget(
        _profile(max_cost_usd=0.01),
        provider="openai",
        model="test-model",
        search_provider="tavily",
        pricing_catalog={
            "providers": {
                "openai": {
                    "test-model": {
                        "input_per_million": 5.0,
                        "output_per_million": 15.0,
                    }
                }
            },
            "search": {"tavily": {"estimated_per_request": 0.008}},
        },
    )

    with pytest.raises(ResearchPlanningError) as exc_info:
        ResearchPlanner().plan(
            _graph(12),
            forecast_run_id="run-critical-budget-failure",
            budget=budget,
            created_at=NOW,
        )

    assert exc_info.value.reasons == ["critical_nodes_exceed_budget"]
    assert budget.state.model_calls == 0
    assert budget.state.search_calls == 0


def test_private_v1_planner_reserves_scenario_capacity_before_research() -> None:
    profile = _profile().model_copy(
        update={"scenario_synthesis_policy": SCENARIO_SYNTHESIS_POLICY_VERSION}
    )
    budget = Budget(profile, provider="mock", search_provider="mock")

    plan = ResearchPlanner().plan(
        _graph(8, critical_count=0),
        forecast_run_id="run-scenario-reservation",
        budget=budget,
        created_at=NOW,
    )

    allocation = plan.budget_allocation
    assert allocation["scenario_synthesis_policy"] == (
        SCENARIO_SYNTHESIS_POLICY_VERSION
    )
    assert allocation["planned_calls_by_kind"][SCENARIO_SYNTHESIS_CALL_KIND] == 1
    assert allocation["reserved_scenario_synthesis_calls"] == 1
    assert allocation["scenario_synthesis_reserved_input_tokens"] == (
        SCENARIO_SYNTHESIS_RESERVED_INPUT_TOKENS
    )
    assert allocation["scenario_synthesis_max_output_tokens"] == (
        SCENARIO_SYNTHESIS_MAX_OUTPUT_TOKENS
    )
    assert allocation["scenario_synthesis_reserved_tokens"] == (
        SCENARIO_SYNTHESIS_RESERVED_INPUT_TOKENS
        + SCENARIO_SYNTHESIS_MAX_OUTPUT_TOKENS
    )
    assert allocation["scenario_synthesis_post_stage_headroom"]["model_calls"] >= 0
    assert budget.state.model_calls == 0
    assert budget.state.tokens == 0
    assert budget.reservations == []


def test_none_policy_preserves_planner_allocation_without_scenario_fields() -> None:
    budget = Budget(_profile(), provider="mock", search_provider="mock")

    plan = ResearchPlanner().plan(
        _graph(8, critical_count=0),
        forecast_run_id="run-no-scenario-reservation",
        budget=budget,
        created_at=NOW,
    )

    allocation = plan.budget_allocation
    assert SCENARIO_SYNTHESIS_CALL_KIND not in allocation["planned_calls_by_kind"]
    assert not any(key.startswith("scenario_synthesis") for key in allocation)
    assert "available_after_scenario_reservation" not in allocation


def test_infeasible_scenario_reservation_fails_before_any_provider_activity() -> None:
    profile = _profile().model_copy(
        update={
            "scenario_synthesis_policy": SCENARIO_SYNTHESIS_POLICY_VERSION,
            "max_tokens": SCENARIO_SYNTHESIS_RESERVED_INPUT_TOKENS,
        }
    )
    budget = Budget(profile, provider="mock", search_provider="mock")

    with pytest.raises(ResearchPlanningError) as exc_info:
        ResearchPlanner().plan(
            _graph(8, critical_count=0),
            forecast_run_id="run-infeasible-scenario-reservation",
            budget=budget,
            created_at=NOW,
        )

    assert exc_info.value.reasons == [
        "scenario_synthesis_reservation_exceeds_run_budget"
    ]
    assert budget.state.model_calls == 0
    assert budget.state.search_calls == 0
    assert budget.state.fetches == 0
    assert budget.reservations == []


def test_parallel_research_returns_deterministic_graph_order() -> None:
    nodes = _graph(4, critical_count=0).nodes
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    def worker(node: ForecastNode) -> GraphResearchResult:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return GraphResearchResult(
            node_id=node.id,
            plan=NodeResearchPlan(
                primary_research_question=node.question,
                supporting_search_queries=[node.question],
                required_evidence_types=["dated source"],
            ),
            queries_attempted=[node.question],
            sources_checked=[],
        )

    results = execute_parallel_research(nodes, worker, max_workers=4)

    assert maximum_active > 1
    assert [result.node.id for result in results] == [node.id for node in nodes]
    assert [result.result.node_id for result in results if result.result] == [
        node.id for node in nodes
    ]


def test_planned_graph_execution_reaches_node_forecasts_and_aggregation() -> None:
    graph = _graph()
    persisted_plan = []
    persisted_nodes: list[str] = []

    result = run_graph_forecast_engine(
        contract=_contract(),
        graph=graph,
        profile_id="research-planner-test",
        mode="demo",
        as_of=None,
        model=MockModelProvider(),
        search=MockSearchProvider(),
        run_id="run-planned-graph",
        profile=_profile(),
        persist_research_plan=persisted_plan.append,
        persist_research=lambda node, _evidence, _rejected, _claims: (
            persisted_nodes.append(node.id)
        ),
    )

    assert persisted_plan == [result.research_plan]
    assert result.research_plan is not None
    assert len(result.research_plan.selected_nodes) == 8
    selected = set(result.research_plan.selected_nodes)
    assert all(
        execution.node_run is not None
        for execution in result.nodes
        if execution.node.id in selected
    )
    assert all(
        not execution.research_selected
        for execution in result.nodes
        if execution.node.id not in selected
    )
    assert persisted_nodes == result.research_plan.budget_allocation[
        "deterministic_persistence_order"
    ]
    assert result.aggregation.ensemble_probability is not None
