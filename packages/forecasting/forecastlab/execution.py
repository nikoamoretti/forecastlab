from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from forecastlab.errors import ConfigurationError
from forecastlab.gitinfo import current_git_commit
from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.pricing import combine_cost_labels, estimate_cost, estimate_search_cost
from forecastlab.profiles import effective_profile, load_profile, profile_hash
from forecastlab.prompts import prompt_hashes
from forecastlab.schemas import ForecastProfile, RunMode
from forecastlab.timeutil import utcnow

MOCK_MODEL_PROVIDERS = {"mock", "demo"}
MOCK_SEARCH_PROVIDERS = {"mock", "demo"}
REAL_MODEL_PROVIDERS = {"openai_compatible", "openai", "xai", "openrouter"}
REAL_SEARCH_PROVIDERS = {"tavily", "openai_web_search"}


def search_api_key_for(
    search_provider: str,
    search_api_key: str | None,
    *,
    model_provider: str | None,
    model_api_key: str | None,
) -> str | None:
    """OpenAI web search reuses the OpenAI model key when no search key is set."""

    if search_api_key:
        return search_api_key
    if search_provider == "openai_web_search" and model_provider == "openai":
        return model_api_key or None
    return None


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
    model_timeout_seconds: float = 60.0
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
    graph_generation_max_completion_tokens: int | None = None
    graph_generation_max_visible_output_tokens: int | None = None
    graph_generation_reasoning_effort: str | None = None
    graph_generation_verbosity: str | None = None
    graph_generation_transport: str | None = None
    graph_generation_transport_max_characters: int | None = None
    graph_generation_node_question_max_characters: int | None = None
    graph_generation_local_id_max_characters: int | None = None
    graph_generation_max_dependencies_per_node: int | None = None
    graph_generation_max_preferred_sources_per_node: int | None = None
    graph_generation_preferred_source_max_characters: int | None = None
    max_candidate_fetch_attempts_per_node: int | None = None
    search_candidate_pool_per_node: int | None = None
    prefer_distinct_candidate_hosts: bool = False

    planned_model_calls: int = 0
    planned_search_calls: int = 0
    planned_fetches: int = 0
    planned_max_tokens: int = 0
    estimated_model_upper_bound_cost_usd: float | None = None
    estimated_search_upper_bound_cost_usd: float | None = None
    estimated_upper_bound_cost_usd: float | None = None
    model_cost_estimate_label: str = "unavailable"
    search_cost_estimate_label: str = "unavailable"
    cost_estimate_label: str = "unavailable"
    cost_estimate_unavailable_reasons: list[str] = Field(default_factory=list)
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
        search_api_key_set=bool(
            search_api_key_for(
                str(data.get("search_provider") or "mock"),
                data.get("search_api_key"),
                model_provider=data.get("model_provider"),
                model_api_key=data.get("model_api_key"),
            )
        ),
        max_cost_usd=float(data.get("max_cost_usd") or 5.0),
        model_timeout_seconds=float(data.get("model_timeout_seconds") or 60.0),
    )


def configuration_hash(payload: dict[str, Any]) -> str:
    return sha256_text(canonical_json(payload))


def preflight_cost_breakdown(context: ExecutionContext) -> dict[str, Any]:
    """Return the public, deterministic cost inputs used by launch preflight."""

    return {
        "profile_id": context.profile_id,
        "estimated_model_upper_bound_cost_usd": (
            context.estimated_model_upper_bound_cost_usd
        ),
        "estimated_search_upper_bound_cost_usd": (
            context.estimated_search_upper_bound_cost_usd
        ),
        "estimated_upper_bound_cost_usd": context.estimated_upper_bound_cost_usd,
        "effective_max_cost_usd": context.effective_max_cost_usd,
        "model_cost_estimate_label": context.model_cost_estimate_label,
        "search_cost_estimate_label": context.search_cost_estimate_label,
        "cost_estimate_label": context.cost_estimate_label,
        "cost_estimate_unavailable_reasons": list(
            context.cost_estimate_unavailable_reasons
        ),
        "estimate_exceeds_ceiling": context.estimate_exceeds_ceiling,
        "graph_generation": {
            "max_completion_tokens": (
                context.graph_generation_max_completion_tokens
            ),
            "max_visible_output_tokens": (
                context.graph_generation_max_visible_output_tokens
            ),
            "reasoning_effort": context.graph_generation_reasoning_effort,
            "verbosity": context.graph_generation_verbosity,
            "transport": context.graph_generation_transport,
            "transport_max_characters": (
                context.graph_generation_transport_max_characters
            ),
            "node_question_max_characters": (
                context.graph_generation_node_question_max_characters
            ),
            "local_id_max_characters": (
                context.graph_generation_local_id_max_characters
            ),
            "max_dependencies_per_node": (
                context.graph_generation_max_dependencies_per_node
            ),
            "max_preferred_sources_per_node": (
                context.graph_generation_max_preferred_sources_per_node
            ),
            "preferred_source_max_characters": (
                context.graph_generation_preferred_source_max_characters
            ),
        },
        "research": {
            "max_candidate_fetch_attempts_per_node": (
                context.max_candidate_fetch_attempts_per_node
            ),
            "search_candidate_pool_per_node": (
                context.search_candidate_pool_per_node
            ),
            "prefer_distinct_candidate_hosts": (
                context.prefer_distinct_candidate_hosts
            ),
        },
    }


