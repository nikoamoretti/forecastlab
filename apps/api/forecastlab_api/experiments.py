from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forecastlab.environment import (
    build_environment_identity,
    compare_environment,
    require_python_lock,
    working_tree_dirty,
)
from forecastlab.errors import ExperimentEnvironmentMismatch
from forecastlab.evaluation import (
    brier_score,
    log_loss,
    mean,
    median,
    paired_profile_comparison,
    reliability_bins,
)
from forecastlab.execution import ExecutionContext, configuration_hash, resolve_execution_context
from forecastlab.gitinfo import current_git_commit
from forecastlab.hashing import canonical_json, import_hash, redact_secrets, sha256_text
from forecastlab.pricing import load_pricing, pricing_hash
from forecastlab.profiles import effective_profile, list_profiles, load_profile, profile_hash
from forecastlab.prompts import PromptBundle, load_prompt_bundle
from forecastlab.schemas import ForecastContract, ForecastProfile, ResolutionContract
from forecastlab.timeutil import as_utc, parse_datetime, utcnow
from forecastlab_api.contracts import store_forecast_contract
from forecastlab_api.jobs import enqueue_job
from forecastlab_api.models import (
    BenchmarkDataset,
    BenchmarkExperiment,
    BenchmarkProfileSnapshot,
    BenchmarkQuestion,
    BenchmarkResult,
    BenchmarkTask,
    EvidenceClaimRow,
    EvidenceItem,
    ForecastContractRow,
    ForecastGraphRow,
    ForecastNodeRow,
    ForecastNodeRunRow,
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

SYNTHETIC_DATASET_NAME = "synthetic_fixtures_v1"
CURRENT_BUILTIN_KEY = "forecastlab.synthetic.binary"
CURRENT_BUILTIN_VERSION = "2"
DEFAULT_EXPERIMENT_PROFILES = ("single_agent_equal_budget_v1", "three_track_equal_budget_v1")
V1_EVALUATION_DATASET_KEY = "forecastlab.v1.evaluation.synthetic"
V1_EVALUATION_DATASET_VERSION = "1"
V1_EVALUATION_PROFILES = (
    "single_model_forecaster_v1",
    "three_track_forecaster",
    "graph_forecaster_v1",
)
V1_EVALUATION_QUESTION_COUNT = 10
V1_EVALUATION_METRICS = (
    "brier_score",
    "log_loss",
    "cost_usd",
    "latency_ms",
    "completion_rate",
    "evidence_coverage",
)
V1_EVALUATION_NOTICE = (
    "This is an experiment framework. Results are descriptive diagnostics and do not establish profile superiority."
)
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed"})
TERMINAL_EXPERIMENT_STATUSES = frozenset({"completed", "completed_with_failures", "failed"})
CRASH_HOOKS: dict[str, Callable[[str], None]] = {}


class InjectedCrash(RuntimeError):
    def __init__(self, phase: str) -> None:
        self.phase = phase
        super().__init__(f"injected_crash:{phase}")


def maybe_crash(phase: str) -> None:
    hook = CRASH_HOOKS.get(phase)
    if hook is not None:
        hook(phase)


def _truthy(value: Any, default: bool = False) -> bool:
    if value is True or value is False:
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes"}


def _parse_fallback_sources(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if text.startswith("["):
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("fallback_sources_invalid")
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in text.replace("|", ";").split(";") if part.strip()]


def _require_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field}_required")
    return text


def _iso(value: datetime) -> str:
    return as_utc(value).isoformat()


