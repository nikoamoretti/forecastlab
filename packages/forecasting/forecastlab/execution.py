from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from forecastlab.errors import ConfigurationError
from forecastlab.gitinfo import current_git_commit
from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.pricing import estimate_cost
from forecastlab.profiles import effective_profile, load_profile, profile_hash
from forecastlab.prompts import prompt_hashes
from forecastlab.schemas import ForecastProfile, RunMode
from forecastlab.timeutil import utcnow

MOCK_MODEL_PROVIDERS = {"mock", "demo"}
MOCK_SEARCH_PROVIDERS = {"mock", "demo"}
REAL_MODEL_PROVIDERS = {"openai_compatible", "openai", "xai"}
REAL_SEARCH_PROVIDERS = {"tavily"}


class ProviderSettings(BaseModel):
    model_provider: str = "mock"
    model_name: str = "mock-forecast-v1"
    model_base_url: str | None = None
    model_api_key_set: bool = False
    search_provider: str = "mock"
    search_api_key_set: bool = False
    max_cost_usd: float = 5.0
    model_timeout_seconds: float = 60.0


class ExecutionContext(BaseModel):
    requested_mode: Literal["demo", "live", "backtest"]
    effective_mode: Literal["demo", "live", "backtest"]
    synthetic_fixture_run: bool = False

    model_provider: str
    model_name: str
    model_base_url: str | None = None
    model_is_mock: bool

    search_provider: str
    search_is_mock: bool

    evidence_policy: str
    fixture_evidence_allowed: bool
    fixture_evidence_used: bool = False

    profile_id: str
    profile_version: int
    profile_hash: str
    source_profile_max_cost_usd: float

    prompt_versions: dict[str, str] = Field(default_factory=dict)
    prompt_hashes: dict[str, str] = Field(default_factory=dict)

    effective_max_cost_usd: float
    effective_max_tokens: int
    effective_max_model_calls: int
    effective_max_search_calls: int
    effective_max_fetched_documents: int
    effective_max_wall_clock_seconds: int

    planned_model_calls: int = 0
    planned_search_calls: int = 0
    planned_fetches: int = 0
    planned_max_tokens: int = 0
    estimated_upper_bound_cost_usd: float | None = None
    cost_is_estimated: bool = True
    estimate_exceeds_ceiling: bool = False

    code_commit: str | None = None
    configuration_hash: str
    created_at: datetime


def _settings_from_mapping(data: dict[str, Any] | ProviderSettings) -> ProviderSettings:
    if isinstance(data, ProviderSettings):
        return data
    return ProviderSettings(
        model_provider=str(data.get("model_provider") or "mock"),
        model_name=str(data.get("model_name") or "mock-forecast-v1"),
        model_base_url=data.get("model_base_url"),
        model_api_key_set=bool(data.get("model_api_key")),
        search_provider=str(data.get("search_provider") or "mock"),
        search_api_key_set=bool(data.get("search_api_key")),
        max_cost_usd=float(data.get("max_cost_usd") or 5.0),
        model_timeout_seconds=float(data.get("model_timeout_seconds") or 60.0),
    )


def configuration_hash(payload: dict[str, Any]) -> str:
    return sha256_text(canonical_json(payload))


def estimate_workload(profile: ForecastProfile) -> dict[str, int]:
    tracks = max(1, len(profile.tracks))
    subq = tracks * profile.subquestions_per_track
    searches = 0 if profile.max_search_calls == 0 else subq
    fetches = 0 if profile.max_fetched_documents == 0 else subq * profile.fetches_per_subquestion
    model_calls = 1 + tracks * 2
    if searches:
        model_calls += 0
    return {
        "tracks": tracks,
        "subquestions": subq,
        "planned_model_calls": min(model_calls, profile.max_model_calls),
        "planned_search_calls": min(searches, profile.max_search_calls),
        "planned_fetches": min(fetches, profile.max_fetched_documents),
        "planned_max_tokens": profile.max_tokens,
    }


