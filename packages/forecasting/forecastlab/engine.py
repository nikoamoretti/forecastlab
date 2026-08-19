from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from forecastlab.aggregation import AggregationBreakdown, aggregate_track_probabilities
from forecastlab.budget import Budget
from forecastlab.fetch import fetch_document
from forecastlab.profiles import load_profile
from forecastlab.prompts import load_prompt
from forecastlab.providers.base import BudgetExceeded, ChatResult, ModelProvider, SearchProvider
from forecastlab.ranking import rank_hits
from forecastlab.schemas import (
    FetchedDocument,
    ForecastProfile,
    ResearchPlan,
    ResolutionContract,
    SearchHit,
    TrackForecastOutput,
)
from forecastlab.wayback import discover_snapshots, mock_snapshots, nearest_eligible_snapshot

ProgressFn = Callable[[str, str, float, dict[str, Any] | None], None]

_SEARCH_CACHE: dict[tuple[str, int], list[SearchHit]] = {}
_FETCH_CACHE: dict[tuple[str, str, str], FetchedDocument] = {}


@dataclass
class TrackResult:
    track_type: str
    plan: ResearchPlan | None
    forecast: TrackForecastOutput | None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass
class EngineResult:
    contract: ResolutionContract
    tracks: list[TrackResult]
    aggregation: AggregationBreakdown
    disagreement_summary: str | None
    prompt_versions: dict[str, str]
    budget: dict[str, Any]
    stopped_early: bool
    stop_reason: str | None
    stop_stage: str | None


def _emit(
    progress: ProgressFn | None,
    stage: str,
    message: str,
    pct: float,
    extra: dict[str, Any] | None = None,
) -> None:
    if progress:
        progress(stage, message, pct, extra)


def _ask_json(
    model: ModelProvider,
    budget: Budget,
    stage: str,
    prompt_name: str,
    user: str,
    schema_name: str,
    prompt_versions: dict[str, str],
) -> dict[str, Any]:
    system, version = load_prompt(prompt_name)
    prompt_versions[prompt_name] = version
    result: ChatResult = model.complete_json(system=system, user=user, schema_name=schema_name)
    tokens = result.usage.prompt_tokens + result.usage.completion_tokens
    budget.add_model_call(stage, tokens, result.usage.cost_usd)
    parsed = result.parsed
    if parsed is None:
        parsed = json.loads(result.content)
    return parsed


def _ask_model(
    model: ModelProvider,
    budget: Budget,
    stage: str,
    prompt_name: str,
    user: str,
    schema_name: str,
    prompt_versions: dict[str, str],
    model_cls: type,
) -> Any:
    parsed = _ask_json(model, budget, stage, prompt_name, user, schema_name, prompt_versions)
    try:
        return model_cls.model_validate(parsed)
    except ValidationError:
        repair = user + "\n\nPrevious JSON failed validation. Return corrected JSON only.\n" + json.dumps(parsed)[:4000]
        parsed = _ask_json(model, budget, stage, prompt_name, repair, schema_name, prompt_versions)
        return model_cls.model_validate(parsed)


def _cached_search(search: SearchProvider, query: str, max_results: int) -> list[SearchHit]:
    key = (query, max_results)
    if key not in _SEARCH_CACHE:
        _SEARCH_CACHE[key] = search.search(query, max_results=max_results)
    return list(_SEARCH_CACHE[key])


def _cached_fetch(
    url: str,
    *,
    as_of: datetime | None,
    allow_local_fixtures: bool,
    snapshot_url: str | None,
    snapshot_at: datetime | None,
) -> FetchedDocument:
    key = (url, snapshot_url or "", as_of.isoformat() if as_of else "")
    if key not in _FETCH_CACHE:
        _FETCH_CACHE[key] = fetch_document(
            url,
            as_of=as_of,
            allow_local_fixtures=allow_local_fixtures,
            snapshot_url=snapshot_url,
            snapshot_at=snapshot_at,
        )
    return _FETCH_CACHE[key]