def canonical_benchmark_row(row: dict[str, Any], *, is_synthetic: bool, provenance: str = "user_import") -> dict[str, Any]:
    question = _require_text(row.get("question"), "question")
    try:
        outcome = int(row["outcome"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid_outcome") from exc
    if outcome not in (0, 1):
        raise ValueError("invalid_outcome")
    forecast_date = parse_datetime(str(row.get("forecast_date") or ""))
    resolution_date = parse_datetime(str(row.get("resolution_date") or ""))
    if forecast_date is None or resolution_date is None:
        raise ValueError("forecast_date_and_resolution_date_required")
    if forecast_date >= resolution_date:
        raise ValueError("forecast_date_after_resolution_date")
    deadline = parse_datetime(str(row.get("resolution_deadline") or "")) or resolution_date
    if deadline is None:
        raise ValueError("resolution_deadline_required")
    resolution_source = str(row.get("resolution_source") or "").strip()
    if is_synthetic:
        exact_yes = str(row.get("exact_yes") or "").strip() or f"Yes if: {question}"
        exact_no = str(row.get("exact_no") or "").strip() or f"No if not: {question}"
        authoritative = str(row.get("authoritative_source") or resolution_source).strip() or "synthetic fixture"
    else:
        exact_yes = _require_text(row.get("exact_yes"), "exact_yes")
        exact_no = _require_text(row.get("exact_no"), "exact_no")
        authoritative = _require_text(row.get("authoritative_source") or resolution_source, "authoritative_source")
    return {
        "question": question,
        "exact_yes": exact_yes,
        "exact_no": exact_no,
        "resolution_deadline": _iso(deadline),
        "authoritative_source": authoritative,
        "fallback_sources": _parse_fallback_sources(row.get("fallback_sources")),
        "geography": str(row.get("geography") or "").strip(),
        "units": str(row.get("units") or "").strip(),
        "ambiguity_notes": str(row.get("ambiguity_notes") or "").strip(),
        "cancellation_conditions": str(row.get("cancellation_conditions") or "").strip(),
        "resolver_risk_notes": str(row.get("resolver_risk_notes") or "").strip(),
        "forecast_date": _iso(forecast_date),
        "resolution_date": _iso(resolution_date),
        "outcome": outcome,
        "resolution_source": resolution_source or authoritative,
        "category": str(row.get("category") or "uncategorized").strip() or "uncategorized",
        "provenance": str(row.get("provenance") or provenance).strip() or provenance,
        "is_synthetic": is_synthetic,
    }


def dataset_hash_for_rows(rows: list[dict], *, is_synthetic: bool, name: str | None = None) -> str:
    del name
    canonical = [canonical_benchmark_row(row, is_synthetic=is_synthetic) for row in rows]
    canonical.sort(key=lambda item: canonical_json(item))
    return sha256_text(canonical_json({"is_synthetic": is_synthetic, "rows": canonical}))


def contract_from_benchmark(item: BenchmarkQuestion) -> ResolutionContract:
    deadline = as_utc(item.resolution_deadline) or as_utc(item.resolution_date)
    if deadline is None:
        raise ValueError("resolution_deadline_required")
    return ResolutionContract(
        exact_yes=item.exact_yes,
        exact_no=item.exact_no,
        resolution_deadline=deadline,
        authoritative_source=item.authoritative_source or item.resolution_source,
        fallback_sources=json.loads(item.fallback_sources_json or "[]"),
        geography=item.geography or None,
        units=item.units or None,
        ambiguity_notes=item.ambiguity_notes or "",
        cancellation_conditions=item.cancellation_conditions or "",
        resolver_risk_notes=item.resolver_risk_notes or "",
    )


def ensure_dataset(
    session: Session,
    *,
    name: str,
    description: str,
    rows: list[dict],
    provenance: str,
    is_synthetic: bool,
    builtin_key: str | None = None,
    builtin_version: str | None = None,
    is_builtin: bool = False,
) -> tuple[BenchmarkDataset, int, int, list[str]]:
    flags = []
    for row in rows:
        raw = row.get("is_synthetic", is_synthetic)
        flags.append(_truthy(raw, default=is_synthetic))
    if flags and any(flag != flags[0] for flag in flags):
        raise ValueError("mixed_synthetic_and_real")
    agreed_synthetic = flags[0] if flags else is_synthetic
    normalized = [canonical_benchmark_row(row, is_synthetic=agreed_synthetic, provenance=provenance) for row in rows]
    digest = sha256_text(
        canonical_json({"is_synthetic": agreed_synthetic, "rows": sorted(normalized, key=canonical_json)})
    )
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
            builtin_key=builtin_key,
            builtin_version=builtin_version,
            is_builtin=is_builtin,
        )
        session.add(existing)
        session.flush()
    seen_hashes: set[str] = set()
    for fields in normalized:
        item_hash = import_hash(fields)
        if item_hash in seen_hashes:
            duplicates += 1
            continue
        found = session.scalar(
            select(BenchmarkQuestion).where(
                BenchmarkQuestion.dataset_id == existing.id,
                BenchmarkQuestion.import_hash == item_hash,
            )
        )
        if found:
            duplicates += 1
            seen_hashes.add(item_hash)
            continue
        seen_hashes.add(item_hash)
        session.add(
            BenchmarkQuestion(
                id=str(uuid.uuid4()),
                dataset_id=existing.id,
                question=fields["question"],
                forecast_date=parse_datetime(fields["forecast_date"]),
                resolution_date=parse_datetime(fields["resolution_date"]),
                outcome=int(fields["outcome"]),
                resolution_source=fields["resolution_source"],
                category=fields["category"],
                provenance=fields["provenance"],
                import_hash=item_hash,
                is_synthetic=agreed_synthetic,
                exact_yes=fields["exact_yes"],
                exact_no=fields["exact_no"],
                resolution_deadline=parse_datetime(fields["resolution_deadline"]),
                authoritative_source=fields["authoritative_source"],
                fallback_sources_json=json.dumps(fields["fallback_sources"]),
                geography=fields["geography"] or None,
                units=fields["units"] or None,
                ambiguity_notes=fields["ambiguity_notes"],
                cancellation_conditions=fields["cancellation_conditions"],
                resolver_risk_notes=fields["resolver_risk_notes"],
            )
        )
        created += 1
    session.flush()
    if builtin_key and builtin_version:
        existing.builtin_key = builtin_key
        existing.builtin_version = builtin_version
        existing.is_builtin = True
        existing.archived_at = None
    existing.question_count = int(
        session.scalar(
            select(func.count()).select_from(BenchmarkQuestion).where(BenchmarkQuestion.dataset_id == existing.id)
        )
        or 0
    )
    return existing, created, duplicates, errors


def _safe_json(payload: Any) -> str:
    text = payload if isinstance(payload, str) else canonical_json(payload)
    return redact_secrets(text)


def _persist_profile_snapshot(
    session: Session,
    *,
    experiment: BenchmarkExperiment,
    profile_id: str,
    settings_data: dict[str, Any],
    frozen: dict[str, Any],
    synthetic: bool,
    as_of: datetime,
    bundle: PromptBundle,
    pricing_payload: dict[str, Any],
    pricing_digest: str,
) -> BenchmarkProfileSnapshot:
    source = load_profile(profile_id)
    context = resolve_execution_context(
        requested_mode="backtest",
        profile_id=profile_id,
        settings=settings_data,
        synthetic_fixture_run=synthetic,
        as_of=as_of,
    )
    context = context.model_copy(
        update={
            "model_provider": frozen["model_provider"],
            "model_name": frozen["model_name"],
            "model_base_url": frozen["model_base_url"],
            "model_timeout_seconds": frozen["model_timeout_seconds"],
            "search_provider": frozen["search_provider"],
            "evidence_policy": frozen["evidence_policy"],
            "model_is_mock": frozen["model_is_mock"],
            "search_is_mock": frozen["search_is_mock"],
            "prompt_versions": bundle.versions(),
            "prompt_hashes": bundle.hashes(),
        }
    )
    effective = apply_execution_limits(effective_profile(source, user_max_cost_usd=float(settings_data["max_cost_usd"])), context)
    snapshot_payload = {
        "profile_id": profile_id,
        "source_profile": source.model_dump(mode="json"),
        "effective_profile": effective.model_dump(mode="json"),
        "profile_hash": profile_hash(source),
        "prompt_hashes": bundle.hashes(),
        "prompt_versions": bundle.versions(),
        "model_provider": frozen["model_provider"],
        "model_name": frozen["model_name"],
        "model_base_url": frozen["model_base_url"],
        "search_provider": frozen["search_provider"],
        "model_timeout_seconds": frozen["model_timeout_seconds"],
        "evidence_policy": frozen["evidence_policy"],
        "effective_max_cost_usd": effective.max_estimated_cost_usd,
        "effective_max_tokens": effective.max_tokens,
        "effective_max_model_calls": effective.max_model_calls,
        "effective_max_search_calls": effective.max_search_calls,
        "effective_max_fetched_documents": effective.max_fetched_documents,
        "effective_max_wall_clock_seconds": effective.max_wall_clock_seconds,
        "pricing_hash": pricing_digest,
    }
    snapshot = BenchmarkProfileSnapshot(
        id=str(uuid.uuid4()),
        experiment_id=experiment.id,
        profile_id=profile_id,
        source_profile_json=_safe_json(source.model_dump(mode="json")),
        effective_profile_json=_safe_json(effective.model_dump(mode="json")),
        profile_hash=profile_hash(source),
        prompt_bundle_json=_safe_json(bundle.model_dump(mode="json")),
        prompt_versions_json=_safe_json(bundle.versions()),
        prompt_hashes_json=_safe_json(bundle.hashes()),
        model_provider=frozen["model_provider"],
        model_name=frozen["model_name"],
        model_base_url=frozen["model_base_url"],
        search_provider=frozen["search_provider"],
        model_timeout_seconds=frozen["model_timeout_seconds"],
        evidence_policy=frozen["evidence_policy"],
        effective_max_cost_usd=effective.max_estimated_cost_usd,
        effective_max_tokens=effective.max_tokens,
        effective_max_model_calls=effective.max_model_calls,
        effective_max_search_calls=effective.max_search_calls,
        effective_max_fetched_documents=effective.max_fetched_documents,
        effective_max_wall_clock_seconds=effective.max_wall_clock_seconds,
        pricing_snapshot_json=_safe_json(pricing_payload),
        pricing_hash=pricing_digest,
        execution_context_json=_safe_json(context.model_dump(mode="json")),
        configuration_hash=configuration_hash(snapshot_payload),
    )
    session.add(snapshot)
    return snapshot


def create_experiment(
    session: Session,
    *,
    dataset_id: str,
    profile_ids: list[str],
) -> BenchmarkExperiment:
    dataset = session.get(BenchmarkDataset, dataset_id)
    if dataset is None:
        raise ValueError("dataset_not_found")
    questions = session.scalars(select(BenchmarkQuestion).where(BenchmarkQuestion.dataset_id == dataset.id)).all()
    if not questions:
        raise ValueError("dataset_empty")
    unique_profiles = list(dict.fromkeys(profile_ids))
    if not unique_profiles:
        raise ValueError("profiles_required")
    for profile_id in unique_profiles:
        load_profile(profile_id)
    if working_tree_dirty() and not dataset.is_synthetic:
        raise ValueError("dirty_working_tree")
    if not dataset.is_synthetic:
        require_python_lock(synthetic=False)
    secrets = load_secrets()
    settings_data = provider_settings_from_secrets(secrets)
    template = resolve_execution_context(
        requested_mode="backtest",
        profile_id=unique_profiles[0],
        settings=settings_data,
        synthetic_fixture_run=dataset.is_synthetic,
        as_of=questions[0].forecast_date,
    )
    frozen = {
        "model_provider": template.model_provider,
        "model_name": template.model_name,
        "model_base_url": template.model_base_url,
        "search_provider": template.search_provider,
        "model_timeout_seconds": float(settings_data.get("model_timeout_seconds") or 60.0),
        "evidence_policy": template.evidence_policy,
        "model_is_mock": template.model_is_mock,
        "search_is_mock": template.search_is_mock,
    }
    bundle = load_prompt_bundle()
    pricing_payload = load_pricing()
    pricing_digest = pricing_hash(catalog=pricing_payload)
    hashes = {profile_id: profile_hash(load_profile(profile_id)) for profile_id in unique_profiles}
    identity = build_environment_identity(
        prompt_bundle_hash=sha256_text(canonical_json(bundle.hashes())),
        profile_hashes=hashes,
        pricing_catalog=pricing_payload,
    )
    experiment = BenchmarkExperiment(
        id=str(uuid.uuid4()),
        dataset_id=dataset.id,
        status="pending",
        code_commit=current_git_commit(),
        execution_context_json=_safe_json(
            {
                **template.model_dump(mode="json"),
                **{key: frozen[key] for key in ("model_provider", "model_name", "model_base_url", "search_provider", "model_timeout_seconds", "evidence_policy")},
            }
        ),
        model_provider=frozen["model_provider"],
        model_name=frozen["model_name"],
        model_base_url=frozen["model_base_url"],
        model_timeout_seconds=frozen["model_timeout_seconds"],
        search_provider=frozen["search_provider"],
        profile_ids_json=json.dumps(unique_profiles),
        profile_hashes_json=json.dumps(hashes),
        prompt_hashes_json=_safe_json(bundle.hashes()),
        evidence_policy=frozen["evidence_policy"],
        is_synthetic=dataset.is_synthetic,
        total_tasks=len(questions) * len(unique_profiles),
        environment_identity_json=_safe_json(identity),
        tracked_source_hash=identity.get("tracked_source_hash"),
        pyproject_hash=identity.get("pyproject_hash"),
        dependency_hash=identity.get("dependency_hash"),
        package_lock_hash=identity.get("package_lock_hash"),
        working_tree_dirty=bool(identity.get("working_tree_dirty")),
        application_version=identity.get("application_version"),
        container_image_digest=identity.get("container_image_digest"),
    )
    session.add(experiment)
    session.flush()
    snapshot_hashes: dict[str, str] = {}
    for profile_id in unique_profiles:
        snapshot = _persist_profile_snapshot(
            session,
            experiment=experiment,
            profile_id=profile_id,
            settings_data=settings_data,
            frozen=frozen,
            synthetic=dataset.is_synthetic,
            as_of=as_utc(questions[0].forecast_date) or questions[0].forecast_date,
            bundle=bundle,
            pricing_payload=pricing_payload,
            pricing_digest=pricing_digest,
        )
        snapshot_hashes[profile_id] = snapshot.configuration_hash
    experiment.experiment_hash = configuration_hash(
        {
            "dataset_hash": dataset.dataset_hash,
            "profile_ids": unique_profiles,
            "profile_hashes": hashes,
            "prompt_hashes": bundle.hashes(),
            "model_provider": frozen["model_provider"],
            "model_name": frozen["model_name"],
            "model_base_url": frozen["model_base_url"],
            "search_provider": frozen["search_provider"],
            "model_timeout_seconds": frozen["model_timeout_seconds"],
            "evidence_policy": frozen["evidence_policy"],
            "snapshot_configuration_hashes": snapshot_hashes,
            "pricing_hash": pricing_digest,
            "is_synthetic": dataset.is_synthetic,
            "tracked_source_hash": identity.get("tracked_source_hash"),
            "environment_identity_hash": sha256_text(canonical_json({k: v for k, v in identity.items() if k != "working_tree_dirty"})),
        }
    )
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


def _load_snapshot(session: Session, experiment_id: str, profile_id: str) -> BenchmarkProfileSnapshot:
    snapshot = session.scalar(
        select(BenchmarkProfileSnapshot).where(
            BenchmarkProfileSnapshot.experiment_id == experiment_id,
            BenchmarkProfileSnapshot.profile_id == profile_id,
        )
    )
    if snapshot is None:
        raise RuntimeError("benchmark_snapshot_missing")
    return snapshot


def _task_question_notes(task_id: str) -> str:
    return f"benchmark-task:{task_id}"


def ensure_task_question(
    session: Session,
    task: BenchmarkTask,
    item: BenchmarkQuestion,
) -> Question:
    if task.question_id:
        question = session.get(Question, task.question_id)
        if question is not None:
            if question.contract is None:
                save_contract(session, question, contract_from_benchmark(item))
                session.flush()
            ensure_benchmark_forecast_contract(session, question, item)
            return question
    notes = _task_question_notes(task.id)
    question = session.scalar(select(Question).where(Question.notes == notes))
    if question is None:
        question = Question(
            id=str(uuid.uuid4()),
            original_text=item.question,
            notes=notes,
            status="draft",
            requested_mode="backtest",
            requested_profile_id=task.profile_id,
            requested_as_of=as_utc(item.forecast_date),
            is_benchmark=True,
        )
        session.add(question)
        session.flush()
    if question.contract is None:
        save_contract(session, question, contract_from_benchmark(item))
        session.flush()
    ensure_benchmark_forecast_contract(session, question, item)
    task.question_id = question.id
    return question


def ensure_benchmark_forecast_contract(
    session: Session,
    question: Question,
    item: BenchmarkQuestion,
) -> ForecastContractRow:
    """Create the approved first-class contract required by graph benchmark runs."""

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
    deadline = as_utc(item.resolution_deadline) or as_utc(item.resolution_date)
    if deadline is None:
        raise ValueError("resolution_deadline_required")
    contract = ForecastContract(
        id=str(uuid.uuid4()),
        question_id=question.id,
        version=1,
        created_at=as_utc(item.forecast_date),
        created_by="benchmark_import",
        original_question=item.question,
        normalized_question=item.question.strip(),
        yes_condition=item.exact_yes,
        no_condition=item.exact_no,
        resolution_date=deadline,
        authoritative_source=item.authoritative_source or item.resolution_source,
        fallback_sources=json.loads(item.fallback_sources_json or "[]"),
        resolution_method=(
            "Resolve the binary outcome against the authoritative source at the stated deadline; "
            "use fallback sources only if the authoritative source is unavailable."
        ),
        ambiguity_notes=item.ambiguity_notes or "",
        cancellation_conditions=item.cancellation_conditions or "",
        resolver_risk_notes=item.resolver_risk_notes or "",
        geography=item.geography,
        units=item.units,
        domain=item.category,
        initial_reference_class=f"Previously resolved {item.category or 'binary'} questions with comparable conditions.",
        suggested_drivers=["historical base rate", "current trend", "resolution mechanics"],
        known_dependencies=[],
        status="approved",
    )
    errors = contract.approval_errors()
    if errors:
        raise ValueError(f"benchmark_forecast_contract_invalid:{','.join(errors)}")
    question.normalized_text = contract.normalized_question
    question.forecast_deadline = contract.resolution_date
    return store_forecast_contract(session, contract)


def ensure_task_run(
    session: Session,
    *,
    task: BenchmarkTask,
    question: Question,
    context: ExecutionContext,
    as_of: datetime | None,
) -> ForecastRun:
    if task.run_id:
        run = session.get(ForecastRun, task.run_id)
        if run is not None:
            if run.benchmark_task_id is None:
                run.benchmark_task_id = task.id
            return run
    existing = session.scalar(select(ForecastRun).where(ForecastRun.benchmark_task_id == task.id))
    if existing is not None:
        task.run_id = existing.id
        return existing
    run = create_run_record(
        session,
        question=question,
        context=context,
        as_of=as_of,
        enqueue=False,
        benchmark_task_id=task.id,
    )
    task.run_id = run.id
    return run


def _graph_for_run(session: Session, run: ForecastRun) -> ForecastGraphRow | None:
    node_run = session.scalar(
        select(ForecastNodeRunRow)
        .where(ForecastNodeRunRow.forecast_run_id == run.id)
        .order_by(ForecastNodeRunRow.created_at, ForecastNodeRunRow.id)
        .limit(1)
    )
    if node_run is not None:
        node = session.get(ForecastNodeRow, node_run.node_id)
        return session.get(ForecastGraphRow, node.graph_id) if node is not None else None
    return session.scalar(
        select(ForecastGraphRow)
        .join(ForecastContractRow, ForecastGraphRow.contract_id == ForecastContractRow.id)
        .where(
            ForecastContractRow.question_id == run.question_id,
            ForecastContractRow.status == "approved",
            ForecastGraphRow.status == "approved",
        )
        .order_by(ForecastContractRow.version.desc(), ForecastGraphRow.version.desc())
        .limit(1)
    )


def _run_execution_strategy(session: Session, run: ForecastRun) -> str:
    if run.benchmark_task_id:
        task = session.get(BenchmarkTask, run.benchmark_task_id)
        if task is not None:
            snapshot = session.scalar(
                select(BenchmarkProfileSnapshot).where(
                    BenchmarkProfileSnapshot.experiment_id == task.experiment_id,
                    BenchmarkProfileSnapshot.profile_id == task.profile_id,
                )
            )
            if snapshot is not None:
                try:
                    frozen_profile = json.loads(snapshot.effective_profile_json or "{}")
                except json.JSONDecodeError:
                    frozen_profile = {}
                strategy = str(frozen_profile.get("execution_strategy") or "").strip()
                if strategy:
                    return strategy
    try:
        return load_profile(run.profile_id).execution_strategy
    except FileNotFoundError:
        return "legacy_tracks"


def evidence_coverage_for_run(session: Session, run: ForecastRun) -> tuple[float | None, int, int]:
    """Measure provenance-backed research-unit coverage for either execution strategy."""

    graph_execution = _run_execution_strategy(session, run) == "graph_nodes"
    if not graph_execution:
        graph_execution = bool(
            session.scalar(
                select(ForecastNodeRunRow.id).where(ForecastNodeRunRow.forecast_run_id == run.id).limit(1)
            )
        )
    if graph_execution:
        graph = _graph_for_run(session, run)
        if graph is None:
            return None, 0, 0
        nodes = session.scalars(select(ForecastNodeRow).where(ForecastNodeRow.graph_id == graph.id)).all()
        node_ids = {node.id for node in nodes}
        node_runs = session.scalars(
            select(ForecastNodeRunRow).where(ForecastNodeRunRow.forecast_run_id == run.id)
        ).all()
        claims = session.scalars(
            select(EvidenceClaimRow)
            .join(EvidenceItem, EvidenceClaimRow.evidence_item_id == EvidenceItem.id)
            .where(EvidenceItem.run_id == run.id, EvidenceClaimRow.forecast_node_id.in_(node_ids or {""}))
        ).all()
        claim_nodes = {claim.id: claim.forecast_node_id for claim in claims}
        covered: set[str] = set()
        for node_run in node_runs:
            selected = [
                *json.loads(node_run.supporting_claim_ids_json or "[]"),
                *json.loads(node_run.opposing_claim_ids_json or "[]"),
            ]
            if any(claim_nodes.get(str(claim_id)) == node_run.node_id for claim_id in selected):
                covered.add(node_run.node_id)
        total = len(nodes)
        return (len(covered) / total if total else None), len(covered), total

    tracks = session.scalars(select(ResearchTrack).where(ResearchTrack.run_id == run.id)).all()
    track_ids = {track.id for track in tracks}
    accepted = session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.run_id == run.id,
            EvidenceItem.track_id.in_(track_ids or {""}),
            EvidenceItem.rejected.is_(False),
            EvidenceItem.as_of_eligible.is_(True),
        )
    ).all()
    covered_tracks = {item.track_id for item in accepted if item.track_id in track_ids}
    total = len(tracks)
    return (len(covered_tracks) / total if total else None), len(covered_tracks), total


