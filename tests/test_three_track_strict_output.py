from __future__ import annotations

import json
from typing import Any

import pytest
from tests.test_openai_compatible import _stub_client

from forecastlab.engine import run_forecast_engine, uses_strict_track_forecast
from forecastlab.errors import StructuredOutputError
from forecastlab.profiles import load_profile
from forecastlab.providers.base import ChatResult
from forecastlab.providers.mock import SAMPLE_QUESTION, MockModelProvider, MockSearchProvider
from forecastlab.providers.openai_compatible import OpenAICompatibleProvider
from forecastlab.structured_outputs import track_forecast_json_schema, validate_structured_output


class RecordingModel(MockModelProvider):
    """Mock model that records request options and can corrupt track estimates."""

    def __init__(self, *, invalid_track_responses: int = 0) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []
        self.invalid_track_responses = invalid_track_responses

    def complete_json(self, **kwargs: Any) -> ChatResult:
        self.calls.append(kwargs)
        result = super().complete_json(**kwargs)
        if kwargs["schema_name"] == "track_forecast" and self.invalid_track_responses > 0:
            self.invalid_track_responses -= 1
            payload = dict(result.parsed or {})
            payload["key_drivers"] = [
                {"factor": "Labor market", "direction": "positive", "importance": 0.5, "evidence_ids": [], "inference": True}
            ]
            return ChatResult(content=json.dumps(payload), parsed=payload, usage=result.usage)
        return result


def _run(profile_id: str, model: RecordingModel):
    return run_forecast_engine(
        question=SAMPLE_QUESTION,
        contract=None,
        profile_id=profile_id,
        mode="demo",
        as_of=None,
        model=model,
        search=MockSearchProvider(),
        allow_local_fixtures=True,
    )


def _track_calls(model: RecordingModel) -> list[dict[str, Any]]:
    return [call for call in model.calls if call["schema_name"] == "track_forecast"]


def _assert_strict_subset(schema: dict[str, Any]) -> None:
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        for child in schema["properties"].values():
            _assert_strict_subset(child)
    if schema.get("type") == "array":
        _assert_strict_subset(schema["items"])


STRICT_PROFILE = "three_track_strict_forecaster_v1"


def test_strict_profile_matches_frozen_comparator_except_transport_and_wall_clock() -> None:
    strict = load_profile(STRICT_PROFILE)
    frozen = load_profile("three_track_forecaster")
    assert uses_strict_track_forecast(strict)
    assert not uses_strict_track_forecast(frozen)
    assert not uses_strict_track_forecast(load_profile("three_track_ensemble"))
    assert strict.version == 2
    assert strict.max_wall_clock_seconds == load_profile("root_event_ensemble_v1").max_wall_clock_seconds == 300
    assert frozen.max_wall_clock_seconds == 180
    differing = {"id", "label", "description", "version", "max_wall_clock_seconds"}
    assert strict.model_dump(exclude=differing) == frozen.model_dump(exclude=differing)


def test_strict_profile_requests_track_estimates_with_bounded_schema() -> None:
    model = RecordingModel()
    result = _run(STRICT_PROFILE, model)

    assert all(track.forecast is not None for track in result.tracks)
    calls = _track_calls(model)
    assert len(calls) == 3
    for call, track in zip(calls, result.tracks, strict=True):
        expected_ids = [item["id"] for item in track.evidence]
        assert call["json_schema"] == track_forecast_json_schema(evidence_ids=expected_ids)


@pytest.mark.parametrize("profile_id", ["three_track_forecaster", "three_track_ensemble"])
def test_legacy_three_track_profiles_keep_json_object_requests(profile_id: str) -> None:
    model = RecordingModel()
    _run(profile_id, model)

    assert _track_calls(model)
    assert all("json_schema" not in call for call in model.calls)


