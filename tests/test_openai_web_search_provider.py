from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from forecastlab.errors import ConfigurationError, PermanentProviderError, TransientProviderError
from forecastlab.execution import readiness, resolve_execution_context, search_api_key_for
from forecastlab.ledger import InMemoryUsageLedger
from forecastlab.providers.search import (
    OpenAIWebSearchProvider,
    build_search_provider,
    openai_web_search_hits,
)

CATALOG = {
    "providers": {"openai": {"unknown": {"input_per_million": 5.0, "output_per_million": 15.0}}},
    "search": {
        "openai_web_search": {
            "per_request": None,
            "estimated_per_request": 0.10,
            "tool_call_fee_usd": 0.01,
            "estimated": True,
        }
    },
}

TEXT = (
    "- Employment Situation Summary — BLS headline unemployment rate for August. "
    "([bls.gov](https://www.bls.gov/news.release/empsit.nr0.htm?utm_source=openai))\n"
    "- Fed minutes — Committee view on labor market slack. "
    "([federalreserve.gov](https://www.federalreserve.gov/minutes.htm?utm_source=openai))\n"
    "Also see https://made-up.example.com/not-from-the-tool"
)


def _payload(*, status: str = "completed") -> dict[str, Any]:
    first = TEXT.index("([bls.gov]")
    second = TEXT.index("([federalreserve.gov]")
    return {
        "id": "resp_test",
        "status": status,
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {
                    "type": "search",
                    "query": "US unemployment rate",
                    "sources": [
                        {"type": "url", "url": "https://www.bls.gov/news.release/empsit.nr0.htm"},
                        {"type": "url", "url": "https://www.reuters.com/markets/us/jobs-report"},
                        {"type": "url", "url": "ftp://files.example.com/data.csv"},
                    ],
                },
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": TEXT,
                        "annotations": [
                            {
                                "type": "url_citation",
                                "start_index": first,
                                "end_index": first + 20,
                                "url": "https://www.bls.gov/news.release/empsit.nr0.htm?utm_source=openai",
                                "title": "Employment Situation Summary",
                            },
                            {
                                "type": "url_citation",
                                "start_index": second,
                                "end_index": second + 20,
                                "url": "https://www.federalreserve.gov/minutes.htm?utm_source=openai",
                                "title": "FOMC Minutes",
                            },
                        ],
                    }
                ],
            },
        ],
        "usage": {"input_tokens": 4000, "output_tokens": 600},
    }


def _stub_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status_code: int = 200,
    payload: dict[str, Any] | None = None,
    text: str = "",
) -> list[dict[str, Any]]:
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
            response.json.return_value = payload if payload is not None else _payload()
            response.text = text
            response.headers = {"x-request-id": "req_test"}
            response.elapsed.total_seconds.return_value = 0.5
            return response

    monkeypatch.setattr("forecastlab.providers.search.httpx.Client", StubClient)
    return requests


def test_request_uses_responses_api_web_search_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _stub_client(monkeypatch)

    OpenAIWebSearchProvider("test-api-key", pricing_catalog=CATALOG).search("US unemployment rate", max_results=3)

    request = requests[0]
    body = request["json"]
    assert request["url"] == "https://api.openai.com/v1/responses"
    assert request["headers"]["Authorization"] == "Bearer test-api-key"
    assert body["model"] == "gpt-5-mini"
    assert body["tools"] == [{"type": "web_search"}]
    assert body["tool_choice"] == "required"
    assert body["include"] == ["web_search_call.action.sources"]
    assert body["store"] is False
    assert "Query: US unemployment rate" in body["input"]
    assert "at most 3 pages" in body["input"]


def test_hits_come_only_from_tool_citations_and_sources() -> None:
    hits, calls = openai_web_search_hits(_payload(), max_results=10)

    assert calls == 1
    assert [hit.url for hit in hits] == [
        "https://www.bls.gov/news.release/empsit.nr0.htm",
        "https://www.federalreserve.gov/minutes.htm",
        "https://www.reuters.com/markets/us/jobs-report",
    ]
    assert hits[0].title == "Employment Situation Summary"
    assert hits[0].snippet.startswith("Employment Situation Summary — BLS headline unemployment rate")
    assert "utm_source" not in hits[0].snippet
    assert hits[2].title == hits[2].url
    assert all(hit.published_at is None and hit.published_at_source is None for hit in hits)
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)


