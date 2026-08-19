from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forecastlab.evaluation import (
    brier_score,
    log_loss,
    mean,
    median,
    paired_profile_comparison,
    reliability_bins,
)
from forecastlab.execution import configuration_hash, resolve_execution_context
from forecastlab.gitinfo import current_git_commit
from forecastlab.hashing import canonical_json, import_hash, sha256_text
from forecastlab.profiles import list_profiles, load_profile, profile_hash
from forecastlab.prompts import prompt_hashes
from forecastlab.timeutil import as_utc, parse_datetime, utcnow
from forecastlab_api.jobs import enqueue_job
from forecastlab_api.models import (
    BenchmarkDataset,
    BenchmarkExperiment,
    BenchmarkQuestion,
    BenchmarkResult,
    BenchmarkTask,
    ForecastVersion,
    Question,
)
from forecastlab_api.pipeline import create_run_record, execute_run, provider_settings_from_secrets
from forecastlab_api.secrets import load_secrets

SYNTHETIC_DATASET_NAME = "synthetic_fixtures_v1"
DEFAULT_EXPERIMENT_PROFILES = ("single_agent_baseline", "three_track_ensemble")


def dataset_hash_for_rows(rows: list[dict], *, is_synthetic: bool, name: str) -> str:
    payload = {
        "name": name,
        "is_synthetic": is_synthetic,
        "rows": [import_hash(_row_fields(row)) for row in rows],
    }
    return sha256_text(canonical_json(payload))


def _row_fields(row: dict) -> dict[str, str]:
    return {
        "question": str(row["question"]).strip(),
        "forecast_date": str(row["forecast_date"]),
        "resolution_date": str(row["resolution_date"]),
        "outcome": str(row["outcome"]),
        "resolution_source": str(row["resolution_source"]),
    }


def ensure_dataset(
    session: Session,
    *,
    name: str,
    description: str,
    rows: list[dict],
    provenance: str,
    is_synthetic: bool,
) -> tuple[BenchmarkDataset, int, int, list[str]]:
    flags = []
    for row in rows:
        raw = row.get("is_synthetic", is_synthetic)
        flag = raw is True or str(raw).lower() == "true"
        flags.append(flag)
    if flags and any(flag != flags[0] for flag in flags):
        raise ValueError("mixed_synthetic_and_real")
    agreed_synthetic = flags[0] if flags else is_synthetic
    digest = dataset_hash_for_rows(rows, is_synthetic=agreed_synthetic, name=name)
    existing = session.scalar(select(BenchmarkDataset).where(BenchmarkDataset.dataset_hash == digest))
    created = 0
    duplicates = 0
    errors: list[str] = []
    if existing is None:
        existing = BenchmarkDataset(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            dataset_hash=digest,
            provenance=provenance,
            is_synthetic=agreed_synthetic,
            question_count=0,
            metadata_json=json.dumps({"row_count": len(rows), "name": name}),
        )
        session.add(existing)
        session.flush()
    for index, row in enumerate(rows, start=1):
        try:
            fields = _row_fields(row)
            item_hash = import_hash(fields)
            found = session.scalar(select(BenchmarkQuestion).where(BenchmarkQuestion.import_hash == item_hash))
            if found:
                if found.dataset_id is None:
                    found.dataset_id = existing.id
                duplicates += 1
                continue
            forecast_date = parse_datetime(fields["forecast_date"])
            resolution_date = parse_datetime(fields["resolution_date"])
            if forecast_date is None or resolution_date is None:
                raise ValueError("forecast_date and resolution_date are required")
            session.add(
                BenchmarkQuestion(
                    id=str(uuid.uuid4()),
                    dataset_id=existing.id,
                    question=fields["question"],
                    forecast_date=forecast_date,
                    resolution_date=resolution_date,
                    outcome=int(fields["outcome"]),
                    resolution_source=fields["resolution_source"],
                    category=str(row.get("category") or "uncategorized"),
                    provenance=str(row.get("provenance") or provenance),
                    import_hash=item_hash,
                    is_synthetic=agreed_synthetic,
                )
            )
            created += 1
        except Exception as exc:
            errors.append(f"row {index}: {exc}")
    session.flush()
    existing.question_count = int(
        session.scalar(
            select(func.count()).select_from(BenchmarkQuestion).where(BenchmarkQuestion.dataset_id == existing.id)
        )
        or 0
    )
    return existing, created, duplicates, errors


