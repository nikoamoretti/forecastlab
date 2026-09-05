from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

import pytest

from forecastlab.budget import Budget
from forecastlab.errors import BudgetExceeded
from forecastlab.profiles import load_profile
from forecastlab.prompts import PromptBundle, PromptRecord
from forecastlab.providers.base import ChatResult, StructuredOutputDiagnostics
from forecastlab.scenario_synthesis import (
    SCENARIO_SYNTHESIS_CALL_KIND,
    SCENARIO_SYNTHESIS_MAX_OUTPUT_TOKENS,
    SCENARIO_SYNTHESIS_POLICY_VERSION,
    SCENARIO_SYNTHESIS_RESERVED_INPUT_TOKENS,
    ScenarioSynthesisOutput,
    ScenarioSynthesizer,
    prepare_scenario_synthesis,
    validate_scenario_grounding,
)
from forecastlab.schemas import (
    EvidenceClaim,
    ForecastContract,
    ForecastGraph,
    ForecastNode,
    ForecastNodeRun,
    ModelUsage,
)
from forecastlab.structured_outputs import (
    scenario_synthesis_json_schema,
    validate_structured_output,
)


def _fixture() -> tuple[
    ForecastContract,
    ForecastGraph,
    list[ForecastNodeRun],
    list[EvidenceClaim],
]:
    now = datetime(2026, 8, 26, tzinfo=UTC)
    contract = ForecastContract(
        id="contract",
        question_id="question",
        created_at=now,
        created_by="test",
        original_question="Will the event occur?",
        normalized_question="Will the exact event occur by 2027?",
        yes_condition="The official resolver records yes.",
        no_condition="The official resolver records no.",
        resolution_date=datetime(2027, 1, 1, tzinfo=UTC),
        authoritative_source="https://resolver.example/",
        resolution_method="Read the official record.",
        resolver_risk_notes="The resolver may revise metadata.",
        status="approved",
    )
    graph_id = "graph"
    graph = ForecastGraph(
        id=graph_id,
        contract_id=contract.id,
        version=1,
        status="approved",
        created_at=now,
        generation_model="stub:graph",
        root_question=contract.normalized_question,
        nodes=[
            ForecastNode(
                id="n0",
                graph_id=graph_id,
                question="What is the base rate?",
                node_type="base_rate",
                importance_weight=0.30,
                required_output_type="probability",
            ),
            ForecastNode(
                id="n1",
                graph_id=graph_id,
                parent_node_id="n0",
                question="What primary driver matters?",
                node_type="driver",
                importance_weight=0.25,
                required_output_type="directional_update",
            ),
            ForecastNode(
                id="n2",
                graph_id=graph_id,
                question="What could overturn the view?",
                node_type="adversarial",
                importance_weight=0.20,
                dependencies=["n1"],
                required_output_type="directional_update",
            ),
            ForecastNode(
                id="n3",
                graph_id=graph_id,
                question="What trend is visible?",
                node_type="trend",
                importance_weight=0.15,
                required_output_type="directional_update",
            ),
            ForecastNode(
                id="n4",
                graph_id=graph_id,
                question="How will the resolver score it?",
                node_type="resolver",
                importance_weight=0.10,
                required_output_type="structured_categorical",
            ),
        ],
    )
    runs: list[ForecastNodeRun] = []
    claims: list[EvidenceClaim] = []
    for index, node_id in enumerate(("n0", "n1", "n2")):
        claim_id = f"c{index}"
        runs.append(
            ForecastNodeRun(
                id=f"run-{node_id}",
                run_id="forecast-run",
                node_id=node_id,
                probability=0.4 + index * 0.1,
                confidence=0.5,
                reasoning=f"Reasoning for {node_id}.",
                supporting_claim_ids=[claim_id],
                opposing_claim_ids=[],
                uncertainty_notes=[f"Uncertainty for {node_id}."],
                model_used="stub:model",
                uncertainty=0.5,
                created_at=now,
            )
        )
        claims.append(
            EvidenceClaim(
                id=claim_id,
                evidence_item_id=f"item-{index}",
                forecast_node_id=node_id,
                claim=f"Grounded claim for {node_id}.",
                excerpt=f"Exact excerpt for {node_id}.",
                source_url=f"https://source{index}.example/item",
                source_title=f"Source {index}",
                publisher=f"Publisher {index}",
                publication_date=now,
                publication_date_verified=True,
                retrieval_date=now,
                source_available_at=now,
                temporal_basis="publication_date",
                supports_or_refutes="supports",
                confidence=0.8,
                source_quality=0.8,
                primary_source=index == 0,
                as_of_eligible=True,
                cutoff_verified=True,
                source_class="primary" if index == 0 else "secondary",
                extraction_method="mock_structured",
                source_host=f"source{index}.example",
            )
        )
    return contract, graph, runs, claims