def test_hits_respect_max_results_and_keep_other_query_params() -> None:
    payload = _payload()
    payload["output"][1]["action"]["sources"].insert(
        0, {"type": "url", "url": "https://example.org/data?series=UNRATE&utm_source=newsletter"}
    )

    hits, _calls = openai_web_search_hits(payload, max_results=3)

    assert len(hits) == 3
    assert hits[2].url == "https://example.org/data?series=UNRATE&utm_source=newsletter"


def test_search_cost_is_tool_fee_plus_tokens_and_skips_token_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_client(monkeypatch)
    ledger = InMemoryUsageLedger()
    attempt = ledger.begin_attempt(run_id="run-1", job_id=None, attempt_number=1)

    OpenAIWebSearchProvider(
        "test-api-key", ledger=ledger, run_id="run-1", run_attempt_id=attempt.id, pricing_catalog=CATALOG
    ).search("US unemployment rate")

    totals = ledger.totals("run-1")
    expected = 0.01 + 4000 / 1_000_000 * 5.0 + 600 / 1_000_000 * 15.0
    assert totals.search_calls == 1
    assert totals.search_cost_usd == pytest.approx(expected)
    assert totals.total_tokens == 0
    entry = ledger.entries("run-1")[0]
    assert entry.provider == "openai_web_search"
    assert entry.model == "gpt-5-mini"
    assert entry.reserved_cost_usd == pytest.approx(0.10)


def test_http_client_error_is_permanent_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_client(monkeypatch, status_code=401, text="bad key sk-abcdefghijklmnopqrstuvwxyz0123456789")

    with pytest.raises(PermanentProviderError) as excinfo:
        OpenAIWebSearchProvider("test-api-key", pricing_catalog=CATALOG).search("q")

    assert "OpenAI web search HTTP 401" in str(excinfo.value)
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in str(excinfo.value)


def test_search_key_falls_back_to_openai_model_key_only() -> None:
    assert search_api_key_for("openai_web_search", None, model_provider="openai", model_api_key="k") == "k"
    assert search_api_key_for("openai_web_search", "s", model_provider="openai", model_api_key="k") == "s"
    assert search_api_key_for("openai_web_search", None, model_provider="xai", model_api_key="k") is None
    assert search_api_key_for("tavily", None, model_provider="openai", model_api_key="k") is None


def _settings(**overrides: Any) -> dict[str, Any]:
    return {
        "model_provider": "openai",
        "model_name": "gpt-5-mini",
        "model_api_key": "test-model-key",
        "search_provider": "openai_web_search",
        "search_api_key": None,
        "max_cost_usd": 3.0,
        **overrides,
    }


def test_live_context_accepts_openai_web_search_with_model_key() -> None:
    context = resolve_execution_context(
        requested_mode="live",
        profile_id="three_track_strict_forecaster_v1",
        settings=_settings(),
    )

    assert readiness(_settings())["live"] == {"ready": True, "reasons": []}
    assert context.search_provider == "openai_web_search"
    assert context.search_is_mock is False
    assert context.estimated_search_upper_bound_cost_usd == pytest.approx(12 * 0.10)
    assert context.estimate_exceeds_ceiling is False
    provider = build_search_provider("ignored", "test-model-key", execution=context)
    assert isinstance(provider, OpenAIWebSearchProvider)


def test_live_context_requires_a_key_when_model_is_not_openai() -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        resolve_execution_context(
            requested_mode="live",
            profile_id="three_track_strict_forecaster_v1",
            settings=_settings(model_provider="xai", model_name="grok-4"),
        )

    assert "search_api_key_missing" in excinfo.value.reasons


def test_failed_response_status_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_client(monkeypatch, payload={"status": "failed", "error": {"code": "server_error"}, "output": []})

    with pytest.raises(TransientProviderError, match="server_error"):
        OpenAIWebSearchProvider("test-api-key", pricing_catalog=CATALOG).search("q")
