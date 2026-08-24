from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.environment import (
    build_environment_identity,
    compare_environment,
    require_python_lock,
    working_tree_dirty,
)
from forecastlab.errors import ExperimentEnvironmentMismatch
from forecastlab.evaluation import brier_score, log_loss, mean, median
from forecastlab.execution import ExecutionContext, configuration_hash, resolve_execution_context
from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.pricing import load_pricing, pricing_hash
from forecastlab.profiles import effective_profile, load_profile, profile_hash
from forecastlab.prompts import PromptBundle, load_prompt_bundle
from forecastlab.schemas import ForecastContract, ForecastProfile, ResolutionContract
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.config import settings
from forecastlab_api.contracts import store_forecast_contract
from forecastlab_api.experiments import evidence_coverage_for_run
from forecastlab_api.jobs import enqueue_job
from forecastlab_api.models import (
    EvaluationDataset,
    EvaluationQuestion,
    ForecastContractRow,
    ForecastExperiment,
    ForecastExperimentResult,
    ForecastExperimentRun,
    ForecastRun,
    ForecastVersion,
    Job,
    Question,
    ResearchTrack,
)
from forecastlab_api.persist import save_contract
from forecastlab_api.pipeline import (
    apply_execution_limits,
    create_run_record,
    execute_run,
    provider_settings_from_secrets,
)
from forecastlab_api.secrets import load_secrets
from forecastlab_api.usage_ledger import apply_totals_to_run, default_ledger

CONTROLLED_FORECAST_PROFILES = (
    "single_model_forecaster_v1",
    "three_track_forecaster",
    "graph_forecaster_v1",
)
CONFIGURATION_SCHEMA_VERSION = 1
TERMINAL_RUN_STATUSES = frozenset({"completed", "partial", "failed"})
TERMINAL_EXPERIMENT_STATUSES = frozenset(
    {"completed", "completed_with_failures", "failed"}
)
COMPARISON_NOTICE = (
    "Measurements only. This report does not establish that any forecasting profile is superior."
)
BUDGET_FIELDS = (
    "max_model_calls",
    "max_search_calls",
    "max_fetched_documents",
    "max_tokens",
    "max_estimated_cost_usd",
    "max_wall_clock_seconds",
)


class ForecastExperimentComparisonReport(BaseModel):
    experiment_id: str
    dataset: dict[str, Any]
    status: str
    configuration_hash: str
    configuration: dict[str, Any]
    profiles: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    notice: str = COMPARISON_NOTICE


def _safe_json(payload: Any) -> str:
    return canonical_json(payload)


def _public_provider(
    context: ExecutionContext,
    settings_data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "model_provider": context.model_provider,
        "model": context.model_name,
        "model_base_url": context.model_base_url,
        "model_timeout_seconds": context.model_timeout_seconds,
        "model_api_key_set": bool(settings_data.get("model_api_key")),
        "search_provider": context.search_provider,
        "search_api_key_set": bool(settings_data.get("search_api_key")),
        "evidence_policy": context.evidence_policy,
    }


def _context_snapshot(context: ExecutionContext) -> dict[str, Any]:
    payload = context.model_dump(mode="json")
    payload.pop("created_at", None)
    return payload


def _question_snapshot(item: EvaluationQuestion) -> dict[str, Any]:
    return {
        "id": item.id,
        "question_hash": item.question_hash,
        "question": item.question,
        "resolution_contract": json.loads(item.resolution_contract),
        "forecast_date": as_utc(item.forecast_date).isoformat(),
        "evidence_cutoff": as_utc(item.forecast_date).isoformat(),
        "resolution_date": as_utc(item.resolution_date).isoformat(),
        "outcome": item.outcome,
        "resolution_source": item.resolution_source,
        "domain": item.domain,
    }


def _budget_snapshot(profile: ForecastProfile) -> dict[str, int | float]:
    return {field: getattr(profile, field) for field in BUDGET_FIELDS}


