from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select

from forecastlab.budget import Budget
from forecastlab.errors import GraphForecastExecutionError
from forecastlab.execution import resolve_execution_context
from forecastlab.graphs import ForecastGraphError, GraphGenerator
from forecastlab.ledger import InMemoryUsageLedger
from forecastlab.profiles import effective_profile, load_profile
from forecastlab.providers.base import ChatResult, StructuredOutputDiagnostics
from forecastlab.providers.openai_compatible import OpenAICompatibleProvider
from forecastlab.research_planning import PLANNER_VERSION, ResearchPlanner
from forecastlab.schemas import ForecastContract, ModelUsage
from forecastlab.structured_outputs import forecast_graph_json_schema
from forecastlab.timeutil import utcnow
from forecastlab_api.graph_executor import GraphForecastExecutor
from forecastlab_api.graphs import forecast_graph_from_row
from forecastlab_api.models import (
    ForecastGraphRow,
    ForecastRun,
    ForecastVersion,
    GraphExecutionFailureRow,
    Question,
)
from forecastlab_api.pipeline import (
    apply_execution_limits,
    create_run_record,
    resolve_for_question,
)
from forecastlab_api.v1_execution import (
    ensure_execution_graph,
    record_execution_graph_resolution,
)

SMOKE_PROFILE_ID = "graph_live_smoke_v1"
MODEL = "gpt-5-mini-2025-08-07"
SMOKE_OUTPUT_CAP = 1536
SMOKE_COMPLETION_ENVELOPE = 8192


def _approved_contract() -> ForecastContract:
    return ForecastContract(
        id="contract-strict-output",
        question_id="question-strict-output",
        version=1,
        created_at=datetime(2026, 8, 25, tzinfo=UTC),
        created_by="test",
        original_question="Will US unemployment reach 5% before July 2027?",
        normalized_question="Will US U-3 unemployment reach 5% before 1 July 2027?",
        yes_condition="BLS reports seasonally adjusted U-3 at or above 5.0% before the deadline.",
        no_condition="No qualifying BLS report reaches 5.0% before the deadline.",
        resolution_date=datetime(2027, 7, 15, tzinfo=UTC),
        authoritative_source="https://www.bls.gov/news.release/empsit.toc.htm",
        resolution_method="Check the official BLS U-3 monthly releases.",
        initial_reference_class="Postwar unemployment threshold crossings.",
        suggested_drivers=["labor demand", "monetary policy"],
        known_dependencies=["business cycle"],
        status="approved",
    )


def _valid_graph_payload() -> dict[str, Any]:
    nodes = [
        ("base", None, "Historical threshold base rate?", "base_rate", 0.9, []),
        ("trend", None, "Current labor-market trend?", "trend", 0.8, []),
        ("driver", None, "Primary labor-demand driver?", "driver", 0.85, ["trend"]),
        ("scenario", "driver", "Alternative recession scenario?", "scenario", 0.7, ["trend"]),
        ("adversarial", None, "Strongest contrary evidence?", "adversarial", 0.75, ["scenario"]),
        ("resolver", None, "BLS resolution mechanics?", "resolver", 0.6, []),
    ]
    output_by_type = {
        "base_rate": "probability",
        "trend": "directional_update",
        "driver": "directional_update",
        "scenario": "scenario_weight",
        "adversarial": "directional_update",
        "resolver": "structured_categorical",
    }
    return {
        "nodes": [
            {
                "id": node_id,
                "parent_node_id": parent_id,
                "question": question,
                "node_type": node_type,
                "importance_weight": weight,
                "dependencies": dependencies,
                "preferred_sources": ["official primary source"],
                "required_output_type": output_by_type[node_type],
                "status": "pending",
            }
            for node_id, parent_id, question, node_type, weight, dependencies in nodes
        ]
    }


