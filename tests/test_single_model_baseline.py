from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import func, select

from forecastlab.errors import StructuredOutputError
from forecastlab.execution import estimate_workload
from forecastlab.profiles import load_profile
from forecastlab.providers.base import ChatResult
from forecastlab.providers.mock import MockModelProvider, MockSearchProvider
from forecastlab.schemas import ForecastContract, ModelUsage
from forecastlab.single_model import DIRECT_MODEL_METHOD, run_single_model_forecast
from forecastlab.timeutil import utcnow
from forecastlab_api.models import (
    EvidenceClaimRow,
    EvidenceItem,
    ForecastAggregationRow,
    ForecastContractRow,
    ForecastGraphRow,
    ForecastNodeRunRow,
    ForecastRun,
    ForecastVersion,
    ProviderCallLedger,
    ResearchTrack,
)


def _approved_contract() -> ForecastContract:
    return ForecastContract(
        id="contract-single-model",
        question_id="question-single-model",
        version=1,
        created_at=utcnow(),
        created_by="test",
        original_question="Will US unemployment exceed 5% before 30 June 2027?",
        normalized_question="Will the US U-3 unemployment rate reach 5% before 30 June 2027?",
        yes_condition="BLS reports seasonally adjusted U-3 at or above 5% by 30 June 2027.",
        no_condition="BLS does not report seasonally adjusted U-3 at or above 5% by 30 June 2027.",
        resolution_date=utcnow().replace(year=2027),
        authoritative_source="Bureau of Labor Statistics Employment Situation",
        resolution_method="Use the first official BLS U-3 release available by the deadline.",
        initial_reference_class="US labor-market threshold forecasts over comparable horizons.",
        status="approved",
    )


class RecordingModel(MockModelProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, **kwargs: Any) -> ChatResult:
        self.calls.append(kwargs)
        return super().complete_json(**kwargs)


class InvalidSingleModel:
    name = "invalid-single-model"

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, **_kwargs: Any) -> ChatResult:
        self.calls += 1
        return ChatResult(
            content="{}",
            parsed={
                "probability": 1.2,
                "reasoning": "Invalid probability for validation testing.",
                "uncertainty": ["Still uncertain."],
                "evidence_ids": [],
            },
            usage=ModelUsage(model=self.name, provider=self.name),
        )


def test_single_model_profile_is_isolated_from_existing_profiles() -> None:
    single = load_profile("single_model_forecaster_v1")
    three_track = load_profile("three_track_forecaster")
    graph = load_profile("graph_forecaster_v1")
    ceiling_fields = (
        "max_model_calls",
        "max_search_calls",
        "max_fetched_documents",
        "max_tokens",
        "max_estimated_cost_usd",
        "max_wall_clock_seconds",
    )

    assert single.execution_strategy == "single_model"
    assert single.aggregation_method == DIRECT_MODEL_METHOD
    assert single.graph_generation_enabled is False
    assert single.evidence_claims_enabled is False
    assert single.node_forecasting_enabled is False
    assert single.graph_aggregation_enabled is False
    assert estimate_workload(single)["planned_model_calls"] == 1
    assert estimate_workload(single)["subquestions"] == 0
    assert three_track.execution_strategy == "legacy_tracks"
    assert graph.execution_strategy == "graph_nodes"
    assert {field: getattr(single, field) for field in ceiling_fields} == {
        field: getattr(three_track, field) for field in ceiling_fields
    } == {field: getattr(graph, field) for field in ceiling_fields}


