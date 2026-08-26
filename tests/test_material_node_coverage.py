from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from forecastlab.material_node_coverage import (
    MaterialNodeFailureIdentity,
    assess_material_node_coverage,
    assess_material_node_plan,
)
from forecastlab.research_planning import ResearchPlan
from forecastlab.schemas import ForecastGraph, ForecastNode, ForecastNodeRun

NOW = datetime(2026, 8, 26, tzinfo=UTC)
WEIGHTS = {"A": 0.30, "B": 0.25, "C": 0.20, "D": 0.15, "E": 0.10}


def _graph(
    weights: dict[str, float] | None = None,
    *,
    order: list[str] | None = None,
    parent_by_node: dict[str, str] | None = None,
    dependencies_by_node: dict[str, list[str]] | None = None,
) -> ForecastGraph:
    values = weights or WEIGHTS
    node_order = order or list(values)
    node_types = {
        "A": "base_rate",
        "B": "driver",
        "C": "adversarial",
        "D": "resolver",
        "E": "trend",
    }
    return ForecastGraph(
        id="graph-1",
        contract_id="contract-1",
        version=1,
        status="approved",
        created_at=NOW,
        generation_model="stub",
        root_question="Will the event occur?",
        nodes=[
            ForecastNode(
                id=node_id,
                graph_id="graph-1",
                parent_node_id=(parent_by_node or {}).get(node_id),
                question=f"Node {node_id}?",
                node_type=node_types.get(node_id, "driver"),  # type: ignore[arg-type]
                importance_weight=values[node_id],
                dependencies=(dependencies_by_node or {}).get(node_id, []),
                required_output_type="probability",
            )
            for node_id in node_order
        ],
    )


def _plan(
    selected: list[str],
    *,
    skipped: list[str] | None = None,
    plan_id: str = "plan-1",
) -> ResearchPlan:
    skipped_ids = skipped if skipped is not None else [
        node_id for node_id in WEIGHTS if node_id not in selected
    ]
    all_ids = [*selected, *skipped_ids]
    return ResearchPlan(
        id=plan_id,
        forecast_run_id="forecast-run-1",
        selected_nodes=selected,
        skipped_nodes=skipped_ids,
        priority_scores={node_id: 1.0 for node_id in all_ids},
        budget_allocation={},
        created_at=NOW,
    )


def _run(
    node_id: str,
    *,
    probability: float = 0.55,
    reasoning: str = "Reasoning is not a policy input.",
) -> ForecastNodeRun:
    return ForecastNodeRun(
        id=f"node-run-{node_id}",
        run_id="forecast-run-1",
        node_id=node_id,
        probability=probability,
        confidence=0.9,
        reasoning=reasoning,
        supporting_claim_ids=[f"claim-{node_id}"],
        opposing_claim_ids=[],
        uncertainty_notes=[],
        model_used="stub",
        uncertainty=0.1,
        created_at=NOW,
    )


def _coverage(
    graph: ForecastGraph,
    plan: ResearchPlan,
    included: list[str],
    *,
    failures: list[MaterialNodeFailureIdentity] | None = None,
):
    return assess_material_node_coverage(
        forecast_run_id="forecast-run-1",
        graph=graph,
        plan=plan,
        node_runs=[_run(node_id) for node_id in included],
        evidence_sufficiency_assessment_id="evidence-assessment-1",
        evidence_sufficiency_assessment_hash="a" * 64,
        node_failures=failures or [],
    )


def test_plan_frontier_passes_when_selected_nodes_are_the_material_prefix() -> None:
    audit = assess_material_node_plan(
        graph=_graph(),
        plan=_plan(["A", "B", "C"]),
    )

    assert audit.status == "passed"
    assert audit.selected_frontier_weight == "0.2"
    assert audit.maximum_skipped_weight == "0.15"
    assert audit.higher_importance_skipped_node_ids == []


def test_plan_frontier_fails_when_higher_weight_node_is_skipped() -> None:
    audit = assess_material_node_plan(
        graph=_graph(),
        plan=_plan(["B", "C", "D"], skipped=["A", "E"]),
    )

    assert audit.status == "failed"
    assert audit.selected_frontier_weight == "0.15"
    assert audit.maximum_skipped_weight == "0.3"
    assert audit.higher_importance_skipped_node_ids == ["A"]
    assert "private_v1_plan_omits_higher_importance_node" in audit.reasons