def _install_http_response(
    monkeypatch: pytest.MonkeyPatch,
    response_data: dict[str, Any],
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []

    class StubClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> StubClient:
            return self

        def __exit__(self, *_args: Any) -> bool:
            return False

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> MagicMock:
            del headers
            requests.append({"url": url, "json": json})
            response = MagicMock()
            response.status_code = 200
            response.text = "stubbed response"
            response.json.return_value = response_data
            response.headers = {"x-request-id": "rid-structured-output-test"}
            response.elapsed.total_seconds.return_value = 0.052
            return response

    monkeypatch.setattr(
        "forecastlab.providers.openai_compatible.httpx.Client",
        StubClient,
    )
    return requests


def _openai_response(
    content: str,
    *,
    finish_reason: str = "stop",
    completion_tokens: int = 600,
    reasoning_tokens: int = 100,
    refusal: str | None = None,
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-structured-output-test",
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": content, "refusal": refusal},
            }
        ],
        "usage": {
            "prompt_tokens": 854,
            "completion_tokens": completion_tokens,
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
        },
    }


class _ResultModel:
    name = "openai"
    model = MODEL

    def __init__(self, result: ChatResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, **kwargs: Any) -> ChatResult:
        self.calls.append(kwargs)
        return self.result


class _RecordingOpenAIProvider(OpenAICompatibleProvider):
    last_result: ChatResult | None = None

    def complete_json(self, **kwargs: Any) -> ChatResult:
        result = super().complete_json(**kwargs)
        self.last_result = result
        return result


