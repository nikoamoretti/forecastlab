from __future__ import annotations

from typing import Any

from forecastlab.errors import ConfigurationError
from forecastlab.execution import ExecutionContext
from forecastlab.ledger import UsageLedger
from forecastlab.providers.mock import MockModelProvider
from forecastlab.providers.openai_compatible import OpenAICompatibleProvider, ProviderError


def build_model_provider(
    *,
    provider: str,
    api_key: str | None,
    base_url: str | None,
    model: str,
    timeout: float,
    execution: ExecutionContext | None = None,
    ledger: UsageLedger | None = None,
    run_id: str | None = None,
    run_attempt_id: str | None = None,
    pricing_catalog: dict[str, Any] | None = None,
) -> MockModelProvider | OpenAICompatibleProvider:
    if execution is not None:
        if execution.model_is_mock:
            return MockModelProvider(
                model=execution.model_name or "mock-forecast-v1",
                ledger=ledger,
                run_id=run_id,
                run_attempt_id=run_attempt_id,
            )
        if not api_key:
            raise ConfigurationError(["model_api_key_missing"])
        return OpenAICompatibleProvider(
            api_key=api_key,
            base_url=execution.model_base_url or base_url or "https://api.openai.com/v1",
            model=execution.model_name,
            timeout=timeout,
            ledger=ledger,
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            pricing_catalog=pricing_catalog,
        )
    if provider in {"mock", "demo"}:
        return MockModelProvider(
            model=model or "mock-forecast-v1",
            ledger=ledger,
            run_id=run_id,
            run_attempt_id=run_attempt_id,
        )
    if not api_key:
        raise ConfigurationError(["model_api_key_missing"])
    if provider in {"openai_compatible", "xai", "openai"}:
        default_base = "https://api.x.ai/v1" if provider == "xai" else "https://api.openai.com/v1"
        if provider == "openai_compatible":
            default_base = base_url or "https://api.openai.com/v1"
        return OpenAICompatibleProvider(
            api_key=api_key,
            base_url=base_url or default_base,
            model=model,
            timeout=timeout,
            ledger=ledger,
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            pricing_catalog=pricing_catalog,
        )
    raise ProviderError(f"Unknown model provider: {provider}")
