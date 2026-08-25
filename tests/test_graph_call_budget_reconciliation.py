from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select

from forecastlab.budget import Budget
from forecastlab.errors import BudgetExceeded
from forecastlab.execution import resolve_execution_context
from forecastlab.hashing import content_hash
from forecastlab.ledger import InMemoryUsageLedger
from forecastlab.profiles import load_profile
from forecastlab.providers.base import ChatResult
from forecastlab.research_planning import (
    PLANNER_VERSION,
    ResearchPlanner,
    ResearchPlanningError,
)
from forecastlab.schemas import (
    FetchedDocument,
    ForecastGraph,
    ForecastNode,
    ForecastProfile,
    ModelUsage,
    SearchHit,
)
from forecastlab.timeutil import utcnow
from forecastlab_api.contracts import forecast_contract_from_row
from forecastlab_api.graph_executor import GraphForecastExecutor
from forecastlab_api.graphs import store_forecast_graph
from forecastlab_api.models import (
    EvidenceClaimRow,
    EvidenceItem,
    ForecastAggregationRow,
    ForecastContractRow,
    ForecastNodeRunRow,
    ForecastRun,
    ForecastVersion,
    Question,
    ResearchPlanRow,
)
from forecastlab_api.research_plans import research_plan_from_row

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
SMOKE_PROFILE_ID = "graph_live_smoke_v1"
MODEL_NAME = "gpt-5-mini-2025-08-07"
FL_R001_SELECTED_IDS = [
    "e70f22d4-2d2f-4ff7-afd1-3cea49458ba7",
    "34cd4b3d-c785-4b85-8653-a445dbb40822",
    "50eaef78-60a4-47c9-8a68-1bdc8d7232e9",
]

_NODE_ROWS = (
    (
        "e70f22d4-2d2f-4ff7-afd1-3cea49458ba7",
        "adversarial",
        0.20,
        "What is the most plausible adversarial pathway (specifying shock type, "
        "magnitude, timing, and measurement anomaly) by which the leading case "
        "would be wrong and how should plausibility be weighted?",
    ),
    (
        "34cd4b3d-c785-4b85-8653-a445dbb40822",
        "resolver",
        0.20,
        "What exactly counts as the first-released seasonally adjusted U-3 monthly "
        "reading, what BLS procedures could restate it, and what are the main "
        "resolver risks?",
    ),
    (
        "50eaef78-60a4-47c9-8a68-1bdc8d7232e9",
        "base_rate",
        0.18,
        "What fraction of monthly BLS first-release seasonally adjusted U-3 "
        "readings from January 1990 through the most recent published month were "
        "at or above 5.0%?",
    ),
    (
        "1d64a891-044e-4a88-abaa-8f592c40b0ce",
        "driver",
        0.25,
        "Given the current level and recent trend in payrolls and household-survey "
        "unemployment counts, what is the conditional probability that U-3 will "
        "reach at least 5.0% by the deadline?",
    ),
    (
        "2b8c0f9f-195b-4d08-9585-ecf7eb928592",
        "driver",
        0.12,
        "How would labor-force participation change the measured U-3 path?",
    ),
    (
        "7b43cfc4-4175-490d-b968-44397a1abdb0",
        "driver",
        0.05,
        "How would payroll revisions change the unemployment threshold risk?",
    ),
    (
        "a8b124c0-4f9a-44af-8b11-fe5ecf1a9153",
        "driver",
        0.10,
        "How would monetary policy transmission alter the U-3 trajectory?",
    ),
    (
        "b9ddd69d-8cd3-429e-b26b-985887c32882",
        "dependency",
        0.05,
        "Which shared data dependencies constrain the selected labor indicators?",
    ),
)

# This acyclic orientation has the exact undirected dependency counts recorded by
# FL-R001: 5, 4, 4, 3, 2, 1, 3, 2.
_DEPENDENCY_EDGES = (
    (0, 2),
    (0, 1),
    (0, 6),
    (0, 3),
    (0, 7),
    (2, 1),
    (2, 6),
    (2, 4),
    (3, 1),
    (3, 7),
    (6, 5),
    (4, 1),
)


