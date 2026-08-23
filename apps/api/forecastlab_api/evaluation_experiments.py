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
    EvaluationExperiment,
    EvaluationQuestion,
    EvaluationResult,
    EvaluationRun,
    ForecastContractRow,
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

CONTROLLED_COMPARISON_PROFILES = ("three_track_forecaster", "graph_forecaster_v1")
CONFIGURATION_SCHEMA_VERSION = 1
TERMINAL_RUN_STATUSES = frozenset({"completed", "partial", "failed"})
TERMINAL_EXPERIMENT_STATUSES = frozenset({"completed", "completed_with_failures", "failed"})
COMPARISON_NOTICE = (
    "Metrics are descriptive research diagnostics. This report does not establish that either profile is superior."
)
BUDGET_FIELDS = (
    "max_model_calls",
    "max_search_calls",
    "max_fetched_documents",
    "max_tokens",
    "max_estimated_cost_usd",
    "max_wall_clock_seconds",
)


class EvaluationComparisonReport(BaseModel):
    experiment_id: str
    dataset: dict[str, Any]
    status: str
    configuration_hash: str
    configuration: dict[str, Any]
    profiles: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    notice: str = COMPARISON_NOTICE


def _safe_json(payload: Any) -> str:
    # The freeze is assembled from public provider metadata and never receives
    # credential values. Keeping the canonical payload byte-for-byte stable is
    # required for configuration-hash verification at execution time.
    return canonical_json(payload)


def _public_provider(context: ExecutionContext, settings_data: dict[str, Any]) -> dict[str, Any]:
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
        "question_text": item.question_text,
        "normalized_question": item.normalized_question,
        "resolution_contract": json.loads(item.resolution_contract),
        "forecast_date": as_utc(item.forecast_date).isoformat(),
        "evidence_cutoff": as_utc(item.forecast_date).isoformat(),
        "resolution_date": as_utc(item.resolution_date).isoformat(),
        "outcome": item.outcome,
        "resolution_source": item.resolution_source,
        "category": item.category,
        "domain": item.domain,
    }


def _budget_snapshot(profile: ForecastProfile) -> dict[str, int | float]:
    return {field: getattr(profile, field) for field in BUDGET_FIELDS}


def _validate_profiles(profile_ids: list[str]) -> list[str]:
    unique = list(dict.fromkeys(profile_ids))
    if set(unique) != set(CONTROLLED_COMPARISON_PROFILES) or len(unique) != len(CONTROLLED_COMPARISON_PROFILES):
        raise ValueError("controlled_profiles_required")
    return list(CONTROLLED_COMPARISON_PROFILES)


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
            effective_profile(source, user_max_cost_usd=float(settings_data["max_cost_usd"])),
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
    identity.pop("working_tree_dirty", None)
    return {
        "schema_version": CONFIGURATION_SCHEMA_VERSION,
        "dataset": {
            "id": dataset.id,
            "name": dataset.name,
            "version": dataset.version,
            "dataset_hash": dataset.dataset_hash,
            "status": dataset.status,
            "frozen_at": as_utc(dataset.frozen_at).isoformat() if dataset.frozen_at else None,
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


def create_controlled_experiment(
    session: Session,
    *,
    dataset_id: str,
    profile_ids: list[str],
    synthetic_test: bool = False,
) -> EvaluationExperiment:
    """Freeze an experiment and enqueue every question/profile cell exactly once."""

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
        questions=questions,
        profile_ids=profiles,
        synthetic_test=synthetic_test,
    )
    digest = configuration_hash(frozen)
    experiment = EvaluationExperiment(
        id=str(uuid.uuid4()),
        dataset_id=dataset.id,
        status="pending",
        profiles=canonical_json(profiles),
        configuration_hash=digest,
        configuration_json=_safe_json(frozen),
    )
    session.add(experiment)
    session.flush()
    for item in questions:
        for profile_id in profiles:
            evaluation_run = EvaluationRun(
                id=str(uuid.uuid4()),
                experiment_id=experiment.id,
                question_id=item.id,
                profile_id=profile_id,
                status="pending",
            )
            session.add(evaluation_run)
            session.flush()
            enqueue_job(
                session,
                job_type="evaluation_run",
                payload={"evaluation_run_id": evaluation_run.id},
                idempotency_key=f"evaluation_run:{evaluation_run.id}",
            )
    return experiment


def _configuration(experiment: EvaluationExperiment) -> dict[str, Any]:
    try:
        frozen = json.loads(experiment.configuration_json)
    except json.JSONDecodeError as exc:
        raise ExperimentEnvironmentMismatch("Evaluation experiment configuration is invalid") from exc
    if configuration_hash(frozen) != experiment.configuration_hash:
        raise ExperimentEnvironmentMismatch("Evaluation experiment configuration hash mismatch")
    return frozen


def _assert_frozen_environment(experiment: EvaluationExperiment, frozen: dict[str, Any]) -> None:
    identity = frozen.get("code") or {}
    if not identity:
        raise ExperimentEnvironmentMismatch("Evaluation experiment is missing a frozen code identity")
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
            f"Evaluation experiment environment mismatch: {', '.join(mismatches)}"
        )


