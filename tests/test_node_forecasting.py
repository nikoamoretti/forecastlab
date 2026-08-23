from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from forecastlab.errors import StructuredOutputError
from forecastlab.node_forecasting import calculate_node_forecast
from forecastlab.schemas import EvidenceClaim, ForecastNode, ForecastNodeOutput


def _node() -> ForecastNode:
    return ForecastNode(
        id="node-1",
        graph_id="graph-1",
        question="What does the evidence imply?",
        node_type="driver",
        importance_weight=0.8,
        required_output_type="probability",
    )


def _claim(
    claim_id: str,
    stance: str,
    *,
    confidence: float,
    source_quality: float,
    primary_source: bool,
) -> EvidenceClaim:
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
        retrieval_date=published,
        supports_or_refutes=stance,
        confidence=confidence,
        source_quality=source_quality,
        primary_source=primary_source,
        as_of_eligible=True,
        cutoff_verified=True,
    )


def test_node_probability_is_deterministic_and_not_authored_by_model() -> None:
    claims = [
        _claim("support", "supports", confidence=0.9, source_quality=0.8, primary_source=True),
        _claim("oppose", "refutes", confidence=0.4, source_quality=0.5, primary_source=False),
    ]
    first = calculate_node_forecast(
        node=_node(),
        claims=claims,
        output=ForecastNodeOutput(
            reasoning="First wording.",
            supporting_claim_ids=["support"],
            opposing_claim_ids=["oppose"],
            uncertainty=0.2,
        ),
    )
    second = calculate_node_forecast(
        node=_node(),
        claims=claims,
        output=ForecastNodeOutput(
            reasoning="Different wording does not change arithmetic.",
            supporting_claim_ids=["support"],
            opposing_claim_ids=["oppose"],
            uncertainty=0.2,
        ),
    )

    assert first.probability == second.probability
    assert first.confidence == second.confidence
    assert first.probability > 0.5
    assert first.supporting_claim_ids == ["support"]
    assert first.opposing_claim_ids == ["oppose"]


def test_node_probability_is_neutral_without_selected_claims() -> None:
    result = calculate_node_forecast(
        node=_node(),
        claims=[],
        output=ForecastNodeOutput(reasoning="No eligible evidence.", uncertainty=1.0),
    )

    assert result.probability == 0.5
    assert result.confidence == 0.0


def test_node_probability_is_reproducible_across_claim_order() -> None:
    claims = [
        _claim("support-a", "supports", confidence=0.9, source_quality=0.8, primary_source=True),
        _claim("support-b", "supports", confidence=0.6, source_quality=0.7, primary_source=False),
    ]
    first = calculate_node_forecast(
        node=_node(),
        claims=claims,
        output=ForecastNodeOutput(
            reasoning="Ordered A then B.",
            supporting_claim_ids=["support-a", "support-b"],
            uncertainty=0.2,
        ),
    )
    second = calculate_node_forecast(
        node=_node(),
        claims=list(reversed(claims)),
        output=ForecastNodeOutput(
            reasoning="Ordered B then A.",
            supporting_claim_ids=["support-b", "support-a"],
            uncertainty=0.2,
        ),
    )

    assert first.probability == second.probability
    assert first.confidence == second.confidence


def test_model_output_schema_rejects_probability() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ForecastNodeOutput.model_validate(
            {
                "probability": 0.99,
                "reasoning": "The model must not set the probability.",
                "supporting_claim_ids": [],
                "opposing_claim_ids": [],
                "uncertainty": 0.5,
            }
        )


def test_node_forecast_rejects_claim_with_wrong_stance() -> None:
    support = _claim("support", "supports", confidence=0.9, source_quality=0.8, primary_source=True)
    with pytest.raises(StructuredOutputError, match="node_forecast_opposing_stance_mismatch"):
        calculate_node_forecast(
            node=_node(),
            claims=[support],
            output=ForecastNodeOutput(
                reasoning="Incorrectly labels the claim.",
                opposing_claim_ids=[support.id],
                uncertainty=0.5,
            ),
        )


def test_node_forecast_rejects_claim_linked_to_different_node() -> None:
    support = _claim("support", "supports", confidence=0.9, source_quality=0.8, primary_source=True)
    support = support.model_copy(update={"forecast_node_id": "other-node"})
    with pytest.raises(StructuredOutputError, match="node_forecast_claim_node_mismatch"):
        calculate_node_forecast(
            node=_node(),
            claims=[support],
            output=ForecastNodeOutput(
                reasoning="The claim has the wrong node provenance.",
                supporting_claim_ids=[support.id],
                uncertainty=0.5,
            ),
        )