def _validate_profiles(profile_ids: list[str]) -> list[str]:
    unique = list(dict.fromkeys(profile_ids))
    required = set(CONTROLLED_FORECAST_PROFILES)
    if set(unique) != required or len(unique) != len(required):
        raise ValueError("controlled_forecast_profiles_required")
    return list(CONTROLLED_FORECAST_PROFILES)


def _freeze_configuration(
    *,
    dataset: EvaluationDataset,
    questions: list[EvaluationQuestion],
    profile_ids: list[str],
    synthetic_test: bool,
) -> dict[str, Any]:
    settings_data = provider_settings_from_secrets(load_secrets())
    prompt_bundle = load_prompt_bundle()
    pricing_catalog = load_pricing()
    pricing_digest = pricing_hash(catalog=pricing_catalog)
    profile_snapshots: dict[str, dict[str, Any]] = {}
    effective_profiles: list[ForecastProfile] = []
    provider_snapshot: dict[str, Any] | None = None

    for profile_id in profile_ids:
        source = load_profile(profile_id)
        context = resolve_execution_context(
            requested_mode="backtest",
            profile_id=profile_id,
            settings=settings_data,
            synthetic_fixture_run=synthetic_test,
            as_of=as_utc(questions[0].forecast_date),
        )
        context = context.model_copy(
            update={
                "prompt_versions": prompt_bundle.versions(),
                "prompt_hashes": prompt_bundle.hashes(),
            }
        )
        effective = apply_execution_limits(
            effective_profile(
                source,
                user_max_cost_usd=float(settings_data["max_cost_usd"]),
            ),
            context,
        )
        effective_profiles.append(effective)
        current_provider = _public_provider(context, settings_data)
        if provider_snapshot is None:
            provider_snapshot = current_provider
        elif current_provider != provider_snapshot:
            raise ValueError("profile_provider_configuration_mismatch")
        profile_snapshots[profile_id] = {
            "profile_id": profile_id,
            "profile_hash": profile_hash(source),
            "source_profile": source.model_dump(mode="json"),
            "effective_profile": effective.model_dump(mode="json"),
            "execution_context": _context_snapshot(context),
            "budget": _budget_snapshot(effective),
        }

    budgets = [_budget_snapshot(profile) for profile in effective_profiles]
    if any(item != budgets[0] for item in budgets[1:]):
        raise ValueError("profile_budget_ceiling_mismatch")
    profile_hashes = {
        profile_id: str(profile_snapshots[profile_id]["profile_hash"])
        for profile_id in profile_ids
    }
    prompt_bundle_hash = sha256_text(canonical_json(prompt_bundle.hashes()))
    identity = build_environment_identity(
        prompt_bundle_hash=prompt_bundle_hash,
        profile_hashes=profile_hashes,
        pricing_catalog=pricing_catalog,
    )
    return {
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "dataset": {
            "id": dataset.id,
            "name": dataset.name,
            "version": dataset.version,
            "hash": dataset.hash,
            "status": dataset.status,
            "frozen_at": as_utc(dataset.frozen_at).isoformat() if dataset.frozen_at else None,
            "provenance": dataset.provenance,
        },
        "questions": [_question_snapshot(item) for item in questions],
        "profiles": profile_snapshots,
        "profile_ids": profile_ids,
        "provider": provider_snapshot or {},
        "common_budget": budgets[0],
        "prompts": {
            "bundle": prompt_bundle.model_dump(mode="json"),
            "hashes": prompt_bundle.hashes(),
            "versions": prompt_bundle.versions(),
            "bundle_hash": prompt_bundle_hash,
        },
        "pricing": {"catalog": pricing_catalog, "hash": pricing_digest},
        "code": identity,
        "synthetic_test": synthetic_test,
    }