def _fl_r001_graph(contract_id: str = "contract-fl-r001") -> ForecastGraph:
    dependencies: dict[int, list[int]] = {index: [] for index in range(8)}
    for left, right in _DEPENDENCY_EDGES:
        dependencies[max(left, right)].append(min(left, right))
    graph_id = "ff4290b3-be14-46c6-b0bb-c8d61ac80a31"
    nodes = [
        ForecastNode(
            id=node_id,
            graph_id=graph_id,
            question=question,
            node_type=node_type,  # type: ignore[arg-type]
            importance_weight=weight,
            dependencies=[_NODE_ROWS[index][0] for index in dependencies[position]],
            preferred_sources=["Bureau of Labor Statistics"],
            required_output_type="probability",
        )
        for position, (node_id, node_type, weight, question) in enumerate(_NODE_ROWS)
    ]
    return ForecastGraph(
        id=graph_id,
        contract_id=contract_id,
        status="approved",
        created_at=NOW,
        generation_model=f"openai:{MODEL_NAME}",
        root_question="Will US seasonally adjusted U-3 reach at least 5.0% by the deadline?",
        nodes=nodes,
    )


def _budget_after_fl_r001_graph_generation(
    profile: ForecastProfile | None = None,
) -> Budget:
    budget = Budget(
        profile or load_profile(SMOKE_PROFILE_ID),
        provider="openai",
        model=MODEL_NAME,
        search_provider="tavily",
    )
    budget.state.model_calls = 1
    budget.state.tokens = 4_776
    budget.state.prompt_tokens = 862
    budget.state.completion_tokens = 3_914
    budget.state.cost_usd = 0.06302
    budget.state.model_cost_usd = 0.06302
    budget.state.provider_request_count = 1
    return budget


def _plan_after_fl_r001_graph_generation(
    *,
    planner: ResearchPlanner | None = None,
    profile: ForecastProfile | None = None,
):
    return (planner or ResearchPlanner()).plan(
        _fl_r001_graph(),
        forecast_run_id="run-fl-r001-regression",
        budget=_budget_after_fl_r001_graph_generation(profile),
        created_at=NOW,
    )


def test_planner_v2_call_envelope_includes_one_bounded_retry() -> None:
    plan = _plan_after_fl_r001_graph_generation()
    allocation = plan.budget_allocation

    assert allocation["planner_version"] == PLANNER_VERSION
    assert allocation["research_plan_calls_per_node"] == 1
    assert allocation["primary_extraction_calls_per_node"] == 1
    assert allocation["extraction_retry_calls_per_node"] == 1
    assert allocation["node_forecast_calls_per_node"] == 1
    assert allocation["total_model_calls_per_node"] == 4


def test_planner_call_envelope_is_three_when_retries_are_disabled() -> None:
    plan = _plan_after_fl_r001_graph_generation(
        planner=ResearchPlanner(extraction_retries_enabled=False)
    )

    assert plan.budget_allocation["extraction_retry_calls_per_node"] == 0
    assert plan.budget_allocation["total_model_calls_per_node"] == 3


def test_extraction_call_envelope_scales_with_allocated_fetches() -> None:
    profile = load_profile("graph_forecaster_v1").model_copy(
        update={"fetches_per_subquestion": 2, "max_wall_clock_seconds": 300}
    )
    plan = ResearchPlanner(max_researched_nodes=3).plan(
        _fl_r001_graph(),
        forecast_run_id="run-two-fetch-allocation",
        budget=Budget(profile),
        created_at=NOW,
    )
    allocation = plan.budget_allocation

    assert allocation["selected_node_count"] == 3
    assert allocation["allocated_fetches_per_node"] == 2
    assert allocation["primary_extraction_calls_per_node"] == 2
    assert allocation["extraction_retry_calls_per_node"] == 2
    assert allocation["total_model_calls_per_node"] == 6


def test_fl_r001_selection_accounts_for_consumed_graph_call_once() -> None:
    plan = _plan_after_fl_r001_graph_generation()
    allocation = plan.budget_allocation

    assert plan.selected_nodes == FL_R001_SELECTED_IDS
    assert len(plan.skipped_nodes) == 5
    assert allocation["model_calls_available_before_research"] == 13
    assert allocation["planned_total_model_calls"] == 12
    assert allocation["planned_research_phase_model_calls"] == 9
    assert allocation["reserved_node_forecast_calls"] == 3
    assert allocation["model_call_headroom_after_plan"] == 1