def create_experiment(
    session: Session,
    *,
    dataset_id: str,
    profile_ids: list[str],
) -> BenchmarkExperiment:
    dataset = session.get(BenchmarkDataset, dataset_id)
    if dataset is None:
        raise ValueError("dataset_not_found")
    questions = session.scalars(
        select(BenchmarkQuestion).where(BenchmarkQuestion.dataset_id == dataset.id)
    ).all()
    if not questions:
        raise ValueError("dataset_empty")
    unique_profiles = list(dict.fromkeys(profile_ids))
    if not unique_profiles:
        raise ValueError("profiles_required")
    for profile_id in unique_profiles:
        load_profile(profile_id)
    secrets = load_secrets()
    settings_data = provider_settings_from_secrets(secrets)
    template = resolve_execution_context(
        requested_mode="backtest",
        profile_id=unique_profiles[0],
        settings=settings_data,
        synthetic_fixture_run=dataset.is_synthetic,
        as_of=questions[0].forecast_date,
    )
    hashes = {profile_id: profile_hash(load_profile(profile_id)) for profile_id in unique_profiles}
    prompts = prompt_hashes()
    experiment_payload = {
        "dataset_hash": dataset.dataset_hash,
        "profile_ids": unique_profiles,
        "profile_hashes": hashes,
        "prompt_hashes": prompts,
        "model_provider": template.model_provider,
        "model_name": template.model_name,
        "search_provider": template.search_provider,
        "evidence_policy": template.evidence_policy,
        "effective_max_cost_usd": template.effective_max_cost_usd,
        "is_synthetic": dataset.is_synthetic,
    }
    experiment = BenchmarkExperiment(
        id=str(uuid.uuid4()),
        dataset_id=dataset.id,
        status="pending",
        code_commit=current_git_commit(),
        execution_context_json=template.model_dump_json(),
        model_provider=template.model_provider,
        model_name=template.model_name,
        search_provider=template.search_provider,
        profile_ids_json=json.dumps(unique_profiles),
        profile_hashes_json=json.dumps(hashes),
        prompt_hashes_json=json.dumps(prompts),
        evidence_policy=template.evidence_policy,
        experiment_hash=configuration_hash(experiment_payload),
        is_synthetic=dataset.is_synthetic,
        total_tasks=len(questions) * len(unique_profiles),
    )
    session.add(experiment)
    session.flush()
    for question in questions:
        for profile_id in unique_profiles:
            task = BenchmarkTask(
                id=str(uuid.uuid4()),
                experiment_id=experiment.id,
                benchmark_question_id=question.id,
                profile_id=profile_id,
                status="pending",
            )
            session.add(task)
            session.flush()
            enqueue_job(
                session,
                job_type="benchmark_task",
                payload={"task_id": task.id},
                idempotency_key=f"benchmark_task:{task.id}",
            )
    return experiment