class _ForbiddenSearch:
    name = "tavily"

    def __init__(self) -> None:
        self.calls = 0

    def search(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        self.calls += 1
        raise AssertionError("Tavily must not be called after graph-generation failure")


def _diagnostics(**overrides: Any) -> StructuredOutputDiagnostics:
    values: dict[str, Any] = {
        "schema_name": "forecast_graph",
        "provider_request_id": "rid-test",
        "finish_reason": "stop",
        "requested_max_output_tokens": SMOKE_OUTPUT_CAP,
        "completion_tokens": 20,
        "reasoning_tokens": 4,
        "visible_output_tokens": 16,
        "content_character_count": 20,
        "json_parsing_succeeded": True,
        "strict_schema_validation_succeeded": True,
    }
    values.update(overrides)
    return StructuredOutputDiagnostics(**values)


def test_forecast_graph_schema_is_strict_and_complete() -> None:
    schema = forecast_graph_json_schema()
    node_schema = schema["$defs"]["ForecastGraphNodeOutput"]

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["nodes"]
    assert schema["properties"]["nodes"]["minItems"] == 5
    assert schema["properties"]["nodes"]["maxItems"] == 10
    assert node_schema["additionalProperties"] is False
    assert set(node_schema["required"]) == {
        "id",
        "parent_node_id",
        "question",
        "node_type",
        "importance_weight",
        "dependencies",
        "preferred_sources",
        "required_output_type",
        "status",
    }
    assert node_schema["properties"]["status"]["const"] == "pending"


def test_graph_generator_uses_each_explicit_profile_controlled_cap() -> None:
    result = ChatResult(
        content=json.dumps(_valid_graph_payload()),
        parsed=_valid_graph_payload(),
        usage=ModelUsage(model=MODEL, provider="openai"),
    )
    model = _ResultModel(result)

    GraphGenerator(model, max_output_tokens=777).generate(_approved_contract())

    request = model.calls[0]
    assert request["max_output_tokens"] == 777
    assert request["reasoning_effort"] == "minimal"
    assert request["json_schema"] == forecast_graph_json_schema()
    assert model.calls == [request]


@pytest.mark.parametrize(
    ("result", "expected_reason"),
    [
        (
            ChatResult(
                content='{"nodes":[',
                parsed=None,
                usage=ModelUsage(model=MODEL, provider="openai"),
                diagnostics=_diagnostics(
                    finish_reason="length",
                    json_parsing_succeeded=False,
                    strict_schema_validation_succeeded=False,
                ),
            ),
            "structured_output_truncated",
        ),
        (
            ChatResult(
                content="",
                parsed=None,
                usage=ModelUsage(model=MODEL, provider="openai"),
                diagnostics=_diagnostics(
                    refusal_present=True,
                    refusal_category="provider_refusal",
                    json_parsing_succeeded=False,
                    strict_schema_validation_succeeded=False,
                ),
            ),
            "structured_output_refused",
        ),
        (
            ChatResult(
                content="",
                parsed=None,
                usage=ModelUsage(model=MODEL, provider="openai"),
                diagnostics=_diagnostics(
                    content_character_count=0,
                    json_parsing_succeeded=False,
                    strict_schema_validation_succeeded=False,
                ),
            ),
            "structured_output_empty",
        ),
        (
            ChatResult(
                content="not-json",
                parsed=None,
                usage=ModelUsage(model=MODEL, provider="openai"),
                diagnostics=_diagnostics(
                    json_parsing_succeeded=False,
                    strict_schema_validation_succeeded=False,
                ),
            ),
            "structured_output_invalid_json",
        ),
        (
            ChatResult(
                content='{"nodes":[]}',
                parsed={"nodes": []},
                usage=ModelUsage(model=MODEL, provider="openai"),
                diagnostics=_diagnostics(strict_schema_validation_succeeded=False),
            ),
            "structured_output_schema_invalid",
        ),
    ],
)
def test_graph_structured_output_failures_are_precise_and_never_retried(
    result: ChatResult,
    expected_reason: str,
) -> None:
    model = _ResultModel(result)

    with pytest.raises(ForecastGraphError) as exc_info:
        GraphGenerator(model, max_output_tokens=SMOKE_OUTPUT_CAP).generate(_approved_contract())

    assert exc_info.value.reasons == [expected_reason]
    assert len(model.calls) == 1
    assert "not-json" not in json.dumps(exc_info.value.audit)


def test_schema_valid_cycle_is_a_domain_validation_failure() -> None:
    payload = _valid_graph_payload()
    payload["nodes"][0]["dependencies"] = ["trend"]
    payload["nodes"][1]["dependencies"] = ["base"]
    model = _ResultModel(
        ChatResult(
            content=json.dumps(payload),
            parsed=payload,
            usage=ModelUsage(model=MODEL, provider="openai"),
        )
    )

    with pytest.raises(ForecastGraphError) as exc_info:
        GraphGenerator(model, max_output_tokens=SMOKE_OUTPUT_CAP).generate(_approved_contract())

    assert exc_info.value.reasons == ["graph_domain_validation_failed"]
    assert "dependency_cycle_detected" in exc_info.value.audit["domain_validation_errors"]
    assert len(model.calls) == 1


def test_fl_r003_regression_is_one_strict_capped_http_success_and_audited_failure(
    client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malformed = '{"nodes":[{"private":"RAW_MALFORMED_SENTINEL"}'
    requests = _install_http_response(
        monkeypatch,
        _openai_response(
            malformed,
            finish_reason="length",
            completion_tokens=SMOKE_COMPLETION_ENVELOPE,
            reasoning_tokens=SMOKE_COMPLETION_ENVELOPE,
        ),
    )
    draft = client.post(
        "/api/contracts/generate",
        json={"question": "Will US unemployment exceed 5% before 30 June 2027?"},
    ).json()
    client.post(f"/api/contracts/{draft['id']}/approve").raise_for_status()

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        question = session.get(Question, draft["question_id"])
        assert question is not None
        context = resolve_for_question(
            question,
            profile_id=SMOKE_PROFILE_ID,
            mode="live",
            settings_data={
                "model_provider": "openai",
                "model_name": MODEL,
                "model_api_key": "stub-key-never-sent",
                "search_provider": "tavily",
                "search_api_key": "stub-key-never-sent",
                "max_cost_usd": 0.50,
            },
        )
        profile = apply_execution_limits(load_profile(SMOKE_PROFILE_ID), context)
        run = create_run_record(
            session,
            question=question,
            context=context,
            as_of=None,
            enqueue=False,
        )
        run.started_at = utcnow() - timedelta(milliseconds=25)
        run.status = "running"
        session.commit()

        ledger = InMemoryUsageLedger()
        provider = OpenAICompatibleProvider(
            api_key="stub-key-never-sent",
            base_url="https://stubbed-openai.invalid/v1",
            model=MODEL,
            provider_id="openai",
            ledger=ledger,
            run_id=run.id,
            stage="forecast_graph",
        )
        search = _ForbiddenSearch()
        executor = GraphForecastExecutor(
            session,
            run=run,
            profile=profile,
            execution=context,
            model=provider,
            search=search,
            allow_local_fixtures=False,
            ledger=ledger,
        )

        with pytest.raises(GraphForecastExecutionError) as exc_info:
            executor.execute()

        assert exc_info.value.reasons == ["structured_output_truncated"]
        assert search.calls == 0
        assert len(requests) == 1
        body = requests[0]["json"]
        assert body["max_completion_tokens"] == SMOKE_COMPLETION_ENVELOPE
        assert "max_tokens" not in body
        assert "temperature" not in body
        assert body["reasoning_effort"] == "minimal"
        assert body["verbosity"] == "low"
        assert body["response_format"]["type"] == "json_schema"
        assert (
            body["response_format"]["json_schema"]["name"]
            == "forecast_graph_compact_indexed_v1"
        )
        assert body["response_format"]["json_schema"]["strict"] is True
        assert set(
            body["response_format"]["json_schema"]["schema"]["properties"]
        ) == {"n"}

        entries = ledger.entries(run.id)
        assert len(entries) == 1
        assert entries[0].status == "succeeded"
        assert entries[0].reserved_output_tokens == SMOKE_COMPLETION_ENVELOPE
        assert entries[0].actual_completion_tokens == SMOKE_COMPLETION_ENVELOPE
        failure = session.scalar(
            select(GraphExecutionFailureRow).where(GraphExecutionFailureRow.forecast_run_id == run.id)
        )
        stored_run = session.get(ForecastRun, run.id)
        assert failure is not None
        assert stored_run is not None
        assert failure.error_code == "structured_output_truncated"
        audit = json.loads(failure.research_plan_json)
        diagnostics = audit["structured_output"]
        assert diagnostics["provider_request_id"] == "rid-structured-output-test"
        assert diagnostics["finish_reason"] == "length"
        assert diagnostics["completion_tokens"] == SMOKE_COMPLETION_ENVELOPE
        assert diagnostics["reasoning_tokens"] == SMOKE_COMPLETION_ENVELOPE
        assert diagnostics["visible_output_tokens"] == 0
        assert diagnostics["requested_max_output_tokens"] == SMOKE_OUTPUT_CAP
        assert diagnostics["requested_max_completion_tokens"] == SMOKE_COMPLETION_ENVELOPE
        assert diagnostics["requested_max_visible_output_tokens"] == SMOKE_OUTPUT_CAP
        assert diagnostics["json_parsing_succeeded"] is False
        assert diagnostics["strict_schema_validation_succeeded"] is False
        assert "RAW_MALFORMED_SENTINEL" not in failure.error_message
        assert "RAW_MALFORMED_SENTINEL" not in failure.research_plan_json
        assert stored_run.status == "failed"
        assert stored_run.latency_ms > 0
        assert (
            session.scalar(
                select(func.count()).select_from(ForecastGraphRow).where(ForecastGraphRow.contract_id == draft["id"])
            )
            == 0
        )
        assert session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run.id)) is None