def _valid_payload() -> dict[str, Any]:
    return {
        "scenarios": [
            {
                "local_id": "base",
                "kind": "base_case",
                "title": "Base pathway",
                "summary": "The base rate, driver, and counterevidence interact.",
                "node_ids": ["n0", "n1", "n2"],
                "claim_ids": ["c0"],
                "mechanisms": ["The driver updates the historical reference class."],
                "triggers": ["The primary indicator changes."],
                "invalidators": ["The driver does not materialize."],
                "unresolved_uncertainties": ["Timing remains uncertain."],
            },
            {
                "local_id": "yes",
                "kind": "yes_case",
                "title": "Yes pathway",
                "summary": "The base rate and primary driver support the yes condition.",
                "node_ids": ["n0", "n1"],
                "claim_ids": ["c1"],
                "mechanisms": ["The driver reinforces the reference class."],
                "triggers": ["The leading measure rises."],
                "invalidators": ["The leading measure reverses."],
                "unresolved_uncertainties": ["The release timing may vary."],
            },
            {
                "local_id": "no",
                "kind": "no_case",
                "title": "No pathway",
                "summary": "The driver and adversarial node explain the no condition.",
                "node_ids": ["n1", "n2"],
                "claim_ids": ["c2"],
                "mechanisms": ["Counterevidence interrupts the primary mechanism."],
                "triggers": ["The counter-indicator strengthens."],
                "invalidators": ["The counter-indicator disappears."],
                "unresolved_uncertainties": ["Measurement noise remains."],
            },
        ]
    }


class StubScenarioModel:
    name = "stub"
    model = "stub-scenario-v1"

    def __init__(
        self,
        *,
        payload: dict[str, Any] | None = None,
        content: str | None = None,
        diagnostics: StructuredOutputDiagnostics | None = None,
    ) -> None:
        self.payload = payload
        self.content = content
        self.diagnostics = diagnostics
        self.calls: list[dict[str, Any]] = []
        self.budget_snapshot: dict[str, Any] | None = None

    def complete_json(self, **kwargs: Any) -> ChatResult:
        self.calls.append(kwargs)
        content = self.content
        if content is None:
            import json

            content = json.dumps(self.payload) if self.payload is not None else ""
        return ChatResult(
            content=content,
            parsed=self.payload,
            usage=ModelUsage(
                prompt_tokens=600,
                completion_tokens=300,
                cost_usd=0.0,
                latency_ms=10,
                model=self.model,
                provider=self.name,
            ),
            diagnostics=self.diagnostics,
        )


def _prepared():
    contract, graph, runs, claims = _fixture()
    prepared = prepare_scenario_synthesis(
        contract=contract,
        graph=graph,
        node_runs=runs,
        claims=claims,
        evidence_sufficiency_assessment_id="evidence-assessment",
        evidence_sufficiency_assessment_hash="a" * 64,
        material_node_coverage_assessment_id="material-assessment",
        material_node_coverage_assessment_hash="b" * 64,
    )
    return contract, graph, runs, claims, prepared


def _budget() -> Budget:
    profile = load_profile("graph_forecaster_v1")
    budget = Budget(
        profile,
        provider="mock",
        model="mock-forecast-v1",
        search_provider="mock",
    )
    budget.freeze_model_call_envelope(
        planner_version="scenario-test-v1",
        planned_calls_by_kind={SCENARIO_SYNTHESIS_CALL_KIND: 1},
    )
    return budget


