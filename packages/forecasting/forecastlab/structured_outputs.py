from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from forecastlab.schemas import ForecastNodeType

ForecastGraphOutputType = Literal[
    "probability",
    "directional_update",
    "bounded_quantity",
    "scenario_weight",
    "structured_categorical",
]

COMPACT_FORECAST_GRAPH_SCHEMA_NAME = "forecast_graph_compact_indexed_v1"
COMPACT_GRAPH_MAX_QUESTION_CHARACTERS = 140
COMPACT_GRAPH_MAX_DEPENDENCIES = 3
COMPACT_GRAPH_MAX_PREFERRED_SOURCES = 2
COMPACT_GRAPH_MAX_SOURCE_CHARACTERS = 48
_FORECAST_NODE_TYPES = (
    "base_rate",
    "trend",
    "driver",
    "dependency",
    "scenario",
    "adversarial",
    "resolver",
)
_FORECAST_OUTPUT_TYPES = (
    "probability",
    "directional_update",
    "bounded_quantity",
    "scenario_weight",
    "structured_categorical",
)


class ForecastNodeTransport(BaseModel):
    """Strict provider transport for an auditable node-level forecast.

    The dynamic JSON schema further constrains each claim-ID list to the
    eligible claims supplied for the node.  This model keeps the stable output
    shape separate from those per-request enumerations.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    probability: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1, max_length=1_200)
    supporting_claim_ids: list[str] = Field(max_length=20)
    opposing_claim_ids: list[str] = Field(max_length=20)
    uncertainty_notes: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        max_length=8
    )


class ForecastGraphNodeOutput(BaseModel):
    """Strict transport schema for one model-authored Forecast Graph node."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1)
    parent_node_id: str | None
    question: str = Field(min_length=1)
    node_type: ForecastNodeType
    importance_weight: float = Field(ge=0.0, le=1.0)
    dependencies: list[str]
    preferred_sources: list[str]
    required_output_type: ForecastGraphOutputType
    status: Literal["pending"]


class ForecastGraphOutput(BaseModel):
    """Strict structural boundary; graph-specific invariants remain separate."""

    model_config = ConfigDict(extra="forbid", strict=True)

    nodes: list[ForecastGraphNodeOutput] = Field(min_length=5, max_length=10)


class CompactForecastGraphNodeOutput(BaseModel):
    """Strict, indexed provider transport for one Forecast Graph node."""

    model_config = ConfigDict(extra="forbid", strict=True)

    q: str = Field(min_length=1, max_length=COMPACT_GRAPH_MAX_QUESTION_CHARACTERS)
    t: ForecastNodeType
    w: float = Field(ge=0.0, le=1.0)
    p: int | None = Field(ge=0)
    d: list[int] = Field(max_length=COMPACT_GRAPH_MAX_DEPENDENCIES)
    s: list[str] = Field(
        max_length=COMPACT_GRAPH_MAX_PREFERRED_SOURCES,
    )
    o: ForecastGraphOutputType

    @field_validator("q")
    @classmethod
    def validate_question(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("compact_graph_question_required")
        return normalized

    @field_validator("d")
    @classmethod
    def validate_dependencies(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate_compact_graph_dependency")
        return value

    @field_validator("s")
    @classmethod
    def validate_sources(cls, value: list[str]) -> list[str]:
        normalized = [" ".join(source.split()).strip() for source in value]
        if any(not source for source in normalized):
            raise ValueError("compact_graph_source_required")
        if any(
            len(source) > COMPACT_GRAPH_MAX_SOURCE_CHARACTERS
            for source in normalized
        ):
            raise ValueError("compact_graph_source_too_long")
        return normalized


class CompactForecastGraphOutput(BaseModel):
    """Strict compact graph boundary; canonical graph validation remains separate."""

    model_config = ConfigDict(extra="forbid", strict=True)

    n: list[CompactForecastGraphNodeOutput] = Field(min_length=5, max_length=10)


class ScenarioPathwayTransport(BaseModel):
    """Strict provider transport for one grounded scenario pathway."""

    model_config = ConfigDict(extra="forbid", strict=True)

    local_id: str = Field(min_length=1, max_length=32)
    kind: Literal["base_case", "yes_case", "no_case"]
    title: str = Field(min_length=1, max_length=96)
    summary: str = Field(min_length=1, max_length=600)
    node_ids: list[Annotated[str, Field(min_length=1)]] = Field(
        min_length=2,
        max_length=8,
    )
    claim_ids: list[Annotated[str, Field(min_length=1)]] = Field(
        min_length=1,
        max_length=16,
    )
    mechanisms: list[Annotated[str, Field(min_length=1, max_length=240)]] = Field(
        min_length=1,
        max_length=4,
    )
    triggers: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1,
        max_length=4,
    )
    invalidators: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1,
        max_length=4,
    )
    unresolved_uncertainties: list[
        Annotated[str, Field(min_length=1, max_length=200)]
    ] = Field(min_length=1, max_length=4)