def _result_is_partial(session: Session, run: ForecastRun, *, failed: bool) -> bool:
    if failed:
        return False
    graph = _graph_for_run(session, run)
    if graph is not None and _run_execution_strategy(session, run) == "graph_nodes":
        total = int(
            session.scalar(select(func.count()).select_from(ForecastNodeRow).where(ForecastNodeRow.graph_id == graph.id))
            or 0
        )
        completed = int(
            session.scalar(
                select(func.count())
                .select_from(ForecastNodeRunRow)
                .where(ForecastNodeRunRow.forecast_run_id == run.id)
            )
            or 0
        )
        return completed < total
    tracks = session.scalars(select(ResearchTrack).where(ResearchTrack.run_id == run.id)).all()
    return any(track.status == "failed" or track.error_message for track in tracks)


def finalize_benchmark_task(
    session: Session,
    task: BenchmarkTask,
    *,
    status: Literal["completed", "failed"],
    experiment: BenchmarkExperiment,
    item: BenchmarkQuestion,
    run: ForecastRun | None,
    probability: float | None = None,
    error: str | None = None,
    error_category: str | None = None,
    partial: bool = False,
) -> BenchmarkResult:
    existing = session.scalar(select(BenchmarkResult).where(BenchmarkResult.benchmark_task_id == task.id))
    if existing is None:
        failed = status == "failed"
        coverage, covered_units, total_units = (
            evidence_coverage_for_run(session, run) if run is not None else (None, 0, 0)
        )
        existing = BenchmarkResult(
            id=str(uuid.uuid4()),
            experiment_id=experiment.id,
            benchmark_task_id=task.id,
            benchmark_question_id=item.id,
            run_id=run.id if run is not None else task.run_id,
            profile_id=task.profile_id,
            probability=probability,
            brier=None if failed or probability is None else brier_score(probability, item.outcome),
            log_loss_value=None if failed or probability is None else log_loss(probability, item.outcome),
            cost_usd=(run.total_cost_usd or run.cost_usd) if run is not None else 0.0,
            latency_ms=run.latency_ms if run is not None else 0,
            evidence_coverage=coverage,
            evidence_covered_units=covered_units,
            evidence_total_units=total_units,
            failed=failed,
            partial=partial,
        )
        session.add(existing)
        try:
            with session.begin_nested():
                session.flush()
        except IntegrityError:
            recovered = session.scalar(select(BenchmarkResult).where(BenchmarkResult.benchmark_task_id == task.id))
            if recovered is None:
                raise
            existing = recovered
        session.commit()
    maybe_crash("after_benchmark_result")
    task.status = status
    task.completed_at = utcnow()
    task.error = error
    task.error_category = error_category
    session.commit()
    maybe_crash("after_task_completion_before_counts")
    _refresh_experiment_counts(session, experiment)
    return existing


