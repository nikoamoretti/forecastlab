from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select

from forecastlab.graph_execution import run_graph_forecast_engine
from forecastlab.providers.base import ChatResult
from forecastlab.schemas import (
    ForecastContract,
    ForecastGraph,
    ForecastNode,
    ForecastProfile,
    ModelUsage,
    SearchHit,
)
from forecastlab_api.models import (
    EvidenceClaimRow,
    ForecastAggregationRow,
    ForecastGraphRow,
    ForecastNodeRunRow,
    ForecastVersion,
    ResearchTrack,
)


class ContextCapturingModel:
    name = "stub"
    model = "stub-v1"

    def __init__(self, events: list[str]) -> None:
        self.forecast_contexts: list[dict[str, Any]] = []
        self.events = events

    def complete_json(self, **kwargs: Any) -> ChatResult:
        schema_name = kwargs["schema_name"]
        payload = json.loads(kwargs["user"])
        if schema_name == "evidence_claims":
            self.events.append("extract")
            excerpt = payload["document"]["text"][:120]
            result = {
                "claims": [
                    {
                        "claim": excerpt,
                        "excerpt": excerpt,
                        "supports_or_refutes": "supports",
                        "confidence": 0.9,
                        "source_quality": 0.9,
                        "primary_source": True,
                    }
                ]
            }
        elif schema_name == "forecast_node":
            self.events.append("forecast")
            self.forecast_contexts.append(payload)
            claim_id = payload["evidence_claims"][0]["id"]
            result = {
                "probability": 0.41,
                "reasoning": f"The estimate is supported by Evidence Claim {claim_id}.",
                "supporting_claim_ids": [claim_id],
                "opposing_claim_ids": [],
                "uncertainty_notes": ["Only one eligible source is available."],
            }
        else:
            raise AssertionError(f"Unexpected schema: {schema_name}")
        return ChatResult(
            content=json.dumps(result),
            parsed=result,
            usage=ModelUsage(model=self.model, provider=self.name),
        )


class FixtureSearch:
    name = "stub-search"

    def search(self, _query: str, *, max_results: int = 5) -> list[SearchHit]:
        return [
            SearchHit(
                title="BLS Employment Situation technical note",
                url="https://fixtures.forecastlab.local/bls-employment-situation",
                snippet="Official U-3 methodology.",
                published_at=datetime(2024, 6, 7, tzinfo=UTC),
                score=1.0,
                source_class="primary",
            )
        ][:max_results]


def _contract() -> ForecastContract:
    return ForecastContract(
        id="contract-v1",
        question_id="question-v1",
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
        created_by="test",
        original_question="Will US unemployment exceed 5% before June 2027?",
        normalized_question="Will US U-3 unemployment exceed 5% before 30 June 2027?",
        yes_condition="BLS publishes U-3 above 5.0% before the deadline.",
        no_condition="BLS does not publish U-3 above 5.0% before the deadline.",
        resolution_date=datetime(2027, 6, 30, tzinfo=UTC),
        authoritative_source="Bureau of Labor Statistics",
        resolution_method="Check official BLS releases through the deadline.",
        status="approved",
    )


def _graph() -> ForecastGraph:
    node = ForecastNode(
        id="node-v1",
        graph_id="graph-v1",
        question="What historical base rate applies?",
        node_type="base_rate",
        importance_weight=0.9,
        preferred_sources=["BLS"],
        required_output_type="probability",
    )
    return ForecastGraph(
        id="graph-v1",
        contract_id="contract-v1",
        status="approved",
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
        generation_model="stub-v1",
        root_question=_contract().normalized_question,
        nodes=[node],
    )


def test_forecasting_model_receives_contract_node_and_claims_not_raw_webpages() -> None:
    events: list[str] = []
    model = ContextCapturingModel(events)
    profile = ForecastProfile(
        id="graph-test",
        label="Graph test",
        description="Test profile",
        execution_strategy="graph_nodes",
        aggregation_method="dependency_discounted_weighted_mean_v1",
        tracks=["single_agent"],
        subquestions_per_track=1,
        search_results_per_subquestion=1,
        fetches_per_subquestion=1,
        max_model_calls=5,
        max_search_calls=1,
        max_fetched_documents=1,
        max_tokens=20_000,
        max_estimated_cost_usd=1,
    )

    result = run_graph_forecast_engine(
        contract=_contract(),
        graph=_graph(),
        profile_id=profile.id,
        mode="demo",
        as_of=None,
        model=model,
        search=FixtureSearch(),
        run_id="run-v1",
        profile=profile,
        persist_research=lambda _node, _evidence, _rejected, _claims: events.append("persist"),
    )

    assert events == ["extract", "persist", "forecast"]
    assert len(model.forecast_contexts) == 1
    context = model.forecast_contexts[0]
    assert set(context) == {"forecast_contract", "node_question", "evidence_claims"}
    assert context["forecast_contract"]["id"] == "contract-v1"
    assert context["node_question"] == "What historical base rate applies?"
    assert len(context["evidence_claims"]) == 1
    assert "document" not in context
    assert "text" not in context
    assert result.nodes[0].node_run is not None
    assert result.nodes[0].node_run.probability == 0.41
    assert result.nodes[0].node_run.uncertainty_notes == ["Only one eligible source is available."]
    assert result.nodes[0].node_run.model_used == "stub:stub-v1"
    assert result.nodes[0].node_run.confidence > 0
    assert result.nodes[0].node_run.normalized_weight == 1.0
    assert result.nodes[0].node_run.probability_contribution == result.nodes[0].node_run.probability
    assert result.aggregation.method == "dependency_discounted_weighted_mean_v1"
    assert result.aggregation.ensemble_probability is not None