def test_valid_strict_graph_uses_one_request_and_continues_into_planner_v3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _valid_graph_payload()
    requests = _install_http_response(
        monkeypatch,
        _openai_response(json.dumps(payload), completion_tokens=700, reasoning_tokens=120),
    )
    ledger = InMemoryUsageLedger()
    provider = _RecordingOpenAIProvider(
        api_key="stub-key-never-sent",
        base_url="https://stubbed-openai.invalid/v1",
        model=MODEL,
        provider_id="openai",
        ledger=ledger,
        run_id="valid-strict-graph-run",
        stage="forecast_graph",
    )

    generation_result = GraphGenerator(
        provider,
        max_output_tokens=SMOKE_OUTPUT_CAP,
    ).generate(_approved_contract())
    graph = generation_result.graph

    assert graph.status == "approved"
    assert 5 <= len(graph.nodes) <= 10
    assert {node.id for node in graph.nodes}.isdisjoint(set(payload_node["id"] for payload_node in payload["nodes"]))
    known_ids = {node.id for node in graph.nodes}
    assert all(node.parent_node_id is None or node.parent_node_id in known_ids for node in graph.nodes)
    assert all(set(node.dependencies) <= known_ids for node in graph.nodes)
    assert len(requests) == 1
    assert requests[0]["json"]["max_completion_tokens"] == SMOKE_OUTPUT_CAP
    assert len(ledger.entries("valid-strict-graph-run")) == 1
    assert provider.last_result is not None
    assert provider.last_result.diagnostics is not None
    assert provider.last_result.diagnostics.strict_schema_validation_succeeded is True
    assert provider.last_result.diagnostics.json_parsing_succeeded is True
    assert generation_result.generation_audit.provider == "openai"
    assert generation_result.generation_audit.model == MODEL
    assert generation_result.generation_audit.schema_name == "forecast_graph"
    assert generation_result.generation_audit.provider_request_id == "rid-structured-output-test"
    assert generation_result.generation_audit.requested_max_output_tokens == SMOKE_OUTPUT_CAP
    assert generation_result.generation_audit.finish_reason == "stop"
    assert generation_result.generation_audit.refusal_present is False
    assert generation_result.generation_audit.completion_tokens == 700
    assert generation_result.generation_audit.reasoning_tokens == 120
    assert generation_result.generation_audit.visible_output_tokens == 580
    assert generation_result.generation_audit.json_parsing_succeeded is True
    assert generation_result.generation_audit.schema_validation_succeeded is True
    assert generation_result.generation_audit.strict_schema_validation_succeeded is True
    assert generation_result.generation_audit.domain_validation_succeeded is True
    assert generation_result.generation_audit.errors == []
    assert graph.generation_audit == generation_result.generation_audit

    profile = effective_profile(load_profile(SMOKE_PROFILE_ID), user_max_cost_usd=0.50)
    budget = Budget(
        profile,
        provider="openai",
        model=MODEL,
        search_provider="tavily",
    )
    graph_reservation = budget.reserve_model_call(
        "forecast_graph",
        estimated_input_tokens=854,
        max_output_tokens=SMOKE_OUTPUT_CAP,
    )
    budget.reconcile_model_call(
        graph_reservation,
        ModelUsage(
            prompt_tokens=854,
            completion_tokens=700,
            cost_usd=0.02,
            model=MODEL,
            provider="openai",
        ),
    )
    plan = ResearchPlanner().plan(
        graph,
        forecast_run_id="valid-strict-graph-run",
        budget=budget,
    )

    assert plan.budget_allocation["planner_version"] == PLANNER_VERSION
    assert len(plan.selected_nodes) >= 3
    assert len(requests) == 1