def test_execution_frontier_passes_without_changing_aggregation_inputs() -> None:
    graph = _graph()
    plan = _plan(["A", "B", "C", "D"])
    assessment = _coverage(graph, plan, ["A", "B", "C"])

    assert assessment.status == "passed"
    assert assessment.included_frontier_weight == "0.2"
    assert assessment.maximum_excluded_weight == "0.15"
    assert assessment.included_graph_weight == "0.75"
    assert assessment.excluded_graph_weight == "0.25"
    assert assessment.included_node_ids == ["A", "B", "C"]


def test_execution_frontier_fails_when_material_node_is_excluded() -> None:
    assessment = _coverage(
        _graph(),
        _plan(["A", "B", "C", "D"]),
        ["B", "C", "D"],
    )

    assert assessment.status == "failed"
    assert assessment.higher_importance_excluded_node_ids == ["A"]
    assert "higher_importance_graph_node_excluded" in assessment.reasons


def test_equal_weight_frontier_is_allowed_and_order_independent() -> None:
    weights = {"A": 0.30, "B": 0.25, "C": 0.20, "D": 0.20, "E": 0.10}
    first_graph = _graph(weights)
    second_graph = _graph(weights, order=["E", "C", "A", "D", "B"])
    first_plan = _plan(["A", "B", "C"], skipped=["D", "E"])
    second_plan = _plan(["C", "A", "B"], skipped=["E", "D"])

    first = assess_material_node_plan(graph=first_graph, plan=first_plan)
    second = assess_material_node_plan(graph=second_graph, plan=second_plan)

    assert first.status == second.status == "passed"
    assert first.frontier_tie_skipped_node_ids == ["D"]
    assert first.warnings == ["equal_weight_nodes_skipped_at_selected_frontier"]
    assert second.audit_input_hash == first.audit_input_hash


def test_equal_weight_execution_frontier_passes_with_warning() -> None:
    graph = _graph(
        {"A": 0.30, "B": 0.25, "C": 0.20, "D": 0.20, "E": 0.10}
    )
    assessment = _coverage(
        graph,
        _plan(["A", "B", "C", "D"]),
        ["A", "B", "C"],
    )

    assert assessment.status == "passed"
    assert assessment.frontier_tie_excluded_node_ids == ["D"]
    assert assessment.warnings == [
        "equal_weight_nodes_excluded_at_included_frontier"
    ]


def test_strict_decimal_comparison_distinguishes_nearby_canonical_weights() -> None:
    graph = _graph(
        {"A": 0.300000000002, "B": 0.300000000001, "C": 0.2, "D": 0.1, "E": 0.0}
    )
    audit = assess_material_node_plan(
        graph=graph,
        plan=_plan(["B", "C", "D"], skipped=["A", "E"]),
    )

    assert audit.status == "failed"
    assert audit.higher_importance_skipped_node_ids == ["A"]


@pytest.mark.parametrize(
    ("graph", "plan", "reason"),
    [
        (
            _graph({node_id: 0.0 for node_id in WEIGHTS}),
            _plan(["A", "B", "C"]),
            "all_graph_importance_weights_zero",
        ),
        (
            _graph(),
            _plan(["A", "B", "ghost"], skipped=["C", "D", "E"]),
            "selected_node_absent_from_graph:ghost",
        ),
    ],
)
def test_invalid_plan_inputs_fail_closed(
    graph: ForecastGraph,
    plan: ResearchPlan,
    reason: str,
) -> None:
    audit = assess_material_node_plan(graph=graph, plan=plan)
    assert audit.status == "failed"
    assert reason in audit.reasons


def test_duplicate_graph_node_id_fails_closed() -> None:
    graph = _graph()
    graph = graph.model_copy(update={"nodes": [*graph.nodes, graph.nodes[0]]})
    audit = assess_material_node_plan(
        graph=graph,
        plan=_plan(["A", "B", "C"]),
    )
    assert audit.status == "failed"
    assert audit.reasons == ["duplicate_graph_node_id"]