def finalize_from_run(
    session: Session,
    *,
    task: BenchmarkTask,
    experiment: BenchmarkExperiment,
    item: BenchmarkQuestion,
    run: ForecastRun,
    error: str | None = None,
    error_category: str | None = None,
) -> BenchmarkResult:
    version = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run.id))
    apply_totals_to_run(run, default_ledger().totals(run.id))
    probability = version.ensemble_probability if version is not None else None
    failed = probability is None
    return finalize_benchmark_task(
        session,
        task,
        status="failed" if failed else "completed",
        experiment=experiment,
        item=item,
        run=run,
        probability=probability,
        error=error or (run.error_message if failed else None),
        error_category=error_category,
        partial=_result_is_partial(session, run, failed=failed),
    )


def fail_job_relatives(session: Session, job: Job, *, error: str, category: str) -> None:
    try:
        payload = json.loads(job.payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    task = session.get(BenchmarkTask, payload["task_id"]) if payload.get("task_id") else None
    run = None
    if task and task.run_id:
        run = session.get(ForecastRun, task.run_id)
    elif payload.get("run_id"):
        run = session.get(ForecastRun, payload["run_id"])
    if run is not None and run.status not in TERMINAL_TASK_STATUSES:
        run.status = "failed"
        run.error_message = error
        run.error_stage = category
        run.finished_at = utcnow()
        run.progress_stage = "failed"
        run.progress_message = error
    if task is None:
        return
    experiment = session.get(BenchmarkExperiment, task.experiment_id)
    item = session.get(BenchmarkQuestion, task.benchmark_question_id)
    if experiment is None or item is None:
        task.status = "failed"
        task.error = error
        task.error_category = category
        task.completed_at = utcnow()
        return
    finalize_benchmark_task(
        session,
        task,
        status="failed",
        experiment=experiment,
        item=item,
        run=run,
        error=error,
        error_category=category,
    )


def execute_benchmark_task(session: Session, task: BenchmarkTask, job=None) -> None:
    existing = session.scalar(select(BenchmarkResult).where(BenchmarkResult.benchmark_task_id == task.id))
    experiment = session.get(BenchmarkExperiment, task.experiment_id)
    item = session.get(BenchmarkQuestion, task.benchmark_question_id)
    if experiment is None or item is None:
        raise RuntimeError("benchmark_task_missing_parent")
    if existing is not None and task.status in TERMINAL_TASK_STATUSES:
        _refresh_experiment_counts(session, experiment)
        return
    snapshot = _load_snapshot(session, experiment.id, task.profile_id)
    _assert_frozen_environment(experiment, snapshot)
    if experiment.status == "pending":
        experiment.status = "running"
        experiment.started_at = experiment.started_at or utcnow()
    if task.status not in TERMINAL_TASK_STATUSES:
        task.status = "running"
        task.started_at = task.started_at or utcnow()
    task.attempts += 1
    profile = ForecastProfile.model_validate(json.loads(snapshot.effective_profile_json))
    bundle = PromptBundle.model_validate(json.loads(snapshot.prompt_bundle_json))
    context = ExecutionContext.model_validate(json.loads(snapshot.execution_context_json))
    context = context.model_copy(
        update={
            "profile_id": task.profile_id,
            "model_provider": snapshot.model_provider,
            "model_name": snapshot.model_name,
            "model_base_url": snapshot.model_base_url,
            "model_timeout_seconds": snapshot.model_timeout_seconds,
            "search_provider": snapshot.search_provider,
            "evidence_policy": snapshot.evidence_policy,
        }
    )
    question = ensure_task_question(session, task, item)
    question.requested_profile_id = task.profile_id
    session.commit()
    maybe_crash("after_task_question_identity")
    run = ensure_task_run(
        session,
        task=task,
        question=question,
        context=context,
        as_of=as_utc(item.forecast_date),
    )
    session.commit()
    maybe_crash("after_run_creation")
    version = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run.id))
    if version is not None or existing is not None:
        finalize_from_run(session, task=task, experiment=experiment, item=item, run=run)
        session.commit()
        return
    try:
        pricing_catalog = json.loads(snapshot.pricing_snapshot_json or "{}")
    except json.JSONDecodeError:
        pricing_catalog = {}
    execute_run(
        session,
        run,
        job=job,
        profile=profile,
        prompt_bundle=bundle,
        model_timeout=snapshot.model_timeout_seconds,
        pricing_catalog=pricing_catalog,
    )
    session.refresh(run)
    maybe_crash("after_forecast_version")
    finalize_from_run(session, task=task, experiment=experiment, item=item, run=run)
    session.commit()


