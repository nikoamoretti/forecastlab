from __future__ import annotations

from datetime import UTC, datetime

import pytest

from forecastlab.errors import ConfigurationError
from forecastlab.execution import configuration_hash, readiness, resolve_execution_context
from forecastlab.prompts import prompt_hashes


def test_demo_always_uses_mock() -> None:
    context = resolve_execution_context(
        requested_mode="demo",
        profile_id="three_track_ensemble",
        settings={
            "model_provider": "xai",
            "model_name": "grok-4",
            "model_api_key": "sk-secret",
            "search_provider": "tavily",
            "search_api_key": "tvly-secret",
        },
    )
    assert context.effective_mode == "demo"
    assert context.model_is_mock is True
    assert context.search_is_mock is True
    assert context.fixture_evidence_allowed is True
    assert context.model_provider == "mock"


def test_live_requires_keys() -> None:
    with pytest.raises(ConfigurationError) as missing_model:
        resolve_execution_context(
            requested_mode="live",
            profile_id="three_track_ensemble",
            settings={"model_provider": "openai_compatible", "model_name": "gpt-4.1", "search_provider": "tavily"},
        )
    assert "model_api_key_missing" in missing_model.value.reasons
    with pytest.raises(ConfigurationError) as missing_search:
        resolve_execution_context(
            requested_mode="live",
            profile_id="three_track_ensemble",
            settings={
                "model_provider": "openai_compatible",
                "model_name": "gpt-4.1",
                "model_base_url": "https://api.openai.com/v1",
                "model_api_key": "sk-test",
                "search_provider": "tavily",
            },
        )
    assert "search_api_key_missing" in missing_search.value.reasons


def test_live_never_falls_back_to_mock() -> None:
    with pytest.raises(ConfigurationError):
        resolve_execution_context(
            requested_mode="live",
            profile_id="three_track_ensemble",
            settings={"model_provider": "mock", "search_provider": "mock"},
        )


def test_backtest_requires_as_of() -> None:
    with pytest.raises(ConfigurationError) as exc:
        resolve_execution_context(
            requested_mode="backtest",
            profile_id="three_track_ensemble",
            settings={"model_provider": "mock"},
        )
    assert "as_of_required" in exc.value.reasons


def test_configuration_hash_changes_with_model_or_prompt() -> None:
    left = configuration_hash({"model_name": "a", "prompt_hashes": {"x": "1"}})
    right = configuration_hash({"model_name": "b", "prompt_hashes": {"x": "1"}})
    assert left != right
    hashes = prompt_hashes()
    mutated = dict(hashes)
    mutated["operationalize"] = "changed"
    assert configuration_hash({"prompt_hashes": hashes}) != configuration_hash({"prompt_hashes": mutated})


def test_cost_ceiling_changes_effective_profile() -> None:
    high = resolve_execution_context(
        requested_mode="demo",
        profile_id="three_track_ensemble",
        settings={"max_cost_usd": 50},
    )
    low = resolve_execution_context(
        requested_mode="demo",
        profile_id="three_track_ensemble",
        settings={"max_cost_usd": 0.01},
    )
    assert low.effective_max_cost_usd <= 0.01
    assert low.effective_max_cost_usd < high.effective_max_cost_usd


def test_readiness_hides_secrets() -> None:
    payload = readiness({"model_api_key": "sk-secret", "search_api_key": "tvly-secret"})
    assert "sk-secret" not in str(payload)
    assert payload["demo"]["ready"] is True


def test_estimate_over_ceiling_rejects_live() -> None:
    with pytest.raises(ConfigurationError) as exc:
        resolve_execution_context(
            requested_mode="live",
            profile_id="three_track_ensemble",
            settings={
                "model_provider": "xai",
                "model_name": "grok-4",
                "model_api_key": "sk-test",
                "search_provider": "tavily",
                "search_api_key": "tvly-test",
                "max_cost_usd": 0.0001,
            },
        )
    assert "estimate_exceeds_cost_ceiling" in exc.value.reasons


def test_synthetic_backtest_is_explicit() -> None:
    context = resolve_execution_context(
        requested_mode="backtest",
        profile_id="three_track_ensemble",
        settings={"model_provider": "mock"},
        synthetic_fixture_run=True,
        as_of=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert context.synthetic_fixture_run is True
    assert context.model_is_mock is True
    assert context.evidence_policy == "synthetic_historical_fixtures"