def test_single_model_forecast_uses_one_contract_and_evidence_call() -> None:
    model = RecordingModel()

    result = run_single_model_forecast(
        contract=_approved_contract(),
        profile_id="single_model_forecaster_v1",
        mode="demo",
        as_of=None,
        model=model,
        search=MockSearchProvider(),
        run_id="single-model-domain-run",
    )

    assert len(model.calls) == 1
    assert model.calls[0]["schema_name"] == "single_model_forecast"
    payload = json.loads(model.calls[0]["user"])
    assert payload["forecast_contract"]["status"] == "approved"
    assert payload["evidence_packet"]
    assert "graph" not in payload
    assert "nodes" not in payload
    assert result.aggregation.method == DIRECT_MODEL_METHOD
    assert result.aggregation.ensemble_probability == 0.36
    assert result.aggregation.formula.endswith("no probability aggregation is performed.")
    assert result.budget["model_calls"] == 1
    assert result.tracks[0].track_type == "single_agent"
    assert result.tracks[0].plan is None
    assert result.tracks[0].forecast is not None
    assert result.tracks[0].forecast.unresolved_uncertainties


def test_single_model_invalid_structured_output_is_not_retried() -> None:
    model = InvalidSingleModel()

    with pytest.raises(StructuredOutputError, match="invalid_structured_output"):
        run_single_model_forecast(
            contract=_approved_contract(),
            profile_id="single_model_forecaster_v1",
            mode="demo",
            as_of=None,
            model=model,
            search=MockSearchProvider(),
            run_id="single-model-invalid-run",
        )

    assert model.calls == 1


def test_single_model_api_persists_forecast_without_graph_claims_or_aggregation(client) -> None:
    draft = client.post(
        "/api/contracts/generate",
        json={
            "question": "Will the US unemployment rate exceed 5% before 30 June 2027?",
            "profile_id": "single_model_forecaster_v1",
            "mode": "demo",
        },
    ).json()
    approved = client.post(f"/api/contracts/{draft['id']}/approve")
    assert approved.status_code == 200

    response = client.post(
        f"/api/questions/{draft['question_id']}/runs",
        json={"profile_id": "single_model_forecaster_v1", "mode": "demo"},
    )

    assert response.status_code == 200
    run_payload = response.json()
    assert run_payload["status"] == "completed"
    run_id = run_payload["id"]

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        run = session.get(ForecastRun, run_id)
        assert run is not None
        assert json.loads(run.execution_context_json)["forecast_contract_id"] == draft["id"]
        assert json.loads(run.aggregation_json)["method"] == DIRECT_MODEL_METHOD
        assert session.scalar(
            select(func.count()).select_from(ForecastVersion).where(ForecastVersion.run_id == run_id)
        ) == 1
        tracks = session.scalars(select(ResearchTrack).where(ResearchTrack.run_id == run_id)).all()
        assert len(tracks) == 1
        assert tracks[0].track_type == "single_agent"
        assert tracks[0].reasoning_summary
        assert json.loads(tracks[0].unresolved_json)
        accepted_evidence = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.run_id == run_id,
                EvidenceItem.rejected.is_(False),
                EvidenceItem.as_of_eligible.is_(True),
            )
        ).all()
        assert accepted_evidence
        model_calls = session.scalars(
            select(ProviderCallLedger).where(
                ProviderCallLedger.run_id == run_id,
                ProviderCallLedger.provider_type == "model",
            )
        ).all()
        assert len(model_calls) == 1
        assert model_calls[0].stage == "single_model_forecast"
        contract = session.get(ForecastContractRow, draft["id"])
        assert contract is not None
        assert session.scalar(
            select(func.count()).select_from(ForecastGraphRow).where(
                ForecastGraphRow.contract_id == draft["id"]
            )
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(ForecastNodeRunRow).where(
                ForecastNodeRunRow.forecast_run_id == run_id
            )
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(ForecastAggregationRow).where(
                ForecastAggregationRow.forecast_run_id == run_id
            )
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(EvidenceClaimRow).join(EvidenceItem).where(
                EvidenceItem.run_id == run_id
            )
        ) == 0


def test_single_model_api_requires_approved_contract(client) -> None:
    question = client.post(
        "/api/questions",
        json={"question": "Will US unemployment exceed 5% before June 2027?"},
    ).json()

    response = client.post(
        f"/api/questions/{question['id']}/runs",
        json={"profile_id": "single_model_forecaster_v1", "mode": "demo"},
    )

    assert response.status_code == 422
    assert "approved_forecast_contract_required" in response.json()["reasons"]
