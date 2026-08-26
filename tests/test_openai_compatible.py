from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from forecastlab.errors import PermanentProviderError
from forecastlab.providers.openai_compatible import OpenAICompatibleProvider
from forecastlab.structured_outputs import (
    COMPACT_FORECAST_GRAPH_SCHEMA_NAME,
    compact_forecast_graph_json_schema,
    forecast_graph_json_schema,
    scenario_synthesis_json_schema,
)


def _stub_client(monkeypatch: pytest.MonkeyPatch, *, status_code: int = 200) -> list[dict[str, Any]]:
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
            response.status_code = status_code
            response.text = '{"error":"bad request"}'
            response.json.return_value = {
                "id": "req-test",
                "choices": [{"message": {"content": '{"probability": 0.61}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            }
            response.headers = {"x-request-id": "rid-test"}
            response.elapsed.total_seconds.return_value = 0.01
            return response

    monkeypatch.setattr("forecastlab.providers.openai_compatible.httpx.Client", StubClient)
    return requests


def _complete(provider_id: str, model: str) -> Any:
    provider = OpenAICompatibleProvider(
        api_key="test-api-key",
        base_url="https://provider.example.test/v1",
        model=model,
        provider_id=provider_id,
    )
    return provider.complete_json(
        system="Return JSON.",
        user="Forecast this event.",
        schema_name="forecast",
        temperature=0.2,
        max_output_tokens=128,
    )


def _complete_graph(provider_id: str, model: str) -> Any:
    provider = OpenAICompatibleProvider(
        api_key="test-api-key",
        base_url="https://provider.example.test/v1",
        model=model,
        provider_id=provider_id,
    )
    return provider.complete_json(
        system="Return a Forecast Graph as JSON.",
        user="Create the graph.",
        schema_name="forecast_graph",
        max_output_tokens=1536,
        json_schema=forecast_graph_json_schema(),
        reasoning_effort="minimal",
    )


def _complete_graph_with_split_envelope(provider_id: str, model: str) -> Any:
    provider = OpenAICompatibleProvider(
        api_key="test-api-key",
        base_url="https://provider.example.test/v1",
        model=model,
        provider_id=provider_id,
    )
    return provider.complete_json(
        system="Return a Forecast Graph as JSON.",
        user="Create the graph.",
        schema_name="forecast_graph",
        max_output_tokens=1536,
        max_completion_tokens=8192,
        max_visible_output_tokens=1536,
        json_schema=forecast_graph_json_schema(),
        reasoning_effort="minimal",
        verbosity="low",
    )


def _complete_compact_graph(provider_id: str, model: str) -> Any:
    provider = OpenAICompatibleProvider(
        api_key="test-api-key",
        base_url="https://provider.example.test/v1",
        model=model,
        provider_id=provider_id,
    )
    return provider.complete_json(
        system="Return a compact indexed Forecast Graph as JSON.",
        user="Create the graph.",
        schema_name=COMPACT_FORECAST_GRAPH_SCHEMA_NAME,
        max_output_tokens=1536,
        max_completion_tokens=8192,
        max_visible_output_tokens=1536,
        json_schema=compact_forecast_graph_json_schema(),
        reasoning_effort="minimal",
        verbosity="low",
    )


def _complete_scenario_synthesis(provider_id: str, model: str) -> Any:
    provider = OpenAICompatibleProvider(
        api_key="test-api-key",
        base_url="https://provider.example.test/v1",
        model=model,
        provider_id=provider_id,
    )
    return provider.complete_json(
        system="Return grounded scenario pathways as JSON.",
        user="Create the pathways.",
        schema_name="scenario_synthesis",
        max_output_tokens=2048,
        max_completion_tokens=2048,
        max_visible_output_tokens=2048,
        estimated_input_tokens=10_000,
        json_schema=scenario_synthesis_json_schema(),
        reasoning_effort="minimal",
        verbosity="low",
    )


@pytest.mark.parametrize("model", ["gpt-5-mini-2025-08-07", "gpt-5-nano-2025-08-07"])
def test_openai_gpt5_request_uses_max_completion_tokens_and_omits_temperature(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    requests = _stub_client(monkeypatch)

    _complete("openai", model)

    body = requests[0]["json"]
    assert body["max_completion_tokens"] == 128
    assert "max_tokens" not in body
    assert "temperature" not in body
    assert body["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("model", ["o1-preview", "o3-mini", "o4-mini"])
def test_openai_reasoning_models_use_max_completion_tokens(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    requests = _stub_client(monkeypatch)

    _complete("openai", model)

    body = requests[0]["json"]
    assert body["max_completion_tokens"] == 128
    assert "max_tokens" not in body
    assert "temperature" not in body


def test_xai_request_keeps_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _stub_client(monkeypatch)

    _complete("xai", "grok-4")

    body = requests[0]["json"]
    assert body["max_tokens"] == 128
    assert "max_completion_tokens" not in body
    assert body["temperature"] == 0.2


def test_generic_openai_compatible_request_remains_backward_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _stub_client(monkeypatch)

    _complete("openai_compatible", "gpt-5-mini-2025-08-07")

    body = requests[0]["json"]
    assert body["max_tokens"] == 128
    assert "max_completion_tokens" not in body
    assert body["temperature"] == 0.2


def test_openai_gpt5_structured_json_parsing_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _stub_client(monkeypatch)

    result = _complete("openai", "gpt-5-mini-2025-08-07")

    assert requests[0]["json"]["response_format"] == {"type": "json_object"}
    assert result.parsed == {"probability": 0.61}


def test_openai_gpt5_forecast_graph_uses_strict_schema_and_minimal_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _stub_client(monkeypatch)

    result = _complete_graph("openai", "gpt-5-mini-2025-08-07")

    body = requests[0]["json"]
    assert body["max_completion_tokens"] == 1536
    assert body["reasoning_effort"] == "minimal"
    assert body["response_format"]["type"] == "json_schema"
    schema_config = body["response_format"]["json_schema"]
    assert schema_config["name"] == "forecast_graph"
    assert schema_config["strict"] is True
    assert schema_config["schema"] == forecast_graph_json_schema()
    assert result.diagnostics is not None
    assert result.diagnostics.provider_request_id == "rid-test"
    assert result.diagnostics.requested_max_output_tokens == 1536
    assert result.diagnostics.json_parsing_succeeded is True
    assert result.diagnostics.strict_schema_validation_succeeded is False


def test_openai_gpt5_graph_supports_distinct_completion_and_visible_envelopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _stub_client(monkeypatch)

    result = _complete_graph_with_split_envelope(
        "openai",
        "gpt-5-mini-2025-08-07",
    )

    body = requests[0]["json"]
    assert body["max_completion_tokens"] == 8192
    assert body["reasoning_effort"] == "minimal"
    assert body["verbosity"] == "low"
    assert "max_tokens" not in body
    assert "temperature" not in body
    assert result.diagnostics is not None
    assert result.diagnostics.requested_max_output_tokens == 1536
    assert result.diagnostics.requested_max_completion_tokens == 8192
    assert result.diagnostics.requested_max_visible_output_tokens == 1536
    assert result.diagnostics.reasoning_effort == "minimal"
    assert result.diagnostics.verbosity == "low"
    assert result.diagnostics.token_split_available is False
    assert (
        result.diagnostics.token_split_interpretation
        == "completion_tokens_used_as_visible_upper_bound"
    )


def test_openai_gpt5_compact_graph_uses_strict_schema_and_split_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _stub_client(monkeypatch)

    _complete_compact_graph("openai", "gpt-5-mini-2025-08-07")

    body = requests[0]["json"]
    assert body["max_completion_tokens"] == 8192
    assert body["reasoning_effort"] == "minimal"
    assert body["verbosity"] == "low"
    schema = body["response_format"]["json_schema"]
    assert schema["name"] == COMPACT_FORECAST_GRAPH_SCHEMA_NAME
    assert schema["strict"] is True
    assert schema["schema"] == compact_forecast_graph_json_schema()


def test_openai_gpt5_scenario_synthesis_uses_strict_bounded_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _stub_client(monkeypatch)

    result = _complete_scenario_synthesis(
        "openai",
        "gpt-5-mini-2025-08-07",
    )

    body = requests[0]["json"]
    assert body["max_completion_tokens"] == 2048
    assert body["reasoning_effort"] == "minimal"
    assert body["verbosity"] == "low"
    assert "max_tokens" not in body
    assert "temperature" not in body
    schema = body["response_format"]["json_schema"]
    assert schema["name"] == "scenario_synthesis"
    assert schema["strict"] is True
    assert schema["schema"] == scenario_synthesis_json_schema()
    assert result.diagnostics is not None
    assert result.diagnostics.requested_max_output_tokens == 2048
    assert result.diagnostics.requested_max_completion_tokens == 2048
    assert result.diagnostics.requested_max_visible_output_tokens == 2048


@pytest.mark.parametrize(
    ("provider_id", "model"),
    [("xai", "grok-4"), ("openai_compatible", "gpt-5-mini-2025-08-07")],
)
def test_compatible_vendors_do_not_receive_scenario_openai_only_controls(
    monkeypatch: pytest.MonkeyPatch,
    provider_id: str,
    model: str,
) -> None:
    requests = _stub_client(monkeypatch)

    _complete_scenario_synthesis(provider_id, model)

    body = requests[0]["json"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 2048
    assert "max_completion_tokens" not in body
    assert "reasoning_effort" not in body
    assert "verbosity" not in body


@pytest.mark.parametrize(
    ("provider_id", "model"),
    [("xai", "grok-4"), ("openai_compatible", "gpt-5-mini-2025-08-07")],
)
def test_compatible_vendors_do_not_receive_compact_openai_only_controls(
    monkeypatch: pytest.MonkeyPatch,
    provider_id: str,
    model: str,
) -> None:
    requests = _stub_client(monkeypatch)

    _complete_compact_graph(provider_id, model)

    body = requests[0]["json"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 1536
    assert "max_completion_tokens" not in body
    assert "reasoning_effort" not in body
    assert "verbosity" not in body


@pytest.mark.parametrize(
    ("provider_id", "model"),
    [("xai", "grok-4"), ("openai_compatible", "gpt-5-mini-2025-08-07")],
)
def test_compatible_vendors_do_not_receive_openai_strict_schema_or_reasoning(
    monkeypatch: pytest.MonkeyPatch,
    provider_id: str,
    model: str,
) -> None:
    requests = _stub_client(monkeypatch)

    _complete_graph(provider_id, model)

    body = requests[0]["json"]
    assert body["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in body
    assert body["max_tokens"] == 1536
    assert "max_completion_tokens" not in body


@pytest.mark.parametrize(
    ("provider_id", "model"),
    [("xai", "grok-4"), ("openai_compatible", "gpt-5-mini-2025-08-07")],
)
def test_compatible_vendors_ignore_openai_only_split_envelope_fields(
    monkeypatch: pytest.MonkeyPatch,
    provider_id: str,
    model: str,
) -> None:
    requests = _stub_client(monkeypatch)

    _complete_graph_with_split_envelope(provider_id, model)

    body = requests[0]["json"]
    assert body["max_tokens"] == 1536
    assert "max_completion_tokens" not in body
    assert "reasoning_effort" not in body
    assert "verbosity" not in body


def test_http_400_is_permanent_and_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _stub_client(monkeypatch, status_code=400)

    with pytest.raises(PermanentProviderError, match="HTTP 400"):
        _complete("openai", "gpt-5-mini-2025-08-07")

    assert len(requests) == 1
