from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from forecastlab.execution import resolve_execution_context
from forecastlab.providers.factory import build_model_provider, default_base_url
from forecastlab.providers.openai_compatible import OpenAICompatibleProvider
from forecastlab.structured_outputs import track_forecast_json_schema


def _stub_client(monkeypatch: pytest.MonkeyPatch, *, usage: dict[str, Any]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []

    class StubClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> StubClient:
            return self

        def __exit__(self, *_args: Any) -> bool:
            return False

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> MagicMock:
            requests.append({"url": url, "headers": headers, "json": json})
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "id": "gen-test",
                "choices": [{"message": {"content": '{"probability": 0.61}'}, "finish_reason": "stop"}],
                "usage": usage,
            }
            response.headers = {}
            response.elapsed.total_seconds.return_value = 0.01
            return response

    monkeypatch.setattr("forecastlab.providers.openai_compatible.httpx.Client", StubClient)
    return requests


def _provider(model: str) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        api_key="test-api-key",
        base_url=default_base_url("openrouter"),
        model=model,
        provider_id="openrouter",
    )


def test_openrouter_openai_models_use_strict_schema_and_unified_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _stub_client(monkeypatch, usage={"prompt_tokens": 12, "completion_tokens": 4, "cost": 0.000123})
    schema = track_forecast_json_schema(evidence_ids=["ev-a"])

    result = _provider("openai/gpt-5-mini").complete_json(
        system="Return JSON.",
        user="Forecast this event.",
        schema_name="track_forecast",
        max_output_tokens=4096,
        json_schema=schema,
        reasoning_effort="minimal",
        verbosity="low",
    )

    request = requests[0]
    body = request["json"]
    assert request["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert body["model"] == "openai/gpt-5-mini"
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "track_forecast", "strict": True, "schema": schema},
    }
    assert body["provider"] == {"require_parameters": True}
    assert body["reasoning"] == {"effort": "minimal"}
    assert "reasoning_effort" not in body
    assert body["verbosity"] == "low"
    assert body["max_completion_tokens"] == 4096
    assert "max_tokens" not in body
    assert "temperature" not in body
    assert result.usage.cost_usd == pytest.approx(0.000123)
    assert result.usage.cost_source == "provider_reported"


def test_openrouter_other_vendors_keep_json_object_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _stub_client(monkeypatch, usage={"prompt_tokens": 1_000_000, "completion_tokens": 0})

    result = _provider("anthropic/claude-sonnet-4.5").complete_json(
        system="Return JSON.",
        user="Forecast this event.",
        schema_name="track_forecast",
        max_output_tokens=2048,
        json_schema=track_forecast_json_schema(evidence_ids=[]),
        reasoning_effort="minimal",
    )

    body = requests[0]["json"]
    assert body["response_format"] == {"type": "json_object"}
    assert "provider" not in body
    assert "reasoning" not in body
    assert body["max_tokens"] == 2048
    assert body["temperature"] == 0.2
    # Without a reported charge the conservative catalog estimate applies.
    assert result.usage.cost_source == "estimated"
    assert result.usage.cost_usd == pytest.approx(5.0)


def test_openrouter_is_a_real_live_provider_with_default_base_url() -> None:
    context = resolve_execution_context(
        requested_mode="live",
        profile_id="three_track_strict_forecaster_v1",
        settings={
            "model_provider": "openrouter",
            "model_name": "openai/gpt-5-mini",
            "model_api_key": "test-not-real",
            "search_provider": "tavily",
            "search_api_key": "test-not-real",
            "max_cost_usd": 5,
        },
    )
    assert context.effective_mode == "live"
    assert context.model_provider == "openrouter"

    provider = build_model_provider(
        provider="openrouter",
        api_key="test-not-real",
        base_url=None,
        model="openai/gpt-5-mini",
        timeout=30,
        execution=context,
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "https://openrouter.ai/api/v1"
    assert provider.provider_id == "openrouter"


def test_direct_openai_requests_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _stub_client(monkeypatch, usage={"prompt_tokens": 12, "completion_tokens": 4, "cost": 99.0})
    provider = OpenAICompatibleProvider(
        api_key="test-api-key",
        base_url=default_base_url("openai"),
        model="gpt-5-mini",
        provider_id="openai",
    )

    result = provider.complete_json(
        system="Return JSON.",
        user="Forecast this event.",
        schema_name="track_forecast",
        max_output_tokens=4096,
        json_schema=track_forecast_json_schema(evidence_ids=[]),
        reasoning_effort="minimal",
    )

    body = requests[0]["json"]
    assert requests[0]["url"] == "https://api.openai.com/v1/chat/completions"
    assert body["reasoning_effort"] == "minimal"
    assert "reasoning" not in body
    assert "provider" not in body
    assert result.usage.cost_source == "estimated"