def create_forecast_experiment(
    session: Session,
    *,
    dataset_id: str,
    profile_ids: list[str],
    synthetic_test: bool = False,
) -> ForecastExperiment:
    """Freeze one comparison and enqueue every question/profile cell exactly once."""

    dataset = session.get(EvaluationDataset, dataset_id)
    if dataset is None:
        raise ValueError("evaluation_dataset_not_found")
    if dataset.status != "frozen" or dataset.frozen_at is None:
        raise ValueError("evaluation_dataset_must_be_frozen")
    questions = session.scalars(
        select(EvaluationQuestion)
        .where(EvaluationQuestion.dataset_id == dataset.id)
        .order_by(EvaluationQuestion.question_hash)
    ).all()
    if not questions:
        raise ValueError("evaluation_dataset_empty")
    profiles = _validate_profiles(profile_ids)
    if synthetic_test and not settings.allow_local_fixtures:
        raise ValueError("synthetic_test_mode_disabled")
    if not synthetic_test:
        if working_tree_dirty():
            raise ValueError("dirty_working_tree")
        require_python_lock(synthetic=False)

    frozen = _freeze_configuration(
        dataset=dataset,
        questions=list(questions),
        profile_ids=profiles,
        synthetic_test=synthetic_test,
    )
    digest = configuration_hash(frozen)
    experiment = ForecastExperiment(
        id=str(uuid.uuid4()),
        dataset_id=dataset.id,
        status="pending",
        profiles_json=canonical_json(profiles),
        configuration_hash=digest,
        configuration_json=_safe_json(frozen),
    )
    session.add(experiment)
    session.flush()
    for item in questions:
        for profile_id in profiles:
            experiment_run = ForecastExperimentRun(
                id=str(uuid.uuid4()),
                experiment_id=experiment.id,
                evaluation_question_id=item.id,
                profile_id=profile_id,
                status="pending",
            )
            session.add(experiment_run)
            session.flush()
            enqueue_job(
                session,
                job_type="forecast_experiment_run",
                payload={"forecast_experiment_run_id": experiment_run.id},
                idempotency_key=f"forecast_experiment_run:{experiment_run.id}",
            )
    return experiment


def _configuration(experiment: ForecastExperiment) -> dict[str, Any]:
    try:
        frozen = json.loads(experiment.configuration_json)
    except json.JSONDecodeError as exc:
        raise ExperimentEnvironmentMismatch("Forecast experiment configuration is invalid") from exc
    if configuration_hash(frozen) != experiment.configuration_hash:
        raise ExperimentEnvironmentMismatch("Forecast experiment configuration hash mismatch")
    return frozen


def _assert_frozen_environment(frozen: dict[str, Any]) -> None:
    identity = frozen.get("code") or {}
    if not identity:
        raise ExperimentEnvironmentMismatch("Forecast experiment is missing a frozen code identity")
    pricing_catalog = (frozen.get("pricing") or {}).get("catalog") or {}
    profiles = frozen.get("profiles") or {}
    profile_hashes = {
        profile_id: str(payload.get("profile_hash") or "")
        for profile_id, payload in profiles.items()
    }
    current = build_environment_identity(
        prompt_bundle_hash=(frozen.get("prompts") or {}).get("bundle_hash"),
        profile_hashes=profile_hashes,
        pricing_catalog=pricing_catalog,
    )
    if not frozen.get("synthetic_test") and (
        not identity.get("dependency_hash") or not current.get("dependency_hash")
    ):
        raise ExperimentEnvironmentMismatch("python_lockfile_required")
    mismatches = compare_environment(identity, current)
    if mismatches:
        raise ExperimentEnvironmentMismatch(
            f"Forecast experiment environment mismatch: {', '.join(mismatches)}"
        )