def test_track_schema_meets_strict_subset_and_bounds_citations() -> None:
    schema = track_forecast_json_schema(evidence_ids=["ev-a", "ev-b"])
    _assert_strict_subset(schema)
    driver = schema["properties"]["key_drivers"]["items"]
    assert driver["properties"]["direction"]["enum"] == ["up", "down", "unclear"]
    assert driver["properties"]["evidence_ids"]["items"]["enum"] == ["ev-a", "ev-b"]

    empty = track_forecast_json_schema(evidence_ids=[])
    _assert_strict_subset(empty)
    assert empty["properties"]["key_drivers"]["items"]["properties"]["evidence_ids"]["maxItems"] == 0


def test_track_forecast_validation_reports_field_paths_without_values() -> None:
    payload = {
        "probability": 0.4,
        "prior_probability": None,
        "key_drivers": [
            {"factor": "Claims", "direction": "positive", "importance": 0.5, "evidence_ids": [], "inference": True}
        ],
        "counterarguments": [],
        "unresolved_uncertainties": [],
        "resolver_risk": 0.1,
        "evidence_quality": 0.5,
        "reasoning_summary": "Summary.",
    }
    validated, errors = validate_structured_output("track_forecast", payload)
    assert validated is None
    assert [error["field_path"] for error in errors] == ["key_drivers.0.direction"]
    assert "positive" not in json.dumps(errors)

    payload["key_drivers"][0]["direction"] = "up"
    validated, errors = validate_structured_output("track_forecast", payload)
    assert errors == []
    assert validated is not None and validated["key_drivers"][0]["direction"] == "up"


def test_strict_repair_prompt_names_failing_fields() -> None:
    model = RecordingModel(invalid_track_responses=1)
    result = _run(STRICT_PROFILE, model)

    assert all(track.forecast is not None for track in result.tracks)
    calls = _track_calls(model)
    assert len(calls) == 4
    repair_user = calls[1]["user"]
    assert "Validation errors:" in repair_user
    assert "key_drivers.0.direction" in repair_user
    assert calls[1]["json_schema"] == calls[0]["json_schema"]


def test_legacy_repair_prompt_is_unchanged() -> None:
    model = RecordingModel(invalid_track_responses=1)
    _run("three_track_ensemble", model)

    repair_user = _track_calls(model)[1]["user"]
    assert "Previous JSON failed validation. Return corrected JSON only." in repair_user
    assert "Validation errors:" not in repair_user


def test_repeated_invalid_track_output_still_fails_closed() -> None:
    model = RecordingModel(invalid_track_responses=2)
    with pytest.raises(StructuredOutputError, match="invalid_structured_output:track_forecast"):
        _run(STRICT_PROFILE, model)


@pytest.mark.parametrize(
    ("provider_id", "model", "strict"),
    [
        ("openai", "gpt-5-mini-2025-08-07", True),
        ("xai", "grok-4", False),
        ("openai_compatible", "gpt-5-mini-2025-08-07", False),
    ],
)
def test_provider_sends_strict_track_schema_only_to_supported_models(
    monkeypatch: pytest.MonkeyPatch,
    provider_id: str,
    model: str,
    strict: bool,
) -> None:
    requests = _stub_client(monkeypatch)
    schema = track_forecast_json_schema(evidence_ids=["ev-a"])
    provider = OpenAICompatibleProvider(
        api_key="test-api-key",
        base_url="https://provider.example.test/v1",
        model=model,
        provider_id=provider_id,
    )

    result = provider.complete_json(
        system="Return JSON.",
        user="Forecast this event.",
        schema_name="track_forecast",
        max_output_tokens=4096,
        json_schema=schema,
    )

    body = requests[0]["json"]
    assert "reasoning_effort" not in body
    if strict:
        assert body["response_format"] == {
            "type": "json_schema",
            "json_schema": {"name": "track_forecast", "strict": True, "schema": schema},
        }
        assert result.diagnostics is not None
        assert result.diagnostics.strict_schema_validation_succeeded is False
    else:
        assert body["response_format"] == {"type": "json_object"}