def test_larger_graph_profile_can_still_select_four_or_more_nodes() -> None:
    profile = load_profile("graph_forecaster_v1")
    budget = _budget_after_fl_r001_graph_generation(profile)

    plan = ResearchPlanner().plan(
        _fl_r001_graph(),
        forecast_run_id="run-larger-graph-profile",
        budget=budget,
        created_at=NOW,
    )

    assert len(plan.selected_nodes) >= 4


def test_parallel_research_cannot_consume_frozen_forecast_calls() -> None:
    profile = load_profile(SMOKE_PROFILE_ID).model_copy(
        update={
            "max_model_calls": 6,
            "max_tokens": 1_000,
            "max_estimated_cost_usd": 10.0,
        }
    )
    budget = Budget(profile)
    budget.freeze_model_call_envelope(
        planner_version=PLANNER_VERSION,
        planned_calls_by_kind={
            "research_plan": 3,
            "primary_extraction": 0,
            "extraction_retry": 0,
            "node_forecast": 3,
        },
    )

    def reserve_research(index: int) -> str:
        try:
            budget.reserve_model_call(
                f"parallel-research-{index}",
                estimated_input_tokens=1,
                max_output_tokens=0,
                estimated_cost_usd=0.0,
                wall_clock_seconds=0,
                call_kind="research_plan",
            )
        except BudgetExceeded as exc:
            return exc.reason
        return "reserved"

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(reserve_research, range(8)))

    assert outcomes.count("reserved") == 3
    assert outcomes.count("unplanned_research_plan_call") == 5
    assert budget.state.stopped is False
    for index in range(3):
        budget.reserve_model_call(
            f"forecast-{index}",
            estimated_input_tokens=1,
            max_output_tokens=0,
            estimated_cost_usd=0.0,
            wall_clock_seconds=0,
            call_kind="node_forecast",
        )
    assert budget.state.model_calls == 6
    assert budget.snapshot()["model_call_envelope"][
        "remaining_node_forecast_calls"
    ] == 0


def test_unused_retry_reserve_is_audit_only_not_fake_usage() -> None:
    profile = load_profile(SMOKE_PROFILE_ID).model_copy(
        update={"max_model_calls": 4, "max_estimated_cost_usd": 10.0}
    )
    budget = Budget(profile)
    budget.freeze_model_call_envelope(
        planner_version=PLANNER_VERSION,
        planned_calls_by_kind={
            "research_plan": 1,
            "primary_extraction": 1,
            "extraction_retry": 1,
            "node_forecast": 1,
        },
    )
    for call_kind in ("research_plan", "primary_extraction", "node_forecast"):
        budget.reserve_model_call(
            call_kind,
            estimated_input_tokens=1,
            max_output_tokens=0,
            estimated_cost_usd=0.0,
            wall_clock_seconds=0,
            call_kind=call_kind,
        )

    snapshot = budget.snapshot()
    assert snapshot["model_calls"] == 3
    assert snapshot["provider_request_count"] == 3
    assert snapshot["model_call_envelope"]["remaining_calls_by_kind"][
        "extraction_retry"
    ] == 1
    assert all(
        reservation["call_kind"] != "extraction_retry"
        for reservation in snapshot["reservations"]
    )


def test_minimum_model_call_infeasibility_fails_before_research() -> None:
    profile = load_profile(SMOKE_PROFILE_ID).model_copy(
        update={"max_model_calls": 11}
    )
    budget = Budget(profile)

    with pytest.raises(ResearchPlanningError) as exc_info:
        ResearchPlanner().plan(
            _fl_r001_graph(),
            forecast_run_id="run-minimum-infeasible",
            budget=budget,
            created_at=NOW,
        )

    assert exc_info.value.reasons == [
        "minimum_graph_execution_exceeds_model_call_budget"
    ]
    assert exc_info.value.audit["minimum_required_model_calls"] == 12
    assert budget.state.model_calls == 0
    assert budget.state.search_calls == 0
    assert budget.state.fetches == 0


def test_critical_model_call_infeasibility_fails_before_research() -> None:
    graph = _fl_r001_graph().model_copy(
        update={
            "nodes": [
                node.model_copy(update={"importance_weight": 0.9})
                if index < 3
                else node
                for index, node in enumerate(_fl_r001_graph().nodes)
            ]
        }
    )
    profile = load_profile(SMOKE_PROFILE_ID).model_copy(
        update={"max_model_calls": 11}
    )
    budget = Budget(profile)

    with pytest.raises(ResearchPlanningError) as exc_info:
        ResearchPlanner().plan(
            graph,
            forecast_run_id="run-critical-infeasible",
            budget=budget,
            created_at=NOW,
        )

    assert exc_info.value.reasons == ["critical_nodes_exceed_budget"]
    assert budget.state.model_calls == 0
    assert budget.state.search_calls == 0
    assert budget.state.fetches == 0