def _refresh_experiment_counts(session: Session, experiment: BenchmarkExperiment) -> None:
    tasks = session.scalars(select(BenchmarkTask).where(BenchmarkTask.experiment_id == experiment.id)).all()
    experiment.completed_tasks = sum(1 for item in tasks if item.status == "completed")
    experiment.failed_tasks = sum(1 for item in tasks if item.status == "failed")
    unfinished = [item for item in tasks if item.status not in TERMINAL_TASK_STATUSES]
    if unfinished:
        experiment.status = "running"
        return
    if experiment.failed_tasks == 0:
        experiment.status = "completed"
    elif experiment.completed_tasks == 0:
        experiment.status = "failed"
    else:
        experiment.status = "completed_with_failures"
    experiment.completed_at = utcnow()


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
        "model_provider": experiment.model_provider,
        "model_name": experiment.model_name,
        "model_base_url": experiment.model_base_url,
        "model_timeout_seconds": experiment.model_timeout_seconds,
        "search_provider": experiment.search_provider,
        "evidence_policy": experiment.evidence_policy,
    }


def _result_status(item: BenchmarkResult) -> str:
    if item.failed:
        return "failed"
    if item.partial:
        return "partial"
    return "full"


def _metric_set(items: list[BenchmarkResult]) -> dict[str, Any]:
    briers = [item.brier for item in items if item.brier is not None]
    losses = [item.log_loss_value for item in items if item.log_loss_value is not None]
    costs = [item.cost_usd for item in items]
    lats = [float(item.latency_ms) for item in items]
    coverage = [item.evidence_coverage for item in items if item.evidence_coverage is not None]
    mean_brier = mean(briers)
    mean_cost = mean(costs)
    return {
        "n": len(items),
        "brier": mean_brier,
        "log_loss": mean(losses),
        "mean_cost_usd": mean_cost,
        "median_cost_usd": median(costs),
        "mean_latency_ms": mean(lats),
        "mean_evidence_coverage": mean(coverage),
        "evidence_coverage_n": len(coverage),
        "brier_per_dollar": None if not mean_cost or mean_brier is None else mean_brier / mean_cost,
    }


