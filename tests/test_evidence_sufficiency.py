from __future__ import annotations

from datetime import UTC, datetime

import pytest

from forecastlab.evidence_sufficiency import (
    EvidenceSufficiencyItem,
    assess_evidence_sufficiency,
)
from forecastlab.research_planning import ResearchPlan
from forecastlab.schemas import EvidenceClaim, ForecastGraph, ForecastNode, ForecastNodeRun

NOW = datetime(2026, 8, 26, tzinfo=UTC)


def _graph(weights: list[float] | None = None) -> ForecastGraph:
    values = weights or [0.25, 0.20, 0.15, 0.20, 0.20]
    types = ["base_rate", "driver", "adversarial", "resolver", "trend"]
    nodes = [
        ForecastNode(
            id=f"n{index + 1}",
            graph_id="graph-1",
            question=f"Node {index + 1}?",
            node_type=node_type,  # type: ignore[arg-type]
            importance_weight=weight,
            required_output_type="probability",
        )
        for index, (node_type, weight) in enumerate(zip(types, values, strict=True))
    ]
    return ForecastGraph(
        id="graph-1",
        contract_id="contract-1",
        version=1,
        status="approved",
        created_at=NOW,
        generation_model="stub",
        root_question="Will the event occur?",
        nodes=nodes,
    )


def _plan(selected: list[str] | None = None, *, critical: list[str] | None = None) -> ResearchPlan:
    selected_ids = selected or ["n1", "n2", "n3"]
    all_ids = ["n1", "n2", "n3", "n4", "n5"]
    return ResearchPlan(
        id="plan-1",
        forecast_run_id="run-1",
        selected_nodes=selected_ids,
        skipped_nodes=[node_id for node_id in all_ids if node_id not in selected_ids],
        priority_scores={node_id: float(6 - index) for index, node_id in enumerate(all_ids)},
        budget_allocation={"critical_node_ids": critical or ["n1"]},
        created_at=NOW,
    )


def _claim(
    claim_id: str,
    node_id: str,
    host: str,
    *,
    source_class: str = "secondary",
    extraction_method: str = "structured_full_document",
    item_id: str | None = None,
    eligible: bool = True,
) -> tuple[EvidenceClaim, EvidenceSufficiencyItem]:
    evidence_item_id = item_id or f"item-{claim_id}"
    url = f"https://{host}/evidence/{evidence_item_id}"
    claim = EvidenceClaim(
        id=claim_id,
        evidence_item_id=evidence_item_id,
        forecast_node_id=node_id,
        claim=f"Claim {claim_id}",
        excerpt=f"Exact excerpt {claim_id}",
        source_url=url,
        source_title=f"Source {claim_id}",
        publisher=host,
        publication_date=NOW,
        publication_date_source="fixture",
        publication_date_verified=True,
        retrieval_date=NOW,
        source_available_at=NOW,
        temporal_basis="publication_date",
        supports_or_refutes="supports",
        confidence=0.1,
        source_quality=0.0,
        primary_source=False,
        as_of_eligible=eligible,
        cutoff_verified=eligible,
        source_class=source_class,  # type: ignore[arg-type]
        extraction_method=extraction_method,  # type: ignore[arg-type]
        source_host=host,
    )
    item = EvidenceSufficiencyItem(
        id=evidence_item_id,
        source_url=url,
        rejected=False,
        as_of_eligible=eligible,
    )
    return claim, item


def _run(node_id: str, claim_ids: list[str], *, probability: float = 0.55) -> ForecastNodeRun:
    return ForecastNodeRun(
        id=f"run-{node_id}",
        run_id="run-1",
        node_id=node_id,
        probability=probability,
        confidence=0.2,
        reasoning="Model reasoning is not policy input.",
        supporting_claim_ids=claim_ids,
        opposing_claim_ids=[],
        uncertainty_notes=[],
        model_used="stub",
        uncertainty=0.8,
        created_at=NOW,
    )


def _passing_inputs() -> tuple[list[ForecastNodeRun], list[EvidenceClaim], list[EvidenceSufficiencyItem]]:
    c1, i1 = _claim("c1", "n1", "bls.gov", source_class="primary")
    c2, i2 = _claim("c2", "n2", "reuters.com")
    c3, i3 = _claim("c3", "n3", "sec.gov", source_class="primary")
    return (
        [_run("n1", ["c1"]), _run("n2", ["c2"]), _run("n3", ["c3"])],
        [c1, c2, c3],
        [i1, i2, i3],
    )


def _assess(
    runs: list[ForecastNodeRun],
    claims: list[EvidenceClaim],
    items: list[EvidenceSufficiencyItem],
    *,
    graph: ForecastGraph | None = None,
    plan: ResearchPlan | None = None,
):
    return assess_evidence_sufficiency(
        forecast_run_id="run-1",
        graph=graph or _graph(),
        plan=plan or _plan(),
        node_runs=runs,
        claims=claims,
        items=items,
    )


