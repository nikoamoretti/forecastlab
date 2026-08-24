from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

from forecastlab.budget import Budget
from forecastlab.fetch import fetch_document as normal_fetch_document
from forecastlab.graph_research import GraphResearchExecutor
from forecastlab.graphs import GraphGenerator
from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.pricing import load_pricing
from forecastlab.profiles import effective_profile, load_profile
from forecastlab.providers.mock import MockModelProvider, MockSearchProvider
from forecastlab.run_cache import RunCache
from forecastlab.schemas import ForecastContract, ForecastNode
from forecastlab.timeutil import as_utc, parse_datetime
from forecastlab_api import cutoff_consistent_mock as corpus_module
from forecastlab_api.cutoff_consistent_mock import (
    CORPUS_DIR,
    CORPUS_ID,
    CORPUS_VERSION,
    MANIFEST_PATH,
    SHADOW_COST_CEILING_USD,
    SHADOW_MODEL,
    SHADOW_MODEL_PROVIDER,
    SHADOW_SEARCH_PROVIDER,
    CutoffConsistentMockError,
    CutoffConsistentMockSearchProvider,
    ShadowPricedResearchPlanner,
    corpus_hash_for_payload,
    load_cutoff_consistent_manifest,
    rank_corpus_documents,
)

QUESTION_CUTOFFS = {
    "5a67ed2ad06d47cc4512030b6cc156b5165bb73dc01b8921fc18ddcc4d646436": "2024-04-05T00:00:00+00:00",
    "ac9fec2d3a84cecf18c74c24ce6e541369607a0dddc6084b3fb8161cfaa02ee9": "2024-07-10T00:00:00+00:00",
    "055be860e87d48b5535aa1bc699423329ec964a240ec17b604cf8b46dc43b134": "2024-02-02T00:00:00+00:00",
    "7fed57c605c9c0844062871cf1976d1925bf910034fc2f899e7ef0c32ef00475": "2024-03-15T00:00:00+00:00",
    "f90f4cff669931ce53694ce5e521444ae1fab760cb05c38feb83e0e016d6db17": "2024-01-05T00:00:00+00:00",
}
FINAL_RESOLUTION_URLS = {
    "5a67ed2ad06d47cc4512030b6cc156b5165bb73dc01b8921fc18ddcc4d646436": "https://www.bls.gov/news.release/archives/empsit_07052024.htm",
    "ac9fec2d3a84cecf18c74c24ce6e541369607a0dddc6084b3fb8161cfaa02ee9": "https://www.bls.gov/news.release/archives/cpi_10102024.htm",
    "055be860e87d48b5535aa1bc699423329ec964a240ec17b604cf8b46dc43b134": "https://www.apple.com/newsroom/2024/05/apple-reports-second-quarter-results/",
    "7fed57c605c9c0844062871cf1976d1925bf910034fc2f899e7ef0c32ef00475": "https://www.apple.com/newsroom/2024/06/introducing-apple-intelligence-for-iphone-ipad-and-mac/",
    "f90f4cff669931ce53694ce5e521444ae1fab760cb05c38feb83e0e016d6db17": "https://www.sec.gov/newsroom/speeches-statements/gensler-statement-mandatory-climate-risk-disclosures-030624",
}


def _provider(
    question_hash: str,
    *,
    cutoff: str | None = None,
    **overrides: object,
) -> CutoffConsistentMockSearchProvider:
    parsed = parse_datetime(cutoff or QUESTION_CUTOFFS[question_hash])
    assert parsed is not None
    kwargs: dict[str, object] = {
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "question_hash": question_hash,
        "forecast_cutoff": parsed,
        "synthetic_fixture_execution": True,
        "workflow_validation": True,
        "execution_mode": "backtest",
        "live_provider_involved": False,
        "real_historical_benchmark": False,
    }
    kwargs.update(overrides)
    return CutoffConsistentMockSearchProvider(**kwargs)


def _unemployment_node() -> ForecastNode:
    return ForecastNode(
        id="unemployment-node",
        graph_id="graph",
        question=(
            "What is the current unemployment U-3 trend relevant to whether the June "
            "2024 rate is at least 4.1%?"
        ),
        node_type="trend",
        importance_weight=0.85,
        dependencies=[],
        preferred_sources=["BLS", "official statistics"],
        required_output_type="directional_update",
    )