def test_reasoning_heavy_graph_uses_split_envelope_and_reaches_planner_v3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _valid_graph_payload()
    requests = _install_http_response(
        monkeypatch,
        _openai_response(
            json.dumps(payload),
            completion_tokens=2_300,
            reasoning_tokens=1_700,
        ),
    )
    ledger = InMemoryUsageLedger()
    provider = _RecordingOpenAIProvider(
        api_key="stub-key-never-sent",
        base_url="https://stubbed-openai.invalid/v1",
        model=MODEL,
        provider_id="openai",
        ledger=ledger,
        run_id="reasoning-heavy-graph-run",
        stage="forecast_graph",
    )

    generated = GraphGenerator(
        provider,
        max_output_tokens=SMOKE_OUTPUT_CAP,
        max_completion_tokens=SMOKE_COMPLETION_ENVELOPE,
        max_visible_output_tokens=SMOKE_OUTPUT_CAP,
        reasoning_effort="minimal",
        verbosity="low",
    ).generate(_approved_contract())

    assert len(requests) == 1
    body = requests[0]["json"]
    assert body["max_completion_tokens"] == SMOKE_COMPLETION_ENVELOPE
    assert body["reasoning_effort"] == "minimal"
    assert body["verbosity"] == "low"
    assert "max_tokens" not in body
    assert "temperature" not in body
    entry = ledger.entries("reasoning-heavy-graph-run")[0]
    assert entry.reserved_output_tokens == SMOKE_COMPLETION_ENVELOPE
    audit = generated.generation_audit
    assert audit.requested_max_completion_tokens == SMOKE_COMPLETION_ENVELOPE
    assert audit.requested_max_visible_output_tokens == SMOKE_OUTPUT_CAP
    assert audit.completion_tokens == 2_300
    assert audit.reasoning_tokens == 1_700
    assert audit.visible_output_tokens == 600
    assert audit.token_split_available is True

    profile = effective_profile(
        load_profile(SMOKE_PROFILE_ID),
        user_max_cost_usd=0.50,
    )
    budget = Budget(
        profile,
        provider="openai",
        model=MODEL,
        search_provider="tavily",
    )
    budget.state.model_calls = 1
    budget.state.tokens = 3_154
    budget.state.prompt_tokens = 854
    budget.state.completion_tokens = 2_300
    budget.state.cost_usd = 0.04
    budget.state.model_cost_usd = 0.04
    plan = ResearchPlanner().plan(
        generated.graph,
        forecast_run_id="reasoning-heavy-graph-run",
        budget=budget,
    )
    assert plan.budget_allocation["planner_version"] == PLANNER_VERSION
    assert len(plan.selected_nodes) == 3