def execute_benchmark_task(session: Session, task: BenchmarkTask, job=None) -> None:
    existing = session.scalar(select(BenchmarkResult).where(BenchmarkResult.benchmark_task_id == task.id))
    if existing is not None and task.status == "completed":
        return
    experiment = session.get(BenchmarkExperiment, task.experiment_id)
    item = session.get(BenchmarkQuestion, task.benchmark_question_id)
    if experiment is None or item is None:
        raise RuntimeError("benchmark_task_missing_parent")
    if experiment.status == "pending":
        experiment.status = "running"
        experiment.started_at = experiment.started_at or utcnow()
    task.status = "running"
    task.started_at = utcnow()
    task.attempts += 1
    settings_data = provider_settings_from_secrets()
    frozen = json.loads(experiment.execution_context_json or "{}")
    settings_data["model_provider"] = experiment.model_provider
    settings_data["model_name"] = experiment.model_name
    settings_data["search_provider"] = experiment.search_provider
    settings_data["max_cost_usd"] = float(
        frozen.get("effective_max_cost_usd") or settings_data.get("max_cost_usd") or 5.0
    )
    context = resolve_execution_context(
        requested_mode="backtest",
        profile_id=task.profile_id,
        settings=settings_data,
        synthetic_fixture_run=experiment.is_synthetic,
        as_of=as_utc(item.forecast_date),
    )
    question = Question(
        id=str(uuid.uuid4()),
        original_text=item.question,
        notes=f"benchmark:{item.id}:{experiment.id}",
        status="draft",
        requested_mode="backtest",
        requested_profile_id=task.profile_id,
        requested_as_of=as_utc(item.forecast_date),
        is_benchmark=True,
    )
    session.add(question)
    session.flush()
    run = create_run_record(
        session,
        question=question,
        context=context,
        as_of=as_utc(item.forecast_date),
        enqueue=False,
    )
    task.run_id = run.id
    session.commit()
    execute_run(session, run, job=job)
    session.refresh(run)
    latest = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run.id))
    prob = latest.ensemble_probability if latest else None
    failed = prob is None
    if existing is None:
        session.add(
            BenchmarkResult(
                id=str(uuid.uuid4()),
                experiment_id=experiment.id,
                benchmark_task_id=task.id,
                benchmark_question_id=item.id,
                run_id=run.id,
                profile_id=task.profile_id,
                probability=prob,
                brier=None if failed else brier_score(prob or 0, item.outcome),
                log_loss_value=None if failed else log_loss(prob or 0, item.outcome),
                cost_usd=run.cost_usd,
                latency_ms=run.latency_ms,
                failed=failed,
            )
        )
    task.status = "failed" if failed else "completed"
    task.completed_at = utcnow()
    task.error = run.error_message if failed else None
    _refresh_experiment_counts(session, experiment)


def _refresh_experiment_counts(session: Session, experiment: BenchmarkExperiment) -> None:
    tasks = session.scalars(select(BenchmarkTask).where(BenchmarkTask.experiment_id == experiment.id)).all()
    experiment.completed_tasks = sum(1 for item in tasks if item.status == "completed")
    experiment.failed_tasks = sum(1 for item in tasks if item.status == "failed")
    if all(item.status in {"completed", "failed"} for item in tasks):
        experiment.status = "completed"
        experiment.completed_at = utcnow()
    else:
        experiment.status = "running"


def experiment_progress(session: Session, experiment: BenchmarkExperiment) -> dict:
    tasks = session.scalars(select(BenchmarkTask).where(BenchmarkTask.experiment_id == experiment.id)).all()
    by_status = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
    per_profile: dict[str, dict[str, int]] = {}
    errors = []
    for task in tasks:
        by_status[task.status] = by_status.get(task.status, 0) + 1
        bucket = per_profile.setdefault(task.profile_id, {"pending": 0, "running": 0, "completed": 0, "failed": 0})
        bucket[task.status] = bucket.get(task.status, 0) + 1
        if task.error:
            errors.append({"task_id": task.id, "profile_id": task.profile_id, "error": task.error})
    total = experiment.total_tasks or len(tasks)
    done = by_status["completed"] + by_status["failed"]
    return {
        "experiment_id": experiment.id,
        "status": experiment.status,
        "pending_tasks": by_status["pending"],
        "running_tasks": by_status["running"],
        "completed_tasks": by_status["completed"],
        "failed_tasks": by_status["failed"],
        "total_tasks": total,
        "percent": round(100.0 * done / total, 1) if total else 0.0,
        "per_profile": per_profile,
        "recent_errors": errors[-8:],
        "is_synthetic": experiment.is_synthetic,
        "experiment_hash": experiment.experiment_hash,
        "code_commit": experiment.code_commit,
    }


