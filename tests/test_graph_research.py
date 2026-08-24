from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from forecastlab.budget import Budget
from forecastlab.errors import PermanentProviderError
from forecastlab.graph_research import GraphResearchExecutor
from forecastlab.providers.base import ChatResult
from forecastlab.providers.mock import MockModelProvider, MockSearchProvider
from forecastlab.run_cache import RunCache
from forecastlab.schemas import ForecastNode, ForecastProfile, ModelUsage


def _profile() -> ForecastProfile:
    return ForecastProfile(
        id="graph-research-test",
        label="Graph research test",
        description="Bounded mock-only graph research.",
        execution_strategy="graph_nodes",
        tracks=["single_agent"],
        search_results_per_subquestion=2,
        fetches_per_subquestion=1,
        max_model_calls=10,
        max_search_calls=8,
        max_fetched_documents=8,
        max_tokens=40_000,
        max_estimated_cost_usd=1.0,
    )


def _node(*, preferred_sources: list[str] | None = None) -> ForecastNode:
    return ForecastNode(
        id="node-research-1",
        graph_id="graph-research-1",
        question="What historical labor-market evidence bears on this outcome?",
        node_type="base_rate",
        importance_weight=0.9,
        preferred_sources=preferred_sources or ["BLS", "NBER"],
        required_output_type="probability",
    )


def _executor(
    *,
    search=None,
    model=None,
    mode: str = "demo",
    as_of: datetime | None = None,
) -> GraphResearchExecutor:
    profile = _profile()
    return GraphResearchExecutor(
        model=model or MockModelProvider(),
        search=search or MockSearchProvider(),
        profile=profile,
        budget=Budget(profile),
        cache=RunCache.create(
            run_id="run-research-1",
            model_provider="mock",
            search_provider="mock",
            mode=mode,
            as_of=as_of,
            configuration_hash="graph-research-test",
        ),
        run_id="run-research-1",
        mode=mode,
        as_of=as_of,
        allow_local_fixtures=True,
        prompt_versions={},
    )


class CapturingSearch:
    name = "mock"

    def __init__(self) -> None:
        self.delegate = MockSearchProvider()
        self.queries: list[str] = []

    def search(self, query: str, *, max_results: int = 5):
        self.queries.append(query)
        return self.delegate.search(query, max_results=max_results)


class EmptySearch:
    name = "mock"

    def search(self, _query: str, *, max_results: int = 5):
        return []


class FailingSearch:
    name = "mock"

    def search(self, _query: str, *, max_results: int = 5):
        raise PermanentProviderError("forced_search_failure")


class EmptyClaimModel:
    name = "stub"
    model = "stub-empty-claims"

    def complete_json(self, **kwargs: Any) -> ChatResult:
        schema_name = kwargs["schema_name"]
        if schema_name == "graph_research_plan":
            node = json.loads(kwargs["user"])["node"]
            payload = {
                "primary_research_question": node["question"],
                "supporting_search_queries": [node["question"]],
                "preferred_sources": node["preferred_sources"],
                "required_evidence_types": ["dated primary record"],
            }
        elif schema_name == "evidence_claims":
            payload = {"claims": []}
        else:
            raise AssertionError(f"Unexpected schema: {schema_name}")
        return ChatResult(
            content=json.dumps(payload),
            parsed=payload,
            usage=ModelUsage(model=self.model, provider=self.name),
        )


class FailingExtractionModel(EmptyClaimModel):
    model = "stub-failing-extraction"

    def complete_json(self, **kwargs: Any) -> ChatResult:
        if kwargs["schema_name"] == "evidence_claims":
            raise PermanentProviderError("forced_extraction_failure")
        return super().complete_json(**kwargs)


def test_node_generates_specific_research_queries() -> None:
    search = CapturingSearch()

    result = _executor(search=search).execute(_node())

    assert result.failure is None
    assert result.plan.primary_research_question == _node().question
    assert result.plan.preferred_sources[:2] == ["BLS", "NBER"]
    assert "historical frequency" in result.plan.required_evidence_types
    assert 2 <= len(result.queries_attempted) <= 4
    assert search.queries == result.queries_attempted
    assert any("BLS" in query for query in result.queries_attempted)


def test_retrieval_creates_node_linked_evidence_claims() -> None:
    result = _executor().execute(_node())

    assert result.failure is None
    assert result.evidence
    assert result.claims
    assert all(claim.forecast_node_id == _node().id for claim in result.claims)
    assert all(claim.excerpt for claim in result.claims)
    assert all(claim.source_url for claim in result.claims)
    assert any(source["outcome"] == "claims_created" for source in result.sources_checked)


def test_historical_research_skips_post_cutoff_documents_and_uses_eligible_source() -> None:
    cutoff = datetime(2024, 2, 1, tzinfo=UTC)

    result = _executor(mode="backtest", as_of=cutoff).execute(
        _node(preferred_sources=["NBER"])
    )

    assert result.failure is None
    assert result.claims
    assert all(claim.publication_date <= cutoff for claim in result.claims)
    assert all(claim.cutoff_verified for claim in result.claims)
    assert any(item["rejection_reason"] == "published_after_as_of" for item in result.rejected)
    assert any("nber-cycles" in claim.source_url for claim in result.claims)
    assert all("published on or before 2024-02-01" in query for query in result.queries_attempted)


def test_no_pre_cutoff_document_produces_structured_cutoff_failure() -> None:
    result = _executor(
        mode="backtest",
        as_of=datetime(2023, 1, 1, tzinfo=UTC),
    ).execute(_node())

    assert result.claims == []
    assert result.failure is not None
    assert result.failure.code == "cutoff_rejection"
    assert result.queries_attempted
    assert result.sources_checked
    assert {source["outcome"] for source in result.sources_checked} == {
        "cutoff_rejection"
    }


def test_missing_or_unusable_sources_produce_specific_structured_failures() -> None:
    no_match = _executor(search=EmptySearch()).execute(_node())
    retrieval = _executor(search=FailingSearch()).execute(_node())
    extraction = _executor(model=EmptyClaimModel()).execute(_node())
    provider_extraction = _executor(model=FailingExtractionModel()).execute(_node())

    assert no_match.failure is not None
    assert no_match.failure.code == "no_matching_source"
    assert no_match.queries_attempted
    assert no_match.sources_checked == []
    assert retrieval.failure is not None
    assert retrieval.failure.code == "retrieval_failure"
    assert "forced_search_failure" in retrieval.failure.reason
    assert extraction.failure is not None
    assert extraction.failure.code == "extraction_failure"
    assert extraction.sources_checked[0]["outcome"] == "extraction_failure"
    assert provider_extraction.failure is not None
    assert provider_extraction.failure.code == "extraction_failure"
    assert "forced_extraction_failure" in provider_extraction.failure.reason