def test_visible_graph_budget_excess_fails_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _valid_graph_payload()
    requests = _install_http_response(
        monkeypatch,
        _openai_response(
            json.dumps(payload),
            completion_tokens=1_700,
            reasoning_tokens=100,
        ),
    )
    provider = _RecordingOpenAIProvider(
        api_key="stub-key-never-sent",
        base_url="https://stubbed-openai.invalid/v1",
        model=MODEL,
        provider_id="openai",
    )

    with pytest.raises(ForecastGraphError) as exc_info:
        GraphGenerator(
            provider,
            max_output_tokens=SMOKE_OUTPUT_CAP,
            max_completion_tokens=SMOKE_COMPLETION_ENVELOPE,
            max_visible_output_tokens=SMOKE_OUTPUT_CAP,
            reasoning_effort="minimal",
            verbosity="low",
        ).generate(_approved_contract())

    assert exc_info.value.reasons == [
        "structured_output_visible_budget_exceeded"
    ]
    assert len(requests) == 1
    diagnostics = exc_info.value.audit["structured_output"]
    assert diagnostics["visible_output_tokens"] == 1_600
    assert diagnostics["requested_max_visible_output_tokens"] == 1_536


def test_success_audit_persists_and_approved_graph_reuse_is_request_free(
    client: Any,
) -> None:
    raw_sentinel = "RAW_SUCCESS_PROVIDER_CONTENT_MUST_NOT_PERSIST"
    payload = _valid_graph_payload()
    model = _ResultModel(
        ChatResult(
            content=f"{json.dumps(payload)} {raw_sentinel}",
            parsed=payload,
            raw_error=raw_sentinel,
            usage=ModelUsage(
                prompt_tokens=854,
                completion_tokens=700,
                model=MODEL,
                provider="openai",
                request_id="rid-fl-r005-shaped-success",
            ),
            diagnostics=_diagnostics(
                provider_request_id="rid-fl-r005-shaped-success",
                finish_reason="stop",
                completion_tokens=700,
                reasoning_tokens=120,
                visible_output_tokens=580,
                content_character_count=2048,
            ),
        )
    )
    draft = client.post(
        "/api/contracts/generate",
        json={"question": "Will US unemployment exceed 5% before 30 June 2027?"},
    ).json()
    client.post(f"/api/contracts/{draft['id']}/approve").raise_for_status()

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        question = session.get(Question, draft["question_id"])
        assert question is not None
        first = ensure_execution_graph(
            session,
            question=question,
            model=model,
            max_output_tokens=SMOKE_OUTPUT_CAP,
        )
        first_run = ForecastRun(
            id="run-fl-r005-shaped-generated",
            question_id=question.id,
            profile_id=SMOKE_PROFILE_ID,
            mode="live",
            execution_context_json="{}",
        )
        session.add(first_run)
        record_execution_graph_resolution(first_run, first)
        session.commit()

        assert first.status == "generated"
        assert first.model_request_issued is True
        assert first.generation_audit is not None
        assert len(model.calls) == 1
        graph_row = session.get(ForecastGraphRow, first.graph.id)
        assert graph_row is not None
        assert graph_row.generation_audit_json is not None
        persisted_audit = json.loads(graph_row.generation_audit_json)
        assert persisted_audit == first.generation_audit.model_dump(mode="json")
        assert persisted_audit["provider_request_id"] == "rid-fl-r005-shaped-success"
        assert persisted_audit["finish_reason"] == "stop"
        assert persisted_audit["requested_max_output_tokens"] == SMOKE_OUTPUT_CAP
        assert persisted_audit["json_parsing_succeeded"] is True
        assert persisted_audit["schema_validation_succeeded"] is True
        assert persisted_audit["strict_schema_validation_succeeded"] is True
        assert persisted_audit["domain_validation_succeeded"] is True
        assert raw_sentinel not in graph_row.generation_audit_json

        second = ensure_execution_graph(
            session,
            question=question,
            model=model,
            max_output_tokens=SMOKE_OUTPUT_CAP,
        )
        second_run = ForecastRun(
            id="run-fl-r005-shaped-reused",
            question_id=question.id,
            profile_id=SMOKE_PROFILE_ID,
            mode="live",
            execution_context_json="{}",
        )
        session.add(second_run)
        record_execution_graph_resolution(second_run, second)
        session.commit()

        assert second.status == "reused"
        assert second.model_request_issued is False
        assert second.graph.id == first.graph.id
        assert second.generation_audit == first.generation_audit
        assert len(model.calls) == 1
        context = json.loads(second_run.execution_context_json)
        graph_context = context["forecast_graph_resolution"]
        assert graph_context["status"] == "reused"
        assert graph_context["model_request_issued"] is False
        assert graph_context["generation_audit"] == persisted_audit
        assert raw_sentinel not in second_run.execution_context_json
        assert forecast_graph_from_row(graph_row).generation_audit == first.generation_audit

    graph_payload = client.get(f"/api/graphs/{first.graph.id}").json()
    assert graph_payload["generation_audit"] == persisted_audit
    assert raw_sentinel not in json.dumps(graph_payload)
    report_payload = client.get(
        f"/api/forecasts/{draft['question_id']}/graph-report"
    ).json()
    assert report_payload["profile_id"] == SMOKE_PROFILE_ID
    assert report_payload["report"]["profile_id"] == SMOKE_PROFILE_ID
    assert report_payload["report"]["graph"]["generation_audit"] == persisted_audit
    assert raw_sentinel not in json.dumps(report_payload)