def readiness(settings: ProviderSettings | dict[str, Any]) -> dict[str, Any]:
    cfg = _settings_from_mapping(settings)
    live_reasons: list[str] = []
    if cfg.model_provider in MOCK_MODEL_PROVIDERS:
        live_reasons.append("model_provider_is_mock")
    if not cfg.model_api_key_set:
        live_reasons.append("model_api_key_missing")
    if not cfg.model_name or cfg.model_name.startswith("mock"):
        live_reasons.append("model_name_missing")
    if cfg.model_provider == "openai_compatible" and not cfg.model_base_url:
        live_reasons.append("model_base_url_missing")
    if cfg.search_provider in MOCK_SEARCH_PROVIDERS:
        live_reasons.append("search_provider_is_mock")
    if cfg.search_provider in REAL_SEARCH_PROVIDERS and not cfg.search_api_key_set:
        live_reasons.append("search_api_key_missing")
    return {
        "demo": {"ready": True, "reasons": []},
        "live": {"ready": not live_reasons, "reasons": live_reasons},
        "backtest": {
            "ready": not live_reasons,
            "reasons": [*live_reasons, "as_of_required_at_run_time"] if live_reasons else ["as_of_required_at_run_time"],
        },
    }


def resolve_execution_context(
    *,
    requested_mode: RunMode,
    profile_id: str,
    settings: ProviderSettings | dict[str, Any],
    synthetic_fixture_run: bool = False,
    as_of: datetime | None = None,
    allow_estimate_over_ceiling: bool = False,
) -> ExecutionContext:
    cfg = _settings_from_mapping(settings)
    source_profile = load_profile(profile_id)
    profile = effective_profile(source_profile, user_max_cost_usd=cfg.max_cost_usd)
    hashes = prompt_hashes()
    versions = {name: "v1" for name in hashes}
    versions.update(profile.prompt_versions)

    if requested_mode == "demo":
        if not synthetic_fixture_run and requested_mode != "demo":
            pass
        context = _build_context(
            requested_mode="demo",
            effective_mode="demo",
            cfg=cfg,
            profile=profile,
            source_profile=source_profile,
            hashes=hashes,
            versions=versions,
            synthetic=True,
            model_provider="mock",
            model_name="mock-forecast-v1",
            model_base_url=None,
            model_is_mock=True,
            search_provider="mock",
            search_is_mock=True,
            evidence_policy="demo_fixtures",
            fixture_allowed=True,
        )
        return context

    if requested_mode == "backtest" and as_of is None:
        raise ConfigurationError(["as_of_required"])

    if requested_mode == "backtest" and synthetic_fixture_run:
        return _build_context(
            requested_mode="backtest",
            effective_mode="backtest",
            cfg=cfg,
            profile=profile,
            source_profile=source_profile,
            hashes=hashes,
            versions=versions,
            synthetic=True,
            model_provider="mock",
            model_name="mock-forecast-v1",
            model_base_url=None,
            model_is_mock=True,
            search_provider="mock",
            search_is_mock=True,
            evidence_policy="synthetic_historical_fixtures",
            fixture_allowed=True,
        )

    reasons: list[str] = []
    if cfg.model_provider in MOCK_MODEL_PROVIDERS:
        reasons.append("model_provider_is_mock")
    if cfg.model_provider not in REAL_MODEL_PROVIDERS:
        reasons.append("unknown_model_provider")
    if not cfg.model_api_key_set:
        reasons.append("model_api_key_missing")
    if not cfg.model_name or cfg.model_name.startswith("mock"):
        reasons.append("model_name_missing")
    if cfg.model_provider == "openai_compatible" and not cfg.model_base_url:
        reasons.append("model_base_url_missing")
    if cfg.search_provider in MOCK_SEARCH_PROVIDERS:
        reasons.append("search_provider_is_mock")
    if cfg.search_provider not in REAL_SEARCH_PROVIDERS:
        reasons.append("unknown_search_provider")
    if cfg.search_provider in REAL_SEARCH_PROVIDERS and not cfg.search_api_key_set:
        reasons.append("search_api_key_missing")
    if reasons:
        raise ConfigurationError(reasons)

    evidence_policy = "live_current" if requested_mode == "live" else "strict_historical_snapshot"
    context = _build_context(
        requested_mode=requested_mode,
        effective_mode=requested_mode,
        cfg=cfg,
        profile=profile,
        source_profile=source_profile,
        hashes=hashes,
        versions=versions,
        synthetic=False,
        model_provider=cfg.model_provider,
        model_name=cfg.model_name,
        model_base_url=cfg.model_base_url,
        model_is_mock=False,
        search_provider=cfg.search_provider,
        search_is_mock=False,
        evidence_policy=evidence_policy,
        fixture_allowed=False,
    )
    if context.estimate_exceeds_ceiling and not allow_estimate_over_ceiling:
        raise ConfigurationError(
            ["estimate_exceeds_cost_ceiling"],
            "Upper-bound workload exceeds the Settings cost ceiling. Choose a lower-workload profile.",
        )
    return context


