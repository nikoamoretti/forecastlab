from __future__ import annotations

import math

from forecastlab.aggregation import clip_track_probability, inv_logit
from forecastlab.errors import StructuredOutputError
from forecastlab.schemas import EvidenceClaim, ForecastNode, ForecastNodeOutput, NodeForecast

MAX_NODE_LOGIT_SHIFT = 3.0
FULL_EVIDENCE_STRENGTH = 2.0
SECONDARY_SOURCE_FACTOR = 0.85


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def validate_node_output(output: ForecastNodeOutput, claims: list[EvidenceClaim]) -> ForecastNodeOutput:
    """Require every model-selected claim to exist and have the declared stance."""

    by_id = {claim.id: claim for claim in claims}
    supporting = _deduplicate(output.supporting_claim_ids)
    opposing = _deduplicate(output.opposing_claim_ids)
    if set(supporting) & set(opposing):
        raise StructuredOutputError("node_forecast_claim_stance_conflict")
    if any(claim_id not in by_id for claim_id in [*supporting, *opposing]):
        raise StructuredOutputError("node_forecast_unknown_claim_id")
    if any(by_id[claim_id].supports_or_refutes != "supports" for claim_id in supporting):
        raise StructuredOutputError("node_forecast_supporting_stance_mismatch")
    if any(by_id[claim_id].supports_or_refutes != "refutes" for claim_id in opposing):
        raise StructuredOutputError("node_forecast_opposing_stance_mismatch")
    return output.model_copy(
        update={
            "supporting_claim_ids": supporting,
            "opposing_claim_ids": opposing,
        }
    )


def claim_strength(claim: EvidenceClaim) -> float:
    """Convert provenance assessments into a bounded deterministic evidence strength."""

    source_factor = 1.0 if claim.primary_source else SECONDARY_SOURCE_FACTOR
    return float(claim.confidence) * float(claim.source_quality) * source_factor


def calculate_node_forecast(
    *,
    node: ForecastNode,
    claims: list[EvidenceClaim],
    output: ForecastNodeOutput,
) -> NodeForecast:
    """Calculate a node probability from cited claim strength; the model never supplies the number."""

    validated = validate_node_output(output, claims)
    by_id = {claim.id: claim for claim in claims}
    selected_ids = [*validated.supporting_claim_ids, *validated.opposing_claim_ids]
    if any(by_id[claim_id].forecast_node_id != node.id for claim_id in selected_ids):
        raise StructuredOutputError("node_forecast_claim_node_mismatch")
    if any(by_id[claim_id].forecasting_errors() for claim_id in selected_ids):
        raise StructuredOutputError("node_forecast_ineligible_claim")
    supporting_strength = math.fsum(
        claim_strength(by_id[claim_id]) for claim_id in validated.supporting_claim_ids
    )
    opposing_strength = math.fsum(
        claim_strength(by_id[claim_id]) for claim_id in validated.opposing_claim_ids
    )
    total_strength = supporting_strength + opposing_strength

    if total_strength == 0:
        probability = 0.5
        confidence = 0.0
    else:
        directional_balance = (supporting_strength - opposing_strength) / total_strength
        evidence_coverage = min(1.0, total_strength / FULL_EVIDENCE_STRENGTH)
        certainty = 1.0 - validated.uncertainty
        probability = clip_track_probability(
            inv_logit(MAX_NODE_LOGIT_SHIFT * directional_balance * evidence_coverage * certainty)
        )
        confidence = evidence_coverage * certainty

    return NodeForecast(
        node_id=node.id,
        probability=round(probability, 12),
        confidence=round(confidence, 12),
        reasoning=validated.reasoning,
        supporting_claim_ids=validated.supporting_claim_ids,
        opposing_claim_ids=validated.opposing_claim_ids,
        uncertainty=validated.uncertainty,
    )