def _research_executor(
    provider: CutoffConsistentMockSearchProvider,
    *,
    cutoff: datetime,
) -> tuple[GraphResearchExecutor, Budget]:
    profile = effective_profile(
        load_profile("graph_forecaster_v1"),
        user_max_cost_usd=SHADOW_COST_CEILING_USD,
    )
    budget = Budget(profile, provider="mock", search_provider="mock")
    cache = RunCache.create(
        run_id="fixture-run",
        model_provider="mock",
        search_provider="mock",
        mode="backtest",
        as_of=cutoff,
        configuration_hash="fixture-config",
    )
    return (
        GraphResearchExecutor(
            model=MockModelProvider(),
            search=provider,
            profile=profile,
            budget=budget,
            cache=cache,
            run_id="fixture-run",
            mode="backtest",
            as_of=cutoff,
            allow_local_fixtures=True,
            max_queries_per_node=1,
            max_fetches_per_node=1,
            max_evidence_claims=2,
        ),
        budget,
    )


def test_corpus_hash_is_deterministic_and_manifest_is_frozen() -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest = load_cutoff_consistent_manifest()

    assert manifest.corpus_id == CORPUS_ID
    assert manifest.corpus_version == CORPUS_VERSION
    assert corpus_hash_for_payload(payload) == payload["corpus_hash"]
    assert corpus_hash_for_payload(payload) == corpus_hash_for_payload(
        json.loads(canonical_json(payload))
    )
    assert sha256_text(canonical_json({k: v for k, v in payload.items() if k != "corpus_hash"})) == manifest.corpus_hash


def test_every_manifest_document_exists_and_content_hash_matches() -> None:
    manifest = load_cutoff_consistent_manifest()

    assert len(manifest.documents) == 15
    for document in manifest.documents:
        path = CORPUS_DIR / document.content_file
        assert path.is_file()
        assert corpus_module._document_text(document)


def test_every_document_and_snapshot_is_at_or_before_its_question_cutoff() -> None:
    manifest = load_cutoff_consistent_manifest()

    for document in manifest.documents:
        cutoff = parse_datetime(QUESTION_CUTOFFS[document.question_hash])
        assert cutoff is not None
        assert as_utc(document.published_at) <= cutoff
        assert as_utc(document.snapshot_at) <= cutoff


def test_fixtures_are_outcome_blind_and_exclude_final_resolution_sources() -> None:
    manifest = load_cutoff_consistent_manifest()

    for document in manifest.documents:
        raw = (CORPUS_DIR / document.content_file).read_text(encoding="utf-8")
        final_url = FINAL_RESOLUTION_URLS[document.question_hash]
        assert final_url not in raw
        assert final_url != document.canonical_source_url
        assert "outcome" not in document.model_dump()
        assert document.outcome_blind is True
        assert document.synthetic_workflow_only is True
        assert "SYNTHETIC WORKFLOW EVIDENCE" in raw


def test_fixture_text_contains_no_post_cutoff_iso_timestamp() -> None:
    manifest = load_cutoff_consistent_manifest()

    for document in manifest.documents:
        cutoff = parse_datetime(QUESTION_CUTOFFS[document.question_hash])
        assert cutoff is not None
        raw = (CORPUS_DIR / document.content_file).read_text(encoding="utf-8")
        timestamps = re.findall(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\+00:00|Z)",
            raw,
        )
        assert timestamps
        for timestamp in timestamps:
            parsed = parse_datetime(timestamp)
            assert parsed is not None
            assert parsed <= cutoff


def test_each_question_has_at_least_three_eligible_documents() -> None:
    for question_hash, cutoff_text in QUESTION_CUTOFFS.items():
        provider = _provider(question_hash, cutoff=cutoff_text)
        audit = provider.audit_snapshot()
        assert len(audit["eligible_documents"]) >= 3
        assert audit["excluded_documents"] == []


