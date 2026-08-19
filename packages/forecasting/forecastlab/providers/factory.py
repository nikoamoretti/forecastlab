from __future__ import annotations

from forecastlab.providers.mock import MockModelProvider
from forecastlab.providers.openai_compatible import OpenAICompatibleProvider, ProviderError


def build_model_provider(
    *,
    provider: str,
    api_key: str | None,
    base_url: str | None,
    model: str,
    timeout: float,
) -> MockModelProvider | OpenAICompatibleProvider:
    if provider in {"mock", "demo"} or not api_key:
        return MockModelProvider(model=model or "mock-forecast-v1")
    if provider in {"openai_compatible", "xai", "openai"}:
        default_base = "https://api.x.ai/v1" if provider == "xai" else "https://api.openai.com/v1"
        if provider == "openai_compatible":
            default_base = base_url or "https://api.openai.com/v1"
        return OpenAICompatibleProvider(
            api_key=api_key,
            base_url=base_url or default_base,
            model=model,
            timeout=timeout,
        )
    raise ProviderError(f"Unknown model provider: {provider}")