def _record(doc: FetchedDocument, *, track: str, subquestion: str, evidence_id: str) -> dict[str, Any]:
    return {
        "id": evidence_id,
        "track_type": track,
        "subquestion": subquestion,
        "url": doc.url,
        "title": doc.title,
        "publisher": doc.publisher,
        "published_at": doc.published_at.isoformat() if doc.published_at else None,
        "retrieved_at": doc.retrieved_at.isoformat(),
        "excerpt": (doc.text or "")[:800],
        "content_hash": doc.content_hash,
        "source_class": "secondary",
        "as_of_eligible": doc.as_of_eligible,
        "rejected": doc.rejected,
        "rejection_reason": doc.rejection_reason,
        "snapshot_url": doc.snapshot_url,
        "snapshot_at": doc.snapshot_at.isoformat() if doc.snapshot_at else None,
        "status_code": doc.status_code,
    }


def _run_track(
    *,
    track: str,
    contract: ResolutionContract,
    profile: ForecastProfile,
    model: ModelProvider,
    search: SearchProvider,
    budget: Budget,
    as_of: datetime | None,
    mode: str,
    allow_local_fixtures: bool,
    prompt_versions: dict[str, str],
    progress: ProgressFn | None = None,
) -> TrackResult:
    evidence: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    plan: ResearchPlan | None = None
    try:
        plan = _ask_model(
            model,
            budget,
            f"plan:{track}",
            f"plan_{track}",
            json.dumps(
                {
                    "track": track,
                    "contract": contract.model_dump(mode="json"),
                    "subquestions_needed": profile.subquestions_per_track,
                },
                default=str,
            ),
            "research_plan",
            prompt_versions,
            ResearchPlan,
        )
        plan.subquestions = plan.subquestions[: profile.subquestions_per_track]
        _emit(progress, "evidence", f"Collecting evidence for {track}", 0.35)
        for sub in plan.subquestions:
            budget.check(f"search:{track}")
            query = sub.search_queries[0] if sub.search_queries else sub.text
            budget.add_search(f"search:{track}")
            hits = rank_hits(_cached_search(search, query, profile.search_results_per_subquestion))
            for hit in hits[: profile.fetches_per_subquestion]:
                snapshot_url = None
                snapshot_at = None
                if mode == "backtest" and as_of:
                    snaps = mock_snapshots(hit.url) if allow_local_fixtures else discover_snapshots(hit.url)
                    nearest = nearest_eligible_snapshot(snaps, as_of)
                    if nearest is not None:
                        snapshot_url = nearest.snapshot_url
                        snapshot_at = nearest.timestamp
                budget.add_fetch(f"fetch:{track}")
                doc = _cached_fetch(
                    hit.url,
                    as_of=as_of if mode == "backtest" else None,
                    allow_local_fixtures=allow_local_fixtures,
                    snapshot_url=snapshot_url,
                    snapshot_at=snapshot_at,
                )
                record = _record(doc, track=track, subquestion=sub.text, evidence_id=f"ev-{uuid.uuid4().hex[:10]}")
                record["source_class"] = hit.source_class
                if doc.rejected or not doc.as_of_eligible:
                    rejected.append(record)
                    continue
                evidence.append(record)
        _emit(progress, "forecast", f"Producing {track} estimate", 0.7)
        forecast = _ask_model(
            model,
            budget,
            f"forecast:{track}",
            f"forecast_{track}",
            json.dumps(
                {
                    "track": track,
                    "contract": contract.model_dump(mode="json"),
                    "evidence": [
                        {
                            "id": item["id"],
                            "url": item["url"],
                            "title": item["title"],
                            "excerpt": item["excerpt"],
                            "published_at": item["published_at"],
                        }
                        for item in evidence
                    ],
                    "instruction": "Use only supplied evidence. Cite evidence ids. Do not invent URLs.",
                },
                default=str,
            ),
            "track_forecast",
            prompt_versions,
            TrackForecastOutput,
        )
        allowed = {item["id"] for item in evidence}
        forecast.key_drivers = [
            driver
            for driver in forecast.key_drivers
            if driver.inference or all(eid in allowed for eid in driver.evidence_ids)
        ]
        return TrackResult(track_type=track, plan=plan, forecast=forecast, evidence=evidence, rejected=rejected)
    except BudgetExceeded:
        raise
    except Exception as exc:
        return TrackResult(
            track_type=track,
            plan=plan,
            forecast=None,
            evidence=evidence,
            rejected=rejected,
            error=str(exc)[:500],
        )