def test_v1_execution_requires_approved_contract(client) -> None:
    question = client.post(
        "/api/questions",
        json={"question": "Will US unemployment exceed 5% before June 2027?"},
    ).json()

    response = client.post(f"/api/forecasts/{question['id']}/execute-v1", json={"mode": "demo"})

    assert response.status_code == 422
    assert "approved_forecast_contract_required" in response.json()["reasons"]


def test_v1_execution_generates_graph_persists_claims_node_runs_and_final_forecast(client) -> None:
    draft = client.post(
        "/api/contracts/generate",
        json={"question": "Will the US unemployment rate exceed 5% before 30 June 2027?"},
    ).json()
    client.post(f"/api/contracts/{draft['id']}/approve").raise_for_status()

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ForecastGraphRow)) == 0

    response = client.post(
        f"/api/forecasts/{draft['question_id']}/node-runs",
        json={"mode": "demo"},
    )

    assert response.status_code == 200
    run = response.json()
    assert run["profile_id"] == "graph_forecaster_v1"
    assert run["status"] == "completed"
    assert len(run["node_runs"]) == 7
    run_id = run["run_id"]

    with main_mod.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ForecastGraphRow)) == 1
        assert session.scalar(
            select(func.count()).select_from(ForecastNodeRunRow).where(
                ForecastNodeRunRow.forecast_run_id == run_id
            )
        ) == 7
        assert session.scalar(select(func.count()).select_from(EvidenceClaimRow)) == 7
        version = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run_id))
        assert version is not None
        assert version.ensemble_probability is not None
        assert version.trigger_event == "graph_forecaster_v1"
        aggregation = session.scalar(
            select(ForecastAggregationRow).where(ForecastAggregationRow.forecast_run_id == run_id)
        )
        assert aggregation is not None
        assert aggregation.final_probability == version.ensemble_probability

    node_runs = client.get(f"/api/forecasts/{draft['question_id']}/node-runs")
    assert node_runs.status_code == 200
    node_payload = node_runs.json()
    assert node_payload["run_id"] == run_id
    assert len(node_payload["node_runs"]) == 7
    assert all(item["supporting_claim_ids"] for item in node_payload["node_runs"])
    assert all(item["run_id"] == run_id for item in node_payload["node_runs"])
    assert all(item["uncertainty_notes"] for item in node_payload["node_runs"])
    assert all(item["model_used"] == "mock:mock-forecast-v1" for item in node_payload["node_runs"])
    assert sum(item["normalized_weight"] for item in node_payload["node_runs"]) == pytest.approx(1.0)
    assert all(item["confidence"] > 0 for item in node_payload["node_runs"])

    report = client.get(f"/api/questions/{draft['question_id']}/report").json()
    latest = report["latest_run"]
    assert len(latest["node_runs"]) == 7
    assert len(latest["evidence_claims"]) == 7
    assert latest["forecast_contract"]["status"] == "approved"
    assert latest["forecast_graph"]["status"] == "approved"
    assert latest["aggregation"]["method"] == "importance_weighted_log_odds_v1"
    assert latest["forecast_aggregation"]["method"] == "importance_weighted_log_odds_v1"
    assert latest["aggregation"]["calculation_trace"][-1]["step"] == "final"
    v1_report = report["v1_report"]
    assert v1_report["final_probability"] == report["latest_probability"]
    assert len(v1_report["nodes"]) == 7
    assert all(node["question"] for node in v1_report["nodes"])
    assert all(node["supporting_evidence"] for node in v1_report["nodes"])
    assert all(node["uncertainty_notes"] for node in v1_report["nodes"])
    assert all(node["model_used"] == "mock:mock-forecast-v1" for node in v1_report["nodes"])
    assert v1_report["evidence_coverage"]["rate"] == 1.0
    assert v1_report["calculation"]["trace"][-1]["step"] == "final"

    markdown = client.get(f"/api/questions/{draft['question_id']}/export.md").text
    assert "Normalized weight:" in markdown
    assert "Weighted log-odds contribution:" in markdown
    assert "Supporting evidence:" in markdown
    assert "Uncertainty:" in markdown
    assert "Model used: mock:mock-forecast-v1" in markdown
    assert "Calculation trace" in markdown


def test_legacy_forecast_execution_remains_available(client) -> None:
    question = client.post(
        "/api/questions",
        json={
            "question": "Will the US unemployment rate exceed 5% before 30 June 2027?",
            "profile_id": "three_track_ensemble",
            "mode": "demo",
            "start": True,
        },
    ).json()
    run_id = question["runs"][0]["id"]

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        assert session.scalar(
            select(func.count()).select_from(ResearchTrack).where(ResearchTrack.run_id == run_id)
        ) == 3
        assert session.scalar(
            select(func.count()).select_from(ForecastNodeRunRow).where(
                ForecastNodeRunRow.forecast_run_id == run_id
            )
        ) == 0
        assert session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run_id)) is not None