def _synthesize(model: StubScenarioModel):
    _contract, graph, runs, claims, prepared = _prepared()
    budget = _budget()
    artifact = ScenarioSynthesizer(model, budget).synthesize(
        forecast_run_id="forecast-run",
        prepared=prepared,
        graph=graph,
        node_runs=runs,
        claims=claims,
        evidence_sufficiency_assessment_id="evidence-assessment",
        evidence_sufficiency_assessment_hash="a" * 64,
        material_node_coverage_assessment_id="material-assessment",
        material_node_coverage_assessment_hash="b" * 64,
    )
    model.budget_snapshot = budget.snapshot()
    return artifact


def test_valid_grounded_synthesis_uses_one_bounded_call() -> None:
    model = StubScenarioModel(payload=_valid_payload())

    artifact = _synthesize(model)

    assert artifact.status == "passed"
    assert artifact.policy_version == SCENARIO_SYNTHESIS_POLICY_VERSION
    assert len(artifact.scenarios) == 3
    assert artifact.coverage_audit.uncovered_node_ids == []
    assert artifact.coverage_audit.uncovered_relationships == []
    assert artifact.output_hash is not None
    assert len(model.calls) == 1
    assert model.budget_snapshot is not None
    assert model.budget_snapshot["model_calls"] == 1
    assert model.budget_snapshot["model_call_envelope"][
        "used_calls_by_kind"
    ] == {SCENARIO_SYNTHESIS_CALL_KIND: 1}
    call = model.calls[0]
    assert call["schema_name"] == "scenario_synthesis"
    assert call["estimated_input_tokens"] == SCENARIO_SYNTHESIS_RESERVED_INPUT_TOKENS
    assert call["max_output_tokens"] == SCENARIO_SYNTHESIS_MAX_OUTPUT_TOKENS
    assert call["max_completion_tokens"] == SCENARIO_SYNTHESIS_MAX_OUTPUT_TOKENS
    assert call["reasoning_effort"] == "minimal"
    assert call["verbosity"] == "low"
    assert call["json_schema"] == scenario_synthesis_json_schema()
    assert all("probability" not in scenario.model_dump() for scenario in artifact.scenarios)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda payload: payload["scenarios"][2].update(
                {"kind": "yes_case"}
            ),
            "scenario_kinds_must_be_exactly_base_yes_no",
        ),
        (
            lambda payload: payload["scenarios"][0]["node_ids"].append("unknown"),
            "scenario_unknown_or_excluded_node:unknown",
        ),
        (
            lambda payload: payload["scenarios"][1].update(
                {"title": "Base pathway"}
            ),
            "scenario_titles_must_be_unique",
        ),
        (
            lambda payload: payload["scenarios"][1].update(
                {"claim_ids": ["c2"]}
            ),
            "scenario_wrong_node_claim:c2",
        ),
        (
            lambda payload: (
                payload["scenarios"][0].update(
                    {"node_ids": ["n0", "n1"]}
                ),
                payload["scenarios"][2].update(
                    {"node_ids": ["n0", "n2"]}
                ),
            ),
            "scenario_relationship_coverage_incomplete",
        ),
        (
            lambda payload: payload["scenarios"][2].update(
                {"node_ids": ["n0", "n1"], "claim_ids": ["c1"]}
            ),
            "yes_no_scenario_node_sets_must_differ",
        ),
    ],
)
def test_grounding_failures_are_deterministic(mutate, expected: str) -> None:
    payload = copy.deepcopy(_valid_payload())
    mutate(payload)
    output = ScenarioSynthesisOutput.model_validate(payload)
    _contract, graph, runs, claims = _fixture()

    audit = validate_scenario_grounding(
        output=output,
        graph=graph,
        node_runs=runs,
        claims=claims,
    )

    assert expected in audit.errors


def test_strict_schema_rejects_probability_or_weight_fields() -> None:
    for field in ("probability", "weight", "confidence", "likelihood"):
        payload = copy.deepcopy(_valid_payload())
        payload["scenarios"][0][field] = 0.5
        validated, errors = validate_structured_output(
            "scenario_synthesis",
            payload,
        )
        assert validated is None
        assert errors