def _frozen_profile_and_context(
    frozen: dict[str, Any],
    profile_id: str,
) -> tuple[ForecastProfile, PromptBundle, ExecutionContext, dict[str, Any], float]:
    profile_payload = (frozen.get("profiles") or {}).get(profile_id)
    if not profile_payload:
        raise RuntimeError("evaluation_profile_snapshot_missing")
    profile = ForecastProfile.model_validate(profile_payload["effective_profile"])
    bundle = PromptBundle.model_validate((frozen.get("prompts") or {})["bundle"])
    context_payload = dict(profile_payload["execution_context"])
    context_payload["created_at"] = utcnow().isoformat()
    context = ExecutionContext.model_validate(context_payload)
    pricing_catalog = (frozen.get("pricing") or {}).get("catalog") or {}
    timeout = float((frozen.get("provider") or {}).get("model_timeout_seconds") or 60.0)
    return profile, bundle, context, pricing_catalog, timeout


def _evaluation_question_notes(run_id: str) -> str:
    return f"evaluation-run:{run_id}"


def _resolution_contract(item: EvaluationQuestion) -> ResolutionContract:
    payload = json.loads(item.resolution_contract)
    return ResolutionContract(
        exact_yes=str(payload["yes_condition"]),
        exact_no=str(payload["no_condition"]),
        resolution_deadline=as_utc(item.resolution_date),
        authoritative_source=str(payload.get("authoritative_source") or item.resolution_source),
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
    contract = ForecastContract(
        id=str(uuid.uuid4()),
        question_id=question.id,
        version=1,
        created_at=as_utc(item.forecast_date),
        created_by="evaluation_dataset",
        original_question=item.question_text,
        normalized_question=item.normalized_question,
        yes_condition=str(payload["yes_condition"]),
        no_condition=str(payload["no_condition"]),
        resolution_date=as_utc(item.resolution_date),
        authoritative_source=str(payload.get("authoritative_source") or item.resolution_source),
        fallback_sources=list(payload.get("fallback_sources") or []),
        resolution_method=(
            "Resolve the binary outcome against the authoritative source at the stated resolution date; "
            "use fallback sources only if the authoritative source is unavailable."
        ),
        ambiguity_notes=str(payload.get("ambiguity_notes") or ""),
        cancellation_conditions=str(payload.get("cancellation_conditions") or ""),
        resolver_risk_notes=str(payload.get("resolver_risk_notes") or ""),
        domain=item.domain,
        initial_reference_class=f"Previously resolved {item.category} questions with comparable conditions.",
        suggested_drivers=["historical base rate", "current trend", "resolution mechanics"],
        known_dependencies=[],
        status="approved",
    )
    errors = contract.approval_errors()
    if errors:
        raise ValueError(f"evaluation_forecast_contract_invalid:{','.join(errors)}")
    question.normalized_text = item.normalized_question
    question.forecast_deadline = as_utc(item.resolution_date)
    return store_forecast_contract(session, contract)


def _ensure_runtime_question(
    session: Session,
    evaluation_run: EvaluationRun,
    item: EvaluationQuestion,
) -> Question:
    notes = _evaluation_question_notes(evaluation_run.id)
    question = session.scalar(select(Question).where(Question.notes == notes))
    if question is None:
        question = Question(
            id=str(uuid.uuid4()),
            original_text=item.question_text,
            normalized_text=item.normalized_question,
            forecast_deadline=as_utc(item.resolution_date),
            notes=notes,
            status="draft",
            requested_mode="backtest",
            requested_profile_id=evaluation_run.profile_id,
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
    evaluation_run: EvaluationRun,
    question: Question,
    context: ExecutionContext,
    item: EvaluationQuestion,
) -> ForecastRun:
    if evaluation_run.forecast_run_id:
        existing = session.get(ForecastRun, evaluation_run.forecast_run_id)
        if existing is not None:
            return existing
    run = create_run_record(
        session,
        question=question,
        context=context,
        as_of=as_utc(item.forecast_date),
        enqueue=False,
    )
    evaluation_run.forecast_run_id = run.id
    return run


def _is_partial(session: Session, run: ForecastRun, profile: ForecastProfile) -> bool:
    if profile.execution_strategy == "graph_nodes":
        return False
    tracks = session.scalars(select(ResearchTrack).where(ResearchTrack.run_id == run.id)).all()
    return any(track.status == "failed" or bool(track.error_message) for track in tracks)


def _persist_result(
    session: Session,
    *,
    evaluation_run: EvaluationRun,
    item: EvaluationQuestion,
    forecast_run: ForecastRun | None,
    probability: float | None,
    completion_status: Literal["completed", "partial", "failed"],
    error: str | None = None,
) -> EvaluationResult:
    existing = session.scalar(select(EvaluationResult).where(EvaluationResult.run_id == evaluation_run.id))
    if existing is not None:
        return existing
    if forecast_run is not None:
        apply_totals_to_run(forecast_run, default_ledger().totals(forecast_run.id))
        coverage, _, _ = evidence_coverage_for_run(session, forecast_run)
        cost = float(forecast_run.total_cost_usd or forecast_run.cost_usd or 0.0)
        latency = float(forecast_run.latency_ms or 0)
    else:
        coverage = None
        cost = 0.0
        latency = 0.0
    failed = completion_status == "failed"
    result = EvaluationResult(
        id=str(uuid.uuid4()),
        run_id=evaluation_run.id,
        probability=None if failed else probability,
        outcome=item.outcome,
        brier_score=None if failed or probability is None else brier_score(probability, item.outcome),
        log_loss=None if failed or probability is None else log_loss(probability, item.outcome),
        cost=cost,
        latency=latency,
        evidence_coverage=coverage,
        completion_status=completion_status,
    )
    session.add(result)
    evaluation_run.status = completion_status
    evaluation_run.error = error
    evaluation_run.completed_at = utcnow()
    return result


def _refresh_experiment(session: Session, experiment: EvaluationExperiment) -> None:
    runs = session.scalars(select(EvaluationRun).where(EvaluationRun.experiment_id == experiment.id)).all()
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


def execute_evaluation_run(session: Session, evaluation_run: EvaluationRun, job: Job | None = None) -> None:
    """Execute one frozen cell without changing either forecasting implementation."""

    experiment = session.get(EvaluationExperiment, evaluation_run.experiment_id)
    item = session.get(EvaluationQuestion, evaluation_run.question_id)
    if experiment is None or item is None:
        raise RuntimeError("evaluation_run_missing_parent")
    existing = session.scalar(select(EvaluationResult).where(EvaluationResult.run_id == evaluation_run.id))
    if existing is not None and evaluation_run.status in TERMINAL_RUN_STATUSES:
        _refresh_experiment(session, experiment)
        return
    frozen = _configuration(experiment)
    _assert_frozen_environment(experiment, frozen)
    frozen_question = next(
        (question for question in frozen.get("questions") or [] if question.get("id") == item.id),
        None,
    )
    if frozen_question is None or frozen_question.get("question_hash") != item.question_hash:
        raise ExperimentEnvironmentMismatch("Evaluation question does not match the frozen experiment")
    profile, bundle, context, pricing_catalog, timeout = _frozen_profile_and_context(
        frozen,
        evaluation_run.profile_id,
    )
    experiment.status = "running"
    evaluation_run.status = "running"
    evaluation_run.started_at = evaluation_run.started_at or utcnow()
    session.commit()
    question = _ensure_runtime_question(session, evaluation_run, item)
    forecast_run = _ensure_forecast_run(session, evaluation_run, question, context, item)
    session.commit()
    version = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == forecast_run.id))
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
        version = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == forecast_run.id))
    if version is None or version.ensemble_probability is None:
        raise RuntimeError("evaluation_forecast_probability_missing")
    completion_status: Literal["completed", "partial", "failed"] = (
        "partial" if _is_partial(session, forecast_run, profile) else "completed"
    )
    _persist_result(
        session,
        evaluation_run=evaluation_run,
        item=item,
        forecast_run=forecast_run,
        probability=version.ensemble_probability,
        completion_status=completion_status,
    )
    _refresh_experiment(session, experiment)
    session.commit()


