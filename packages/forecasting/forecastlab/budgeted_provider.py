from __future__ import annotations

from typing import Any, Literal

from forecastlab.budget import Budget, estimate_prompt_tokens
from forecastlab.providers.base import ChatResult, ModelProvider


class BudgetedModelProvider:
    """Apply one shared run budget to a structured model-provider seam."""

    def __init__(
        self,
        delegate: ModelProvider,
        budget: Budget,
        *,
        stage: str,
        max_output_tokens_cap: int | None = None,
        call_kind: str | None = None,
    ) -> None:
        self.delegate = delegate
        self.budget = budget
        self.stage = stage
        self.max_output_tokens_cap = (
            max(1, max_output_tokens_cap)
            if max_output_tokens_cap is not None
            else None
        )
        self.call_kind = call_kind
        self.name = delegate.name
        self.model = str(getattr(delegate, "model", delegate.name))

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
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None,
    ) -> ChatResult:
        estimated_input = estimated_input_tokens or estimate_prompt_tokens(system, user)
        allowed_output = self.budget.max_output_tokens_for_call(estimated_input)
        if self.max_output_tokens_cap is not None:
            allowed_output = min(allowed_output, self.max_output_tokens_cap)
        if max_output_tokens is not None:
            allowed_output = min(allowed_output, max_output_tokens)
        reservation = self.budget.reserve_model_call(
            self.stage,
            estimated_input_tokens=estimated_input,
            max_output_tokens=allowed_output,
            call_kind=self.call_kind,
        )
        try:
            result = self.delegate.complete_json(
                system=system,
                user=user,
                schema_name=schema_name,
                temperature=temperature,
                timeout=timeout,
                max_output_tokens=allowed_output,
                estimated_input_tokens=estimated_input,
                json_schema=json_schema,
                reasoning_effort=reasoning_effort,
            )
        except Exception:
            self.budget.release_reservation(reservation)
            raise
        self.budget.reconcile_model_call(reservation, result.usage)
        return result