def _build_context(
    *,
    requested_mode: RunMode,
    effective_mode: RunMode,
    cfg: ProviderSettings,
    profile: ForecastProfile,
    source_profile: ForecastProfile,
    hashes: dict[str, str],
    versions: dict[str, str],
    synthetic: bool,
    model_provider: str,
    model_name: str,
    model_base_url: str | None,
    model_is_mock: bool,
    search_provider: str,
    search_is_mock: bool,
    evidence_policy: str,
    fixture_allowed: bool,
) -> ExecutionContext:
    workload = estimate_workload(profile)
    pricing = estimate_cost(model_provider, model_name, profile.max_tokens)
    estimated = pricing.get("estimated_cost_usd")
    exceeds = bool(estimated is not None and estimated > profile.max_estimated_cost_usd)
    payload = {
        "effective_mode": effective_mode,
        "model_provider": model_provider,
        "model_name": model_name,
        "model_base_url": model_base_url,
        "search_provider": search_provider,
        "evidence_policy": evidence_policy,
        "fixture_evidence_allowed": fixture_allowed,
        "profile_id": profile.id,
        "profile_hash": profile_hash(profile),
        "prompt_hashes": hashes,
        "effective_max_cost_usd": profile.max_estimated_cost_usd,
        "effective_max_tokens": profile.max_tokens,
        "effective_max_model_calls": profile.max_model_calls,
        "effective_max_search_calls": profile.max_search_calls,
        "effective_max_fetched_documents": profile.max_fetched_documents,
        "synthetic_fixture_run": synthetic,
    }
    return ExecutionContext(
        requested_mode=requested_mode,
        effective_mode=effective_mode,
        synthetic_fixture_run=synthetic,
        model_provider=model_provider,
        model_name=model_name,
        model_base_url=model_base_url,
        model_is_mock=model_is_mock,
        search_provider=search_provider,
        search_is_mock=search_is_mock,
        evidence_policy=evidence_policy,
        fixture_evidence_allowed=fixture_allowed,
        profile_id=profile.id,
        profile_version=profile.version,
        profile_hash=profile_hash(profile),
        source_profile_max_cost_usd=source_profile.max_estimated_cost_usd,
        prompt_versions=versions,
        prompt_hashes=hashes,
        effective_max_cost_usd=profile.max_estimated_cost_usd,
        effective_max_tokens=profile.max_tokens,
        effective_max_model_calls=profile.max_model_calls,
        effective_max_search_calls=profile.max_search_calls,
        effective_max_fetched_documents=profile.max_fetched_documents,
        effective_max_wall_clock_seconds=profile.max_wall_clock_seconds,
        planned_model_calls=workload["planned_model_calls"],
        planned_search_calls=workload["planned_search_calls"],
        planned_fetches=workload["planned_fetches"],
        planned_max_tokens=workload["planned_max_tokens"],
        estimated_upper_bound_cost_usd=estimated,
        cost_is_estimated=True,
        estimate_exceeds_ceiling=exceeds,
        code_commit=current_git_commit(),
        configuration_hash=configuration_hash(payload),
        created_at=utcnow(),
    )


def assert_no_fixture_evidence(urls: list[str], *, live: bool) -> None:
    if not live:
        return
    if any("fixtures.forecastlab.local" in url for url in urls):
        raise ConfigurationError(["live_run_fixture_evidence_violation"])
