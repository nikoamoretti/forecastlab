from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from forecastlab.errors import StructuredOutputError
from forecastlab.node_forecasting import NodeForecaster
from forecastlab.providers.base import ChatResult
from forecastlab.schemas import EvidenceClaim, ForecastContract, ForecastNode, ModelUsage
from forecastlab.structured_outputs import forecast_node_json_schema


class StubModel:
    name = "stub"
    model = "stub-node-v1"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, **kwargs: Any) -> ChatResult:
        self.calls.append(kwargs)
        return ChatResult(
            content=json.dumps(self.payload),
            parsed=self.payload,
            usage=ModelUsage(model=self.model, provider=self.name),
        )


def _contract() -> ForecastContract:
    return ForecastContract(
        id="contract-1",
        question_id="question-1",
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
        created_by="test",
        original_question="Will the outcome occur?",
        normalized_question="Will the defined outcome occur before 2027?",
        yes_condition="The defined outcome occurs before 2027.",
        no_condition="The defined outcome does not occur before 2027.",
        resolution_date=datetime(2027, 1, 1, tzinfo=UTC),
        authoritative_source="Official fixture source",
        resolution_method="Check the official fixture record.",
        status="approved",
    )


def _node() -> ForecastNode:
    return ForecastNode(
        id="node-1",
        graph_id="graph-1",
        question="What does the eligible evidence imply?",
        node_type="driver",
        importance_weight=0.8,
        required_output_type="probability",
    )


def _claim(claim_id: str = "support", stance: str = "supports") -> EvidenceClaim:
    published = datetime(2026, 1, 1, tzinfo=UTC)
    return EvidenceClaim(
        id=claim_id,
        evidence_item_id=f"evidence-{claim_id}",
        forecast_node_id="node-1",
        claim=f"Claim {claim_id}",
        excerpt=f"Excerpt {claim_id}",
        source_url=f"https://example.com/{claim_id}",
        source_title=f"Source {claim_id}",
        publisher="Example",
        publication_date=published,
        publication_date_source="test_fixture_metadata",
        publication_date_verified=True,
        retrieval_date=published,
        source_available_at=published,
        temporal_basis="publication_date",
        supports_or_refutes=stance,
        confidence=0.9,
        source_quality=0.8,
        primary_source=True,
        as_of_eligible=True,
        cutoff_verified=True,
    )


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "probability": 0.63,
        "reasoning": "The cited claim supports a moderately positive estimate.",
        "supporting_claim_ids": ["support"],
        "opposing_claim_ids": [],
        "uncertainty_notes": ["Only one eligible source is available."],
    }
    payload.update(overrides)
    return payload


def test_node_forecaster_generates_probability_from_contract_question_and_claims_only() -> None:
    model = StubModel(_valid_payload())
    result = NodeForecaster(model).forecast(contract=_contract(), node=_node(), claims=[_claim()])

    assert result.probability == 0.63
    assert result.node_id == "node-1"
    assert result.supporting_claim_ids == ["support"]
    assert result.opposing_claim_ids == []
    assert result.uncertainty_notes == ["Only one eligible source is available."]
    assert result.confidence == 0.36
    assert result.model_used == "stub:stub-node-v1"
    context = json.loads(model.calls[0]["user"])
    assert set(context) == {"forecast_contract", "node_question", "evidence_claims"}
    assert context["forecast_contract"]["id"] == "contract-1"
    assert context["node_question"] == _node().question
    assert context["evidence_claims"][0]["id"] == "support"
    assert "document" not in context
    assert model.calls[0]["json_schema"] == forecast_node_json_schema(
        supporting_claim_ids=["support"],
        opposing_claim_ids=[],
    )
    assert model.calls[0]["reasoning_effort"] == "minimal"
    assert model.calls[0]["verbosity"] == "low"


def test_node_forecast_schema_uses_only_openai_structured_output_compatible_keywords() -> None:
    schema = forecast_node_json_schema(
        supporting_claim_ids=["support"],
        opposing_claim_ids=["oppose"],
    )

    assert schema["properties"]["supporting_claim_ids"]["items"]["enum"] == ["support"]
    assert schema["properties"]["opposing_claim_ids"]["items"]["enum"] == ["oppose"]
    assert "uniqueItems" not in schema["properties"]["supporting_claim_ids"]
    assert "uniqueItems" not in schema["properties"]["opposing_claim_ids"]


def test_node_forecast_schema_keeps_items_for_empty_claim_stance() -> None:
    schema = forecast_node_json_schema(
        supporting_claim_ids=["support"],
        opposing_claim_ids=[],
    )

    opposing = schema["properties"]["opposing_claim_ids"]
    assert opposing["maxItems"] == 0
    assert opposing["items"] == {
        "type": "string",
        "enum": ["__no_eligible_claim_id__"],
    }


@pytest.mark.parametrize("probability", [-0.01, 1.01])
def test_node_forecaster_rejects_probability_outside_unit_interval(probability: float) -> None:
    model = StubModel(_valid_payload(probability=probability))

    with pytest.raises(StructuredOutputError, match="invalid_structured_output:forecast_node"):
        NodeForecaster(model).forecast(contract=_contract(), node=_node(), claims=[_claim()])


def test_node_forecaster_rejects_unknown_claim_reference() -> None:
    model = StubModel(_valid_payload(supporting_claim_ids=["not-supplied"]))

    with pytest.raises(StructuredOutputError, match="node_forecast_unknown_claim_id"):
        NodeForecaster(model).forecast(contract=_contract(), node=_node(), claims=[_claim()])


def test_node_forecaster_rejects_missing_evidence_before_model_call() -> None:
    model = StubModel(_valid_payload())

    with pytest.raises(StructuredOutputError, match="node_forecast_evidence_required"):
        NodeForecaster(model).forecast(contract=_contract(), node=_node(), claims=[])
    assert model.calls == []


def test_node_forecaster_rejects_missing_structured_probability() -> None:
    payload = _valid_payload()
    del payload["probability"]

    with pytest.raises(StructuredOutputError, match="invalid_structured_output:forecast_node"):
        NodeForecaster(StubModel(payload)).forecast(contract=_contract(), node=_node(), claims=[_claim()])


def test_node_forecaster_requires_at_least_one_supported_claim_reference() -> None:
    model = StubModel(_valid_payload(supporting_claim_ids=[], opposing_claim_ids=[]))

    with pytest.raises(StructuredOutputError, match="node_forecast_claim_reference_required"):
        NodeForecaster(model).forecast(contract=_contract(), node=_node(), claims=[_claim()])


def test_node_forecaster_rejects_wrong_stance_and_cross_node_claims() -> None:
    with pytest.raises(StructuredOutputError, match="node_forecast_opposing_stance_mismatch"):
        NodeForecaster(StubModel(_valid_payload(supporting_claim_ids=[], opposing_claim_ids=["support"]))).forecast(
            contract=_contract(),
            node=_node(),
            claims=[_claim()],
        )

    wrong_node = _claim().model_copy(update={"forecast_node_id": "node-2"})
    model = StubModel(_valid_payload())
    with pytest.raises(StructuredOutputError, match="node_forecast_claim_node_mismatch"):
        NodeForecaster(model).forecast(contract=_contract(), node=_node(), claims=[wrong_node])
    assert model.calls == []
