from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from forecastlab.errors import TransientProviderError
from forecastlab.execution import resolve_execution_context
from forecastlab.ledger import InMemoryUsageLedger
from forecastlab.pricing import estimate_call_cost, lookup_rate
from forecastlab.providers.factory import build_model_provider
from forecastlab.providers.openai_compatible import OpenAICompatibleProvider

CATALOG = {
    "providers": {
        "xai": {"grok-test": {"input_per_million": 5.0, "output_per_million": 15.0}},
        "openai": {"gpt-test": {"input_per_million": 5.0, "output_per_million": 15.0}},
        "openai_compatible": {"unknown": {"input_per_million": 9.0, "output_per_million": 27.0}},
    }
}


class _TimeoutClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> _TimeoutClient:
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def post(self, *_args, **_kwargs):
        raise httpx.TimeoutException("timed out")


class _SuccessClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> _SuccessClient:
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def post(self, *_args, **_kwargs):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "id": "req-1",
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        }
        response.headers = {"x-request-id": "rid-1"}
        response.elapsed.total_seconds.return_value = 0.01
        return response


def _provider(provider_id: str, model: str, ledger: InMemoryUsageLedger, attempt_id: str) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        api_key="sk-test",
        base_url="https://api.example.test/v1",
        model=model,
        ledger=ledger,
        run_id="run-1",
        run_attempt_id=attempt_id,
        pricing_catalog=CATALOG,
        provider_id=provider_id,
    )


def test_failed_real_model_request_reserves_input_and_output(monkeypatch) -> None:
    monkeypatch.setattr("forecastlab.physical.time.sleep", lambda _s: None)
    monkeypatch.setattr("forecastlab.providers.openai_compatible.httpx.Client", _TimeoutClient)
    ledger = InMemoryUsageLedger()
    attempt = ledger.begin_attempt(run_id="run-1", job_id=None, attempt_number=1)
    provider = _provider("xai", "grok-test", ledger, attempt.id)
    with pytest.raises(TransientProviderError):
        provider.complete_json(
            system="sys " * 20,
            user="user " * 20,
            schema_name="research_plan",
            max_output_tokens=100,
            estimated_input_tokens=80,
        )
    rows = ledger.entries("run-1")
    assert rows
    expected = estimate_call_cost("xai", "grok-test", 80, 100, catalog=CATALOG)
    assert expected > 0
    for row in rows:
        assert row.status == "failed"
        assert row.reserved_input_tokens == 80
        assert row.reserved_output_tokens == 100
        assert row.reserved_cost_usd == pytest.approx(expected)
        assert row.actual_prompt_tokens == 80
        assert row.actual_completion_tokens == 100
        assert row.actual_cost_usd == pytest.approx(expected)
        assert row.provider == "xai"


def test_failed_real_model_cost_includes_input_and_output_reservation(monkeypatch) -> None:
    monkeypatch.setattr("forecastlab.physical.time.sleep", lambda _s: None)
    monkeypatch.setattr("forecastlab.providers.openai_compatible.httpx.Client", _TimeoutClient)
    ledger = InMemoryUsageLedger()
    attempt = ledger.begin_attempt(run_id="run-1", job_id=None, attempt_number=1)
    provider = _provider("openai", "gpt-test", ledger, attempt.id)
    with pytest.raises(TransientProviderError):
        provider.complete_json(
            system="s",
            user="u",
            schema_name="x",
            max_output_tokens=200,
            estimated_input_tokens=50,
        )
    totals = ledger.totals("run-1")
    reserved = estimate_call_cost("openai", "gpt-test", 50, 200, catalog=CATALOG)
    assert reserved > 0
    assert totals.total_cost_usd == pytest.approx(reserved * 3)
    assert totals.prompt_tokens == 50 * 3
    assert totals.completion_tokens == 200 * 3


def test_xai_identity_remains_xai_in_ledger_usage_and_reports(monkeypatch) -> None:
    monkeypatch.setattr("forecastlab.providers.openai_compatible.httpx.Client", _SuccessClient)
    ledger = InMemoryUsageLedger()
    attempt = ledger.begin_attempt(run_id="run-1", job_id=None, attempt_number=1)
    provider = build_model_provider(
        provider="xai",
        api_key="sk-test",
        base_url="https://api.x.ai/v1",
        model="grok-test",
        timeout=5,
        ledger=ledger,
        run_id="run-1",
        run_attempt_id=attempt.id,
        pricing_catalog=CATALOG,
    )
    assert provider.name == "xai"
    result = provider.complete_json(
        system="s",
        user="u",
        schema_name="x",
        max_output_tokens=32,
        estimated_input_tokens=16,
    )
    assert result.usage.provider == "xai"
    assert result.usage.model == "grok-test"
    row = ledger.entries("run-1")[0]
    assert row.provider == "xai"
    assert lookup_rate("xai", "grok-test", catalog=CATALOG) is not None
    assert lookup_rate("openai_compatible", "grok-test", catalog=CATALOG)["fallback"] is True
    context = resolve_execution_context(
        requested_mode="live",
        profile_id="live_smoke_v1",
        settings={
            "model_provider": "xai",
            "model_name": "grok-test",
            "model_api_key": "sk-test",
            "search_provider": "tavily",
            "search_api_key": "tvly-test",
            "max_cost_usd": 1.0,
        },
    )
    assert context.model_provider == "xai"
    report = SimpleNamespace(
        model_provider=context.model_provider,
        ledger_provider=row.provider,
        usage_provider=result.usage.provider,
        budget_provider=context.model_provider,
    )
    assert report.model_provider == "xai"
    assert report.ledger_provider == "xai"
    assert report.usage_provider == "xai"
    assert "openai_compatible" not in {report.model_provider, report.ledger_provider, report.usage_provider}


def test_openai_identity_remains_openai(monkeypatch) -> None:
    monkeypatch.setattr("forecastlab.providers.openai_compatible.httpx.Client", _SuccessClient)
    ledger = InMemoryUsageLedger()
    attempt = ledger.begin_attempt(run_id="run-1", job_id=None, attempt_number=1)
    provider = build_model_provider(
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model="gpt-test",
        timeout=5,
        ledger=ledger,
        run_id="run-1",
        run_attempt_id=attempt.id,
        pricing_catalog=CATALOG,
    )
    result = provider.complete_json(
        system="s",
        user="u",
        schema_name="x",
        max_output_tokens=16,
        estimated_input_tokens=8,
    )
    assert provider.name == "openai"
    assert result.usage.provider == "openai"
    assert ledger.entries("run-1")[0].provider == "openai"
    context = resolve_execution_context(
        requested_mode="live",
        profile_id="live_smoke_v1",
        settings={
            "model_provider": "openai",
            "model_name": "gpt-test",
            "model_api_key": "sk-test",
            "search_provider": "tavily",
            "search_api_key": "tvly-test",
            "max_cost_usd": 1.0,
        },
    )
    assert context.model_provider == "openai"