@pytest.mark.parametrize(
    ("model", "reason"),
    [
        (
            StubScenarioModel(
                payload=None,
                content="{",
                diagnostics=StructuredOutputDiagnostics(
                    schema_name="scenario_synthesis",
                    finish_reason="length",
                    completion_tokens=SCENARIO_SYNTHESIS_MAX_OUTPUT_TOKENS,
                ),
            ),
            "structured_output_truncated",
        ),
        (
            StubScenarioModel(
                payload=None,
                content="",
                diagnostics=StructuredOutputDiagnostics(
                    schema_name="scenario_synthesis",
                    refusal_present=True,
                    refusal_category="provider_refusal",
                ),
            ),
            "structured_output_refused",
        ),
        (StubScenarioModel(payload=None, content=""), "structured_output_empty"),
        (StubScenarioModel(payload=None, content="not-json"), "structured_output_invalid_json"),
        (
            StubScenarioModel(payload={"scenarios": []}),
            "structured_output_schema_invalid",
        ),
    ],
)
def test_structured_output_failures_are_one_call_without_repair(
    model: StubScenarioModel,
    reason: str,
) -> None:
    artifact = _synthesize(model)

    assert artifact.status == "failed"
    assert artifact.failure_reasons == [reason]
    assert artifact.scenarios == []
    assert len(model.calls) == 1
    assert model.budget_snapshot is not None
    assert model.budget_snapshot["model_calls"] == 1
    assert model.budget_snapshot["model_call_envelope"][
        "used_calls_by_kind"
    ] == {SCENARIO_SYNTHESIS_CALL_KIND: 1}


def test_domain_invalid_output_fails_without_second_call() -> None:
    payload = copy.deepcopy(_valid_payload())
    payload["scenarios"][1]["claim_ids"] = ["c2"]
    model = StubScenarioModel(payload=payload)

    artifact = _synthesize(model)

    assert artifact.status == "failed"
    assert artifact.failure_reasons == [
        "scenario_synthesis_domain_validation_failed"
    ]
    assert "scenario_wrong_node_claim:c2" in artifact.coverage_audit.errors
    assert len(model.calls) == 1


def test_bounded_input_hash_includes_forecasts_and_cited_claims_only() -> None:
    contract, graph, runs, claims = _fixture()

    def prepare() -> str:
        return prepare_scenario_synthesis(
            contract=contract,
            graph=graph,
            node_runs=runs,
            claims=claims,
            evidence_sufficiency_assessment_id="evidence-assessment",
            evidence_sufficiency_assessment_hash="a" * 64,
            material_node_coverage_assessment_id="material-assessment",
            material_node_coverage_assessment_hash="b" * 64,
        ).input_hash

    original = prepare()
    uncited = claims[0].model_copy(
        update={
            "id": "uncited",
            "evidence_item_id": "uncited-item",
            "claim": "Uncited material that must not enter the packet.",
        }
    )
    claims.append(uncited)
    assert prepare() == original
    claims[0] = claims[0].model_copy(update={"claim": "Changed cited claim."})
    assert prepare() != original
    claims[0] = claims[0].model_copy(
        update={"claim": "Grounded claim for n0."}
    )
    runs[0] = runs[0].model_copy(update={"reasoning": "Changed reasoning."})
    assert prepare() != original


def test_packet_excludes_outcomes_scores_and_final_aggregation() -> None:
    _contract, _graph_value, _runs, _claims, prepared = _prepared()

    assert "resolved_outcome" not in prepared.packet_json
    assert "benchmark" not in prepared.packet_json
    assert "external_score" not in prepared.packet_json
    assert "final_probability" not in prepared.packet_json
    assert "aggregation" not in prepared.packet_json