def test_private_v1_evidence_gate_passes_deterministically_without_using_probabilities() -> None:
    runs, claims, items = _passing_inputs()
    first = _assess(runs, claims, items)
    changed_probabilities = [
        run.model_copy(update={"probability": 0.01 + index * 0.48})
        for index, run in enumerate(runs)
    ]
    second = _assess(changed_probabilities, claims, items)

    assert first.status == "passed"
    assert first.reasons == []
    assert first.selected_node_coverage == 1.0
    assert first.graph_weight_coverage == pytest.approx(0.60)
    assert first.distinct_hosts == ["bls.gov", "reuters.com", "sec.gov"]
    assert [item.grade for item in first.per_node] == [
        "strong_primary",
        "adequate_secondary",
        "strong_primary",
    ]
    assert second.assessment_input_hash == first.assessment_input_hash
    assert second.id == first.id


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("fallback_only", "included_node_evidence_insufficient:n2"),
        ("no_primary", "structured_primary_evidence_required"),
        ("single_host", "two_distinct_source_hosts_required"),
        ("critical_one_secondary", "selected_critical_node_requirement_not_met:n1"),
        ("unknown_legacy", "included_node_evidence_insufficient:n2"),
        ("wrong_node", "included_node_evidence_insufficient:n2"),
        ("temporally_ineligible", "included_node_evidence_insufficient:n2"),
    ],
)
def test_private_v1_evidence_gate_fail_closed_cases(
    mutation: str,
    expected_reason: str,
) -> None:
    runs, claims, items = _passing_inputs()
    if mutation == "fallback_only":
        claims[1] = claims[1].model_copy(update={"extraction_method": "document_fallback"})
    elif mutation == "no_primary":
        claims = [claim.model_copy(update={"source_class": "secondary"}) for claim in claims]
    elif mutation == "single_host":
        claims = [
            claim.model_copy(update={"source_host": "one.example", "source_url": f"https://one.example/{claim.id}"})
            for claim in claims
        ]
        items = [
            item.model_copy(update={"source_url": f"https://one.example/{claim.id}"})
            for item, claim in zip(items, claims, strict=True)
        ]
    elif mutation == "critical_one_secondary":
        claims[0] = claims[0].model_copy(update={"source_class": "secondary"})
    elif mutation == "unknown_legacy":
        claims[1] = claims[1].model_copy(update={"extraction_method": "unknown_legacy"})
    elif mutation == "wrong_node":
        claims[1] = claims[1].model_copy(update={"forecast_node_id": "n3"})
    elif mutation == "temporally_ineligible":
        claims[1] = claims[1].model_copy(update={"as_of_eligible": False, "cutoff_verified": False})
        items[1] = items[1].model_copy(update={"as_of_eligible": False})

    assessment = _assess(runs, claims, items)
    assert assessment.status == "failed"
    assert expected_reason in assessment.reasons


def test_critical_node_two_structured_secondary_hosts_satisfies_node_rule() -> None:
    runs, claims, items = _passing_inputs()
    c4, i4 = _claim("c4", "n1", "census.gov")
    claims[0] = claims[0].model_copy(update={"source_class": "secondary"})
    runs[0] = _run("n1", ["c1", "c4"])
    assessment = _assess(runs, [*claims, c4], [*items, i4])

    node = next(item for item in assessment.per_node if item.node_id == "n1")
    assert node.passed is True
    assert node.grade == "corroborated_secondary"


def test_selected_node_coverage_uses_exact_two_thirds_integer_comparison() -> None:
    runs, claims, items = _passing_inputs()
    assessment = _assess(
        runs,
        claims,
        items,
        plan=_plan(["n1", "n2", "n3", "n4", "n5"]),
    )
    assert assessment.selected_coverage_numerator == 3
    assert assessment.selected_coverage_denominator == 5
    assert "selected_node_coverage_below_two_thirds" in assessment.reasons


def test_graph_weight_coverage_below_half_fails() -> None:
    runs, claims, items = _passing_inputs()
    assessment = _assess(
        runs,
        claims,
        items,
        graph=_graph([0.10, 0.10, 0.10, 0.35, 0.35]),
    )
    assert assessment.graph_weight_coverage == pytest.approx(0.30)
    assert "graph_weight_coverage_below_half" in assessment.reasons


def test_duplicate_evidence_item_and_url_do_not_inflate_host_diversity() -> None:
    primary, item = _claim("c1", "n1", "one.example", source_class="primary", item_id="shared")
    c2 = primary.model_copy(
        update={"id": "c2", "forecast_node_id": "n2", "source_class": "secondary"}
    )
    c3 = primary.model_copy(
        update={"id": "c3", "forecast_node_id": "n3", "source_class": "secondary"}
    )
    assessment = _assess(
        [_run("n1", ["c1"]), _run("n2", ["c2"]), _run("n3", ["c3"])],
        [primary, c2, c3],
        [item],
    )
    assert assessment.cited_item_count == 1
    assert assessment.cited_source_count == 1
    assert assessment.distinct_host_count == 1
    assert "two_distinct_source_hosts_required" in assessment.reasons


def test_immutable_assessment_is_idempotent_and_rejects_conflicting_input(client) -> None:
    from sqlalchemy import func, select
    from tests.test_graph_forecaster_integration import _approved_forecast, _executor

    from forecastlab_api import main as main_mod
    from forecastlab_api.evidence_sufficiency import (
        EvidenceSufficiencyStoreError,
        store_evidence_sufficiency_assessment,
    )
    from forecastlab_api.models import EvidenceSufficiencyAssessmentRow

    draft = _approved_forecast(client)
    runs, claims, items = _passing_inputs()
    with main_mod.SessionLocal() as session:
        executor_obj, run_id = _executor(session, draft["question_id"])
        assert executor_obj.run.id == run_id
        assessment = _assess(runs, claims, items).model_copy(
            update={"forecast_run_id": run_id}
        )
        first = store_evidence_sufficiency_assessment(session, assessment)
        second = store_evidence_sufficiency_assessment(session, assessment)
        assert first.id == second.id
        with pytest.raises(
            EvidenceSufficiencyStoreError,
            match="conflicting_immutable_evidence_sufficiency_assessment",
        ):
            store_evidence_sufficiency_assessment(
                session,
                assessment.model_copy(update={"assessment_input_hash": "f" * 64}),
            )
        assert session.scalar(
            select(func.count()).select_from(EvidenceSufficiencyAssessmentRow).where(
                EvidenceSufficiencyAssessmentRow.forecast_run_id == run_id
            )
        ) == 1
