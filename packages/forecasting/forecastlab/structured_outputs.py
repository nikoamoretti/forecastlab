from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from forecastlab.schemas import ForecastNodeType

ForecastGraphOutputType = Literal[
    "probability",
    "directional_update",
    "bounded_quantity",
    "scenario_weight",
    "structured_categorical",
]


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


def forecast_graph_json_schema() -> dict[str, Any]:
    return ForecastGraphOutput.model_json_schema()


def structured_output_json_schema(schema_name: str) -> dict[str, Any] | None:
    if schema_name == "forecast_graph":
        return forecast_graph_json_schema()
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
    if schema_name != "forecast_graph":
        return payload if isinstance(payload, dict) else None, []
    try:
        validated = ForecastGraphOutput.model_validate(payload)
    except ValidationError as exc:
        return None, sanitize_validation_errors(exc)
    return validated.model_dump(mode="json"), []