def run_forecast_engine(
    *,
    question: str,
    contract: ResolutionContract | None,
    profile_id: str,
    mode: str,
    as_of: datetime | None,
    model: ModelProvider,
    search: SearchProvider,
    allow_local_fixtures: bool = True,
    progress: ProgressFn | None = None,
) -> EngineResult:
    profile = load_profile(profile_id)
    budget = Budget(profile)
    prompt_versions = dict(profile.prompt_versions)
    _emit(progress, "operationalize", "Operationalizing the question", 0.08)

    if contract is None:
        contract = _ask_model(
            model,
            budget,
            "operationalize",
            "operationalize",
            json.dumps({"question": question}),
            "resolution_contract",
            prompt_versions,
            ResolutionContract,
        )

    tracks: list[TrackResult] = []
    n = max(1, len(profile.tracks))
    for index, track in enumerate(profile.tracks):
        _emit(progress, "research", f"Running independent track: {track}", 0.18 + 0.52 * (index / n))
        try:
            tracks.append(
                _run_track(
                    track=track,
                    contract=contract,
                    profile=profile,
                    model=model,
                    search=search,
                    budget=budget,
                    as_of=as_of,
                    mode=mode,
                    allow_local_fixtures=allow_local_fixtures,
                    prompt_versions=prompt_versions,
                    progress=progress,
                )
            )
        except BudgetExceeded as exc:
            tracks.append(TrackResult(track_type=track, plan=None, forecast=None, error=str(exc)))
            _emit(progress, "budget", f"Stopped early: {exc.reason}", 0.8, {"stage": exc.stage})
            break

    _emit(progress, "aggregate", "Aggregating track probabilities in code", 0.86)
    probs: dict[str, float | None] = {}
    failed: dict[str, str] = {}
    for item in tracks:
        if item.forecast is None:
            probs[item.track_type] = None
            failed[item.track_type] = item.error or "track_failed"
        else:
            probs[item.track_type] = item.forecast.probability
    breakdown = aggregate_track_probabilities(
        probs,
        shrinkage=profile.shrinkage,
        anchor_track="base_rate",
        failed_tracks=failed,
    )

    summary = None
    if sum(1 for item in tracks if item.forecast is not None) >= 2:
        try:
            parsed = _ask_json(
                model,
                budget,
                "disagreement",
                "disagreement",
                json.dumps(
                    {
                        "tracks": [
                            {
                                "track": item.track_type,
                                "probability": item.forecast.probability if item.forecast else None,
                                "summary": item.forecast.reasoning_summary if item.forecast else item.error,
                            }
                            for item in tracks
                        ],
                        "ensemble": breakdown.ensemble_probability,
                    }
                ),
                "disagreement_summary",
                prompt_versions,
            )
            summary = str(parsed.get("summary") or "")
        except (BudgetExceeded, ValueError, ValidationError, json.JSONDecodeError):
            summary = None

    _emit(progress, "report", "Preparing the forecast report", 0.96)
    return EngineResult(
        contract=contract,
        tracks=tracks,
        aggregation=breakdown,
        disagreement_summary=summary,
        prompt_versions=prompt_versions,
        budget=budget.snapshot(),
        stopped_early=budget.state.stopped,
        stop_reason=budget.state.stop_reason,
        stop_stage=budget.state.stop_stage,
    )


def operationalize_only(*, question: str, model: ModelProvider) -> ResolutionContract:
    profile = load_profile("three_track_ensemble")
    budget = Budget(profile)
    prompt_versions: dict[str, str] = {}
    return _ask_model(
        model,
        budget,
        "operationalize",
        "operationalize",
        json.dumps({"question": question}),
        "resolution_contract",
        prompt_versions,
        ResolutionContract,
    )