def test_included_node_absent_from_graph_fails_closed() -> None:
    assessment = _coverage(
        _graph(),
        _plan(["A", "B", "C", "ghost"], skipped=["D", "E"]),
        ["A", "B", "ghost"],
    )
    assert assessment.status == "failed"
    assert "included_node_absent_from_graph:ghost" in assessment.reasons


def test_all_zero_execution_weights_fail_closed() -> None:
    graph = _graph({node_id: 0.0 for node_id in WEIGHTS})
    assessment = _coverage(
        graph,
        _plan(["A", "B", "C", "D"]),
        ["A", "B", "C"],
    )

    assert assessment.status == "failed"
    assert "all_graph_importance_weights_zero" in assessment.reasons


def test_zero_weight_omission_below_positive_frontier_passes() -> None:
    graph = _graph({"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1, "E": 0.0})
    assessment = _coverage(
        graph,
        _plan(["A", "B", "C", "D"]),
        ["A", "B", "C"],
    )
    assert assessment.status == "passed"


def test_zero_weight_included_while_positive_node_is_omitted_fails() -> None:
    graph = _graph({"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1, "E": 0.0})
    assessment = _coverage(
        graph,
        _plan(["A", "B", "C", "D", "E"]),
        ["B", "C", "E"],
    )
    assert assessment.status == "failed"
    assert assessment.higher_importance_excluded_node_ids == ["A", "D"]


def test_relationship_findings_are_warnings_only() -> None:
    graph = _graph(
        parent_by_node={"B": "D"},
        dependencies_by_node={"C": ["D"]},
    )
    assessment = _coverage(
        graph,
        _plan(["A", "B", "C", "D"]),
        ["A", "B", "C"],
    )

    assert assessment.status == "passed"
    assert assessment.missing_parent_relationships[0].model_dump() == {
        "node_id": "B",
        "excluded_parent_node_id": "D",
    }
    assert assessment.missing_dependency_relationships[0].model_dump() == {
        "node_id": "C",
        "excluded_dependency_node_id": "D",
    }
    assert "included_nodes_have_excluded_parents" in assessment.warnings
    assert "included_nodes_have_excluded_dependencies" in assessment.warnings


def test_probability_reasoning_and_outcome_cannot_change_material_assessment() -> None:
    graph = _graph()
    plan = _plan(["A", "B", "C", "D"])
    base_runs = [_run(node_id) for node_id in ["A", "B", "C"]]
    changed_runs = [
        run.model_copy(
            update={
                "probability": 0.01 + index * 0.48,
                "reasoning": f"Changed reasoning {index}",
                "confidence": 0.0,
            }
        )
        for index, run in enumerate(base_runs)
    ]
    kwargs = {
        "forecast_run_id": "forecast-run-1",
        "graph": graph,
        "plan": plan,
        "evidence_sufficiency_assessment_id": "evidence-assessment-1",
        "evidence_sufficiency_assessment_hash": "a" * 64,
        "node_failures": [],
    }
    first = assess_material_node_coverage(node_runs=base_runs, **kwargs)
    second = assess_material_node_coverage(node_runs=changed_runs, **kwargs)

    assert second.assessment_input_hash == first.assessment_input_hash
    parameters = inspect.signature(assess_material_node_coverage).parameters
    assert "outcome" not in parameters
    assert "source_quality" not in parameters
    assert "evidence_confidence" not in parameters


def test_importance_weight_and_node_failure_identity_change_hash() -> None:
    graph = _graph()
    plan = _plan(["A", "B", "C", "D"])
    first = _coverage(graph, plan, ["A", "B", "C"])
    changed_weight = _coverage(
        _graph({"A": 0.31, "B": 0.24, "C": 0.20, "D": 0.15, "E": 0.10}),
        plan,
        ["A", "B", "C"],
    )
    changed_failure = _coverage(
        graph,
        plan,
        ["A", "B", "C"],
        failures=[
            MaterialNodeFailureIdentity(
                id="failure-D",
                node_id="D",
                stage="node_research",
                error_code="retrieval_failure",
                impact="excluded_reduced_confidence",
            )
        ],
    )

    assert changed_weight.assessment_input_hash != first.assessment_input_hash
    assert changed_failure.assessment_input_hash != first.assessment_input_hash
