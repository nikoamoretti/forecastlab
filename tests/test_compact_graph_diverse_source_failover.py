from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from forecastlab.budget import Budget
from forecastlab.graph_research import GraphResearchExecutor
from forecastlab.graphs import ForecastGraphError, GraphGenerator
from forecastlab.profiles import load_profile
from forecastlab.providers.base import ChatResult, StructuredOutputDiagnostics
from forecastlab.providers.mock import MockModelProvider
from forecastlab.run_cache import RunCache
from forecastlab.schemas import (
    FetchedDocument,
    ForecastContract,
    ForecastGraph,
    ForecastNode,
    ForecastProfile,
    ModelUsage,
    SearchHit,
)
from forecastlab.timeutil import utcnow

SMOKE_PROFILE_ID = "graph_live_smoke_v1"
COMPACT_SCHEMA = "forecast_graph_compact_indexed_v1"


def _contract() -> ForecastContract:
    return ForecastContract(
        id="contract-compact-graph",
        question_id="question-compact-graph",
        version=1,
        created_at=datetime(2026, 8, 26, tzinfo=UTC),
        created_by="test",
        original_question="Will a qualifying event occur by the deadline?",
        normalized_question="Will a qualifying event occur by 31 December 2026?",
        yes_condition="The authoritative resolver records the qualifying event.",
        no_condition="The authoritative resolver records no qualifying event.",
        resolution_date=datetime(2027, 1, 15, tzinfo=UTC),
        authoritative_source="https://official.example.gov/resolver",
        resolution_method="Read the first official resolver publication.",
        initial_reference_class="Comparable scheduled binary outcomes.",
        suggested_drivers=["current trend"],
        known_dependencies=["institutional reporting"],
        status="approved",
    )


def _compact_payload() -> dict[str, Any]:
    rows = [
        ("What historical base rate applies?", "base_rate", 0.82, None, [], "probability"),
        ("What is the current measurable trend?", "trend", 0.74, None, [], "directional_update"),
        ("What primary driver changes the outcome?", "driver", 0.86, None, [1], "directional_update"),
        ("What dependency constrains that driver?", "dependency", 0.63, 2, [1], "structured_categorical"),
        ("What alternative scenario matters most?", "scenario", 0.69, 2, [3], "scenario_weight"),
        ("What evidence could overturn the leading case?", "adversarial", 0.79, None, [1, 4], "directional_update"),
        ("What resolver mechanics could alter scoring?", "resolver", 0.61, None, [], "structured_categorical"),
        ("What secondary driver could change timing?", "driver", 0.52, None, [1], "bounded_quantity"),
    ]
    return {
        "n": [
            {
                "q": question,
                "t": node_type,
                "w": weight,
                "p": parent,
                "d": dependencies,
                "s": ["official primary source"],
                "o": output_type,
            }
            for question, node_type, weight, parent, dependencies, output_type in rows
        ]
    }


def _canonical_payload(compact: dict[str, Any]) -> dict[str, Any]:
    local_ids = [f"n{index}" for index in range(len(compact["n"]))]
    return {
        "nodes": [
            {
                "id": local_ids[index],
                "parent_node_id": (
                    local_ids[node["p"]] if node["p"] is not None else None
                ),
                "question": node["q"],
                "node_type": node["t"],
                "importance_weight": node["w"],
                "dependencies": [local_ids[item] for item in node["d"]],
                "preferred_sources": node["s"],
                "required_output_type": node["o"],
                "status": "pending",
            }
            for index, node in enumerate(compact["n"])
        ]
    }