def experiment_summary(session: Session, experiment: BenchmarkExperiment) -> dict:
    dataset = session.get(BenchmarkDataset, experiment.dataset_id)
    rows = session.scalars(select(BenchmarkResult).where(BenchmarkResult.experiment_id == experiment.id)).all()
    questions = {
        item.id: item
        for item in session.scalars(
            select(BenchmarkQuestion).where(BenchmarkQuestion.dataset_id == experiment.dataset_id)
        ).all()
    }
    by_profile: dict[str, list[BenchmarkResult]] = {}
    for row in rows:
        by_profile.setdefault(row.profile_id, []).append(row)
    profiles_out = []
    reliability_by_profile = {}
    paired_maps: dict[str, dict[str, dict[str, float]]] = {}
    profile_hashes = json.loads(experiment.profile_hashes_json or "{}")
    for profile_id, items in by_profile.items():
        briers = [item.brier for item in items if item.brier is not None]
        losses = [item.log_loss_value for item in items if item.log_loss_value is not None]
        costs = [item.cost_usd for item in items]
        lats = [float(item.latency_ms) for item in items]
        failures = [item for item in items if item.failed]
        mean_brier = mean(briers)
        mean_cost = mean(costs)
        profiles_out.append(
            {
                "profile_id": profile_id,
                "profile_hash": profile_hashes.get(profile_id),
                "n": len(items),
                "brier": mean_brier,
                "log_loss": mean(losses),
                "mean_cost_usd": mean_cost,
                "median_cost_usd": median(costs),
                "mean_latency_ms": mean(lats),
                "failure_rate": len(failures) / len(items) if items else None,
                "brier_per_dollar": None
                if not mean_cost
                else (mean_brier / mean_cost if mean_brier is not None else None),
            }
        )
        pairs: list[tuple[float, int]] = []
        paired_maps[profile_id] = {}
        for item in items:
            question = questions.get(item.benchmark_question_id)
            if item.probability is not None and question is not None and item.brier is not None:
                pairs.append((item.probability, question.outcome))
                paired_maps[profile_id][item.benchmark_question_id] = {
                    "brier": item.brier,
                    "log_loss": item.log_loss_value or 0.0,
                    "cost_usd": item.cost_usd,
                    "latency_ms": float(item.latency_ms),
                }
        reliability_by_profile[profile_id] = reliability_bins(pairs)
    comparisons = []
    ids = list(paired_maps)
    for i, left in enumerate(ids):
        for right in ids[i + 1 :]:
            comparisons.append(
                paired_profile_comparison(paired_maps[left], paired_maps[right], left_id=left, right_id=right)
            )
    question_rows = []
    for item in rows:
        question = questions.get(item.benchmark_question_id)
        question_rows.append(
            {
                "id": item.id,
                "benchmark_task_id": item.benchmark_task_id,
                "profile_id": item.profile_id,
                "probability": item.probability,
                "brier": item.brier,
                "log_loss_value": item.log_loss_value,
                "cost_usd": item.cost_usd,
                "latency_ms": item.latency_ms,
                "failed": item.failed,
                "question": question.question if question else None,
                "category": question.category if question else None,
                "outcome": question.outcome if question else None,
            }
        )
    return {
        "experiment_id": experiment.id,
        "experiment_hash": experiment.experiment_hash,
        "dataset_id": experiment.dataset_id,
        "dataset_name": dataset.name if dataset else None,
        "dataset_hash": dataset.dataset_hash if dataset else None,
        "synthetic": bool(experiment.is_synthetic),
        "is_synthetic": bool(experiment.is_synthetic),
        "sample_size": len(rows),
        "profile_hashes": profile_hashes,
        "prompt_hashes": json.loads(experiment.prompt_hashes_json or "{}"),
        "code_commit": experiment.code_commit,
        "evidence_policy": experiment.evidence_policy,
        "model_provider": experiment.model_provider,
        "model_name": experiment.model_name,
        "search_provider": experiment.search_provider,
        "notice": (
            "Software-verification fixtures only. Not evidence of real-world forecasting quality."
            if experiment.is_synthetic
            else "Real imported dataset. Scores are research diagnostics, not a calibration claim."
        ),
        "profiles": profiles_out,
        "reliability_by_profile": reliability_by_profile,
        "paired_comparisons": comparisons,
        "rows": question_rows,
        "progress": experiment_progress(session, experiment),
        "profile_configs": [item.model_dump() for item in list_profiles()],
        "execution_context": json.loads(experiment.execution_context_json or "{}"),
    }


def serialize_dataset(dataset: BenchmarkDataset) -> dict:
    return {
        "id": dataset.id,
        "name": dataset.name,
        "description": dataset.description,
        "dataset_hash": dataset.dataset_hash,
        "provenance": dataset.provenance,
        "is_synthetic": dataset.is_synthetic,
        "created_at": dataset.created_at.isoformat() if isinstance(dataset.created_at, datetime) else dataset.created_at,
        "question_count": dataset.question_count,
    }
