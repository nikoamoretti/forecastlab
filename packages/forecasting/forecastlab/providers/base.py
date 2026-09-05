from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from forecastlab.errors import BudgetExceeded
from forecastlab.schemas import ModelUsage, SearchHit
from forecastlab.wayback import WaybackSnapshot

__all__ = [
    "BudgetExceeded",
    "ChatResult",
    "FrozenHistoricalEvidenceSearchProvider",
    "ModelProvider",
    "NullUsage",
    "SearchProvider",
    "StructuredOutputDiagnostics",
    "effective_search_provider_identity",
]


@dataclass
class StructuredOutputDiagnostics:
    """Sanitized provider and validation metadata; never includes response content."""

    schema_name: str
    provider_request_id: str | None = None
    finish_reason: str | None = None
    refusal_present: bool = False
    refusal_category: str | None = None
    requested_max_output_tokens: int | None = None
    requested_max_completion_tokens: int | None = None
    requested_max_visible_output_tokens: int | None = None
    reasoning_effort: str | None = None
    verbosity: str | None = None
    completion_tokens: int = 0
    reasoning_tokens: int | None = None
    visible_output_tokens: int | None = None
    token_split_available: bool | None = None
    token_split_interpretation: str | None = None
    content_character_count: int = 0
    json_parsing_succeeded: bool = False
    strict_schema_validation_succeeded: bool | None = None
    schema_validation_errors: list[dict[str, str]] = field(default_factory=list)

    def audit_payload(self) -> dict[str, Any]:
        return {
            "schema_name": self.schema_name,
            "provider_request_id": self.provider_request_id,
            "finish_reason": self.finish_reason,
            "refusal_present": self.refusal_present,
            "refusal_category": self.refusal_category,
            "requested_max_output_tokens": self.requested_max_output_tokens,
            "requested_max_completion_tokens": self.requested_max_completion_tokens,
            "requested_max_visible_output_tokens": self.requested_max_visible_output_tokens,
            "reasoning_effort": self.reasoning_effort,
            "verbosity": self.verbosity,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "visible_output_tokens": self.visible_output_tokens,
            "token_split_available": self.token_split_available,
            "token_split_interpretation": self.token_split_interpretation,
            "content_character_count": self.content_character_count,
            "json_parsing_succeeded": self.json_parsing_succeeded,
            "strict_schema_validation_succeeded": self.strict_schema_validation_succeeded,
            "schema_validation_errors": list(self.schema_validation_errors),
        }


@dataclass
class ChatResult:
    content: str
    parsed: dict[str, Any] | None
    usage: ModelUsage
    raw_error: str | None = None
    diagnostics: StructuredOutputDiagnostics | None = None


class ModelProvider(Protocol):
    name: str

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        temperature: float = 0.2,
        timeout: float | None = None,
        max_output_tokens: int | None = None,
        max_completion_tokens: int | None = None,
        max_visible_output_tokens: int | None = None,
        estimated_input_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None,
        verbosity: Literal["low", "medium", "high"] | None = None,
    ) -> ChatResult: ...


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]: ...


@runtime_checkable
class FrozenHistoricalEvidenceSearchProvider(Protocol):
    """Capability-gated offline snapshot discovery for a verified release."""

    name: str
    offline_frozen_evidence: bool

    def discover_frozen_snapshots(
        self,
        url: str,
        *,
        as_of: datetime,
    ) -> list[WaybackSnapshot]: ...


def effective_search_provider_identity(
    search: SearchProvider,
    *,
    configured_provider: str | None = None,
) -> str:
    """Use the frozen provider identity when execution cannot reach live search."""

    if (
        isinstance(search, FrozenHistoricalEvidenceSearchProvider)
        and search.offline_frozen_evidence
    ):
        return search.name
    return configured_provider or search.name


@dataclass
class NullUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
