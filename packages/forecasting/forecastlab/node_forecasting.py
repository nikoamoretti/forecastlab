from __future__ import annotations

import json
import math
from typing import Any

from pydantic import ValidationError

from forecastlab.errors import StructuredOutputError
from forecastlab.prompts import PromptBundle, load_prompt
from forecastlab.providers.base import ModelProvider
from forecastlab.schemas import (
    EvidenceClaim,
    ForecastContract,
    ForecastNode,
    ForecastNodeOutput,
    NodeForecast,
)

FULL_EVIDENCE_STRENGTH = 2.0
SECONDARY_SOURCE_FACTOR = 0.85


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def claim_strength(claim: EvidenceClaim) -> float:
    """Return a bounded provenance-quality score used only for node confidence."""

    source_factor = 1.0 if claim.primary_source else SECONDARY_SOURCE_FACTOR
    return float(claim.confidence) * float(claim.source_quality) * source_factor


def validate_node_claims(node: ForecastNode, claims: list[EvidenceClaim]) -> dict[str, EvidenceClaim]:
    """Reject empty, duplicated, ineligible, or cross-node evidence before a model call."""

    if not claims:
        raise StructuredOutputError("node_forecast_evidence_required")
    by_id = {claim.id: claim for claim in claims}
    if len(by_id) != len(claims):
        raise StructuredOutputError("node_forecast_duplicate_claim_id")
    if any(claim.forecast_node_id != node.id for claim in claims):
        raise StructuredOutputError("node_forecast_claim_node_mismatch")
    if any(claim.forecasting_errors() for claim in claims):
        raise StructuredOutputError("node_forecast_ineligible_claim")
    return by_id


def validate_node_output(
    output: ForecastNodeOutput,
    *,
    node: ForecastNode,
    claims: list[EvidenceClaim],
) -> ForecastNodeOutput:
    """Require every model-selected claim to exist, belong to the node, and match its stance."""

    by_id = validate_node_claims(node, claims)
    supporting = _deduplicate(output.supporting_claim_ids)
    opposing = _deduplicate(output.opposing_claim_ids)
    selected = [*supporting, *opposing]
    if not selected:
        raise StructuredOutputError("node_forecast_claim_reference_required")
    if set(supporting) & set(opposing):
        raise StructuredOutputError("node_forecast_claim_stance_conflict")
    if any(claim_id not in by_id for claim_id in selected):
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


def calculate_node_forecast(
    *,
    node: ForecastNode,
    claims: list[EvidenceClaim],
    output: ForecastNodeOutput,
    model_used: str,
) -> NodeForecast:
    """Validate a model probability and derive an auditable evidence-confidence score."""

    validated = validate_node_output(output, node=node, claims=claims)
    by_id = {claim.id: claim for claim in claims}
    selected_ids = [*validated.supporting_claim_ids, *validated.opposing_claim_ids]
    total_strength = math.fsum(claim_strength(by_id[claim_id]) for claim_id in selected_ids)
    confidence = min(1.0, total_strength / FULL_EVIDENCE_STRENGTH)
    return NodeForecast(
        node_id=node.id,
        probability=round(validated.probability, 12),
        confidence=round(confidence, 12),
        reasoning=validated.reasoning,
        supporting_claim_ids=validated.supporting_claim_ids,
        opposing_claim_ids=validated.opposing_claim_ids,
        uncertainty_notes=validated.uncertainty_notes,
        model_used=model_used,
        uncertainty=round(1.0 - confidence, 12),
    )


class NodeForecaster:
    """Generate one node probability from only its contract, question, and Evidence Claims."""

    def __init__(
        self,
        model: ModelProvider,
        *,
        prompt_bundle: PromptBundle | None = None,
        prompt_versions: dict[str, str] | None = None,
    ) -> None:
        self.model = model
        self.prompt_bundle = prompt_bundle
        self.prompt_versions = prompt_versions

    @property
    def model_used(self) -> str:
        model_name = str(getattr(self.model, "model", self.model.name))
        return f"{self.model.name}:{model_name}"

    def _prompt(self) -> tuple[str, str]:
        if self.prompt_bundle is not None:
            return self.prompt_bundle.get("forecast_node")
        return load_prompt("forecast_node")

    @staticmethod
    def _context(
        contract: ForecastContract,
        node: ForecastNode,
        claims: list[EvidenceClaim],
    ) -> dict[str, Any]:
        return {
            "forecast_contract": contract.model_dump(mode="json"),
            "node_question": node.question,
            "evidence_claims": [claim.model_dump(mode="json") for claim in claims],
        }

    def forecast(
        self,
        *,
        contract: ForecastContract,
        node: ForecastNode,
        claims: list[EvidenceClaim],
    ) -> NodeForecast:
        validate_node_claims(node, claims)
        system, version = self._prompt()
        if self.prompt_versions is not None:
            self.prompt_versions["forecast_node"] = version
        user = json.dumps(self._context(contract, node, claims))
        result = self.model.complete_json(
            system=system,
            user=user,
            schema_name="forecast_node",
        )
        try:
            parsed = result.parsed if result.parsed is not None else json.loads(result.content)
            output = ForecastNodeOutput.model_validate(parsed)
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            raise StructuredOutputError("invalid_structured_output:forecast_node") from exc
        return calculate_node_forecast(
            node=node,
            claims=claims,
            output=output,
            model_used=self.model_used,
        )
