from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from forecastlab.errors import PermanentProviderError
from forecastlab.providers.openai_compatible import OpenAICompatibleProvider


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


def test_http_400_is_permanent_and_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _stub_client(monkeypatch, status_code=400)

    with pytest.raises(PermanentProviderError, match="HTTP 400"):
        _complete("openai", "gpt-5-mini-2025-08-07")

    assert len(requests) == 1