def test_query_ranking_is_domain_relevant_across_the_full_corpus() -> None:
    manifest = load_cutoff_consistent_manifest()

    unemployment = rank_corpus_documents(
        manifest,
        "U.S. U-3 unemployment labor market base rate and claims",
    )
    apple = rank_corpus_documents(
        manifest,
        "Apple fiscal Q2 net sales revenue guidance and seasonality",
    )
    sec = rank_corpus_documents(
        manifest,
        "SEC Scope 3 climate disclosure proposed rule comments",
    )

    unemployment_hash = next(iter(QUESTION_CUTOFFS))
    assert all(item.document.question_hash == unemployment_hash for item in unemployment[:3])
    assert all(item.document.domain == "business" for item in apple[:3])
    assert all(item.document.domain == "regulation" for item in sec[:3])
    assert unemployment[2].score > max(
        item.score
        for item in unemployment
        if item.document.domain in {"business", "technology", "regulation"}
    )
    assert apple[2].score > max(
        item.score for item in apple if item.document.domain == "economics"
    )


def test_bound_search_returns_only_question_relevant_pre_cutoff_documents() -> None:
    question_hash = next(iter(QUESTION_CUTOFFS))
    provider = _provider(question_hash)

    hits = provider.search(
        "What does the historical U-3 unemployment reference class imply?",
        max_results=3,
    )
    audit = provider.audit_snapshot()

    assert len(hits) == 3
    assert {hit.url for hit in hits} == {
        item["fixture_url"] for item in audit["eligible_documents"]
    }
    assert all(hit.published_at <= provider.forecast_cutoff for hit in hits)
    assert audit["query_rankings"][0]["ranked_documents"][0]["score"] > 0


def test_snapshot_discovery_returns_only_the_exact_manifest_snapshot() -> None:
    question_hash = next(iter(QUESTION_CUTOFFS))
    cutoff = parse_datetime(QUESTION_CUTOFFS[question_hash])
    assert cutoff is not None
    provider = _provider(question_hash)
    hit = provider.search("unemployment U-3 trend", max_results=1)[0]
    snapshot = provider.discover_fixture_snapshots(hit.url, as_of=cutoff)
    document = next(
        item
        for item in provider.manifest.documents
        if item.fixture_url == hit.url
    )

    assert len(snapshot) == 1
    assert snapshot[0].snapshot_url == document.fixture_url
    assert snapshot[0].url == document.canonical_source_url
    assert snapshot[0].timestamp == document.snapshot_at
    assert snapshot[0].timestamp <= cutoff


@pytest.mark.parametrize(
    ("override", "error"),
    [
        ({"corpus_id": "unknown"}, "unknown_validation_corpus_id"),
        ({"synthetic_fixture_execution": False}, "synthetic_fixture_execution_required"),
        ({"workflow_validation": False}, "workflow_validation_marker_required"),
        ({"execution_mode": "live"}, "validation_adapter_backtest_mode_required"),
        ({"execution_mode": "demo"}, "validation_adapter_backtest_mode_required"),
        ({"live_provider_involved": True}, "validation_adapter_live_provider_forbidden"),
        ({"real_historical_benchmark": True}, "validation_adapter_real_benchmark_forbidden"),
    ],
)
def test_adapter_fails_closed_outside_explicit_synthetic_workflow_validation(
    override: dict[str, object],
    error: str,
) -> None:
    question_hash = next(iter(QUESTION_CUTOFFS))

    with pytest.raises(CutoffConsistentMockError, match=error):
        _provider(question_hash, **override)


def test_adapter_uses_normal_document_cutoff_validation_and_exact_manifest_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question_hash = next(iter(QUESTION_CUTOFFS))
    cutoff = parse_datetime(QUESTION_CUTOFFS[question_hash])
    assert cutoff is not None
    calls: list[dict[str, object]] = []

    def recording_fetch(url: str, **kwargs: object):
        calls.append({"url": url, **kwargs})
        return normal_fetch_document(url, **kwargs)

    monkeypatch.setattr(corpus_module, "fetch_document", recording_fetch)
    provider = _provider(question_hash)
    executor, budget = _research_executor(provider, cutoff=cutoff)

    result = executor.execute(_unemployment_node())

    assert result.failure is None
    assert len(result.claims) == 1
    assert len(calls) == 1
    assert calls[0]["mode"] == "backtest"
    assert calls[0]["as_of"] == cutoff
    assert budget.state.fetches == 1
    fetch = provider.audit_snapshot()["fetches"][0]
    assert fetch["outcome"] == "accepted"
    assert fetch["content_hash"] == result.evidence[0]["content_hash"]
    assert result.evidence[0]["snapshot_verification_status"] == "cutoff_consistent_mock_manifest_verified"