class _GraphModel:
    name = "openai"
    model = "gpt-5-mini-2025-08-07"

    def __init__(
        self,
        payload: dict[str, Any],
        *,
        visible_tokens: int = 900,
        raw_content: str | None = None,
    ) -> None:
        self.payload = payload
        self.visible_tokens = visible_tokens
        self.raw_content = raw_content
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, **kwargs: Any) -> ChatResult:
        self.calls.append(dict(kwargs))
        content = self.raw_content or json.dumps(
            self.payload,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return ChatResult(
            content=content,
            parsed=self.payload,
            usage=ModelUsage(
                prompt_tokens=420,
                completion_tokens=1_900,
                model=self.model,
                provider=self.name,
            ),
            diagnostics=StructuredOutputDiagnostics(
                schema_name=str(kwargs["schema_name"]),
                provider_request_id="rid-compact-offline",
                finish_reason="stop",
                requested_max_output_tokens=int(kwargs["max_output_tokens"]),
                requested_max_completion_tokens=int(
                    kwargs["max_completion_tokens"]
                ),
                requested_max_visible_output_tokens=int(
                    kwargs["max_visible_output_tokens"]
                ),
                reasoning_effort=str(kwargs["reasoning_effort"]),
                verbosity=str(kwargs["verbosity"]),
                completion_tokens=1_900,
                reasoning_tokens=1_000,
                visible_output_tokens=self.visible_tokens,
                token_split_available=True,
                token_split_interpretation="provider_reported_reasoning_split",
                content_character_count=len(content),
                json_parsing_succeeded=True,
                strict_schema_validation_succeeded=True,
            ),
        )


def _generator(model: _GraphModel, *, compact: bool = True) -> GraphGenerator:
    profile = load_profile(SMOKE_PROFILE_ID)
    return GraphGenerator(
        model,
        max_output_tokens=profile.max_output_tokens_per_call,
        max_completion_tokens=profile.graph_generation_max_completion_tokens,
        max_visible_output_tokens=profile.graph_generation_max_visible_output_tokens,
        reasoning_effort=profile.graph_generation_reasoning_effort,
        verbosity=profile.graph_generation_verbosity,
        transport=profile.graph_generation_transport if compact else None,
        transport_max_characters=(
            profile.graph_generation_transport_max_characters if compact else None
        ),
        node_question_max_characters=(
            profile.graph_generation_node_question_max_characters
            if compact
            else None
        ),
        local_id_max_characters=(
            profile.graph_generation_local_id_max_characters if compact else None
        ),
        max_dependencies_per_node=(
            profile.graph_generation_max_dependencies_per_node
            if compact
            else None
        ),
        max_preferred_sources_per_node=(
            profile.graph_generation_max_preferred_sources_per_node
            if compact
            else None
        ),
        preferred_source_max_characters=(
            profile.graph_generation_preferred_source_max_characters
            if compact
            else None
        ),
    )


def _graph_semantics(graph: ForecastGraph) -> list[dict[str, Any]]:
    index_by_id = {node.id: index for index, node in enumerate(graph.nodes)}
    return [
        {
            "question": node.question,
            "node_type": node.node_type,
            "importance_weight": node.importance_weight,
            "parent": (
                index_by_id[node.parent_node_id]
                if node.parent_node_id is not None
                else None
            ),
            "dependencies": [index_by_id[item] for item in node.dependencies],
            "preferred_sources": node.preferred_sources,
            "required_output_type": node.required_output_type,
            "status": node.status,
        }
        for node in graph.nodes
    ]


def test_q4_shaped_compact_graph_fits_and_restores_approved_canonical_graph() -> None:
    payload = _compact_payload()
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    model = _GraphModel(payload, raw_content=raw)

    result = _generator(model).generate(_contract())

    assert len(raw) <= 4_200
    assert len(raw) // 4 <= 1_536
    assert len(model.calls) == 1
    request = model.calls[0]
    assert request["schema_name"] == COMPACT_SCHEMA
    assert "stable-local-id" not in request["system"]
    assert '"status": "pending"' not in request["system"]
    assert "zero-based indexes" in request["system"]
    assert set(request["json_schema"]["properties"]) == {"n"}
    node_schema = request["json_schema"]["properties"]["n"]["items"]
    assert set(node_schema["properties"]) == {"q", "t", "w", "p", "d", "s", "o"}
    assert "UUID" not in json.dumps(request["json_schema"])
    assert result.graph.status == "approved"
    assert len(result.graph.nodes) == 8
    assert result.generation_audit.transport == "compact_indexed_v1"
    assert result.generation_audit.transport_character_count == len(raw)
    assert result.generation_audit.transport_max_characters == 4_200
    assert result.generation_audit.visible_output_tokens == 900


def test_compact_and_canonical_transports_have_identical_graph_semantics() -> None:
    compact = _compact_payload()
    compact_result = _generator(_GraphModel(compact)).generate(_contract())
    canonical = _canonical_payload(compact)
    canonical_result = _generator(
        _GraphModel(canonical),
        compact=False,
    ).generate(_contract())

    assert _graph_semantics(compact_result.graph) == _graph_semantics(
        canonical_result.graph
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["n"][0].update(q="q" * 141),
        lambda payload: payload["n"][0].update(s=["s" * 49]),
        lambda payload: payload["n"][0].update(s=["a", "b", "c"]),
        lambda payload: payload["n"][0].update(d=[1, 2, 3, 4]),
        lambda payload: payload["n"][0].update(p=99),
        lambda payload: payload["n"][0].pop("p"),
    ],
)
def test_invalid_compact_graph_shapes_fail_closed(mutate: Any) -> None:
    payload = deepcopy(_compact_payload())
    mutate(payload)
    model = _GraphModel(payload)

    with pytest.raises(ForecastGraphError) as exc_info:
        _generator(model).generate(_contract())

    assert exc_info.value.reasons == ["structured_output_schema_invalid"]
    assert len(model.calls) == 1


