from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlalchemy import select

from forecastlab.errors import ConfigurationError
from forecastlab.execution import resolve_execution_context
from forecastlab.hashing import content_hash
from forecastlab.profiles import PROFILES_DIR, load_profile
from forecastlab.providers.mock import MockModelProvider
from forecastlab.schemas import FetchedDocument, SearchHit
from forecastlab.timeutil import utcnow
from forecastlab_api.experiments import (
    DEFAULT_EXPERIMENT_PROFILES,
    V1_EVALUATION_PROFILES,
)
from forecastlab_api.forecast_experiments import CONTROLLED_FORECAST_PROFILES
from forecastlab_api.graph_executor import GraphForecastExecutor
from forecastlab_api.models import (
    EvidenceClaimRow,
    EvidenceItem,
    ForecastAggregationRow,
    ForecastNodeRunRow,
    ForecastRun,
    Question,
    ResearchPlanRow,
)

SMOKE_PROFILE_ID = "graph_live_smoke_v1"
GRAPH_PROFILE_SHA256 = "acdbb378b6c5afbd5c82e9085c7be92043b9057f3a7f5842d73a1cdd80f0f6d7"
SMOKE_PROFILE_SHA256 = "adfc0b3173e5e1617debc2dfb3412324b366d871a8f258ef02826c1364acd2e2"
LIVE_PROVIDER_METADATA = {
    "model_provider": "openai",
    "model_name": "gpt-5-mini-2025-08-07",
    "model_api_key": "test-key-never-sent",
    "search_provider": "tavily",
    "search_api_key": "test-key-never-sent",
}


def _live_settings(*, ceiling: float) -> dict[str, object]:
    return {**LIVE_PROVIDER_METADATA, "max_cost_usd": ceiling}


def _approved_contract(client) -> dict[str, object]:
    draft_response = client.post(
        "/api/contracts/generate",
        json={
            "question": (
                "Will the US unemployment rate exceed 5% before 30 June 2027?"
            ),
            "mode": "demo",
        },
    )
    draft_response.raise_for_status()
    draft = draft_response.json()
    approved = client.post(f"/api/contracts/{draft['id']}/approve")
    approved.raise_for_status()
    return draft


def test_graph_live_smoke_profile_loads_with_exact_graph_limits() -> None:
    smoke = load_profile(SMOKE_PROFILE_ID)
    graph = load_profile("graph_forecaster_v1")

    assert smoke.id == SMOKE_PROFILE_ID
    assert smoke.version == 2
    assert smoke.execution_strategy == "graph_nodes"
    assert smoke.graph_generation_enabled is True
    assert smoke.evidence_claims_enabled is True
    assert smoke.node_forecasting_enabled is True
    assert smoke.graph_aggregation_enabled is True
    assert smoke.aggregation_method == graph.aggregation_method
    assert smoke.prompt_versions == graph.prompt_versions
    assert graph.graph_generation_max_completion_tokens is None
    assert graph.graph_generation_max_visible_output_tokens is None
    assert graph.graph_generation_reasoning_effort is None
    assert graph.graph_generation_verbosity is None
    assert graph.max_candidate_fetch_attempts_per_node is None
    assert {
        "max_model_calls": smoke.max_model_calls,
        "max_search_calls": smoke.max_search_calls,
        "max_fetched_documents": smoke.max_fetched_documents,
        "max_tokens": smoke.max_tokens,
        "max_output_tokens_per_call": smoke.max_output_tokens_per_call,
        "graph_generation_max_completion_tokens": (
            smoke.graph_generation_max_completion_tokens
        ),
        "graph_generation_max_visible_output_tokens": (
            smoke.graph_generation_max_visible_output_tokens
        ),
        "graph_generation_reasoning_effort": (
            smoke.graph_generation_reasoning_effort
        ),
        "graph_generation_verbosity": smoke.graph_generation_verbosity,
        "max_candidate_fetch_attempts_per_node": (
            smoke.max_candidate_fetch_attempts_per_node
        ),
        "max_estimated_cost_usd": smoke.max_estimated_cost_usd,
        "max_wall_clock_seconds": smoke.max_wall_clock_seconds,
    } == {
        "max_model_calls": 14,
        "max_search_calls": 4,
        "max_fetched_documents": 8,
        "max_tokens": 50_000,
        "max_output_tokens_per_call": 1_536,
        "graph_generation_max_completion_tokens": 8_192,
        "graph_generation_max_visible_output_tokens": 1_536,
        "graph_generation_reasoning_effort": "minimal",
        "graph_generation_verbosity": "low",
        "max_candidate_fetch_attempts_per_node": 2,
        "max_estimated_cost_usd": 0.50,
        "max_wall_clock_seconds": 300,
    }