def test_unrelated_task_does_not_receive_graph_reasoning_or_schema_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _install_http_response(
        monkeypatch,
        _openai_response('{"probability":0.51}'),
    )
    provider = OpenAICompatibleProvider(
        api_key="stub-key-never-sent",
        base_url="https://stubbed-openai.invalid/v1",
        model=MODEL,
        provider_id="openai",
    )

    provider.complete_json(
        system="Return JSON.",
        user="Forecast.",
        schema_name="forecast_node",
        max_output_tokens=321,
        json_schema=forecast_graph_json_schema(),
        reasoning_effort="minimal",
    )

    body = requests[0]["json"]
    assert body["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in body


def test_live_smoke_context_still_exposes_profile_cap_without_changing_profile() -> None:
    context = resolve_execution_context(
        requested_mode="live",
        profile_id=SMOKE_PROFILE_ID,
        settings={
            "model_provider": "openai",
            "model_name": MODEL,
            "model_api_key": "stub-key-never-sent",
            "search_provider": "tavily",
            "search_api_key": "stub-key-never-sent",
            "max_cost_usd": 0.50,
        },
    )
    profile = apply_execution_limits(load_profile(SMOKE_PROFILE_ID), context)

    assert profile.max_output_tokens_per_call == SMOKE_OUTPUT_CAP