def fail_evaluation_job(session: Session, job: Job, *, error: str, category: str) -> bool:
    try:
        payload = json.loads(job.payload_json or "{}")
    except json.JSONDecodeError:
        return False
    run_id = payload.get("evaluation_run_id")
    if not run_id:
        return False
    evaluation_run = session.get(EvaluationRun, run_id)
    if evaluation_run is None:
        return True
    experiment = session.get(EvaluationExperiment, evaluation_run.experiment_id)
    item = session.get(EvaluationQuestion, evaluation_run.question_id)
    forecast_run = (
        session.get(ForecastRun, evaluation_run.forecast_run_id)
        if evaluation_run.forecast_run_id
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
            evaluation_run=evaluation_run,
            item=item,
            forecast_run=forecast_run,
            probability=None,
            completion_status="failed",
            error=error,
        )
    else:
        evaluation_run.status = "failed"
        evaluation_run.error = error
        evaluation_run.completed_at = utcnow()
    if experiment is not None:
        _refresh_experiment(session, experiment)
    return True


def evaluation_experiment_progress(session: Session, experiment: EvaluationExperiment) -> dict[str, Any]:
    runs = session.scalars(
        select(EvaluationRun)
        .where(EvaluationRun.experiment_id == experiment.id)
        .order_by(EvaluationRun.question_id, EvaluationRun.profile_id)
    ).all()
    status_counts = {status: 0 for status in ("pending", "running", "completed", "partial", "failed")}
    per_profile: dict[str, dict[str, int]] = {}
    for run in runs:
        status_counts[run.status] = status_counts.get(run.status, 0) + 1
        bucket = per_profile.setdefault(
            run.profile_id,
            {status: 0 for status in ("pending", "running", "completed", "partial", "failed")},
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
        "profiles": json.loads(experiment.profiles),
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
                "question_id": run.question_id,
                "profile_id": run.profile_id,
                "forecast_run_id": run.forecast_run_id,
                "status": run.status,
                "error": run.error,
            }
            for run in runs
        ],
    }