def _frozen_profile_and_context(
    frozen: dict[str, Any],
    profile_id: str,
) -> tuple[ForecastProfile, PromptBundle, ExecutionContext, dict[str, Any], float]:
    profile_payload = (frozen.get("profiles") or {}).get(profile_id)
    if not profile_payload:
        raise RuntimeError("forecast_experiment_profile_snapshot_missing")
    profile = ForecastProfile.model_validate(profile_payload["effective_profile"])
    bundle = PromptBundle.model_validate((frozen.get("prompts") or {})["bundle"])
    context_payload = dict(profile_payload["execution_context"])
    context_payload["created_at"] = utcnow().isoformat()
    context = ExecutionContext.model_validate(context_payload)
    pricing_catalog = (frozen.get("pricing") or {}).get("catalog") or {}
    timeout = float((frozen.get("provider") or {}).get("model_timeout_seconds") or 60.0)
    return profile, bundle, context, pricing_catalog, timeout


def _runtime_question_notes(experiment_run_id: str) -> str:
    return f"forecast-experiment-run:{experiment_run_id}"


def _resolution_contract(item: EvaluationQuestion) -> ResolutionContract:
    payload = json.loads(item.resolution_contract)
    return ResolutionContract(
        exact_yes=str(payload["yes_condition"]),
        exact_no=str(payload["no_condition"]),
        resolution_deadline=as_utc(item.resolution_date),
        authoritative_source=item.resolution_source,
        fallback_sources=list(payload.get("fallback_sources") or []),
        ambiguity_notes=str(payload.get("ambiguity_notes") or ""),
        cancellation_conditions=str(payload.get("cancellation_conditions") or ""),
        resolver_risk_notes=str(payload.get("resolver_risk_notes") or ""),
    )


def _ensure_forecast_contract(
    session: Session,
    question: Question,
    item: EvaluationQuestion,
) -> ForecastContractRow:
    existing = session.scalar(
        select(ForecastContractRow)
        .where(
            ForecastContractRow.question_id == question.id,
            ForecastContractRow.status == "approved",
        )
        .order_by(ForecastContractRow.version.desc())
        .limit(1)
    )
    if existing is not None:
        return existing
    payload = json.loads(item.resolution_contract)
    resolver = str(payload.get("authoritative_resolver") or "")
    contract = ForecastContract(
        id=str(uuid.uuid4()),
        question_id=question.id,
        version=1,
        created_at=as_utc(item.forecast_date),
        created_by="forecast_experiment",
        original_question=item.question,
        normalized_question=item.question,
        yes_condition=str(payload["yes_condition"]),
        no_condition=str(payload["no_condition"]),
        resolution_date=as_utc(item.resolution_date),
        authoritative_source=item.resolution_source,
        fallback_sources=list(payload.get("fallback_sources") or []),
        resolution_method=(
            "Resolve the binary outcome against the authoritative source at the stated resolution date; "
            "use fallback sources only if the authoritative source is unavailable."
        ),
        ambiguity_notes=str(payload.get("ambiguity_notes") or ""),
        cancellation_conditions=str(payload.get("cancellation_conditions") or ""),
        resolver_risk_notes=(
            str(payload.get("resolver_risk_notes") or "")
            or (f"Authoritative resolver: {resolver}." if resolver else "")
        ),
        domain=item.domain,
        initial_reference_class=(
            f"Previously resolved {item.domain or 'binary'} questions with comparable conditions."
        ),
        suggested_drivers=["historical base rate", "current trend", "resolution mechanics"],
        known_dependencies=[],
        status="approved",
    )
    errors = contract.approval_errors()
    if errors:
        raise ValueError(f"forecast_experiment_contract_invalid:{','.join(errors)}")
    question.normalized_text = contract.normalized_question
    question.forecast_deadline = contract.resolution_date
    return store_forecast_contract(session, contract)


def _ensure_runtime_question(
    session: Session,
    experiment_run: ForecastExperimentRun,
    item: EvaluationQuestion,
) -> Question:
    notes = _runtime_question_notes(experiment_run.id)
    question = session.scalar(select(Question).where(Question.notes == notes))
    if question is None:
        question = Question(
            id=str(uuid.uuid4()),
            original_text=item.question,
            normalized_text=item.question,
            forecast_deadline=as_utc(item.resolution_date),
            notes=notes,
            status="draft",
            requested_mode="backtest",
            requested_profile_id=experiment_run.profile_id,
            requested_as_of=as_utc(item.forecast_date),
            is_benchmark=True,
        )
        session.add(question)
        session.flush()
    if question.contract is None:
        save_contract(session, question, _resolution_contract(item))
        session.flush()
    _ensure_forecast_contract(session, question, item)
    return question