def test_compact_transport_rejects_raw_json_over_character_budget() -> None:
    payload = _compact_payload()
    raw = json.dumps(payload, indent=500)
    assert len(raw) > 4_200
    model = _GraphModel(payload, raw_content=raw)

    with pytest.raises(ForecastGraphError) as exc_info:
        _generator(model).generate(_contract())

    assert exc_info.value.reasons == [
        "structured_output_transport_character_budget_exceeded"
    ]
    assert len(model.calls) == 1


@pytest.mark.parametrize("failure_kind", ["duplicate_question", "cycle"])
def test_compact_relationship_domain_failures_are_not_repaired(
    failure_kind: str,
) -> None:
    payload = deepcopy(_compact_payload())
    if failure_kind == "duplicate_question":
        payload["n"][1]["q"] = payload["n"][0]["q"]
    else:
        payload["n"][0]["d"] = [1]
        payload["n"][1]["d"] = [0]
    model = _GraphModel(payload)

    with pytest.raises(ForecastGraphError) as exc_info:
        _generator(model).generate(_contract())

    expected = (
        ["graph_domain_validation_failed"]
        if failure_kind == "duplicate_question"
        else ["structured_output_schema_invalid"]
    )
    assert exc_info.value.reasons == expected
    if failure_kind == "cycle":
        assert any(
            error["error_type"] == "non_topological_reference"
            for error in exc_info.value.audit["schema_validation_errors"]
        )
    assert len(model.calls) == 1


def test_compact_transport_instructs_and_enforces_topological_relationships() -> None:
    model = _GraphModel(_compact_payload())
    _generator(model).generate(_contract())

    assert model.calls[0]["system"].count("strictly smaller than i") == 1


def test_visible_output_guard_precedes_compact_conversion_and_never_retries() -> None:
    model = _GraphModel(_compact_payload(), visible_tokens=1_746)

    with pytest.raises(ForecastGraphError) as exc_info:
        _generator(model).generate(_contract())

    assert exc_info.value.reasons == [
        "structured_output_visible_budget_exceeded"
    ]
    assert len(model.calls) == 1


def test_profile_rejects_incoherent_candidate_pool_and_transport_controls() -> None:
    profile = load_profile(SMOKE_PROFILE_ID).model_dump(mode="json")
    profile["search_candidate_pool_per_node"] = 1
    with pytest.raises(ValidationError):
        ForecastProfile.model_validate(profile)

    profile = load_profile(SMOKE_PROFILE_ID).model_dump(mode="json")
    profile["graph_generation_transport"] = None
    with pytest.raises(ValidationError):
        ForecastProfile.model_validate(profile)


class _FiveCandidateSearch:
    name = "tavily"

    def __init__(self, *, all_blocked_host: bool = False) -> None:
        self.calls = 0
        self.max_results: list[int] = []
        self.all_blocked_host = all_blocked_host

    def search(self, _query: str, *, max_results: int = 5) -> list[SearchHit]:
        self.calls += 1
        self.max_results.append(max_results)
        hosts = (
            ["blocked.example.gov"] * 5
            if self.all_blocked_host
            else [
                "blocked.example.gov",
                "blocked.example.gov",
                "alternate-c.example.gov",
                "alternate-d.example.gov",
                "alternate-e.example.gov",
            ]
        )
        return [
            SearchHit(
                title=f"Candidate {index}",
                url=f"https://{host}/source-{index}",
                snippet="Directly relevant official source for the selected node.",
                score=5.0 - index * 0.1,
                source_class="primary",
            )
            for index, host in enumerate(hosts, start=1)
        ][:max_results]


def _research_node() -> ForecastNode:
    return ForecastNode(
        id="node-source-failover",
        graph_id="graph-source-failover",
        question="What official evidence resolves the selected forecast node?",
        node_type="resolver",
        importance_weight=0.8,
        preferred_sources=["official.example.gov"],
        required_output_type="structured_categorical",
    )


def _document(url: str, *, rejected: str | None = None) -> FetchedDocument:
    observed = utcnow()
    text = (
        "The official source records a directly relevant fact for this selected "
        "forecast node and preserves its provenance."
    )
    status = 403 if rejected == "http_403" else 200
    return FetchedDocument(
        url=url,
        title="Official relevant source" if rejected is None else "",
        publisher="Official Agency" if rejected is None else None,
        published_at=None,
        retrieved_at=observed,
        source_available_at=observed,
        temporal_basis="retrieval_date",
        text=text if rejected is None else "",
        content_hash="accepted-content-hash" if rejected is None else "",
        status_code=status,
        rejected=rejected is not None,
        rejection_reason=rejected,
        as_of_eligible=rejected is None,
        published_at_unknown=True,
    )