def _paired_map(items: list[BenchmarkResult], questions: dict[str, BenchmarkQuestion]) -> dict[str, dict[str, float]]:
    payload: dict[str, dict[str, float]] = {}
    for item in items:
        question = questions.get(item.benchmark_question_id)
        if item.probability is None or question is None or item.brier is None:
            continue
        payload[item.benchmark_question_id] = {
            "brier": item.brier,
            "log_loss": item.log_loss_value or 0.0,
            "cost_usd": item.cost_usd,
            "latency_ms": float(item.latency_ms),
        }
        if item.evidence_coverage is not None:
            payload[item.benchmark_question_id]["evidence_coverage"] = item.evidence_coverage
    return payload


def _experiment_spend(session: Session, experiment: BenchmarkExperiment, rows: list[BenchmarkResult]) -> dict[str, Any]:
    tasks = session.scalars(select(BenchmarkTask).where(BenchmarkTask.experiment_id == experiment.id)).all()
    runs = session.scalars(
        select(ForecastRun).where(ForecastRun.benchmark_task_id.in_([task.id for task in tasks] or [""]))
    ).all()
    run_by_task = {item.benchmark_task_id: item for item in runs if item.benchmark_task_id}
    result_task_ids = {item.benchmark_task_id for item in rows}

    def run_cost(run: ForecastRun | None, fallback: float = 0.0) -> float:
        if run is None:
            return fallback
        return float(run.total_cost_usd or run.cost_usd or fallback)

    full_cost = 0.0
    partial_cost = 0.0
    failed_cost = 0.0
    for item in rows:
        amount = run_cost(run_by_task.get(item.benchmark_task_id), item.cost_usd)
        if item.failed:
            failed_cost += amount
        elif item.partial:
            partial_cost += amount
        else:
            full_cost += amount
    for run in runs:
        if run.benchmark_task_id not in result_task_ids:
            failed_cost += run_cost(run)
    started = experiment.total_tasks or len(tasks)
    total = sum(run_cost(run) for run in runs) or (full_cost + partial_cost + failed_cost)
    return {
        "total_cost_usd": total,
        "total_full_cost_usd": full_cost,
        "total_partial_cost_usd": partial_cost,
        "total_failed_task_cost_usd": failed_cost,
        "mean_cost_per_started_task": (total / started) if started else None,
        "model_cost_usd": sum(float(run.model_cost_usd or 0) for run in runs),
        "search_cost_usd": sum(float(run.search_cost_usd or 0) for run in runs),
        "failed_attempt_cost_usd": sum(float(run.failed_attempt_cost_usd or 0) for run in runs),
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
    paired_all: dict[str, dict[str, dict[str, float]]] = {}
    paired_full: dict[str, dict[str, dict[str, float]]] = {}
    profile_hashes = json.loads(experiment.profile_hashes_json or "{}")
    for profile_id, items in by_profile.items():
        full_items = [item for item in items if not item.failed and not item.partial]
        partial_items = [item for item in items if item.partial and not item.failed]
        failed_items = [item for item in items if item.failed]
        valid_items = [item for item in items if item.probability is not None and not item.failed]
        all_valid = _metric_set(valid_items)
        full_only = _metric_set(full_items)
        total = len(items)
        profiles_out.append(
            {
                "profile_id": profile_id,
                "profile_hash": profile_hashes.get(profile_id),
                "n": total,
                "total_count": total,
                "full_count": len(full_items),
                "partial_count": len(partial_items),
                "failed_count": len(failed_items),
                "completion_rate": len(full_items) / total if total else None,
                "partial_rate": len(partial_items) / total if total else None,
                "failure_rate": len(failed_items) / total if total else None,
                "all_valid": all_valid,
                "full_only": full_only,
                "brier": all_valid["brier"],
                "log_loss": all_valid["log_loss"],
                "mean_cost_usd": all_valid["mean_cost_usd"],
                "median_cost_usd": all_valid["median_cost_usd"],
                "mean_latency_ms": all_valid["mean_latency_ms"],
                "mean_evidence_coverage": all_valid["mean_evidence_coverage"],
                "brier_per_dollar": all_valid["brier_per_dollar"],
            }
        )
        pairs: list[tuple[float, int]] = []
        for item in valid_items:
            question = questions.get(item.benchmark_question_id)
            if item.probability is not None and question is not None:
                pairs.append((item.probability, question.outcome))
        reliability_by_profile[profile_id] = reliability_bins(pairs)
        paired_all[profile_id] = _paired_map(valid_items, questions)
        paired_full[profile_id] = _paired_map(full_items, questions)
    comparisons = []
    comparisons_full = []
    ids = list(paired_all)
    for i, left in enumerate(ids):
        for right in ids[i + 1 :]:
            comparisons.append(
                paired_profile_comparison(paired_all[left], paired_all[right], left_id=left, right_id=right)
            )
            comparisons_full.append(
                paired_profile_comparison(paired_full[left], paired_full[right], left_id=left, right_id=right)
            )
    question_rows = []
    for item in rows:
        question = questions.get(item.benchmark_question_id)
        status = _result_status(item)
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
                "evidence_coverage": item.evidence_coverage,
                "evidence_covered_units": item.evidence_covered_units,
                "evidence_total_units": item.evidence_total_units,
                "failed": item.failed,
                "partial": item.partial,
                "status": status,
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
        "model_base_url": experiment.model_base_url,
        "model_timeout_seconds": experiment.model_timeout_seconds,
        "search_provider": experiment.search_provider,
        "notice": (
            "Software-verification fixtures only. Not evidence of real-world forecasting quality."
            if experiment.is_synthetic
            else "Real imported dataset. Scores are research diagnostics, not a calibration claim."
        ),
        "comparison_interpretation": V1_EVALUATION_NOTICE,
        "metric_definitions": {
            "brier_score": "Mean squared error between the forecast probability and binary outcome; lower is better.",
            "log_loss": "Mean logarithmic penalty for the probability assigned to the resolved outcome; lower is better.",
            "cost_usd": "Recorded total provider cost, including search and failed-attempt charges.",
            "latency_ms": "Recorded end-to-end forecast run latency in milliseconds.",
            "completion_rate": "Fraction of scheduled tasks that produced a full, non-partial forecast.",
            "evidence_coverage": (
                "Graph profile: fraction of planned graph nodes citing a persisted Evidence Claim. "
                "Legacy profile: fraction of research tracks with accepted, as-of-eligible evidence."
            ),
        },
        "profiles": profiles_out,
        "reliability_by_profile": reliability_by_profile,
        "paired_comparisons": comparisons,
        "paired_comparisons_all_valid": comparisons,
        "paired_comparisons_full_only": comparisons_full,
        "spend": _experiment_spend(session, experiment, rows),
        "rows": question_rows,
        "environment_identity": json.loads(experiment.environment_identity_json or "{}"),
        "progress": experiment_progress(session, experiment),
        "profile_configs": [item.model_dump() for item in list_profiles()],
        "execution_context": json.loads(experiment.execution_context_json or "{}"),
    }