def _ensure_forecast_run(
    session: Session,
    experiment_run: ForecastExperimentRun,
    question: Question,
    context: ExecutionContext,
    item: EvaluationQuestion,
) -> ForecastRun:
    if experiment_run.forecast_run_id:
        existing = session.get(ForecastRun, experiment_run.forecast_run_id)
        if existing is not None:
            return existing
    run = create_run_record(
        session,
        question=question,
        context=context,
        as_of=as_utc(item.forecast_date),
        enqueue=False,
    )
    experiment_run.forecast_run_id = run.id
    return run


def _is_partial(
    session: Session,
    run: ForecastRun,
    profile: ForecastProfile,
) -> bool:
    if profile.execution_strategy in {"graph_nodes", "single_model"}:
        return False
    tracks = session.scalars(select(ResearchTrack).where(ResearchTrack.run_id == run.id)).all()
    return any(track.status == "failed" or bool(track.error_message) for track in tracks)


def _persist_result(
    session: Session,
    *,
    experiment_run: ForecastExperimentRun,
    item: EvaluationQuestion,
    forecast_run: ForecastRun | None,
    probability: float | None,
    completion_status: Literal["completed", "partial", "failed"],
    error: str | None = None,
    error_category: str | None = None,
) -> ForecastExperimentResult:
    existing = session.scalar(
        select(ForecastExperimentResult).where(
            ForecastExperimentResult.experiment_run_id == experiment_run.id
        )
    )
    if existing is not None:
        return existing
    if forecast_run is not None:
        apply_totals_to_run(forecast_run, default_ledger().totals(forecast_run.id))
        coverage, covered_units, total_units = evidence_coverage_for_run(session, forecast_run)
        cost = float(forecast_run.total_cost_usd or forecast_run.cost_usd or 0.0)
        latency = int(forecast_run.latency_ms or 0)
    else:
        coverage, covered_units, total_units = None, 0, 0
        cost, latency = 0.0, 0
    failed = completion_status == "failed"
    result = ForecastExperimentResult(
        id=str(uuid.uuid4()),
        experiment_run_id=experiment_run.id,
        probability=None if failed else probability,
        outcome=item.outcome,
        brier_score=(
            None if failed or probability is None else brier_score(probability, item.outcome)
        ),
        log_loss=None if failed or probability is None else log_loss(probability, item.outcome),
        cost_usd=cost,
        latency_ms=latency,
        evidence_coverage=coverage,
        evidence_covered_units=covered_units,
        evidence_total_units=total_units,
        completion_status=completion_status,
    )
    session.add(result)
    experiment_run.status = completion_status
    experiment_run.error = error
    experiment_run.error_category = error_category
    experiment_run.completed_at = utcnow()
    return result


def _refresh_experiment(session: Session, experiment: ForecastExperiment) -> None:
    runs = session.scalars(
        select(ForecastExperimentRun).where(
            ForecastExperimentRun.experiment_id == experiment.id
        )
    ).all()
    if any(item.status not in TERMINAL_RUN_STATUSES for item in runs):
        experiment.status = "running" if any(item.status == "running" for item in runs) else "pending"
        return
    failed = sum(item.status == "failed" for item in runs)
    partial = sum(item.status == "partial" for item in runs)
    if failed == len(runs):
        experiment.status = "failed"
    elif failed or partial:
        experiment.status = "completed_with_failures"
    else:
        experiment.status = "completed"
    experiment.completed_at = utcnow()