def _research_executor(
    search: _FiveCandidateSearch,
    *,
    mode: str = "live",
    as_of: datetime | None = None,
) -> GraphResearchExecutor:
    profile = load_profile(SMOKE_PROFILE_ID)
    budget = Budget(
        profile,
        provider="mock",
        model="mock-forecast-v1",
        search_provider="mock",
    )
    cache = RunCache.create(
        run_id="run-source-failover",
        model_provider="mock",
        search_provider="tavily",
        mode=mode,
        as_of=as_of,
        configuration_hash="offline-source-failover",
    )
    return GraphResearchExecutor(
        model=MockModelProvider(),
        search=search,
        profile=profile,
        budget=budget,
        cache=cache,
        run_id="run-source-failover",
        mode=mode,
        as_of=as_of,
        allow_local_fixtures=False,
        max_queries_per_node=1,
        max_fetches_per_node=2,
        target_successful_documents_per_node=1,
        max_candidate_fetch_attempts_per_node=2,
        search_candidate_pool_per_node=5,
        prefer_distinct_candidate_hosts=True,
        enable_extraction_fallbacks=False,
        max_research_plan_model_calls=1,
        max_primary_extraction_calls=1,
        max_extraction_retry_calls=0,
    )


def test_q5_shaped_access_block_uses_distinct_host_from_same_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = _FiveCandidateSearch()
    fetches: list[str] = []

    def fetch(url: str, **_kwargs: Any) -> FetchedDocument:
        fetches.append(url)
        return _document(url, rejected="http_403") if "source-1" in url else _document(url)

    monkeypatch.setattr("forecastlab.run_cache.fetch_document", fetch)

    executor = _research_executor(search)
    result = executor.execute(_research_node())

    assert search.calls == 1
    assert search.max_results == [5]
    assert fetches == [
        "https://blocked.example.gov/source-1",
        "https://alternate-c.example.gov/source-3",
    ]
    assert len(result.claims) == 1
    assert result.claims[0].source_url == fetches[1]
    assert result.failure is None
    by_url = {source["url"]: source for source in result.sources_checked}
    blocked = by_url["https://blocked.example.gov/source-1"]
    skipped = by_url["https://blocked.example.gov/source-2"]
    accepted = by_url["https://alternate-c.example.gov/source-3"]
    assert blocked["actual_fetch_attempt"] is True
    assert blocked["http_status"] == 403
    assert blocked["access_blocked_host"] is True
    assert skipped["actual_fetch_attempt"] is False
    assert skipped["outcome"] == "skipped_access_blocked_host"
    assert skipped["skipped_access_blocked_host"] is True
    assert accepted["ranked_position"] == 3
    assert accepted["scheduled_position"] == 2
    assert accepted["diversity_reordered"] is True
    assert accepted["backup_used"] is True
    assert accepted["claim_created"] is True
    assert all("body" not in source for source in result.sources_checked)


def test_all_candidates_on_blocked_host_consume_one_fetch_then_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = _FiveCandidateSearch(all_blocked_host=True)
    fetches: list[str] = []

    def fetch(url: str, **_kwargs: Any) -> FetchedDocument:
        fetches.append(url)
        return _document(url, rejected="http_403")

    monkeypatch.setattr("forecastlab.run_cache.fetch_document", fetch)

    result = _research_executor(search).execute(_research_node())

    assert len(fetches) == 1
    assert result.claims == []
    assert result.failure is not None
    assert result.failure.code == "retrieval_failure"
    assert sum(
        source["outcome"] == "skipped_access_blocked_host"
        for source in result.sources_checked
    ) == 4


def test_distinct_host_alternate_failure_stops_at_two_attempts_without_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = _FiveCandidateSearch()
    fetches: list[str] = []

    def fetch(url: str, **_kwargs: Any) -> FetchedDocument:
        fetches.append(url)
        if "source-1" in url:
            return _document(url, rejected="http_403")
        return _document(url, rejected="access_wall_or_challenge")

    monkeypatch.setattr("forecastlab.run_cache.fetch_document", fetch)

    result = _research_executor(search).execute(_research_node())

    assert len(fetches) == 2
    assert len(set(fetches)) == 2
    assert search.calls == 1
    assert result.claims == []
    assert result.failure is not None
    assert result.failure.code == "retrieval_failure"


def test_historical_distinct_host_alternate_cannot_bypass_snapshot_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = _FiveCandidateSearch()
    fetches: list[str] = []
    cutoff = datetime(2025, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(
        "forecastlab.graph_research.discover_snapshots",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "forecastlab.run_cache.fetch_document",
        lambda url, **_kwargs: fetches.append(url),
    )

    result = _research_executor(
        search,
        mode="backtest",
        as_of=cutoff,
    ).execute(_research_node())

    assert fetches == []
    assert result.claims == []
    assert result.failure is not None
    assert result.failure.code == "cutoff_rejection"
    assert all(
        source["outcome"] == "cutoff_rejection"
        for source in result.sources_checked
    )