class ScenarioSynthesisTransport(BaseModel):
    """Exactly three pathways; domain grounding remains a separate boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)

    scenarios: list[ScenarioPathwayTransport] = Field(min_length=3, max_length=3)


def forecast_graph_json_schema() -> dict[str, Any]:
    return ForecastGraphOutput.model_json_schema()


def compact_forecast_graph_json_schema(
    *,
    question_max_characters: int = COMPACT_GRAPH_MAX_QUESTION_CHARACTERS,
    max_dependencies_per_node: int = COMPACT_GRAPH_MAX_DEPENDENCIES,
    max_preferred_sources_per_node: int = COMPACT_GRAPH_MAX_PREFERRED_SOURCES,
    preferred_source_max_characters: int = COMPACT_GRAPH_MAX_SOURCE_CHARACTERS,
) -> dict[str, Any]:
    """Return the exact strict provider schema for compact indexed graph output."""

    return {
        "type": "object",
        "properties": {
            "n": {
                "type": "array",
                "minItems": 5,
                "maxItems": 10,
                "items": {
                    "type": "object",
                    "properties": {
                        "q": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": question_max_characters,
                        },
                        "t": {"type": "string", "enum": list(_FORECAST_NODE_TYPES)},
                        "w": {"type": "number", "minimum": 0, "maximum": 1},
                        "p": {"type": ["integer", "null"], "minimum": 0},
                        "d": {
                            "type": "array",
                            "maxItems": max_dependencies_per_node,
                            "items": {"type": "integer", "minimum": 0},
                        },
                        "s": {
                            "type": "array",
                            "maxItems": max_preferred_sources_per_node,
                            "items": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": preferred_source_max_characters,
                            },
                        },
                        "o": {"type": "string", "enum": list(_FORECAST_OUTPUT_TYPES)},
                    },
                    "required": ["q", "t", "w", "p", "d", "s", "o"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["n"],
        "additionalProperties": False,
    }


def scenario_synthesis_json_schema() -> dict[str, Any]:
    return ScenarioSynthesisTransport.model_json_schema()


def forecast_node_json_schema(
    *,
    supporting_claim_ids: list[str],
    opposing_claim_ids: list[str],
) -> dict[str, Any]:
    """Return a strict node schema bounded to the supplied eligible claims.

    Claim identifiers are data, not prose.  Encoding their allowed sets in the
    request keeps a successful structured response from inventing a citation
    that the deterministic post-validation must reject.
    """

    def claim_array(allowed: list[str]) -> dict[str, Any]:
        if not allowed:
            # OpenAI's strict-schema subset still requires an ``items`` schema
            # even when maxItems makes the only valid array empty.  The
            # sentinel is unreachable and never leaves this transport schema.
            return {
                "type": "array",
                "maxItems": 0,
                "items": {"type": "string", "enum": ["__no_eligible_claim_id__"]},
            }
        return {
            "type": "array",
            "maxItems": min(20, len(allowed)),
            "items": {"type": "string", "enum": allowed},
        }

    return {
        "type": "object",
        "properties": {
            "probability": {"type": "number", "minimum": 0, "maximum": 1},
            "reasoning": {"type": "string", "minLength": 1, "maxLength": 1_200},
            "supporting_claim_ids": claim_array(supporting_claim_ids),
            "opposing_claim_ids": claim_array(opposing_claim_ids),
            "uncertainty_notes": {
                "type": "array",
                "maxItems": 8,
                "items": {"type": "string", "minLength": 1, "maxLength": 300},
            },
        },
        "required": [
            "probability",
            "reasoning",
            "supporting_claim_ids",
            "opposing_claim_ids",
            "uncertainty_notes",
        ],
        "additionalProperties": False,
    }


def structured_output_json_schema(schema_name: str) -> dict[str, Any] | None:
    if schema_name == "forecast_graph":
        return forecast_graph_json_schema()
    if schema_name == COMPACT_FORECAST_GRAPH_SCHEMA_NAME:
        return compact_forecast_graph_json_schema()
    if schema_name == "scenario_synthesis":
        return scenario_synthesis_json_schema()
    return None


def sanitize_validation_errors(exc: ValidationError) -> list[dict[str, str]]:
    """Return validation diagnostics without values from the provider response."""

    sanitized: list[dict[str, str]] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part) for part in error.get("loc", ())) or "$"
        message = " ".join(str(error.get("msg") or "validation failed").split())[:240]
        sanitized.append(
            {
                "field_path": location,
                "error_type": str(error.get("type") or "validation_error")[:128],
                "message": message,
            }
        )
    return sanitized


def validate_structured_output(
    schema_name: str,
    payload: Any,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    if schema_name == COMPACT_FORECAST_GRAPH_SCHEMA_NAME:
        try:
            compact = CompactForecastGraphOutput.model_validate(payload)
        except ValidationError as exc:
            return None, sanitize_validation_errors(exc)
        return compact.model_dump(mode="json"), []
    if schema_name == "scenario_synthesis":
        try:
            scenario = ScenarioSynthesisTransport.model_validate(payload)
        except ValidationError as exc:
            return None, sanitize_validation_errors(exc)
        return scenario.model_dump(mode="json"), []
    if schema_name == "forecast_node":
        try:
            node = ForecastNodeTransport.model_validate(payload)
        except ValidationError as exc:
            return None, sanitize_validation_errors(exc)
        return node.model_dump(mode="json"), []
    if schema_name != "forecast_graph":
        return payload if isinstance(payload, dict) else None, []
    try:
        validated = ForecastGraphOutput.model_validate(payload)
    except ValidationError as exc:
        return None, sanitize_validation_errors(exc)
    return validated.model_dump(mode="json"), []