def test_call_envelope_allocation_and_selection_are_deterministic() -> None:
    left = _plan_after_fl_r001_graph_generation()
    right = _plan_after_fl_r001_graph_generation()
    call_envelope_fields = (
        "planner_version",
        "research_plan_calls_per_node",
        "primary_extraction_calls_per_node",
        "extraction_retry_calls_per_node",
        "node_forecast_calls_per_node",
        "total_model_calls_per_node",
        "selected_node_count",
        "planned_calls_by_kind",
        "planned_research_phase_model_calls",
        "reserved_node_forecast_calls",
        "planned_total_model_calls",
        "model_calls_available_before_research",
        "model_call_headroom_after_plan",
        "extraction_fallbacks_enabled",
        "allocated_fetches_per_node",
        "allocated_searches_per_node",
        "estimated_tokens_by_phase",
        "estimated_cost_by_phase",
        "per_node",
    )

    assert left.selected_nodes == right.selected_nodes
    assert left.skipped_nodes == right.skipped_nodes
    assert left.priority_scores == right.priority_scores
    assert {
        field: left.budget_allocation[field] for field in call_envelope_fields
    } == {
        field: right.budget_allocation[field] for field in call_envelope_fields
    }


class _FallbackPathModel:
    name = "openai"
    model = MODEL_NAME

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: Counter[str] = Counter()

    def complete_json(self, **kwargs: Any) -> ChatResult:
        schema_name = str(kwargs["schema_name"])
        with self._lock:
            self.calls[schema_name] += 1
        if schema_name == "graph_research_plan":
            node = json.loads(str(kwargs["user"]))["node"]
            payload: dict[str, Any] = {
                "primary_research_question": node["question"],
                "supporting_search_queries": [node["question"]],
                "preferred_sources": node["preferred_sources"],
                "required_evidence_types": ["dated primary record"],
            }
        elif schema_name == "evidence_claims":
            payload = {
                "claims": [
                    {
                        "claim": "Unsupported generated assertion.",
                        "excerpt": "This excerpt is deliberately absent.",
                        "supports_or_refutes": "supports",
                        "confidence": 0.7,
                        "source_quality": 0.8,
                        "primary_source": True,
                    }
                ]
            }
        elif schema_name == "forecast_node":
            claims = json.loads(str(kwargs["user"]))["evidence_claims"]
            payload = {
                "probability": 0.57,
                "reasoning": "The bounded stub cites the deterministic fallback claim.",
                "supporting_claim_ids": [claims[0]["id"]],
                "opposing_claim_ids": [],
                "uncertainty_notes": [
                    "Structured extraction failed before the verbatim fallback."
                ],
            }
        else:
            raise AssertionError(f"unexpected_schema:{schema_name}")
        return ChatResult(
            content=json.dumps(payload),
            parsed=payload,
            usage=ModelUsage(
                prompt_tokens=40,
                completion_tokens=20,
                cost_usd=0.0,
                model=self.model,
                provider=self.name,
                cost_source="provider_reported",
            ),
        )


class _ExternalStubSearch:
    name = "tavily"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        with self._lock:
            self.calls += 1
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


def _approved_contract(client) -> dict[str, Any]:
    response = client.post(
        "/api/contracts/generate",
        json={
            "question": "Will US unemployment exceed 5% before 30 June 2027?",
            "mode": "demo",
        },
    )
    response.raise_for_status()
    draft = response.json()
    approved = client.post(f"/api/contracts/{draft['id']}/approve")
    approved.raise_for_status()
    return draft