def test_input_character_limit_fails_before_a_model_call() -> None:
    contract, graph, runs, claims = _fixture()
    runs[0] = runs[0].model_copy(update={"reasoning": "x" * 31_000})

    with pytest.raises(
        ValueError,
        match="scenario_synthesis_input_character_limit_exceeded",
    ):
        prepare_scenario_synthesis(
            contract=contract,
            graph=graph,
            node_runs=runs,
            claims=claims,
            evidence_sufficiency_assessment_id="evidence-assessment",
            evidence_sufficiency_assessment_hash="a" * 64,
            material_node_coverage_assessment_id="material-assessment",
            material_node_coverage_assessment_hash="b" * 64,
        )


def test_input_token_reservation_fails_before_a_model_call() -> None:
    _contract, graph, runs, claims, prepared = _prepared()
    model = StubScenarioModel(payload=_valid_payload())
    budget = _budget()
    prompt_bundle = PromptBundle(
        prompts={
            "scenario_synthesis": PromptRecord(
                name="scenario_synthesis",
                text="x" * 40_000,
                version="v1",
                sha256="a" * 64,
            )
        }
    )

    artifact = ScenarioSynthesizer(
        model,
        budget,
        prompt_bundle=prompt_bundle,
    ).synthesize(
        forecast_run_id="forecast-run",
        prepared=prepared,
        graph=graph,
        node_runs=runs,
        claims=claims,
        evidence_sufficiency_assessment_id="evidence-assessment",
        evidence_sufficiency_assessment_hash="a" * 64,
        material_node_coverage_assessment_id="material-assessment",
        material_node_coverage_assessment_hash="b" * 64,
    )

    assert artifact.status == "failed"
    assert artifact.failure_reasons == [
        "scenario_synthesis_input_token_reservation_exceeded"
    ]
    assert model.calls == []
    assert budget.state.model_calls == 0
    assert budget.reservations == []


def test_budget_reservation_is_frozen_but_not_fake_usage() -> None:
    profile = load_profile("graph_forecaster_v1")
    budget = Budget(profile, provider="mock", search_provider="mock")

    envelope = budget.freeze_model_call_envelope(
        planner_version="scenario-reservation-test-v1",
        planned_calls_by_kind={
            "research_plan": 1,
            "node_forecast": 1,
            SCENARIO_SYNTHESIS_CALL_KIND: 1,
        },
    )

    assert envelope.as_dict()["reserved_scenario_synthesis_calls"] == 1
    assert budget.state.model_calls == 0
    assert budget.reservations == []


def test_research_cannot_consume_the_reserved_scenario_call() -> None:
    profile = load_profile("graph_forecaster_v1").model_copy(
        update={"max_model_calls": 2}
    )
    budget = Budget(profile, provider="mock", search_provider="mock")
    budget.freeze_model_call_envelope(
        planner_version="scenario-reservation-test-v1",
        planned_calls_by_kind={
            "research_plan": 1,
            SCENARIO_SYNTHESIS_CALL_KIND: 1,
        },
    )
    research = budget.reserve_model_call(
        "node_research_plan",
        estimated_input_tokens=1,
        max_output_tokens=1,
        call_kind="research_plan",
    )
    budget.reconcile_model_call(
        research,
        ModelUsage(
            prompt_tokens=1,
            completion_tokens=1,
            provider="mock",
            model="mock-forecast-v1",
        ),
    )

    with pytest.raises(BudgetExceeded, match="unplanned_research_plan_call"):
        budget.reserve_model_call(
            "node_research_plan",
            estimated_input_tokens=1,
            max_output_tokens=1,
            call_kind="research_plan",
        )

    scenario = budget.reserve_model_call(
        "scenario_synthesis",
        estimated_input_tokens=SCENARIO_SYNTHESIS_RESERVED_INPUT_TOKENS,
        max_output_tokens=SCENARIO_SYNTHESIS_MAX_OUTPUT_TOKENS,
        call_kind=SCENARIO_SYNTHESIS_CALL_KIND,
    )
    budget.reconcile_model_call(
        scenario,
        ModelUsage(
            prompt_tokens=10,
            completion_tokens=10,
            provider="mock",
            model="mock-forecast-v1",
        ),
    )
    assert budget.snapshot()["model_call_envelope"]["used_calls_by_kind"] == {
        "research_plan": 1,
        SCENARIO_SYNTHESIS_CALL_KIND: 1,
    }