def _profile_metrics(runs: list[EvaluationRun], results: dict[str, EvaluationResult]) -> dict[str, Any]:
    rows = [results[item.id] for item in runs if item.id in results]
    valid = [item for item in rows if item.probability is not None and item.completion_status != "failed"]
    costs = [item.cost for item in rows]
    latencies = [item.latency for item in rows]
    coverage = [item.evidence_coverage for item in rows if item.evidence_coverage is not None]
    total = len(runs)
    completed = sum(item.status == "completed" for item in runs)
    partial = sum(item.status == "partial" for item in runs)
    failed = sum(item.status == "failed" for item in runs)
    return {
        "profile_id": runs[0].profile_id if runs else "",
        "assigned_questions": total,
        "scored_questions": len(valid),
        "brier_score": mean([item.brier_score for item in valid if item.brier_score is not None]),
        "log_loss": mean([item.log_loss for item in valid if item.log_loss is not None]),
        "total_cost": sum(costs),
        "mean_cost": mean(costs),
        "median_cost": median(costs),
        "mean_latency": mean(latencies),
        "evidence_coverage": mean(coverage),
        "completion_rate": completed / total if total else None,
        "partial_rate": partial / total if total else None,
        "failure_rate": failed / total if total else None,
        "completed": completed,
        "partial": partial,
        "failed": failed,
    }


def evaluation_comparison_report(
    session: Session,
    experiment: EvaluationExperiment,
) -> EvaluationComparisonReport:
    dataset = session.get(EvaluationDataset, experiment.dataset_id)
    if dataset is None:
        raise RuntimeError("evaluation_dataset_not_found")
    runs = session.scalars(
        select(EvaluationRun)
        .where(EvaluationRun.experiment_id == experiment.id)
        .order_by(EvaluationRun.question_id, EvaluationRun.profile_id)
    ).all()
    result_rows = session.scalars(
        select(EvaluationResult).where(EvaluationResult.run_id.in_([item.id for item in runs] or [""]))
    ).all()
    results = {item.run_id: item for item in result_rows}
    questions = {
        item.id: item
        for item in session.scalars(
            select(EvaluationQuestion).where(EvaluationQuestion.dataset_id == experiment.dataset_id)
        ).all()
    }
    profile_groups = {
        profile_id: [item for item in runs if item.profile_id == profile_id]
        for profile_id in json.loads(experiment.profiles)
    }
    frozen = _configuration(experiment)
    rows: list[dict[str, Any]] = []
    for run in runs:
        result = results.get(run.id)
        item = questions.get(run.question_id)
        rows.append(
            {
                "run_id": run.id,
                "forecast_run_id": run.forecast_run_id,
                "question_id": run.question_id,
                "question": item.question_text if item else None,
                "profile_id": run.profile_id,
                "status": run.status,
                "error": run.error,
                "probability": result.probability if result else None,
                "outcome": result.outcome if result else (item.outcome if item else None),
                "brier_score": result.brier_score if result else None,
                "log_loss": result.log_loss if result else None,
                "cost": result.cost if result else 0.0,
                "latency": result.latency if result else 0.0,
                "evidence_coverage": result.evidence_coverage if result else None,
                "completion_status": result.completion_status if result else run.status,
            }
        )
    return EvaluationComparisonReport(
        experiment_id=experiment.id,
        dataset={
            "id": dataset.id,
            "name": dataset.name,
            "version": dataset.version,
            "dataset_hash": dataset.dataset_hash,
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
            "question_hashes": [item.get("question_hash") for item in frozen.get("questions") or []],
            "evidence_cutoffs": [item.get("evidence_cutoff") for item in frozen.get("questions") or []],
            "synthetic_test": bool(frozen.get("synthetic_test")),
        },
        profiles=[_profile_metrics(profile_groups[profile_id], results) for profile_id in profile_groups],
        rows=rows,
    )
