from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from forecastlab.aggregation import AggregationBreakdown, TrackContribution, logit
from forecastlab.budget import Budget, estimate_prompt_tokens
from forecastlab.engine import EngineResult, ProgressFn, TrackResult
from forecastlab.errors import StructuredOutputError
from forecastlab.execution import ExecutionContext, assert_no_fixture_evidence
from forecastlab.ledger import RunUsageTotals, UsageLedger
from forecastlab.profiles import load_profile
from forecastlab.prompts import PromptBundle, load_prompt
from forecastlab.providers.base import ModelProvider, SearchProvider
from forecastlab.ranking import rank_hits
from forecastlab.run_cache import RunCache
from forecastlab.schemas import (
    Driver,
    FetchedDocument,
    ForecastContract,
    ForecastProfile,
    SingleModelForecastOutput,
    TrackForecastOutput,
)
from forecastlab.timeutil import as_utc, utcnow
from forecastlab.wayback import discover_snapshots, mock_snapshots, nearest_eligible_snapshot

DIRECT_MODEL_METHOD = "direct_model_probability_v1"


def _emit(
    progress: ProgressFn | None,
    stage: str,
    message: str,
    pct: float,
) -> None:
    if progress is not None:
        progress(stage, message, pct, None)


def _stable_id(kind: str, *parts: str) -> str:
    identity = ":".join(("forecastlab", "single_model_forecaster_v1", kind, *parts))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))


def _document_record(
    document: FetchedDocument,
    *,
    run_id: str,
    query: str,
    source_class: str,
) -> dict[str, Any]:
    return {
        "id": _stable_id("evidence", run_id, document.url),
        "track_type": "single_agent",
        "subquestion": query,
        "url": document.url,
        "title": document.title,
        "publisher": document.publisher,
        "published_at": document.published_at.isoformat() if document.published_at else None,
        "retrieved_at": document.retrieved_at.isoformat(),
        "excerpt": (document.text or "")[:800],
        "content_hash": document.content_hash,
        "source_class": source_class,
        "as_of_eligible": document.as_of_eligible,
        "rejected": document.rejected,
        "rejection_reason": document.rejection_reason,
        "snapshot_url": document.snapshot_url,
        "snapshot_at": document.snapshot_at.isoformat() if document.snapshot_at else None,
        "requested_snapshot_url": document.requested_snapshot_url,
        "requested_snapshot_at": (
            document.requested_snapshot_at.isoformat() if document.requested_snapshot_at else None
        ),
        "final_snapshot_url": document.final_snapshot_url,
        "final_snapshot_at": (
            document.final_snapshot_at.isoformat() if document.final_snapshot_at else None
        ),
        "archived_original_url": document.archived_original_url,
        "snapshot_verification_status": document.snapshot_verification_status,
        "status_code": document.status_code,
        "published_at_unknown": document.published_at_unknown,
    }


def _no_snapshot_record(
    *,
    run_id: str,
    query: str,
    url: str,
    title: str,
    source_class: str,
) -> dict[str, Any]:
    return {
        "id": _stable_id("evidence", run_id, url),
        "track_type": "single_agent",
        "subquestion": query,
        "url": url,
        "title": title,
        "publisher": None,
        "published_at": None,
        "retrieved_at": utcnow().isoformat(),
        "excerpt": "",
        "content_hash": "",
        "source_class": source_class,
        "as_of_eligible": False,
        "rejected": True,
        "rejection_reason": "no_eligible_historical_snapshot",
        "snapshot_url": None,
        "snapshot_at": None,
        "requested_snapshot_url": None,
        "requested_snapshot_at": None,
        "final_snapshot_url": None,
        "final_snapshot_at": None,
        "archived_original_url": url,
        "snapshot_verification_status": "no_eligible_historical_snapshot",
        "status_code": 0,
        "published_at_unknown": True,
    }