def _assert_frozen_environment(experiment: BenchmarkExperiment, snapshot: BenchmarkProfileSnapshot) -> None:
    frozen = json.loads(experiment.environment_identity_json or "{}")
    if not frozen:
        raise ExperimentEnvironmentMismatch("Experiment is missing a frozen execution identity")
    try:
        frozen_pricing = json.loads(snapshot.pricing_snapshot_json or "{}")
    except json.JSONDecodeError:
        frozen_pricing = {}
    current = build_environment_identity(
        prompt_bundle_hash=frozen.get("prompt_bundle_hash"),
        profile_hashes=frozen.get("profile_hashes") or {},
        pricing_catalog=frozen_pricing,
    )
    if not experiment.is_synthetic and (not frozen.get("dependency_hash") or not current.get("dependency_hash")):
        raise ExperimentEnvironmentMismatch("python_lockfile_required")
    mismatches = compare_environment(frozen, current)
    if mismatches:
        raise ExperimentEnvironmentMismatch(
            f"Experiment environment mismatch: {', '.join(mismatches)}"
        )


def current_builtin_dataset(session: Session) -> BenchmarkDataset | None:
    return session.scalar(
        select(BenchmarkDataset).where(
            BenchmarkDataset.builtin_key == CURRENT_BUILTIN_KEY,
            BenchmarkDataset.builtin_version == CURRENT_BUILTIN_VERSION,
            BenchmarkDataset.is_builtin.is_(True),
            BenchmarkDataset.archived_at.is_(None),
        )
    )