def execute_forecast_experiment_run(
    session: Session,
    experiment_run: ForecastExperimentRun,
    job: Job | None = None,
) -> None:
    """Execute one frozen cell through an existing forecast profile unchanged."""

    experiment = session.get(ForecastExperiment, experiment_run.experiment_id)
    item = session.get(EvaluationQuestion, experiment_run.evaluation_question_id)
    if experiment is None or item is None:
        raise RuntimeError("forecast_experiment_run_missing_parent")
    existing = session.scalar(
        select(ForecastExperimentResult).where(
            ForecastExperimentResult.experiment_run_id == experiment_run.id
        )
    )
    if existing is not None and experiment_run.status in TERMINAL_RUN_STATUSES:
        _refresh_experiment(session, experiment)
        return
    frozen = _configuration(experiment)
    _assert_frozen_environment(frozen)
    frozen_dataset = frozen.get("dataset") or {}
    dataset = session.get(EvaluationDataset, experiment.dataset_id)
    if (
        dataset is None
        or dataset.status != "frozen"
        or dataset.hash != frozen_dataset.get("hash")
    ):
        raise ExperimentEnvironmentMismatch(
            "Evaluation dataset does not match the frozen forecast experiment"
        )
    frozen_question = next(
        (
            question
            for question in frozen.get("questions") or []
            if question.get("id") == item.id
        ),
        None,
    )
    if frozen_question is None or canonical_json(frozen_question) != canonical_json(
        _question_snapshot(item)
    ):
        raise ExperimentEnvironmentMismatch(
            "Evaluation question does not match the frozen forecast experiment"
        )
    profile, bundle, context, pricing_catalog, timeout = _frozen_profile_and_context(
        frozen,
        experiment_run.profile_id,
    )
    experiment.status = "running"
    experiment_run.status = "running"
    experiment_run.started_at = experiment_run.started_at or utcnow()
    session.commit()
    question = _ensure_runtime_question(session, experiment_run, item)
    forecast_run = _ensure_forecast_run(session, experiment_run, question, context, item)
    session.commit()
    version = session.scalar(
        select(ForecastVersion).where(ForecastVersion.run_id == forecast_run.id)
    )
    if version is None:
        execute_run(
            session,
            forecast_run,
            job=job,
            profile=profile,
            prompt_bundle=bundle,
            model_timeout=timeout,
            pricing_catalog=pricing_catalog,
        )
        session.refresh(forecast_run)
        version = session.scalar(
            select(ForecastVersion).where(ForecastVersion.run_id == forecast_run.id)
        )
    if version is None or version.ensemble_probability is None:
        raise RuntimeError("forecast_experiment_probability_missing")
    completion_status: Literal["completed", "partial", "failed"] = (
        "partial" if _is_partial(session, forecast_run, profile) else "completed"
    )
    _persist_result(
        session,
        experiment_run=experiment_run,
        item=item,
        forecast_run=forecast_run,
        probability=version.ensemble_probability,
        completion_status=completion_status,
    )
    _refresh_experiment(session, experiment)
    session.commit()


def fail_forecast_experiment_job(
    session: Session,
    job: Job,
    *,
    error: str,
    category: str,
) -> bool:
    try:
        payload = json.loads(job.payload_json or "{}")
    except json.JSONDecodeError:
        return False
    run_id = payload.get("forecast_experiment_run_id")
    if not run_id:
        return False
    experiment_run = session.get(ForecastExperimentRun, run_id)
    if experiment_run is None:
        return True
    experiment = session.get(ForecastExperiment, experiment_run.experiment_id)
    item = session.get(EvaluationQuestion, experiment_run.evaluation_question_id)
    forecast_run = (
        session.get(ForecastRun, experiment_run.forecast_run_id)
        if experiment_run.forecast_run_id
        else None
    )
    if forecast_run is not None and forecast_run.status not in {"completed", "failed"}:
        forecast_run.status = "failed"
        forecast_run.error_message = error
        forecast_run.error_stage = category
        forecast_run.finished_at = utcnow()
        forecast_run.progress_stage = "failed"
        forecast_run.progress_message = error
    if item is not None:
        _persist_result(
            session,
            experiment_run=experiment_run,
            item=item,
            forecast_run=forecast_run,
            probability=None,
            completion_status="failed",
            error=error,
            error_category=category,
        )
    else:
        experiment_run.status = "failed"
        experiment_run.error = error
        experiment_run.error_category = category
        experiment_run.completed_at = utcnow()
    if experiment is not None:
        _refresh_experiment(session, experiment)
    return True