def test_graph_forecaster_profile_is_byte_for_byte_unchanged() -> None:
    digest = hashlib.sha256(
        (PROFILES_DIR / "graph_forecaster_v1.yaml").read_bytes()
    ).hexdigest()
    assert digest == GRAPH_PROFILE_SHA256


def test_graph_live_smoke_profile_matches_intentional_v2_bytes() -> None:
    digest = hashlib.sha256(
        (PROFILES_DIR / "graph_live_smoke_v1.yaml").read_bytes()
    ).hexdigest()
    assert digest == SMOKE_PROFILE_SHA256


def test_smoke_profile_is_not_a_default_scientific_evaluation_profile() -> None:
    assert SMOKE_PROFILE_ID not in CONTROLLED_FORECAST_PROFILES
    assert SMOKE_PROFILE_ID not in V1_EVALUATION_PROFILES
    assert SMOKE_PROFILE_ID not in DEFAULT_EXPERIMENT_PROFILES


def test_live_smoke_preflight_includes_model_and_search_cost_and_passes_at_fifty_cents() -> None:
    context = resolve_execution_context(
        requested_mode="live",
        profile_id=SMOKE_PROFILE_ID,
        settings=_live_settings(ceiling=0.50),
    )

    assert context.estimated_model_upper_bound_cost_usd == pytest.approx(0.400)
    assert context.estimated_search_upper_bound_cost_usd == pytest.approx(0.032)
    assert context.estimated_upper_bound_cost_usd == pytest.approx(0.432)
    assert context.effective_max_cost_usd == pytest.approx(0.50)
    assert context.estimate_exceeds_ceiling is False
    assert context.cost_estimate_unavailable_reasons == []
    assert context.model_is_mock is False
    assert context.search_is_mock is False
    assert context.fixture_evidence_allowed is False
    assert context.synthetic_fixture_run is False
    assert context.graph_generation_max_completion_tokens == 8_192
    assert context.graph_generation_max_visible_output_tokens == 1_536
    assert context.graph_generation_reasoning_effort == "minimal"
    assert context.graph_generation_verbosity == "low"
    assert context.max_candidate_fetch_attempts_per_node == 2