def test_fl_r001_full_fallback_regression_reaches_forecast_version(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = _approved_contract(client)
    provider_http_calls = {"count": 0}
    fetch_calls = {"count": 0}

    class ForbiddenProviderClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            provider_http_calls["count"] += 1
            raise AssertionError("live_provider_http_call_forbidden")

    def stub_external_fetch(url: str, **_kwargs: Any) -> FetchedDocument:
        fetch_calls["count"] += 1
        observed_at = utcnow()
        text = (
            "The official external source reports a directly observed labor-market "
            "indicator relevant to this forecast node. "
        ) * 160
        return FetchedDocument(
            url=url,
            title="External undated official source",
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
        settings={
            "model_provider": "openai",
            "model_name": MODEL_NAME,
            "model_api_key": "test-key-never-sent",
            "search_provider": "tavily",
            "search_api_key": "test-key-never-sent",
            "max_cost_usd": 0.50,
        },
    )
    profile = load_profile(SMOKE_PROFILE_ID)
    model = _FallbackPathModel()
    search = _ExternalStubSearch()
    ledger = InMemoryUsageLedger()

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        question = session.get(Question, str(draft["question_id"]))
        contract_row = session.get(ForecastContractRow, str(draft["id"]))
        assert question is not None
        assert contract_row is not None
        contract = forecast_contract_from_row(contract_row)
        graph = _fl_r001_graph(contract.id)
        store_forecast_graph(session, graph)
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
                {"model_provider": "openai", "search_provider": "tavily"}
            ),
        )
        session.add(run)
        session.commit()

        graph_entry = ledger.reserve(
            run_id=run.id,
            run_attempt_id=None,
            logical_call_id="fl-r001-graph-generation",
            physical_attempt_number=1,
            stage="forecast_graph",
            provider_type="model",
            provider="openai",
            model=MODEL_NAME,
            reserved_input_tokens=862,
            reserved_output_tokens=3_914,
            reserved_cost_usd=0.06302,
        )
        ledger.reconcile(
            graph_entry.id,
            ModelUsage(
                prompt_tokens=862,
                completion_tokens=3_914,
                cost_usd=0.06302,
                model=MODEL_NAME,
                provider="openai",
                cost_source="provider_reported",
            ),
        )

        def graph_resolver(*_args: Any, **_kwargs: Any):
            return contract, graph

        version = GraphForecastExecutor(
            session,
            run=run,
            profile=profile,
            execution=execution,
            model=model,
            search=search,
            allow_local_fixtures=False,
            ledger=ledger,
            graph_resolver=graph_resolver,
        ).execute()

        stored_run = session.get(ForecastRun, run.id)
        plan_row = session.scalar(
            select(ResearchPlanRow).where(
                ResearchPlanRow.forecast_run_id == run.id
            )
        )
        claims = session.scalars(
            select(EvidenceClaimRow)
            .join(EvidenceItem, EvidenceClaimRow.evidence_item_id == EvidenceItem.id)
            .where(EvidenceItem.run_id == run.id)
        ).all()
        node_runs = session.scalars(
            select(ForecastNodeRunRow).where(
                ForecastNodeRunRow.forecast_run_id == run.id
            )
        ).all()
        aggregation = session.scalar(
            select(ForecastAggregationRow).where(
                ForecastAggregationRow.forecast_run_id == run.id
            )
        )
        stored_version = session.scalar(
            select(ForecastVersion).where(ForecastVersion.run_id == run.id)
        )

        assert stored_run is not None
        assert stored_run.status == "completed"
        assert plan_row is not None
        plan = research_plan_from_row(plan_row)
        assert plan.selected_nodes == FL_R001_SELECTED_IDS
        assert len(plan.skipped_nodes) == 5
        assert plan.budget_allocation["planned_total_model_calls"] == 12
        assert plan.budget_allocation["reserved_node_forecast_calls"] == 3
        assert len(claims) == 3
        assert len(node_runs) == 3
        assert aggregation is not None
        assert stored_version is not None
        assert stored_version.id == version.id

        budget = json.loads(stored_run.budget_json)
        envelope = budget["model_call_envelope"]
        assert budget["model_calls"] == 13
        assert budget["search_calls"] == 3
        assert budget["fetches"] == 3
        assert envelope["baseline_model_calls"] == 1
        assert envelope["used_calls_by_kind"] == {
            "research_plan": 3,
            "primary_extraction": 3,
            "extraction_retry": 3,
            "node_forecast": 3,
        }
        assert envelope["used_total_model_calls"] == 12
        assert envelope["remaining_node_forecast_calls"] == 0
        assert model.calls == Counter(
            {
                "graph_research_plan": 3,
                "evidence_claims": 6,
                "forecast_node": 3,
            }
        )
        assert sum(model.calls.values()) + 1 == 13
        assert search.calls == 3
        assert fetch_calls["count"] == 3
        assert provider_http_calls["count"] == 0