def forecast_experiment_progress(
    session: Session,
    experiment: ForecastExperiment,
) -> dict[str, Any]:
    runs = session.scalars(
        select(ForecastExperimentRun)
        .where(ForecastExperimentRun.experiment_id == experiment.id)
        .order_by(
            ForecastExperimentRun.evaluation_question_id,
            ForecastExperimentRun.profile_id,
        )
    ).all()
    status_counts = {
        status: 0 for status in ("pending", "running", "completed", "partial", "failed")
    }
    per_profile: dict[str, dict[str, int]] = {}
    for run in runs:
        status_counts[run.status] = status_counts.get(run.status, 0) + 1
        bucket = per_profile.setdefault(
            run.profile_id,
            {
                status: 0
                for status in ("pending", "running", "completed", "partial", "failed")
            },
        )
        bucket[run.status] = bucket.get(run.status, 0) + 1
    finished = sum(status_counts[item] for item in TERMINAL_RUN_STATUSES)
    total = len(runs)
    frozen = _configuration(experiment)
    return {
        "id": experiment.id,
        "experiment_id": experiment.id,
        "dataset_id": experiment.dataset_id,
        "status": experiment.status,
        "profiles": json.loads(experiment.profiles_json),
        "configuration_hash": experiment.configuration_hash,
        "created_at": experiment.created_at.isoformat(),
        "completed_at": experiment.completed_at.isoformat() if experiment.completed_at else None,
        "total_runs": total,
        "completed_runs": status_counts["completed"],
        "partial_runs": status_counts["partial"],
        "failed_runs": status_counts["failed"],
        "pending_runs": status_counts["pending"],
        "running_runs": status_counts["running"],
        "percent": round(100.0 * finished / total, 1) if total else 0.0,
        "per_profile": per_profile,
        "configuration": {
            "dataset": frozen.get("dataset"),
            "provider": frozen.get("provider"),
            "common_budget": frozen.get("common_budget"),
            "prompt_hashes": (frozen.get("prompts") or {}).get("hashes"),
            "code": frozen.get("code"),
            "synthetic_test": bool(frozen.get("synthetic_test")),
        },
        "runs": [
            {
                "id": run.id,
                "evaluation_question_id": run.evaluation_question_id,
                "profile_id": run.profile_id,
                "forecast_run_id": run.forecast_run_id,
                "status": run.status,
                "error": run.error,
                "error_category": run.error_category,
            }
            for run in runs
        ],
    }


def _profile_metrics(
    runs: list[ForecastExperimentRun],
    results: dict[str, ForecastExperimentResult],
) -> dict[str, Any]:
    rows = [results[item.id] for item in runs if item.id in results]
    valid = [
        item
        for item in rows
        if item.probability is not None and item.completion_status != "failed"
    ]
    costs = [item.cost_usd for item in rows]
    latencies = [float(item.latency_ms) for item in rows]
    coverage = [item.evidence_coverage for item in rows if item.evidence_coverage is not None]
    total = len(runs)
    completed = sum(item.status == "completed" for item in runs)
    partial = sum(item.status == "partial" for item in runs)
    failed = sum(item.status == "failed" for item in runs)
    return {
        "profile_id": runs[0].profile_id if runs else "",
        "assigned_questions": total,
        "scored_questions": len(valid),
        "brier_score": mean(
            [item.brier_score for item in valid if item.brier_score is not None]
        ),
        "log_loss": mean([item.log_loss for item in valid if item.log_loss is not None]),
        "total_cost_usd": sum(costs),
        "mean_cost_usd": mean(costs),
        "median_cost_usd": median(costs),
        "mean_latency_ms": mean(latencies),
        "evidence_coverage": mean(coverage),
        "completion_rate": completed / total if total else None,
        "partial_rate": partial / total if total else None,
        "failure_rate": failed / total if total else None,
        "completed": completed,
        "partial": partial,
        "failed": failed,
    }