def test_known_post_cutoff_candidates_are_excluded_before_fetch_allowance() -> None:
    question_hash = next(iter(QUESTION_CUTOFFS))
    early_cutoff = parse_datetime("2023-01-01T00:00:00+00:00")
    assert early_cutoff is not None
    provider = _provider(question_hash, cutoff=early_cutoff.isoformat())
    executor, budget = _research_executor(provider, cutoff=early_cutoff)

    result = executor.execute(_unemployment_node())
    audit = provider.audit_snapshot()

    assert result.failure is not None
    assert result.failure.code == "no_matching_source"
    assert len(audit["excluded_documents"]) == 3
    assert audit["fetches"] == []
    assert budget.state.fetches == 0


def test_corpus_contents_do_not_change_with_supplied_as_of() -> None:
    question_hash = next(iter(QUESTION_CUTOFFS))
    assigned = _provider(question_hash)
    earlier = _provider(question_hash, cutoff="2023-01-01T00:00:00+00:00")

    assert assigned.manifest.model_dump(mode="json") == earlier.manifest.model_dump(mode="json")
    assert len(assigned.audit_snapshot()["eligible_documents"]) == 3
    assert len(earlier.audit_snapshot()["eligible_documents"]) == 0


def test_ordinary_mock_search_behavior_is_unchanged() -> None:
    provider = MockSearchProvider()
    hits = provider.search("unemployment BLS", max_results=2)

    assert [hit.url for hit in hits] == [
        "https://fixtures.forecastlab.local/bls-employment-situation",
        "https://fixtures.forecastlab.local/fred-unrate",
    ]


def test_shadow_pricing_uses_existing_planner_without_charging_mock_execution() -> None:
    created_at = parse_datetime("2026-08-24T00:00:00+00:00")
    resolution = parse_datetime("2027-01-01T00:00:00+00:00")
    assert created_at is not None
    assert resolution is not None
    contract = ForecastContract(
        id="contract",
        question_id="question",
        created_at=created_at,
        created_by="test",
        original_question="Will the unemployment rate exceed 5%?",
        normalized_question="Will the U.S. unemployment rate exceed 5% by 2027?",
        yes_condition="The rate exceeds 5%.",
        no_condition="The rate does not exceed 5%.",
        resolution_date=resolution,
        authoritative_source="https://www.bls.gov/",
        resolution_method="Read the official release.",
        initial_reference_class="historical U.S. unemployment thresholds",
        suggested_drivers=["labor demand"],
        known_dependencies=["monetary policy"],
        status="approved",
    )
    graph = GraphGenerator(MockModelProvider()).generate(contract)
    profile = effective_profile(
        load_profile("graph_forecaster_v1"),
        user_max_cost_usd=SHADOW_COST_CEILING_USD,
    )
    actual_budget = Budget(
        profile,
        provider="mock",
        model="mock-forecast-v1",
        search_provider="mock",
    )
    planner = ShadowPricedResearchPlanner(
        pricing_catalog=load_pricing()
    )

    plan = planner.plan(graph, forecast_run_id="run", budget=actual_budget)
    shadow = plan.budget_allocation["shadow_pricing"]
    critical = {
        node.id for node in graph.nodes if node.importance_weight >= 0.8
    }

    assert 3 <= len(plan.selected_nodes) <= 8
    assert critical <= set(plan.selected_nodes)
    assert float(shadow["estimated_total_cost_usd"]) <= SHADOW_COST_CEILING_USD
    assert shadow["model_provider"] == SHADOW_MODEL_PROVIDER
    assert shadow["model"] == SHADOW_MODEL
    assert shadow["search_provider"] == SHADOW_SEARCH_PROVIDER
    assert shadow["actual_provider_instances_created"] is False
    assert shadow["api_keys_loaded"] is False
    assert actual_budget.state.cost_usd == 0.0
    assert actual_budget.state.model_cost_usd == 0.0
    assert actual_budget.state.search_cost_usd == 0.0
