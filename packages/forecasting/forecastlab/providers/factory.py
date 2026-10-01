from __future__ import annotations

from typing import Any

from forecastlab.errors import ConfigurationError
from forecastlab.execution import ExecutionContext
from forecastlab.ledger import UsageLedger
from forecastlab.providers.mock import MockModelProvider
from forecastlab.providers.openai_compatible import (
    DEFAULT_OPENAI_BASE,
    DEFAULT_OPENROUTER_BASE,
    DEFAULT_XAI_BASE,
    OpenAICompatibleProvider,
    ProviderError,
)

_DEFAULT_BASE_URLS = {"openrouter": DEFAULT_OPENROUTER_BASE, "xai": DEFAULT_XAI_BASE}


def default_base_url(provider: str) -> str:
    return _DEFAULT_BASE_URLS.get(provider, DEFAULT_OPENAI_BASE)


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
            base_url=execution.model_base_url or base_url or default_base_url(execution.model_provider),
            model=execution.model_name,
            timeout=timeout,
            ledger=ledger,
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            pricing_catalog=pricing_catalog,
            provider_id=execution.model_provider,
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
    if provider in {"openai_compatible", "xai", "openai", "openrouter"}:
        return OpenAICompatibleProvider(
            api_key=api_key,
            base_url=base_url or default_base_url(provider),
            model=model,
            timeout=timeout,
            ledger=ledger,
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            pricing_catalog=pricing_catalog,
            provider_id=provider,
        )
    raise ProviderError(f"Unknown model provider: {provider}")