def forecast_experiment_report(
    session: Session,
    experiment: ForecastExperiment,
) -> ForecastExperimentComparisonReport:
    dataset = session.get(EvaluationDataset, experiment.dataset_id)
    if dataset is None:
        raise RuntimeError("evaluation_dataset_not_found")
    runs = session.scalars(
        select(ForecastExperimentRun)
        .where(ForecastExperimentRun.experiment_id == experiment.id)
        .order_by(
            ForecastExperimentRun.evaluation_question_id,
            ForecastExperimentRun.profile_id,
        )
    ).all()
    result_rows = session.scalars(
        select(ForecastExperimentResult).where(
            ForecastExperimentResult.experiment_run_id.in_(
                [item.id for item in runs] or [""]
            )
        )
    ).all()
    results = {item.experiment_run_id: item for item in result_rows}
    questions = {
        item.id: item
        for item in session.scalars(
            select(EvaluationQuestion).where(EvaluationQuestion.dataset_id == experiment.dataset_id)
        ).all()
    }
    profile_ids = json.loads(experiment.profiles_json)
    profile_groups = {
        profile_id: [item for item in runs if item.profile_id == profile_id]
        for profile_id in profile_ids
    }
    frozen = _configuration(experiment)
    rows: list[dict[str, Any]] = []
    for run in runs:
        result = results.get(run.id)
        item = questions.get(run.evaluation_question_id)
        rows.append(
            {
                "experiment_run_id": run.id,
                "forecast_run_id": run.forecast_run_id,
                "evaluation_question_id": run.evaluation_question_id,
                "question": item.question if item else None,
                "profile_id": run.profile_id,
                "status": run.status,
                "error": run.error,
                "error_category": run.error_category,
                "probability": result.probability if result else None,
                "outcome": result.outcome if result else (item.outcome if item else None),
                "brier_score": result.brier_score if result else None,
                "log_loss": result.log_loss if result else None,
                "cost_usd": result.cost_usd if result else 0.0,
                "latency_ms": result.latency_ms if result else 0,
                "evidence_coverage": result.evidence_coverage if result else None,
                "evidence_covered_units": result.evidence_covered_units if result else 0,
                "evidence_total_units": result.evidence_total_units if result else 0,
                "completion_status": result.completion_status if result else run.status,
            }
        )
    return ForecastExperimentComparisonReport(
        experiment_id=experiment.id,
        dataset={
            "id": dataset.id,
            "name": dataset.name,
            "version": dataset.version,
            "hash": dataset.hash,
            "question_count": dataset.question_count,
        },
        status=experiment.status,
        configuration_hash=experiment.configuration_hash,
        configuration={
            "profiles": frozen.get("profile_ids"),
            "provider": frozen.get("provider"),
            "common_budget": frozen.get("common_budget"),
            "prompt_hashes": (frozen.get("prompts") or {}).get("hashes"),
            "pricing_hash": (frozen.get("pricing") or {}).get("hash"),
            "code": frozen.get("code"),
            "question_hashes": [
                item.get("question_hash") for item in frozen.get("questions") or []
            ],
            "evidence_cutoffs": [
                item.get("evidence_cutoff") for item in frozen.get("questions") or []
            ],
            "synthetic_test": bool(frozen.get("synthetic_test")),
        },
        profiles=[
            _profile_metrics(profile_groups[profile_id], results)
            for profile_id in profile_ids
        ],
        rows=rows,
    )