def test_execution_preview_exposes_the_cost_breakdown(client) -> None:
    saved = client.put(
        "/api/settings",
        json={**LIVE_PROVIDER_METADATA, "max_cost_usd": 0.50},
    )
    assert saved.status_code == 200

    response = client.get(
        "/api/execution/preview",
        params={"profile_id": SMOKE_PROFILE_ID, "mode": "live"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    context = payload["context"]
    assert context["estimated_model_upper_bound_cost_usd"] == pytest.approx(0.400)
    assert context["estimated_search_upper_bound_cost_usd"] == pytest.approx(0.032)
    assert context["estimated_upper_bound_cost_usd"] == pytest.approx(0.432)


def test_smoke_fails_closed_at_twenty_five_cents_before_forecast_run(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = _approved_contract(client)
    saved = client.put(
        "/api/settings",
        json={**LIVE_PROVIDER_METADATA, "max_cost_usd": 0.25},
    )
    assert saved.status_code == 200

    def provider_call_forbidden(*_args, **_kwargs):
        raise AssertionError("provider construction must not occur after failed preflight")

    monkeypatch.setattr(
        "forecastlab_api.pipeline.build_model_provider",
        provider_call_forbidden,
    )
    monkeypatch.setattr(
        "forecastlab_api.pipeline.build_search_provider",
        provider_call_forbidden,
    )

    response = client.post(
        f"/api/questions/{draft['question_id']}/runs",
        json={"profile_id": SMOKE_PROFILE_ID, "mode": "live"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["reasons"] == ["estimate_exceeds_cost_ceiling"]
    assert SMOKE_PROFILE_ID in payload["detail"]
    assert "model=$0.400000" in payload["detail"]
    assert "search=$0.032000" in payload["detail"]
    assert "total=$0.432000" in payload["detail"]
    assert "ceiling=$0.250000" in payload["detail"]
    assert payload["preflight"]["estimated_upper_bound_cost_usd"] == pytest.approx(
        0.432
    )
    question = client.get(f"/api/questions/{draft['question_id']}").json()
    assert question["runs"] == []


def test_graph_forecaster_still_fails_closed_at_fifty_cents() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_execution_context(
            requested_mode="live",
            profile_id="graph_forecaster_v1",
            settings=_live_settings(ceiling=0.50),
        )

    error = exc_info.value
    assert error.reasons == ["estimate_exceeds_cost_ceiling"]
    assert error.details is not None
    assert error.details["estimated_model_upper_bound_cost_usd"] == pytest.approx(
        1.600
    )
    assert error.details["estimated_search_upper_bound_cost_usd"] == pytest.approx(
        0.288
    )
    assert error.details["estimated_upper_bound_cost_usd"] == pytest.approx(1.888)


def test_unavailable_search_pricing_is_explicit_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "forecastlab.execution.estimate_search_cost",
        lambda _provider: (None, "unavailable"),
    )

    with pytest.raises(ConfigurationError) as exc_info:
        resolve_execution_context(
            requested_mode="live",
            profile_id=SMOKE_PROFILE_ID,
            settings=_live_settings(ceiling=0.50),
        )

    error = exc_info.value
    assert error.reasons == ["search_pricing_unavailable"]
    assert error.details is not None
    assert error.details["estimated_search_upper_bound_cost_usd"] is None
    assert error.details["estimated_upper_bound_cost_usd"] is None
    assert error.details["cost_estimate_label"] == "unavailable"
    assert "search=unavailable" in str(error)
    assert "total=unavailable" in str(error)


def test_live_smoke_mode_cannot_select_fixture_or_synthetic_adapters() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_execution_context(
            requested_mode="live",
            profile_id=SMOKE_PROFILE_ID,
            settings={
                "model_provider": "mock",
                "model_name": "mock-forecast-v1",
                "model_api_key": "test-key-never-sent",
                "search_provider": "mock",
                "search_api_key": "test-key-never-sent",
                "max_cost_usd": 0.50,
            },
            synthetic_fixture_run=True,
        )

    assert "model_provider_is_mock" in exc_info.value.reasons
    assert "search_provider_is_mock" in exc_info.value.reasons

    context = resolve_execution_context(
        requested_mode="live",
        profile_id=SMOKE_PROFILE_ID,
        settings=_live_settings(ceiling=0.50),
        synthetic_fixture_run=True,
    )
    assert context.synthetic_fixture_run is False
    assert context.fixture_evidence_allowed is False
    assert context.model_is_mock is False
    assert context.search_is_mock is False


def test_stubbed_graph_smoke_reaches_complete_auditable_pipeline(client) -> None:
    draft = _approved_contract(client)

    response = client.post(
        f"/api/questions/{draft['question_id']}/runs",
        json={"profile_id": SMOKE_PROFILE_ID, "mode": "demo"},
    )

    assert response.status_code == 200
    run = response.json()
    assert run["status"] == "completed"
    assert run["profile_id"] == SMOKE_PROFILE_ID

    stored = client.get(f"/api/runs/{run['id']}").json()
    assert stored["forecast_graph"] is not None
    assert stored["research_plan"] is not None
    assert 2 <= len(stored["research_plan"]["selected_nodes"]) <= 4
    assert len(stored["evidence_claims"]) >= 2
    assert len(stored["node_runs"]) >= 2
    assert stored["forecast_aggregation"] is not None
    assert stored["forecast_aggregation"]["final_probability"] is not None
    assert client.get(f"/api/questions/{draft['question_id']}/report").json()[
        "version_count"
    ] == 1

    budget = stored["budget"]
    assert budget["model_calls"] <= 14
    assert budget["search_calls"] <= 4
    assert budget["fetches"] <= 8
    assert budget["tokens"] <= 50_000
    assert budget["total_cost_usd"] <= 0.50
    context = stored["execution_context"]
    assert context["effective_max_model_calls"] == 14
    assert context["effective_max_search_calls"] == 4
    assert context["effective_max_fetched_documents"] == 8
    assert context["effective_max_tokens"] == 50_000
    assert context["effective_max_cost_usd"] == pytest.approx(0.50)
    assert context["estimated_model_upper_bound_cost_usd"] == pytest.approx(0.0)
    assert context["estimated_search_upper_bound_cost_usd"] == pytest.approx(0.0)
    assert context["estimated_upper_bound_cost_usd"] == pytest.approx(0.0)


def test_stubbed_live_graph_smoke_accepts_three_external_undated_documents(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = _approved_contract(client)
    provider_http_calls = {"count": 0}

    class ForbiddenProviderClient:
        def __init__(self, *_args, **_kwargs) -> None:
            provider_http_calls["count"] += 1
            raise AssertionError("live_provider_http_call_forbidden")

    class StubExternalSearch:
        name = "tavily"

        def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
            suffix = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
            return [
                SearchHit(
                    title=f"External undated source {suffix}",
                    url=f"https://evidence.example.org/{suffix}",
                    snippet="Current external source with no publication metadata.",
                    score=1.0,
                    source_class="primary",
                )
            ][:max_results]

    def stub_external_fetch(url: str, **_kwargs) -> FetchedDocument:
        observed_at = utcnow()
        text = (
            "The external source reports a current, directly observed indicator relevant "
            "to this forecast node."
        )
        return FetchedDocument(
            url=url,
            title="External undated source",
            publisher="External Official Agency",
            published_at=None,
            publication_date_source=None,
            publication_date_verified=False,
            published_at_unknown=True,
            retrieved_at=observed_at,
            source_available_at=observed_at,
            temporal_basis="retrieval_date",
            text=text,
            content_hash=content_hash(text),
            snapshot_verification_status="live",
            rejected=False,
            as_of_eligible=True,
        )

    monkeypatch.setattr(
        "forecastlab.providers.openai_compatible.httpx.Client",
        ForbiddenProviderClient,
    )
    monkeypatch.setattr(
        "forecastlab.providers.search.httpx.Client",
        ForbiddenProviderClient,
    )
    monkeypatch.setattr("forecastlab.run_cache.fetch_document", stub_external_fetch)

    execution = resolve_execution_context(
        requested_mode="live",
        profile_id=SMOKE_PROFILE_ID,
        settings=_live_settings(ceiling=0.50),
    )
    profile = load_profile(SMOKE_PROFILE_ID)

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        question = session.get(Question, str(draft["question_id"]))
        assert question is not None
        run = ForecastRun(
            id=str(uuid.uuid4()),
            question_id=question.id,
            profile_id=SMOKE_PROFILE_ID,
            mode="live",
            status="running",
            started_at=utcnow(),
            execution_context_json=json.dumps(execution.model_dump(mode="json")),
            configuration_hash=execution.configuration_hash,
            evidence_policy=execution.evidence_policy,
            provider_json=json.dumps(
                {
                    "model_provider": "openai",
                    "search_provider": "tavily",
                }
            ),
        )
        session.add(run)
        session.commit()

        version = GraphForecastExecutor(
            session,
            run=run,
            profile=profile,
            execution=execution,
            model=MockModelProvider(),
            search=StubExternalSearch(),
            allow_local_fixtures=False,
        ).execute()

        stored_run = session.get(ForecastRun, run.id)
        assert stored_run is not None
        claims = session.scalars(
            select(EvidenceClaimRow)
            .join(EvidenceItem, EvidenceClaimRow.evidence_item_id == EvidenceItem.id)
            .where(EvidenceItem.run_id == run.id)
        ).all()
        items = session.scalars(
            select(EvidenceItem).where(EvidenceItem.run_id == run.id)
        ).all()
        node_runs = session.scalars(
            select(ForecastNodeRunRow).where(ForecastNodeRunRow.forecast_run_id == run.id)
        ).all()
        research_plan = session.scalar(
            select(ResearchPlanRow).where(ResearchPlanRow.forecast_run_id == run.id)
        )
        aggregation = session.scalar(
            select(ForecastAggregationRow).where(ForecastAggregationRow.forecast_run_id == run.id)
        )

        assert stored_run.status == "completed"
        assert research_plan is not None
        assert len(items) == 3
        assert all(item.url.startswith("https://evidence.example.org/") for item in items)
        assert all(item.published_at is None for item in items)
        assert len(claims) >= 3
        assert len(node_runs) == 3
        assert aggregation is not None
        assert version.id
        assert all(claim.publication_date is None for claim in claims)
        assert all(claim.publication_date_verified is False for claim in claims)
        assert all(claim.temporal_basis == "retrieval_date" for claim in claims)
        assert all(claim.source_available_at == claim.retrieval_date for claim in claims)
        assert all(claim.cutoff_verified for claim in claims)
        assert provider_http_calls["count"] == 0


@pytest.mark.parametrize(
    "profile_id",
    [
        "three_track_ensemble",
        "live_smoke_v1",
        "graph_forecaster_v1",
        "single_model_forecaster_v1",
        "three_track_forecaster",
        "single_agent_equal_budget_v1",
        "three_track_equal_budget_v1",
    ],
)
def test_existing_profiles_remain_loadable(profile_id: str) -> None:
    assert load_profile(profile_id).id == profile_id