def _display_cost(value: float | None) -> str:
    return "unavailable" if value is None else f"${value:.6f}"


def _preflight_error_message(context: ExecutionContext, *, unavailable: bool) -> str:
    prefix = (
        "Upper-bound workload cost is unavailable"
        if unavailable
        else "Upper-bound workload exceeds the effective cost ceiling"
    )
    suffix = (
        "Live execution fails closed until pricing is available."
        if unavailable
        else "Choose a lower-workload profile or raise the existing Settings ceiling explicitly."
    )
    return (
        f"{prefix} for profile '{context.profile_id}': "
        f"model={_display_cost(context.estimated_model_upper_bound_cost_usd)}, "
        f"search={_display_cost(context.estimated_search_upper_bound_cost_usd)}, "
        f"total={_display_cost(context.estimated_upper_bound_cost_usd)}, "
        f"ceiling=${context.effective_max_cost_usd:.6f}. {suffix}"
    )


def estimate_workload(profile: ForecastProfile) -> dict[str, int]:
    if profile.execution_strategy == "graph_nodes":
        return {
            "tracks": 0,
            "subquestions": 0,
            "planned_model_calls": profile.max_model_calls,
            "planned_search_calls": profile.max_search_calls,
            "planned_fetches": profile.max_fetched_documents,
            "planned_max_tokens": profile.max_tokens,
        }
    if profile.execution_strategy == "single_model":
        searches = 1 if profile.max_search_calls > 0 else 0
        fetches = (
            min(profile.fetches_per_subquestion, profile.max_fetched_documents)
            if searches and profile.max_fetched_documents > 0
            else 0
        )
        return {
            "tracks": 1,
            "subquestions": 0,
            "planned_model_calls": min(1, profile.max_model_calls),
            "planned_search_calls": searches,
            "planned_fetches": fetches,
            "planned_max_tokens": profile.max_tokens,
        }
    if profile.execution_strategy == "statistical_baseline":
        # One official-data request; no model or search call.
        return {
            "tracks": 0,
            "subquestions": 0,
            "planned_model_calls": 0,
            "planned_search_calls": 0,
            "planned_fetches": min(1, profile.max_fetched_documents),
            "planned_max_tokens": 0,
        }
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
    if context.cost_estimate_unavailable_reasons:
        raise ConfigurationError(
            context.cost_estimate_unavailable_reasons,
            _preflight_error_message(context, unavailable=True),
            details=preflight_cost_breakdown(context),
        )
    if context.estimate_exceeds_ceiling and not allow_estimate_over_ceiling:
        raise ConfigurationError(
            ["estimate_exceeds_cost_ceiling"],
            _preflight_error_message(context, unavailable=False),
            details=preflight_cost_breakdown(context),
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
    raw_model_estimate = pricing.get("estimated_cost_usd")
    model_estimate = (
        float(raw_model_estimate) if raw_model_estimate is not None else None
    )
    model_label = str(pricing.get("label") or "unavailable")
    search_unit_cost, search_label = estimate_search_cost(search_provider)
    if workload["planned_search_calls"] == 0:
        search_estimate = 0.0
        search_label = "estimated"
    elif search_unit_cost is None:
        search_estimate = None
    else:
        search_estimate = round(
            workload["planned_search_calls"] * search_unit_cost,
            6,
        )
    unavailable_reasons: list[str] = []
    if model_estimate is None:
        unavailable_reasons.append("model_pricing_unavailable")
    if search_estimate is None:
        unavailable_reasons.append("search_pricing_unavailable")
    estimated = (
        round(model_estimate + search_estimate, 6)
        if model_estimate is not None and search_estimate is not None
        else None
    )
    combined_label = (
        "unavailable"
        if unavailable_reasons
        else combine_cost_labels([model_label, search_label])
    )
    exceeds = bool(
        estimated is not None and estimated > profile.max_estimated_cost_usd
    )
    payload = {
        "effective_mode": effective_mode,
        "model_provider": model_provider,
        "model_name": model_name,
        "model_base_url": model_base_url,
        "model_timeout_seconds": cfg.model_timeout_seconds,
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
        "graph_generation_max_completion_tokens": (
            profile.graph_generation_max_completion_tokens
            or profile.max_output_tokens_per_call
        ),
        "graph_generation_max_visible_output_tokens": (
            profile.graph_generation_max_visible_output_tokens
            or profile.max_output_tokens_per_call
        ),
        "graph_generation_reasoning_effort": (
            profile.graph_generation_reasoning_effort or "minimal"
        ),
        "graph_generation_verbosity": profile.graph_generation_verbosity,
        "graph_generation_transport": profile.graph_generation_transport,
        "graph_generation_transport_max_characters": (
            profile.graph_generation_transport_max_characters
        ),
        "graph_generation_node_question_max_characters": (
            profile.graph_generation_node_question_max_characters
        ),
        "graph_generation_local_id_max_characters": (
            profile.graph_generation_local_id_max_characters
        ),
        "graph_generation_max_dependencies_per_node": (
            profile.graph_generation_max_dependencies_per_node
        ),
        "graph_generation_max_preferred_sources_per_node": (
            profile.graph_generation_max_preferred_sources_per_node
        ),
        "graph_generation_preferred_source_max_characters": (
            profile.graph_generation_preferred_source_max_characters
        ),
        "max_candidate_fetch_attempts_per_node": (
            profile.max_candidate_fetch_attempts_per_node
            or profile.fetches_per_subquestion
        ),
        "search_candidate_pool_per_node": (
            profile.search_candidate_pool_per_node
            or profile.search_results_per_subquestion
        ),
        "prefer_distinct_candidate_hosts": profile.prefer_distinct_candidate_hosts,
        "synthetic_fixture_run": synthetic,
    }
    return ExecutionContext(
        requested_mode=requested_mode,
        effective_mode=effective_mode,
        synthetic_fixture_run=synthetic,
        model_provider=model_provider,
        model_name=model_name,
        model_base_url=model_base_url,
        model_timeout_seconds=cfg.model_timeout_seconds,
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
        graph_generation_max_completion_tokens=(
            profile.graph_generation_max_completion_tokens
            or profile.max_output_tokens_per_call
        ),
        graph_generation_max_visible_output_tokens=(
            profile.graph_generation_max_visible_output_tokens
            or profile.max_output_tokens_per_call
        ),
        graph_generation_reasoning_effort=(
            profile.graph_generation_reasoning_effort or "minimal"
        ),
        graph_generation_verbosity=profile.graph_generation_verbosity,
        graph_generation_transport=profile.graph_generation_transport,
        graph_generation_transport_max_characters=(
            profile.graph_generation_transport_max_characters
        ),
        graph_generation_node_question_max_characters=(
            profile.graph_generation_node_question_max_characters
        ),
        graph_generation_local_id_max_characters=(
            profile.graph_generation_local_id_max_characters
        ),
        graph_generation_max_dependencies_per_node=(
            profile.graph_generation_max_dependencies_per_node
        ),
        graph_generation_max_preferred_sources_per_node=(
            profile.graph_generation_max_preferred_sources_per_node
        ),
        graph_generation_preferred_source_max_characters=(
            profile.graph_generation_preferred_source_max_characters
        ),
        max_candidate_fetch_attempts_per_node=(
            profile.max_candidate_fetch_attempts_per_node
            or profile.fetches_per_subquestion
        ),
        search_candidate_pool_per_node=(
            profile.search_candidate_pool_per_node
            or profile.search_results_per_subquestion
        ),
        prefer_distinct_candidate_hosts=profile.prefer_distinct_candidate_hosts,
        planned_model_calls=workload["planned_model_calls"],
        planned_search_calls=workload["planned_search_calls"],
        planned_fetches=workload["planned_fetches"],
        planned_max_tokens=workload["planned_max_tokens"],
        estimated_model_upper_bound_cost_usd=model_estimate,
        estimated_search_upper_bound_cost_usd=search_estimate,
        estimated_upper_bound_cost_usd=estimated,
        model_cost_estimate_label=model_label,
        search_cost_estimate_label=search_label,
        cost_estimate_label=combined_label,
        cost_estimate_unavailable_reasons=unavailable_reasons,
        cost_is_estimated=combined_label != "provider_reported",
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
