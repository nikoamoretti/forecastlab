from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, date, datetime, time
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator

from forecastlab.prompts import PromptBundle, load_prompt
from forecastlab.providers.base import ModelProvider
from forecastlab.schemas import ForecastContract
from forecastlab.structured_outputs import forecast_contract_json_schema
from forecastlab.timeutil import utcnow

_BINARY_QUESTION = re.compile(r"\b(will|would|does|do|did|is|are|was|were|has|have|can|could|should)\b", re.I)
_OBJECTIVE_MARKER = re.compile(
    r"(?:\d|%|\$|\b(?:before|after|by|on|above|below|at least|at most|exceed|reach|close|win|publish|launch)\b)",
    re.I,
)
_VAGUE_OUTCOMES = (
    "change everything",
    "do well",
    "be successful",
    "get better",
    "make things better",
    "have a big impact",
    "transform society",
)


class ForecastContractError(ValueError):
    def __init__(self, reasons: list[str], message: str = "Forecast Contract could not be generated") -> None:
        self.reasons = reasons
        super().__init__(message)


class _CompiledContractFields(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    normalized_question: str = ""
    yes_condition: str = Field(default="", validation_alias=AliasChoices("yes_condition", "exact_yes"))
    no_condition: str = Field(default="", validation_alias=AliasChoices("no_condition", "exact_no"))
    resolution_date: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("resolution_date", "resolution_deadline"),
    )
    authoritative_source: str = ""
    fallback_sources: list[str] = Field(default_factory=list)
    resolution_method: str = ""
    ambiguity_notes: str = ""
    cancellation_conditions: str = ""
    resolver_risk_notes: str = ""
    forecast_type: str = "binary"
    geography: str | None = None
    units: str | None = None
    domain: str | None = None
    initial_reference_class: str = ""
    suggested_drivers: list[str] = Field(default_factory=list)
    known_dependencies: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)

    @field_validator("resolution_date", mode="before")
    @classmethod
    def normalize_resolution_date(cls, value: Any) -> Any:
        if isinstance(value, date) and not isinstance(value, datetime):
            return datetime.combine(value, time.max, tzinfo=UTC)
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
            parsed = date.fromisoformat(value.strip())
            return datetime.combine(parsed, time.max, tzinfo=UTC)
        return value

    @field_validator("fallback_sources", "suggested_drivers", "known_dependencies", "rejection_reasons", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        return value


def _normalize_question(question: str) -> str:
    return " ".join(question.split()).strip()


def _question_preflight(question: str) -> list[str]:
    reasons: list[str] = []
    if len(question) < 12:
        reasons.append("question_too_short")
    if not _BINARY_QUESTION.search(question):
        reasons.append("binary_outcome_not_identifiable")
    lowered = question.casefold()
    if any(phrase in lowered for phrase in _VAGUE_OUTCOMES) and not _OBJECTIVE_MARKER.search(question):
        reasons.append("vague_outcome")
    return reasons


def _default_resolution_method(fields: _CompiledContractFields) -> str:
    if not fields.authoritative_source:
        return ""
    fallback = " Use fallback sources in listed order only if the authoritative source is unavailable."
    if not fields.fallback_sources:
        fallback = " No fallback source is defined."
    return (
        "Apply the yes and no conditions to the authoritative source's published record as of the resolution date."
        + fallback
    )


class QuestionCompiler:
    """Compile a natural-language binary question into an auditable draft contract."""

    def __init__(self, model: ModelProvider, *, prompt_bundle: PromptBundle | None = None) -> None:
        self.model = model
        self.prompt_bundle = prompt_bundle

    def compile(
        self,
        question: str,
        *,
        question_id: str,
        version: int = 1,
        created_by: str = "user",
        contract_id: str | None = None,
    ) -> ForecastContract:
        original = _normalize_question(question)
        preflight_reasons = _question_preflight(original)
        if preflight_reasons:
            raise ForecastContractError(preflight_reasons, "Question is too vague for a Forecast Contract")

        system, _prompt_version = self.prompt_bundle.get("forecast_contract") if self.prompt_bundle else load_prompt("forecast_contract")
        schema_name = "resolution_contract" if self.model.name == "mock" else "forecast_contract"
        # Providers that support strict schemas reject non-ISO dates and missing
        # keys at the boundary; others still receive JSON-object mode.
        request: dict[str, Any] = (
            {"json_schema": forecast_contract_json_schema()} if schema_name == "forecast_contract" else {}
        )
        try:
            result = self.model.complete_json(
                system=system,
                user=json.dumps({"question": original}),
                schema_name=schema_name,
                max_output_tokens=4096,
                **request,
            )
            payload = result.parsed if result.parsed is not None else json.loads(result.content)
            fields = _CompiledContractFields.model_validate(payload)
        except (json.JSONDecodeError, TypeError, ValidationError, KeyError) as exc:
            raise ForecastContractError(["invalid_structured_output"]) from exc

        if fields.rejection_reasons:
            raise ForecastContractError(fields.rejection_reasons, "Question is too vague for a Forecast Contract")
        if fields.yes_condition and fields.yes_condition.casefold().strip() == fields.no_condition.casefold().strip():
            raise ForecastContractError(["outcome_conditions_must_differ"])

        normalized = _normalize_question(fields.normalized_question) or original
        return ForecastContract(
            id=contract_id or str(uuid.uuid4()),
            question_id=question_id,
            version=version,
            created_at=utcnow(),
            created_by=created_by.strip() or "user",
            original_question=original,
            normalized_question=normalized,
            yes_condition=fields.yes_condition.strip(),
            no_condition=fields.no_condition.strip(),
            resolution_date=fields.resolution_date,
            authoritative_source=fields.authoritative_source.strip(),
            fallback_sources=[item.strip() for item in fields.fallback_sources if item.strip()],
            resolution_method=fields.resolution_method.strip() or _default_resolution_method(fields),
            ambiguity_notes=fields.ambiguity_notes.strip(),
            cancellation_conditions=fields.cancellation_conditions.strip(),
            resolver_risk_notes=fields.resolver_risk_notes.strip(),
            forecast_type=fields.forecast_type.strip() or "binary",
            geography=fields.geography.strip() if fields.geography else None,
            units=fields.units.strip() if fields.units else None,
            domain=fields.domain.strip() if fields.domain else "general",
            initial_reference_class=(
                fields.initial_reference_class.strip()
                or "Comparable historical events with the same outcome definition and forecast horizon."
            ),
            suggested_drivers=[item.strip() for item in fields.suggested_drivers if item.strip()],
            known_dependencies=[item.strip() for item in fields.known_dependencies if item.strip()],
            status="draft",
        )