def current_v1_evaluation_dataset(session: Session) -> BenchmarkDataset | None:
    return session.scalar(
        select(BenchmarkDataset).where(
            BenchmarkDataset.builtin_key == V1_EVALUATION_DATASET_KEY,
            BenchmarkDataset.builtin_version == V1_EVALUATION_DATASET_VERSION,
            BenchmarkDataset.is_builtin.is_(True),
            BenchmarkDataset.archived_at.is_(None),
        )
    )


def v1_evaluation_workflow(session: Session) -> dict[str, Any]:
    dataset = current_v1_evaluation_dataset(session)
    if dataset is None:
        raise ValueError("v1_evaluation_dataset_not_found")
    questions = session.scalars(
        select(BenchmarkQuestion)
        .where(BenchmarkQuestion.dataset_id == dataset.id)
        .order_by(BenchmarkQuestion.forecast_date, BenchmarkQuestion.question)
    ).all()
    if len(questions) != V1_EVALUATION_QUESTION_COUNT:
        raise ValueError(
            f"v1_evaluation_requires_{V1_EVALUATION_QUESTION_COUNT}_questions:found_{len(questions)}"
        )
    return {
        "dataset": serialize_dataset(dataset),
        "profiles": list(V1_EVALUATION_PROFILES),
        "question_count": len(questions),
        "task_count": len(questions) * len(V1_EVALUATION_PROFILES),
        "metrics": list(V1_EVALUATION_METRICS),
        "questions": [
            {
                "id": item.id,
                "question": item.question,
                "forecast_date": _iso(item.forecast_date),
                "resolution_date": _iso(item.resolution_date),
                "category": item.category,
                "outcome": item.outcome,
            }
            for item in questions
        ],
        "notice": V1_EVALUATION_NOTICE,
    }


def create_v1_evaluation(session: Session) -> tuple[BenchmarkExperiment, dict[str, Any]]:
    workflow = v1_evaluation_workflow(session)
    experiment = create_experiment(
        session,
        dataset_id=workflow["dataset"]["id"],
        profile_ids=list(V1_EVALUATION_PROFILES),
    )
    return experiment, workflow


def serialize_dataset(dataset: BenchmarkDataset) -> dict:
    return {
        "id": dataset.id,
        "name": dataset.name,
        "description": dataset.description,
        "dataset_hash": dataset.dataset_hash,
        "provenance": dataset.provenance,
        "is_synthetic": dataset.is_synthetic,
        "builtin_key": dataset.builtin_key,
        "builtin_version": dataset.builtin_version,
        "is_builtin": dataset.is_builtin,
        "archived_at": dataset.archived_at.isoformat() if isinstance(dataset.archived_at, datetime) else dataset.archived_at,
        "created_at": dataset.created_at.isoformat() if isinstance(dataset.created_at, datetime) else dataset.created_at,
        "question_count": dataset.question_count,
    }