def _collect_evidence(
    *,
    contract: ForecastContract,
    run_id: str,
    profile: ForecastProfile,
    search: SearchProvider,
    budget: Budget,
    cache: RunCache,
    mode: str,
    as_of: datetime | None,
    allow_local_fixtures: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if profile.max_search_calls <= 0 or profile.max_fetched_documents <= 0:
        return evidence, rejected

    query = " ".join(
        part
        for part in (
            contract.normalized_question,
            contract.authoritative_source,
        )
        if part
    ).strip()
    budget.add_search("single_model_evidence_search")
    hits = rank_hits(cache.search(search, query, profile.search_results_per_subquestion))
    fetch_limit = min(profile.fetches_per_subquestion, profile.max_fetched_documents)
    for hit in hits[:fetch_limit]:
        snapshot_url = None
        snapshot_at = None
        if mode == "backtest" and as_of is not None:
            snapshots = (
                mock_snapshots(hit.url)
                if allow_local_fixtures
                else discover_snapshots(hit.url, as_of=as_of)
            )
            nearest = nearest_eligible_snapshot(snapshots, as_of)
            if nearest is None:
                rejected.append(
                    _no_snapshot_record(
                        run_id=run_id,
                        query=query,
                        url=hit.url,
                        title=hit.title,
                        source_class=hit.source_class,
                    )
                )
                continue
            snapshot_url = nearest.snapshot_url
            snapshot_at = nearest.timestamp

        budget.add_fetch("single_model_evidence_fetch")
        document = cache.fetch(
            hit.url,
            as_of=as_of if mode == "backtest" else None,
            allow_local_fixtures=allow_local_fixtures,
            snapshot_url=snapshot_url,
            snapshot_at=snapshot_at,
            mode=mode,
        )
        record = _document_record(
            document,
            run_id=run_id,
            query=query,
            source_class=hit.source_class,
        )
        if document.rejected or not document.as_of_eligible:
            rejected.append(record)
        else:
            evidence.append(record)
    return evidence, rejected


def _forecast_once(
    *,
    contract: ForecastContract,
    evidence: list[dict[str, Any]],
    model: ModelProvider,
    budget: Budget,
    prompt_versions: dict[str, str],
    prompt_bundle: PromptBundle | None,
) -> SingleModelForecastOutput:
    if prompt_bundle is not None:
        system, version = prompt_bundle.get("forecast_single_model")
    else:
        system, version = load_prompt("forecast_single_model")
    prompt_versions["forecast_single_model"] = version
    user = json.dumps(
        {
            "forecast_contract": contract.model_dump(mode="json"),
            "evidence_packet": [
                {
                    "id": item["id"],
                    "url": item["url"],
                    "title": item["title"],
                    "publisher": item["publisher"],
                    "publication_date": item["published_at"],
                    "retrieval_date": item["retrieved_at"],
                    "excerpt": item["excerpt"],
                    "source_class": item["source_class"],
                }
                for item in evidence
            ],
            "instruction": (
                "Return one probability, reasoning, material uncertainty, and only supplied evidence ids."
            ),
        },
        default=str,
    )
    estimated_input = estimate_prompt_tokens(system, user)
    max_output = budget.max_output_tokens_for_call(estimated_input)
    reservation = budget.reserve_model_call(
        "single_model_forecast",
        estimated_input_tokens=estimated_input,
        max_output_tokens=max_output,
    )
    try:
        response = model.complete_json(
            system=system,
            user=user,
            schema_name="single_model_forecast",
            max_output_tokens=max_output,
            estimated_input_tokens=estimated_input,
        )
    except Exception:
        budget.release_reservation(reservation)
        raise
    budget.reconcile_model_call(reservation, response.usage)
    try:
        parsed = response.parsed if response.parsed is not None else json.loads(response.content)
        output = SingleModelForecastOutput.model_validate(parsed)
    except (ValidationError, json.JSONDecodeError, TypeError) as exc:
        raise StructuredOutputError("invalid_structured_output:single_model_forecast") from exc

    allowed_ids = {str(item["id"]) for item in evidence}
    unsupported = [evidence_id for evidence_id in output.evidence_ids if evidence_id not in allowed_ids]
    if unsupported:
        raise StructuredOutputError("single_model_forecast_unsupported_evidence_reference")
    return output


def _direct_probability_breakdown(probability: float) -> AggregationBreakdown:
    probability_logit = logit(probability)
    contribution = TrackContribution(
        track_type="single_agent",
        raw_probability=probability,
        clipped_probability=probability,
        logit=probability_logit,
        included=True,
    )
    return AggregationBreakdown(
        method=DIRECT_MODEL_METHOD,
        shrinkage=0.0,
        anchor_probability=probability,
        anchor_logit=probability_logit,
        included_track_types=["single_agent"],
        missing_track_types=[],
        contributions=[contribution],
        pooled_logit=probability_logit,
        shrunk_logit=probability_logit,
        ensemble_probability=probability,
        track_spread=0.0,
        formula="Direct structured model probability; no probability aggregation is performed.",
    )


def run_single_model_forecast(
    *,
    contract: ForecastContract,
    profile_id: str,
    mode: str,
    as_of: datetime | None,
    model: ModelProvider,
    search: SearchProvider,
    run_id: str,
    allow_local_fixtures: bool = True,
    progress: ProgressFn | None = None,
    profile: ForecastProfile | None = None,
    execution: ExecutionContext | None = None,
    prompt_bundle: PromptBundle | None = None,
    cache: RunCache | None = None,
    ledger: UsageLedger | None = None,
    pricing_catalog: dict[str, Any] | None = None,
    prior_elapsed_seconds: float = 0.0,
) -> EngineResult:
    """Build one evidence packet and make exactly one structured forecasting call."""

    profile = profile or load_profile(profile_id)
    if profile.execution_strategy != "single_model":
        raise ValueError("single_model_execution_strategy_required")
    if contract.status != "approved":
        raise ValueError("approved_forecast_contract_required")
    if as_of is not None:
        as_of = as_utc(as_of)
    if execution is not None:
        allow_local_fixtures = execution.fixture_evidence_allowed
        mode = execution.effective_mode

    model_provider = execution.model_provider if execution is not None else model.name
    search_provider = execution.search_provider if execution is not None else search.name
    configuration_hash = execution.configuration_hash if execution is not None else "none"
    cache = cache or RunCache.create(
        run_id=run_id,
        model_provider=model_provider,
        search_provider=search_provider,
        mode=mode,
        as_of=as_of,
        configuration_hash=configuration_hash,
    )
    totals = ledger.totals(run_id) if ledger is not None else RunUsageTotals()
    budget = Budget.from_persisted(
        profile,
        totals,
        provider=model_provider,
        model=(
            execution.model_name
            if execution is not None
            else str(getattr(model, "model", getattr(model, "name", "mock")))
        ),
        search_provider=search_provider,
        pricing_catalog=pricing_catalog,
        prior_elapsed_seconds=prior_elapsed_seconds,
    )
    prompt_versions = dict(profile.prompt_versions)

    _emit(progress, "research", "Building the single-model evidence packet", 0.25)
    evidence, rejected = _collect_evidence(
        contract=contract,
        run_id=run_id,
        profile=profile,
        search=search,
        budget=budget,
        cache=cache,
        mode=mode,
        as_of=as_of,
        allow_local_fixtures=allow_local_fixtures,
    )
    all_urls = [str(item.get("url") or "") for item in [*evidence, *rejected]]
    if execution is not None and execution.effective_mode == "live":
        assert_no_fixture_evidence(all_urls, live=True)

    _emit(progress, "forecast", "Producing one direct single-model estimate", 0.75)
    output = _forecast_once(
        contract=contract,
        evidence=evidence,
        model=model,
        budget=budget,
        prompt_versions=prompt_versions,
        prompt_bundle=prompt_bundle,
    )
    cited = output.evidence_ids
    forecast = TrackForecastOutput(
        probability=output.probability,
        prior_probability=None,
        key_drivers=[
            Driver(
                factor="Single-model assessment of the supplied contract and evidence packet",
                direction="unclear",
                importance=1.0,
                evidence_ids=cited,
                inference=not cited,
            )
        ],
        counterarguments=[],
        unresolved_uncertainties=output.uncertainty,
        resolver_risk=0.0,
        evidence_quality=(len(cited) / len(evidence) if evidence else 0.0),
        reasoning_summary=output.reasoning,
    )
    fixture_used = any("fixtures.forecastlab.local" in url for url in all_urls if url)
    _emit(progress, "report", "Single-model forecast ready", 0.96)
    return EngineResult(
        contract=contract.to_resolution_contract(),
        tracks=[
            TrackResult(
                track_type="single_agent",
                plan=None,
                forecast=forecast,
                evidence=evidence,
                rejected=rejected,
            )
        ],
        aggregation=_direct_probability_breakdown(output.probability),
        disagreement_summary=None,
        prompt_versions=prompt_versions,
        budget=budget.snapshot(),
        stopped_early=budget.state.stopped,
        stop_reason=budget.state.stop_reason,
        stop_stage=budget.state.stop_stage,
        fixture_evidence_used=fixture_used,
        partial=False,
    )
