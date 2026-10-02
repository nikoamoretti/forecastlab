from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from forecastlab.schemas import ForecastNodeType, TrackForecastOutput

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

    def relationship_index_schema(index: int) -> dict[str, Any]:
        """Constrain references to the earlier portion of the compact array.

        A compact graph's references are positional.  A generic non-negative
        integer schema cannot express that a nine-node response has indexes
        0..8, so a provider can return an otherwise schema-valid dangling
        index.  ``prefixItems`` lets the strict provider boundary enforce the
        existing topological transport convention before canonicalization.
        """

        if index == 0:
            return {"type": "null"}
        return {"type": ["integer", "null"], "minimum": 0, "maximum": index - 1}

    def dependency_item_schema(index: int) -> dict[str, Any]:
        # Strict schemas still need an items schema for an array constrained to
        # zero entries.  The maximum is unreachable for index zero.
        return {"type": "integer", "minimum": 0, "maximum": max(index - 1, 0)}

    def compact_node_schema(index: int) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": question_max_characters,
                },
                "t": {"type": "string", "enum": list(_FORECAST_NODE_TYPES)},
                "w": {"type": "number", "minimum": 0, "maximum": 1},
                "p": relationship_index_schema(index),
                "d": {
                    "type": "array",
                    "maxItems": min(max_dependencies_per_node, index),
                    "items": dependency_item_schema(index),
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
        }

    return {
        "type": "object",
        "properties": {
            "n": {
                "type": "array",
                "minItems": 5,
                "maxItems": 10,
                "prefixItems": [compact_node_schema(index) for index in range(10)],
                # OpenAI's strict-schema subset requires ``items`` to be an
                # object.  ``maxItems`` prevents this overflow shape from ever
                # being used; positions zero through nine are governed by the
                # prefix schemas above.
                "items": compact_node_schema(9),
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


def track_forecast_json_schema(*, evidence_ids: list[str]) -> dict[str, Any]:
    """Return the strict three-track estimate schema bounded to supplied evidence.

    The shape mirrors ``TrackForecastOutput``.  Strict schemas require every
    property, so optional model fields are required here and nullable where
    the model allows ``None``.  Driver citations are limited to the evidence
    identifiers supplied to the track, matching the post-validation filter.
    """

    unit_interval = {"type": "number", "minimum": 0, "maximum": 1}
    if evidence_ids:
        evidence_array: dict[str, Any] = {
            "type": "array",
            "items": {"type": "string", "enum": list(evidence_ids)},
        }
    else:
        # OpenAI's strict-schema subset still requires an ``items`` schema
        # even when maxItems makes the only valid array empty.  The sentinel
        # is unreachable and never leaves this transport schema.
        evidence_array = {
            "type": "array",
            "maxItems": 0,
            "items": {"type": "string", "enum": ["__no_supplied_evidence_id__"]},
        }
    string_list = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": {
            "probability": {"type": "number", "minimum": 0.01, "maximum": 0.99},
            "prior_probability": {
                "type": ["number", "null"],
                "minimum": 0.01,
                "maximum": 0.99,
            },
            "key_drivers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "factor": {"type": "string"},
                        "direction": {"type": "string", "enum": ["up", "down", "unclear"]},
                        "importance": unit_interval,
                        "evidence_ids": evidence_array,
                        "inference": {"type": "boolean"},
                    },
                    "required": ["factor", "direction", "importance", "evidence_ids", "inference"],
                    "additionalProperties": False,
                },
            },
            "counterarguments": string_list,
            "unresolved_uncertainties": string_list,
            "resolver_risk": unit_interval,
            "evidence_quality": unit_interval,
            "reasoning_summary": {"type": "string"},
        },
        "required": [
            "probability",
            "prior_probability",
            "key_drivers",
            "counterarguments",
            "unresolved_uncertainties",
            "resolver_risk",
            "evidence_quality",
            "reasoning_summary",
        ],
        "additionalProperties": False,
    }


# An ISO 8601 date or timestamp, optionally with a zone.  Strict schemas use a
# pattern rather than ``format`` so prose such as "2026-11-06 (release day)"
# is rejected at the provider boundary instead of failing validation later.
ISO_8601_DATE_OR_TIMESTAMP_PATTERN = (
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?$"
)


def _strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def resolution_contract_json_schema() -> dict[str, Any]:
    """Return the strict schema for the engine's ``ResolutionContract`` step."""

    text = {"type": "string"}
    nullable_text = {"type": ["string", "null"]}
    return _strict_object(
        {
            "exact_yes": text,
            "exact_no": text,
            "resolution_deadline": {"type": "string", "pattern": ISO_8601_DATE_OR_TIMESTAMP_PATTERN},
            "authoritative_source": text,
            "fallback_sources": {"type": "array", "items": text},
            "geography": nullable_text,
            "units": nullable_text,
            "ambiguity_notes": text,
            "cancellation_conditions": text,
            "resolver_risk_notes": text,
        }
    )


def forecast_contract_json_schema() -> dict[str, Any]:
    """Return the strict schema for the personal Forecast Contract compiler.

    Keys follow the ``forecast_contract`` prompt.  ``resolution_date`` stays
    nullable because the prompt allows an undeterminable date, but any value
    must be an ISO 8601 date or timestamp.
    """

    text = {"type": "string"}
    nullable_text = {"type": ["string", "null"]}
    text_list = {"type": "array", "items": text}
    return _strict_object(
        {
            "normalized_question": text,
            "yes_condition": text,
            "no_condition": text,
            "resolution_date": {"type": ["string", "null"], "pattern": ISO_8601_DATE_OR_TIMESTAMP_PATTERN},
            "authoritative_source": text,
            "fallback_sources": text_list,
            "resolution_method": text,
            "ambiguity_notes": text,
            "cancellation_conditions": text,
            "resolver_risk_notes": text,
            "forecast_type": {"type": "string", "enum": ["binary"]},
            "geography": nullable_text,
            "units": nullable_text,
            "domain": nullable_text,
            "initial_reference_class": text,
            "suggested_drivers": text_list,
            "known_dependencies": text_list,
            "rejection_reasons": text_list,
        }
    )


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
    if schema_name == "track_forecast":
        try:
            track = TrackForecastOutput.model_validate(payload)
        except ValidationError as exc:
            return None, sanitize_validation_errors(exc)
        return track.model_dump(mode="json"), []
    if schema_name != "forecast_graph":
        return payload if isinstance(payload, dict) else None, []
    try:
        validated = ForecastGraphOutput.model_validate(payload)
    except ValidationError as exc:
        return None, sanitize_validation_errors(exc)
    return validated.model_dump(mode="json"), []
