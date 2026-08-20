from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from forecastlab.errors import BudgetExceeded
from forecastlab.schemas import ModelUsage, SearchHit

__all__ = ["BudgetExceeded", "ChatResult", "ModelProvider", "SearchProvider", "NullUsage"]


@dataclass
class ChatResult:
    content: str
    parsed: dict[str, Any] | None
    usage: ModelUsage
    raw_error: str | None = None


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
        estimated_input_tokens: int | None = None,
    ) -> ChatResult: ...


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]: ...


@dataclass
class NullUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
