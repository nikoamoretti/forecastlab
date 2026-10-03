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
    assert strict.version == 4
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


def test_strict_profile_requests_resolution_contract_with_strict_schema() -> None:
    from forecastlab.structured_outputs import resolution_contract_json_schema

    model = RecordingModel()
    _run(STRICT_PROFILE, model)
    contract_calls = [call for call in model.calls if call["schema_name"] == "resolution_contract"]
    assert len(contract_calls) == 1
    assert contract_calls[0]["json_schema"] == resolution_contract_json_schema()

    legacy = RecordingModel()
    _run("three_track_forecaster", legacy)
    assert all("json_schema" not in call for call in legacy.calls)


@pytest.mark.parametrize("schema_fn", ["resolution_contract_json_schema", "forecast_contract_json_schema"])
def test_contract_schemas_meet_strict_subset_and_require_iso_dates(schema_fn: str) -> None:
    import re

    from forecastlab import structured_outputs

    schema = getattr(structured_outputs, schema_fn)()
    _assert_strict_subset(schema)
    date_field = "resolution_deadline" if schema_fn == "resolution_contract_json_schema" else "resolution_date"
    pattern = re.compile(schema["properties"][date_field]["pattern"])
    for valid in ("2026-11-06", "2026-11-06T13:30:00Z", "2026-11-06T08:30:00-05:00", "2026-11-06T08:30"):
        assert pattern.fullmatch(valid), valid
    for invalid in ("2026-11-06 (the date of the Employment Situation news release).", "Nov 6, 2026", ""):
        assert not pattern.fullmatch(invalid), invalid


def test_question_compiler_requests_strict_forecast_contract() -> None:
    from forecastlab.contracts import QuestionCompiler
    from forecastlab.structured_outputs import forecast_contract_json_schema

    calls: list[dict[str, Any]] = []
    payload = {
        "normalized_question": "Will the U.S. unemployment rate for October 2026 exceed 4.2%?",
        "yes_condition": "The first published BLS rate is above 4.2%.",
        "no_condition": "The first published BLS rate is 4.2% or lower.",
        "resolution_date": "2026-11-06",
        "authoritative_source": "BLS Employment Situation",
        "fallback_sources": [],
        "resolution_method": "Compare the first published rate with 4.2%.",
        "ambiguity_notes": "",
        "cancellation_conditions": "",
        "resolver_risk_notes": "",
        "forecast_type": "binary",
        "geography": "United States",
        "units": "percent",
        "domain": "macro",
        "initial_reference_class": "",
        "suggested_drivers": [],
        "known_dependencies": [],
        "rejection_reasons": [],
    }

    class OpenAILikeModel:
        name = "openai"

        def complete_json(self, **kwargs: Any) -> ChatResult:
            calls.append(kwargs)
            return ChatResult(content=json.dumps(payload), parsed=payload, usage=MockModelProvider().complete_json(
                system="PROMPT_ID: x", user="{}", schema_name="x").usage)

    contract = QuestionCompiler(OpenAILikeModel()).compile(
        "Will the U.S. unemployment rate for October 2026 exceed 4.2%?", question_id="q"
    )
    assert calls[0]["schema_name"] == "forecast_contract"
    assert calls[0]["json_schema"] == forecast_contract_json_schema()
    assert contract.resolution_date is not None and contract.resolution_date.date().isoformat() == "2026-11-06"


@pytest.mark.parametrize("schema_name", ["resolution_contract", "forecast_contract"])
def test_provider_sends_strict_contract_schema_to_openai(monkeypatch: pytest.MonkeyPatch, schema_name: str) -> None:
    from forecastlab.structured_outputs import forecast_contract_json_schema, resolution_contract_json_schema

    requests = _stub_client(monkeypatch)
    schema = resolution_contract_json_schema() if schema_name == "resolution_contract" else forecast_contract_json_schema()
    OpenAICompatibleProvider(
        api_key="test-api-key",
        base_url="https://provider.example.test/v1",
        model="gpt-5-mini-2025-08-07",
        provider_id="openai",
    ).complete_json(system="Return JSON.", user="{}", schema_name=schema_name, max_output_tokens=4096, json_schema=schema)

    body = requests[0]["json"]
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "strict": True, "schema": schema},
    }
    assert "reasoning_effort" not in body


def test_strict_profile_requests_research_plans_with_strict_schema() -> None:
    from forecastlab.structured_outputs import research_plan_json_schema

    model = RecordingModel()
    _run(STRICT_PROFILE, model)
    plan_calls = [call for call in model.calls if call["schema_name"] == "research_plan"]
    assert len(plan_calls) == 3
    expected = research_plan_json_schema(max_subquestions=load_profile(STRICT_PROFILE).subquestions_per_track)
    assert all(call["json_schema"] == expected for call in plan_calls)

    legacy = RecordingModel()
    _run("three_track_forecaster", legacy)
    assert all("json_schema" not in call for call in legacy.calls)


def test_research_plan_schema_meets_strict_subset_and_bounds_lists() -> None:
    from forecastlab.schemas import ResearchPlan
    from forecastlab.structured_outputs import research_plan_json_schema

    schema = research_plan_json_schema(max_subquestions=4)
    _assert_strict_subset(schema)
    assert schema["properties"]["subquestions"]["minItems"] == 1
    assert schema["properties"]["subquestions"]["maxItems"] == 4
    subquestion = schema["properties"]["subquestions"]["items"]
    assert subquestion["properties"]["search_queries"]["minItems"] == 1
    assert set(schema["properties"]) == set(ResearchPlan.model_fields)
    assert set(subquestion["properties"]) == set(ResearchPlan.model_fields["subquestions"].annotation.__args__[0].model_fields)


def test_provider_sends_strict_research_plan_schema_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    from forecastlab.structured_outputs import research_plan_json_schema

    requests = _stub_client(monkeypatch)
    schema = research_plan_json_schema(max_subquestions=4)
    OpenAICompatibleProvider(
        api_key="test-api-key",
        base_url="https://provider.example.test/v1",
        model="gpt-5-mini-2025-08-07",
        provider_id="openai",
    ).complete_json(system="Return JSON.", user="{}", schema_name="research_plan", max_output_tokens=4096, json_schema=schema)

    assert requests[0]["json"]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "research_plan", "strict": True, "schema": schema},
    }
