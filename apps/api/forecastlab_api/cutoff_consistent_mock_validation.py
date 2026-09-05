from __future__ import annotations

import json
import subprocess
import time
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.environment import build_environment_identity, working_tree_dirty
from forecastlab.execution import configuration_hash, resolve_execution_context
from forecastlab.fetch import fetch_document
from forecastlab.graph_execution import GraphNodeForecastResult, run_graph_node_forecasts
from forecastlab.hashing import canonical_json, redact_secrets, sha256_text
from forecastlab.pricing import load_pricing, pricing_hash
from forecastlab.profiles import effective_profile, load_profile, profile_hash
from forecastlab.prompts import load_prompt_bundle
from forecastlab.providers.mock import MockModelProvider
from forecastlab.timeutil import as_utc, parse_datetime, utcnow
from forecastlab_api.config import ROOT
from forecastlab_api.cutoff_consistent_mock import (
    CORPUS_ID,
    CORPUS_VERSION,
    MANIFEST_PATH,
    SHADOW_COST_CEILING_USD,
    SHADOW_MODEL,
    SHADOW_MODEL_PROVIDER,
    SHADOW_SEARCH_PROVIDER,
    CutoffConsistentMockSearchProvider,
    ShadowPricedResearchPlanner,
    load_cutoff_consistent_manifest,
)
from forecastlab_api.forecast_experiments import (
    _ensure_forecast_contract,
    _resolution_contract,
)
from forecastlab_api.graph_executor import GraphForecastExecutor
from forecastlab_api.graph_planner_validation import collect_graph_planner_run_metrics
from forecastlab_api.models import (
    EvaluationQuestion,
    ForecastRun,
    Question,
)
from forecastlab_api.persist import save_contract
from forecastlab_api.pilot_benchmark import import_pilot_benchmark
from forecastlab_api.pipeline import apply_execution_limits, create_run_record
from forecastlab_api.usage_ledger import PersistentUsageLedger, apply_totals_to_run

STARTING_COMMIT = "ea1657c0f3bd71598c97971168cd5dd93c2cdb83"
PLANNER_SOURCE_COMMIT = "83f1ff8fbcebbf90ed631e0823ece154bd651df2"
PREVIOUS_VALIDATION_A_ID = "4a5c23e0-0cf6-463b-a608-a1ce65c88e63"
PREVIOUS_VALIDATION_B_ID = "b0159d60-4b86-4638-8fe7-337a7d1ab300"
PILOT_V1_DATASET_HASH = "c9cd748c1114eff5d16321ecf7d78b18be4a0b65b49ff7719aa5276e302f7888"
VALIDATION_PROFILE_ID = "graph_forecaster_v1"
VALIDATION_PROFILE_VERSION = 5
VALIDATION_SCHEMA_VERSION = 1
VALIDATION_NAME = "Cutoff-Consistent Mock Graph Validation"
VALIDATION_NOTE_PREFIX = "cutoff-consistent-mock-validation:"
PER_QUESTION_COST_CEILING_USD = 0.25
REQUIRED_NOTICE = (
    "Cutoff-consistent synthetic workflow validation. This result tests execution "
    "reliability only and is not evidence of forecasting quality."
)

PREVIOUS_A_ARTIFACT_PATH = (
    ROOT / "artifacts" / "graph_execution_validation" / "validation_results.json"
)
PREVIOUS_B_ARTIFACT_PATH = (
    ROOT / "artifacts" / "graph_research_planner_validation" / "validation_results.json"
)
DEFAULT_ARTIFACT_PATH = (
    ROOT / "artifacts" / "cutoff_consistent_mock_validation" / "validation_results.json"
)
DEFAULT_REPORT_PATH = ROOT / "docs" / "CUTOFF_CONSISTENT_MOCK_VALIDATION_REPORT.md"

EXACT_QUESTION_IDS = (
    "94f806bf-fc0b-41da-84bd-f9ce9792b8e2",
    "67ca0f85-ee8f-4371-b5b3-0b9cc2f9bd56",
    "dfda5684-99a3-492b-89d3-316be4bedc23",
    "e93a736f-df6d-4fbf-a833-8afa2c557aa6",
    "292e0bf3-3b58-43bb-b877-61412481e2d1",
)

_ALLOWED_EXACT_PATHS = frozenset(
    {
        "apps/api/forecastlab_api/cutoff_consistent_mock.py",
        "apps/api/forecastlab_api/cutoff_consistent_mock_validation.py",
        "packages/forecasting/forecastlab/graph_research.py",
        "scripts/run_cutoff_consistent_mock_validation.py",
        "tests/test_cutoff_consistent_mock_corpus.py",
        "tests/test_cutoff_consistent_mock_validation.py",
    }
)
_ALLOWED_PREFIXES = ("fixtures/backtests/graph_validation_v1/",)
_PROTECTED_BEHAVIOR_PREFIXES = (
    "configs/forecast_profiles/",
    "configs/prompts/",
    "fixtures/benchmarks/",
)
_PROTECTED_BEHAVIOR_PATHS = frozenset(
    {
        "packages/forecasting/forecastlab/contracts.py",
        "packages/forecasting/forecastlab/evidence_claims.py",
        "packages/forecasting/forecastlab/graph_aggregation.py",
        "packages/forecasting/forecastlab/graphs.py",
        "packages/forecasting/forecastlab/node_forecasting.py",
        "packages/forecasting/forecastlab/research_planning.py",
        "apps/api/forecastlab_api/forecast_experiments.py",
        "apps/api/forecastlab_api/pilot_benchmark.py",
    }
)
_PRODUCTION_CUTOFF_PATHS = frozenset(
    {
        "packages/forecasting/forecastlab/evidence_claims.py",
        "packages/forecasting/forecastlab/fetch.py",
        "packages/forecasting/forecastlab/wayback.py",
    }
)


class CutoffConsistentValidationError(RuntimeError):
    """The corrected validation failed a reproducibility or safety preflight."""


def _artifact(path: Path, expected_id: str) -> dict[str, Any]:
    if not path.is_file():
        raise CutoffConsistentValidationError(
            f"source_validation_artifact_missing:{path.name}"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("validation_id") != expected_id:
        raise CutoffConsistentValidationError("source_validation_id_mismatch")
    return payload


def exact_question_manifests() -> list[dict[str, Any]]:
    previous = _artifact(PREVIOUS_B_ARTIFACT_PATH, PREVIOUS_VALIDATION_B_ID)
    if previous.get("freeze", {}).get("dataset", {}).get("hash") != PILOT_V1_DATASET_HASH:
        raise CutoffConsistentValidationError("source_validation_dataset_hash_mismatch")
    manifests = previous["freeze"]["dataset"]["questions"]
    rows = previous.get("rows") or []
    if len(manifests) != 5 or len(rows) != 5:
        raise CutoffConsistentValidationError("source_validation_question_count_mismatch")
    ids = tuple(str(item["evaluation_question_id"]) for item in manifests)
    row_ids = tuple(str(item["evaluation_question_id"]) for item in rows)
    if ids != EXACT_QUESTION_IDS or row_ids != EXACT_QUESTION_IDS:
        raise CutoffConsistentValidationError("source_validation_question_order_mismatch")
    if [item["question_hash"] for item in manifests] != [
        item["question_hash"] for item in rows
    ]:
        raise CutoffConsistentValidationError("source_validation_question_hash_mismatch")
    return [dict(item) for item in manifests]


def _mock_provider_settings() -> dict[str, Any]:
    return {
        "model_provider": "mock",
        "model_name": "mock-forecast-v1",
        "model_base_url": None,
        "model_api_key": None,
        "search_provider": "mock",
        "search_api_key": None,
        "max_cost_usd": PER_QUESTION_COST_CEILING_USD,
        "model_timeout_seconds": 60.0,
    }


def _changed_files_since_start() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{STARTING_COMMIT}..HEAD"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CutoffConsistentValidationError(
            "validation_git_scope_unavailable"
        ) from exc
    if result.returncode != 0:
        raise CutoffConsistentValidationError("validation_git_scope_unavailable")
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


def _allowed_validation_path(path: str) -> bool:
    return path in _ALLOWED_EXACT_PATHS or path.startswith(_ALLOWED_PREFIXES)


def _normal_cutoff_self_check() -> dict[str, Any]:
    early_cutoff = parse_datetime("2024-04-05T00:00:00+00:00")
    eligible_cutoff = parse_datetime("2024-07-01T00:00:00+00:00")
    snapshot_at = parse_datetime("2024-03-01T00:00:00+00:00")
    assert early_cutoff is not None
    assert eligible_cutoff is not None
    assert snapshot_at is not None
    fixture_url = "https://fixtures.forecastlab.local/bls-employment-situation"
    early = fetch_document(
        fixture_url,
        as_of=early_cutoff,
        allow_local_fixtures=True,
        snapshot_url=fixture_url,
        snapshot_at=snapshot_at,
        mode="backtest",
    )
    eligible = fetch_document(
        fixture_url,
        as_of=eligible_cutoff,
        allow_local_fixtures=True,
        snapshot_url=fixture_url,
        snapshot_at=snapshot_at,
        mode="backtest",
    )
    no_snapshot = fetch_document(
        "https://example.com/historical-source",
        as_of=early_cutoff,
        allow_local_fixtures=False,
        mode="backtest",
    )
    passed = bool(
        early.rejected
        and early.rejection_reason == "published_after_as_of"
        and not eligible.rejected
        and eligible.as_of_eligible
        and no_snapshot.rejected
        and no_snapshot.rejection_reason == "no_eligible_historical_snapshot"
    )
    return {
        "passed": passed,
        "pre_cutoff_fixture_reason": early.rejection_reason,
        "eligible_fixture_accepted": not eligible.rejected,
        "missing_snapshot_reason": no_snapshot.rejection_reason,
    }


def validation_already_started(session: Session) -> bool:
    return (
        session.scalar(
            select(Question.id)
            .where(Question.notes.like(f"{VALIDATION_NOTE_PREFIX}%"))
            .limit(1)
        )
        is not None
    )


def freeze_cutoff_consistent_validation(
    session: Session,
    *,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    dataset = import_pilot_benchmark(session)
    session.commit()
    if dataset.hash != PILOT_V1_DATASET_HASH:
        raise CutoffConsistentValidationError("validation_pilot_hash_mismatch")

    source_manifests = exact_question_manifests()
    selected: list[tuple[dict[str, Any], EvaluationQuestion]] = []
    for manifest in source_manifests:
        item = session.get(EvaluationQuestion, manifest["evaluation_question_id"])
        if item is None or item.question_hash != manifest["question_hash"]:
            raise CutoffConsistentValidationError("validation_question_identity_mismatch")
        if item.dataset_id != dataset.id or item.question != manifest["question"]:
            raise CutoffConsistentValidationError("validation_question_content_mismatch")
        selected.append((manifest, item))

    profile = load_profile(VALIDATION_PROFILE_ID)
    if profile.version != VALIDATION_PROFILE_VERSION:
        raise CutoffConsistentValidationError("validation_profile_version_mismatch")
    contexts = [
        resolve_execution_context(
            requested_mode="backtest",
            profile_id=VALIDATION_PROFILE_ID,
            settings=_mock_provider_settings(),
            synthetic_fixture_run=True,
            as_of=as_utc(item.forecast_date),
        )
        for _manifest, item in selected
    ]
    if any(
        not context.model_is_mock
        or not context.search_is_mock
        or not context.synthetic_fixture_run
        or not context.fixture_evidence_allowed
        for context in contexts
    ):
        raise CutoffConsistentValidationError("validation_mock_only_context_required")
    if any(
        abs(context.effective_max_cost_usd - SHADOW_COST_CEILING_USD) > 1e-12
        for context in contexts
    ):
        raise CutoffConsistentValidationError("validation_budget_mismatch")

    corpus = load_cutoff_consistent_manifest()
    corpus_by_question: dict[str, list[dict[str, Any]]] = {}
    for _manifest, item in selected:
        cutoff = as_utc(item.forecast_date)
        documents = [
            document
            for document in corpus.documents
            if document.question_hash == item.question_hash
        ]
        if len(documents) < 3:
            raise CutoffConsistentValidationError(
                f"validation_corpus_document_count:{item.question_hash}"
            )
        if any(
            as_utc(document.published_at) > cutoff
            or as_utc(document.snapshot_at) > cutoff
            for document in documents
        ):
            raise CutoffConsistentValidationError(
                f"validation_corpus_post_cutoff_document:{item.question_hash}"
            )
        corpus_by_question[item.question_hash] = [
            {
                "document_id": document.document_id,
                "published_at": document.published_at.isoformat(),
                "snapshot_at": document.snapshot_at.isoformat(),
                "content_hash": document.content_hash,
            }
            for document in documents
        ]

    bundle = load_prompt_bundle()
    catalog = load_pricing()
    identity = build_environment_identity(
        prompt_bundle_hash=sha256_text(canonical_json(bundle.hashes())),
        profile_hashes={VALIDATION_PROFILE_ID: profile_hash(profile)},
        pricing_catalog=catalog,
    )
    changed_files = _changed_files_since_start()
    unexpected = [path for path in changed_files if not _allowed_validation_path(path)]
    protected = [
        path
        for path in changed_files
        if path in _PROTECTED_BEHAVIOR_PATHS
        or path.startswith(_PROTECTED_BEHAVIOR_PREFIXES)
    ]
    cutoff_paths = [path for path in changed_files if path in _PRODUCTION_CUTOFF_PATHS]
    cutoff_self_check = _normal_cutoff_self_check()
    previous_a = _artifact(PREVIOUS_A_ARTIFACT_PATH, PREVIOUS_VALIDATION_A_ID)
    previous_b = _artifact(PREVIOUS_B_ARTIFACT_PATH, PREVIOUS_VALIDATION_B_ID)
    selection = [
        {**manifest, "evaluation_question_id": item.id}
        for manifest, item in selected
    ]
    frozen: dict[str, Any] = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "validation_name": VALIDATION_NAME,
        "validation_id": str(uuid.uuid4()),
        "created_at": as_utc(created_at or utcnow()).isoformat(),
        "source_validations": {
            "validation_a": {
                "validation_id": PREVIOUS_VALIDATION_A_ID,
                "artifact_path": str(PREVIOUS_A_ARTIFACT_PATH.relative_to(ROOT)),
                "artifact_hash": sha256_text(
                    PREVIOUS_A_ARTIFACT_PATH.read_text(encoding="utf-8")
                ),
                "summary": previous_a["summary"],
            },
            "validation_b": {
                "validation_id": PREVIOUS_VALIDATION_B_ID,
                "artifact_path": str(PREVIOUS_B_ARTIFACT_PATH.relative_to(ROOT)),
                "artifact_hash": sha256_text(
                    PREVIOUS_B_ARTIFACT_PATH.read_text(encoding="utf-8")
                ),
                "summary": previous_b["summary"],
            },
        },
        "dataset": {
            "name": dataset.name,
            "version": dataset.version,
            "hash": dataset.hash,
            "selected_question_count": len(selection),
            "selected_question_hash": sha256_text(canonical_json(selection)),
            "questions": selection,
            "selection_policy": (
                "exact IDs, content, cutoffs, contracts, and order from validation "
                f"{PREVIOUS_VALIDATION_B_ID}"
            ),
        },
        "corpus": {
            "corpus_id": corpus.corpus_id,
            "corpus_version": corpus.corpus_version,
            "corpus_hash": corpus.corpus_hash,
            "manifest_path": str(MANIFEST_PATH.relative_to(ROOT)),
            "document_count": len(corpus.documents),
            "documents_by_question": corpus_by_question,
            "outcome_blind": all(document.outcome_blind for document in corpus.documents),
            "synthetic_workflow_only": all(
                document.synthetic_workflow_only for document in corpus.documents
            ),
        },
        "profile": {
            "id": profile.id,
            "version": profile.version,
            "hash": profile_hash(profile),
            "snapshot": profile.model_dump(mode="json"),
        },
        "provider": {
            "model_provider": "mock",
            "model": "mock-forecast-v1",
            "model_api_key_set": False,
            "search_provider": "mock",
            "search_adapter": "CutoffConsistentMockSearchProvider",
            "search_api_key_set": False,
            "fixture_evidence_allowed": True,
            "synthetic_fixture_execution": True,
            "workflow_validation": True,
            "live_provider_calls_authorized": False,
            "real_historical_benchmark": False,
        },
        "shadow_pricing": {
            "model_provider": SHADOW_MODEL_PROVIDER,
            "model": SHADOW_MODEL,
            "search_provider": SHADOW_SEARCH_PROVIDER,
            "pricing_catalog_hash": pricing_hash(catalog=catalog),
            "per_question_cost_ceiling_usd": SHADOW_COST_CEILING_USD,
            "provider_instances_created": False,
            "api_keys_loaded": False,
        },
        "budget": {
            "shadow_per_question_cost_ceiling_usd": SHADOW_COST_CEILING_USD,
            "actual_mock_cost_ceiling_usd": PER_QUESTION_COST_CEILING_USD,
            "max_model_calls": contexts[0].effective_max_model_calls,
            "max_search_calls": contexts[0].effective_max_search_calls,
            "max_fetched_documents": contexts[0].effective_max_fetched_documents,
            "max_tokens": contexts[0].effective_max_tokens,
            "max_wall_clock_seconds": contexts[0].effective_max_wall_clock_seconds,
        },
        "prompts": {
            "versions": bundle.versions(),
            "hashes": bundle.hashes(),
            "bundle_hash": sha256_text(canonical_json(bundle.hashes())),
        },
        "pricing_hash": pricing_hash(catalog=catalog),
        "contexts": {
            manifest["question_hash"]: {
                "configuration_hash": context.configuration_hash,
                "evidence_cutoff": manifest["evidence_cutoff"],
            }
            for (manifest, _item), context in zip(selected, contexts, strict=True)
        },
        "code": {
            **identity,
            "starting_commit": STARTING_COMMIT,
            "planner_source_commit": PLANNER_SOURCE_COMMIT,
        },
        "validation_scope": {
            "changed_files_since_start": changed_files,
            "unexpected_changes": unexpected,
            "protected_behavior_changes": protected,
            "production_cutoff_path_changes": cutoff_paths,
            "normal_cutoff_self_check": cutoff_self_check,
            "production_cutoff_rules_weakened": bool(
                cutoff_paths or not cutoff_self_check["passed"]
            ),
            "forecasting_behavior_tuned": bool(protected),
            "adapter_boundary_only": not unexpected,
        },
        "execution_policy": {
            "whole_run_attempts_per_question": 1,
            "automatic_whole_run_retry": False,
            "provider_transient_retries": 0,
            "accuracy_metrics": "not_computed",
            "full_pilot_executed": False,
        },
        "required_notice": REQUIRED_NOTICE,
    }
    frozen["configuration_hash"] = configuration_hash(frozen)
    return frozen


def _prepare_run(
    session: Session,
    *,
    item: EvaluationQuestion,
    validation_id: str,
    context: Any,
) -> ForecastRun:
    question = Question(
        id=str(uuid.uuid4()),
        original_text=item.question,
        normalized_text=item.question,
        forecast_deadline=as_utc(item.resolution_date),
        notes=(
            f"{VALIDATION_NOTE_PREFIX}{validation_id}:{item.id}:{item.question_hash}"
        ),
        status="draft",
        requested_mode="backtest",
        requested_profile_id=VALIDATION_PROFILE_ID,
        requested_as_of=as_utc(item.forecast_date),
        is_benchmark=True,
    )
    session.add(question)
    session.flush()
    save_contract(session, question, _resolution_contract(item))
    _ensure_forecast_contract(session, question, item)
    run = create_run_record(
        session,
        question=question,
        context=context,
        as_of=as_utc(item.forecast_date),
        enqueue=False,
    )
    session.commit()
    return run


def _augment_metrics(
    base: dict[str, Any],
    *,
    corpus_audit: dict[str, Any],
    frozen: dict[str, Any],
) -> dict[str, Any]:
    allocation = base["research_plan"]["budget_allocation"]
    shadow = allocation.get("shadow_pricing") or {}
    ledger_rows = base["operations"]["provider_ledger_rows"]
    actual_model_cost = round(
        sum(
            float(row["actual_cost_usd"])
            for row in ledger_rows
            if row["provider_type"] == "model"
        ),
        12,
    )
    actual_search_cost = round(
        sum(
            float(row["actual_cost_usd"])
            for row in ledger_rows
            if row["provider_type"] == "search"
        ),
        12,
    )
    actual_tokens = sum(
        int(row.get("actual_prompt_tokens") or 0)
        + int(row.get("actual_completion_tokens") or 0)
        for row in ledger_rows
        if row["provider_type"] == "model"
    )
    documents_fetched = int(base["research_execution"]["documents_fetched"])
    base["corpus"] = corpus_audit
    base["research_plan"].update(
        {
            "shadow_estimated_model_cost_usd": float(
                shadow.get("estimated_model_cost_usd") or 0.0
            ),
            "shadow_estimated_search_cost_usd": float(
                shadow.get("estimated_search_cost_usd") or 0.0
            ),
            "shadow_estimated_total_cost_usd": float(
                shadow.get("estimated_total_cost_usd") or 0.0
            ),
            "shadow_pricing_identity": shadow,
        }
    )
    base["operations"].update(
        {
            "actual_mock_model_cost_usd": actual_model_cost,
            "actual_mock_search_cost_usd": actual_search_cost,
            "actual_mock_total_cost_usd": round(
                actual_model_cost + actual_search_cost,
                12,
            ),
            "actual_mock_tokens": actual_tokens,
            "retries": int(base["operations"]["whole_run_retries"])
            + int(base["operations"]["physical_provider_retries"]),
        }
    )
    limits = frozen["budget"]
    base["committed_limits_respected"] = bool(
        base["search_and_evidence_limits_respected"]
        and int(base["operations"]["actual_model_calls"])
        <= int(limits["max_model_calls"])
        and int(base["operations"]["actual_search_calls"])
        <= int(limits["max_search_calls"])
        and documents_fetched <= int(limits["max_fetched_documents"])
        and actual_tokens <= int(limits["max_tokens"])
    )
    return base


def _execute_question_once(
    session_factory: Callable[[], Session],
    *,
    frozen: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    run_id: str | None = None
    node_result: GraphNodeForecastResult | None = None
    execution_error: str | None = None
    started = time.perf_counter()
    catalog = load_pricing()
    bundle = load_prompt_bundle()
    profile = load_profile(VALIDATION_PROFILE_ID)
    search: CutoffConsistentMockSearchProvider | None = None

    with session_factory() as session:
        item = session.get(EvaluationQuestion, manifest["evaluation_question_id"])
        if item is None or item.question_hash != manifest["question_hash"]:
            raise CutoffConsistentValidationError("validation_question_missing")
        context = resolve_execution_context(
            requested_mode="backtest",
            profile_id=VALIDATION_PROFILE_ID,
            settings=_mock_provider_settings(),
            synthetic_fixture_run=True,
            as_of=as_utc(item.forecast_date),
        )
        if (
            context.configuration_hash
            != frozen["contexts"][manifest["question_hash"]]["configuration_hash"]
        ):
            raise CutoffConsistentValidationError("validation_context_drift")
        run = _prepare_run(
            session,
            item=item,
            validation_id=str(frozen["validation_id"]),
            context=context,
        )
        run_id = run.id
        run.started_at = utcnow()
        run.status = "running"
        session.commit()

        effective = apply_execution_limits(
            effective_profile(
                profile,
                user_max_cost_usd=context.effective_max_cost_usd,
            ),
            context,
        )
        ledger = PersistentUsageLedger(
            session_factory,
            max_cost_usd=effective.max_estimated_cost_usd,
            max_tokens=effective.max_tokens,
        )
        attempt = ledger.begin_attempt(
            run_id=run.id,
            job_id=None,
            attempt_number=1,
        )
        model = MockModelProvider(
            model=context.model_name,
            ledger=ledger,
            run_id=run.id,
            run_attempt_id=attempt.id,
        )
        search = CutoffConsistentMockSearchProvider(
            corpus_id=CORPUS_ID,
            corpus_version=CORPUS_VERSION,
            question_hash=item.question_hash,
            forecast_cutoff=as_utc(item.forecast_date),
            synthetic_fixture_execution=True,
            workflow_validation=True,
            execution_mode="backtest",
            live_provider_involved=False,
            real_historical_benchmark=False,
            ledger=ledger,
            run_id=run.id,
            run_attempt_id=attempt.id,
        )
        holder: dict[str, GraphNodeForecastResult] = {}

        def audited_node_runner(**kwargs: Any) -> GraphNodeForecastResult:
            kwargs["research_planner"] = ShadowPricedResearchPlanner(
                pricing_catalog=catalog
            )
            result = run_graph_node_forecasts(**kwargs)
            holder["result"] = result
            return result

        try:
            GraphForecastExecutor(
                session,
                run=run,
                profile=effective,
                execution=context,
                model=model,
                search=search,
                allow_local_fixtures=True,
                prompt_bundle=bundle,
                ledger=ledger,
                pricing_catalog=catalog,
                prior_elapsed_seconds=0.0,
                node_runner=audited_node_runner,
            ).execute()
        except Exception as exc:  # retain the one authorized terminal result
            execution_error = f"{exc.__class__.__name__}:{redact_secrets(str(exc))}"
            session.rollback()
            failed_run = session.get(ForecastRun, run.id)
            if failed_run is not None and failed_run.status != "failed":
                failed_run.status = "failed"
                failed_run.error_stage = (
                    failed_run.error_stage or "validation_execution"
                )
                failed_run.error_message = execution_error
                failed_run.finished_at = utcnow()
                session.commit()
            ledger.finish_attempt(
                attempt.id,
                status="failed",
                error_category=exc.__class__.__name__,
                error_message=str(exc),
            )
        else:
            ledger.finish_attempt(attempt.id, status="completed")

        node_result = holder.get("result")
        session.expire_all()
        persisted_run = session.get(ForecastRun, run.id)
        if persisted_run is None:
            raise CutoffConsistentValidationError("validation_run_disappeared")
        apply_totals_to_run(persisted_run, ledger.totals(run.id))
        persisted_run.latency_ms = int((time.perf_counter() - started) * 1000)
        session.commit()

    wall_latency_ms = int((time.perf_counter() - started) * 1000)
    assert run_id is not None
    assert search is not None
    with session_factory() as session:
        base = collect_graph_planner_run_metrics(
            session,
            run_id=run_id,
            manifest=manifest,
            wall_latency_ms=wall_latency_ms,
            execution_error=execution_error,
            node_result=node_result,
            search_calls=search.calls,
            per_question_ceiling=PER_QUESTION_COST_CEILING_USD,
        )
    return _augment_metrics(
        base,
        corpus_audit=search.audit_snapshot(),
        frozen=frozen,
    )


def summarize_cutoff_consistent_validation(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    latencies = [int(row["operations"]["latency_ms"]) for row in rows]
    failures: Counter[str] = Counter()
    for row in rows:
        failures.update(row.get("failure_categories") or {})
    return {
        "assigned_questions": len(rows),
        "completed_questions": sum(row["status"] == "completed" for row in rows),
        "failed_questions": sum(row["status"] != "completed" for row in rows),
        "eligible_corpus_documents": sum(
            len(row["corpus"]["eligible_documents"]) for row in rows
        ),
        "excluded_corpus_documents": sum(
            len(row["corpus"]["excluded_documents"]) for row in rows
        ),
        "all_graph_nodes": sum(
            len(row["research_plan"]["all_graph_nodes"]) for row in rows
        ),
        "selected_nodes": sum(
            len(row["research_plan"]["selected_nodes"]) for row in rows
        ),
        "skipped_nodes": sum(
            len(row["research_plan"]["skipped_nodes"]) for row in rows
        ),
        "evidence_claims_created": sum(
            int(row["research_execution"]["evidence_claims_persisted"])
            for row in rows
        ),
        "node_forecast_runs_created": sum(
            int(row["forecast_execution"]["node_forecast_runs_persisted"])
            for row in rows
        ),
        "forecast_aggregations_created": sum(
            bool(row["forecast_execution"]["forecast_aggregation_persisted"])
            for row in rows
        ),
        "forecast_versions_created": sum(
            bool(row["forecast_execution"]["forecast_version_persisted"])
            for row in rows
        ),
        "cutoff_rejections": sum(
            int(row["research_execution"]["cutoff_rejections"]) for row in rows
        ),
        "critical_node_failures": sum(
            int(row["forecast_execution"]["critical_node_failures"])
            for row in rows
        ),
        "queries_attempted": sum(
            int(row["research_execution"]["queries_attempted"]) for row in rows
        ),
        "documents_fetched": sum(
            int(row["research_execution"]["documents_fetched"]) for row in rows
        ),
        "extraction_fallbacks": sum(
            int(row["research_execution"]["smaller_chunk_retries"])
            + int(row["research_execution"]["document_level_fallbacks"])
            for row in rows
        ),
        "extraction_failures": sum(
            int(row["research_execution"]["extraction_failures"]) for row in rows
        ),
        "mock_model_calls": sum(
            int(row["operations"]["actual_model_calls"]) for row in rows
        ),
        "mock_search_calls": sum(
            int(row["operations"]["actual_search_calls"]) for row in rows
        ),
        "shadow_estimated_model_cost_usd": round(
            sum(
                float(row["research_plan"]["shadow_estimated_model_cost_usd"])
                for row in rows
            ),
            12,
        ),
        "shadow_estimated_search_cost_usd": round(
            sum(
                float(row["research_plan"]["shadow_estimated_search_cost_usd"])
                for row in rows
            ),
            12,
        ),
        "shadow_estimated_total_cost_usd": round(
            sum(
                float(row["research_plan"]["shadow_estimated_total_cost_usd"])
                for row in rows
            ),
            12,
        ),
        "actual_mock_model_cost_usd": round(
            sum(
                float(row["operations"]["actual_mock_model_cost_usd"])
                for row in rows
            ),
            12,
        ),
        "actual_mock_search_cost_usd": round(
            sum(
                float(row["operations"]["actual_mock_search_cost_usd"])
                for row in rows
            ),
            12,
        ),
        "actual_mock_total_cost_usd": round(
            sum(
                float(row["operations"]["actual_mock_total_cost_usd"])
                for row in rows
            ),
            12,
        ),
        "live_provider_calls": sum(
            int(row["operations"]["live_provider_call_count"]) for row in rows
        ),
        "retries": sum(int(row["operations"]["retries"]) for row in rows),
        "total_latency_ms": sum(latencies),
        "mean_latency_ms": (
            round(sum(latencies) / len(latencies), 3) if latencies else None
        ),
        "failure_categories": dict(sorted(failures.items())),
    }


def evaluate_cutoff_consistent_gates(
    rows: list[dict[str, Any]],
    frozen: dict[str, Any],
) -> list[dict[str, Any]]:
    summary = summarize_cutoff_consistent_validation(rows)
    ceiling = float(frozen["shadow_pricing"]["per_question_cost_ceiling_usd"])
    exact_order = tuple(row["evaluation_question_id"] for row in rows)
    run_ids = [row["forecast_run_id"] for row in rows]
    gates = [
        (
            "Five of five runs produce a ForecastVersion",
            summary["forecast_versions_created"] == 5,
            f"{summary['forecast_versions_created']}/5",
        ),
        (
            "Five of five runs produce a ForecastAggregation",
            summary["forecast_aggregations_created"] == 5,
            f"{summary['forecast_aggregations_created']}/5",
        ),
        (
            "Every run produces at least one NodeForecastRun",
            len(rows) == 5
            and all(
                int(row["forecast_execution"]["node_forecast_runs_persisted"]) > 0
                for row in rows
            ),
            ", ".join(
                str(row["forecast_execution"]["node_forecast_runs_persisted"])
                for row in rows
            ),
        ),
        (
            "Every run produces at least one accepted EvidenceClaim",
            len(rows) == 5
            and all(
                int(row["research_execution"]["evidence_claims_persisted"]) > 0
                for row in rows
            ),
            ", ".join(
                str(row["research_execution"]["evidence_claims_persisted"])
                for row in rows
            ),
        ),
        (
            "Zero runs fail due to cutoff_rejection",
            summary["cutoff_rejections"] == 0
            and not any(
                "cutoff_rejection" in (row.get("failure_categories") or {})
                for row in rows
            ),
            str(summary["cutoff_rejections"]),
        ),
        (
            "Zero critical selected nodes fail",
            summary["critical_node_failures"] == 0,
            str(summary["critical_node_failures"]),
        ),
        (
            "Every shadow research plan fits within $0.25",
            len(rows) == 5
            and all(
                float(row["research_plan"]["shadow_estimated_total_cost_usd"])
                <= ceiling + 1e-12
                for row in rows
            ),
            ", ".join(
                f"${float(row['research_plan']['shadow_estimated_total_cost_usd']):.6f}"
                for row in rows
            ),
        ),
        (
            "Every run respects committed model, search, fetch, and token limits",
            len(rows) == 5
            and all(bool(row["committed_limits_respected"]) for row in rows),
            ", ".join(
                "within" if row["committed_limits_respected"] else "exceeded"
                for row in rows
            ),
        ),
        (
            "Actual provider cost remains exactly $0",
            summary["actual_mock_model_cost_usd"] == 0.0
            and summary["actual_mock_search_cost_usd"] == 0.0
            and summary["actual_mock_total_cost_usd"] == 0.0,
            (
                f"model ${summary['actual_mock_model_cost_usd']:.6f}; "
                f"search ${summary['actual_mock_search_cost_usd']:.6f}; "
                f"total ${summary['actual_mock_total_cost_usd']:.6f}"
            ),
        ),
        (
            "No live OpenAI or Tavily request occurs",
            summary["live_provider_calls"] == 0
            and frozen["provider"]["model_provider"] == "mock"
            and frozen["provider"]["search_provider"] == "mock"
            and not frozen["provider"]["live_provider_calls_authorized"],
            f"live ledger rows: {summary['live_provider_calls']}",
        ),
        (
            "The corpus hash is frozen and recorded",
            all(
                row["corpus"]["corpus_hash"]
                == frozen["corpus"]["corpus_hash"]
                for row in rows
            )
            and frozen["corpus"]["corpus_hash"]
            == load_cutoff_consistent_manifest().corpus_hash,
            frozen["corpus"]["corpus_hash"],
        ),
        (
            "No production cutoff rule was weakened",
            not frozen["validation_scope"]["production_cutoff_rules_weakened"],
            json.dumps(
                frozen["validation_scope"]["normal_cutoff_self_check"],
                sort_keys=True,
            ),
        ),
        (
            "No graph, planner, prompt, node-forecasting, or aggregation behavior was tuned",
            not frozen["validation_scope"]["forecasting_behavior_tuned"]
            and frozen["validation_scope"]["adapter_boundary_only"],
            ", ".join(
                frozen["validation_scope"]["protected_behavior_changes"]
            )
            or "none",
        ),
        (
            "The exact five questions were executed once",
            exact_order == EXACT_QUESTION_IDS
            and len(set(run_ids)) == 5
            and all(
                int(row["operations"]["whole_run_attempts"]) == 1
                and int(row["operations"]["whole_run_retries"]) == 0
                for row in rows
            ),
            ", ".join(exact_order),
        ),
    ]
    return [
        {
            "condition": index,
            "description": description,
            "passed": bool(passed),
            "evidence": evidence,
        }
        for index, (description, passed, evidence) in enumerate(gates, start=1)
    ]


def execute_cutoff_consistent_validation(
    session_factory: Callable[[], Session],
    frozen: dict[str, Any],
) -> dict[str, Any]:
    expected_hash = frozen.get("configuration_hash")
    unhashed = dict(frozen)
    unhashed.pop("configuration_hash", None)
    if expected_hash != configuration_hash(unhashed):
        raise CutoffConsistentValidationError(
            "validation_configuration_hash_mismatch"
        )
    if working_tree_dirty():
        raise CutoffConsistentValidationError("validation_requires_clean_worktree")
    if frozen.get("code", {}).get("working_tree_dirty"):
        raise CutoffConsistentValidationError("validation_frozen_dirty_worktree")
    if frozen.get("profile", {}).get("version") != VALIDATION_PROFILE_VERSION:
        raise CutoffConsistentValidationError("validation_profile_version_mismatch")
    if frozen.get("provider", {}).get("model_provider") != "mock":
        raise CutoffConsistentValidationError("validation_live_model_forbidden")
    if frozen.get("provider", {}).get("search_provider") != "mock":
        raise CutoffConsistentValidationError("validation_live_search_forbidden")
    if frozen.get("provider", {}).get("model_api_key_set") or frozen.get(
        "provider", {}
    ).get("search_api_key_set"):
        raise CutoffConsistentValidationError("validation_provider_key_forbidden")
    if frozen["validation_scope"]["production_cutoff_rules_weakened"]:
        raise CutoffConsistentValidationError("validation_cutoff_rule_changed")
    if frozen["validation_scope"]["forecasting_behavior_tuned"]:
        raise CutoffConsistentValidationError("validation_forecasting_behavior_tuned")
    if not frozen["validation_scope"]["adapter_boundary_only"]:
        raise CutoffConsistentValidationError("validation_scope_expanded")

    with session_factory() as session:
        dataset = import_pilot_benchmark(session)
        session.commit()
        if dataset.hash != PILOT_V1_DATASET_HASH:
            raise CutoffConsistentValidationError("validation_pilot_hash_mismatch")
        if validation_already_started(session):
            raise CutoffConsistentValidationError("validation_already_started")

    rows = [
        _execute_question_once(
            session_factory,
            frozen=frozen,
            manifest=manifest,
        )
        for manifest in frozen["dataset"]["questions"]
    ]
    gates = evaluate_cutoff_consistent_gates(rows, frozen)
    return {
        "artifact_schema_version": VALIDATION_SCHEMA_VERSION,
        "validation_id": frozen["validation_id"],
        "validation_name": VALIDATION_NAME,
        "created_at": frozen["created_at"],
        "completed_at": utcnow().isoformat(),
        "freeze": frozen,
        "summary": summarize_cutoff_consistent_validation(rows),
        "rows": rows,
        "gate_results": gates,
        "passed": all(gate["passed"] for gate in gates),
        "required_notice": REQUIRED_NOTICE,
    }


def _fmt_cost(value: float | int | None) -> str:
    return "n/a" if value is None else f"${float(value):.6f}"


def _fmt_ms(value: float | int | None) -> str:
    return "n/a" if value is None else f"{float(value):.1f} ms"


def render_cutoff_consistent_validation_report(artifact: dict[str, Any]) -> str:
    frozen = artifact["freeze"]
    summary = artifact["summary"]
    previous_a = frozen["source_validations"]["validation_a"]["summary"]
    previous_b = frozen["source_validations"]["validation_b"]["summary"]
    lines = [
        "# Cutoff-Consistent Mock Validation Report",
        "",
        f"> {REQUIRED_NOTICE}",
        "",
        "## Result",
        "",
        f"- Overall gate: **{'PASS' if artifact['passed'] else 'FAIL'}**",
        f"- Validation ID: `{artifact['validation_id']}`",
        f"- Code commit frozen before execution: `{frozen['code']['git_commit']}`",
        f"- Graph profile: `{frozen['profile']['id']}` version {frozen['profile']['version']}",
        f"- Corpus: `{frozen['corpus']['corpus_id']}` version {frozen['corpus']['corpus_version']}",
        f"- Corpus hash: `{frozen['corpus']['corpus_hash']}`",
        "- Execution providers: MockModelProvider and cutoff-consistent mock search/fetch adapter",
        f"- Shadow pricing identity: `{SHADOW_MODEL_PROVIDER}` / `{SHADOW_MODEL}` / `{SHADOW_SEARCH_PROVIDER}`",
        "- Live provider calls authorized: no",
        "- Automatic rerun: no",
        "- Accuracy metrics: not computed",
        "",
        "## Exact reused questions and corpus",
        "",
        "| # | Evaluation question ID | Domain | Forecast cutoff | Eligible / excluded docs | Fixture document IDs |",
        "| ---: | --- | --- | --- | ---: | --- |",
    ]
    rows_by_id = {row["evaluation_question_id"]: row for row in artifact["rows"]}
    for index, item in enumerate(frozen["dataset"]["questions"], start=1):
        row = rows_by_id[item["evaluation_question_id"]]
        eligible = row["corpus"]["eligible_documents"]
        excluded = row["corpus"]["excluded_documents"]
        document_ids = ", ".join(
            f"`{document['document_id']}`" for document in eligible
        )
        lines.append(
            f"| {index} | `{item['evaluation_question_id']}` | {item['domain']} | "
            f"{item['evidence_cutoff']} | {len(eligible)} / {len(excluded)} | "
            f"{document_ids} |"
        )
    lines.extend(
        [
            "",
            "## Previous versus current",
            "",
            "| Measure | Validation A | Validation B | Current corrected validation |",
            "| --- | ---: | ---: | ---: |",
            f"| Completion | {previous_a['completed_questions']}/5 | {previous_b['completed_questions']}/5 | {summary['completed_questions']}/5 |",
            f"| Evidence Claims | {previous_a['evidence_claims_created']} | {previous_b['evidence_claims_created']} | {summary['evidence_claims_created']} |",
            f"| NodeForecastRuns | {previous_a['node_forecasts_created']} | {previous_b['node_forecast_runs_created']} | {summary['node_forecast_runs_created']} |",
            f"| ForecastAggregations | {previous_a['final_aggregations_created']} | {previous_b['forecast_aggregations_created']} | {summary['forecast_aggregations_created']} |",
            f"| ForecastVersions | {previous_a['forecast_versions_created']} | {previous_b['forecast_versions_created']} | {summary['forecast_versions_created']} |",
            f"| Cutoff rejections | n/a | {previous_b['cutoff_rejections']} | {summary['cutoff_rejections']} |",
            f"| Critical failures | n/a | {previous_b['critical_node_failures']} | {summary['critical_node_failures']} |",
            f"| Actual/mock total cost | {_fmt_cost(previous_a['total_cost_usd'])} | {_fmt_cost(previous_b['total_actual_cost_usd'])} | {_fmt_cost(summary['actual_mock_total_cost_usd'])} |",
            f"| Mean latency | {_fmt_ms(previous_a['mean_latency_ms'])} | {_fmt_ms(previous_b['mean_latency_ms'])} | {_fmt_ms(summary['mean_latency_ms'])} |",
            "",
            "Validation A stopped with budget exhaustion before claims or node forecasts. Validation B used zero-cost generic mock evidence, completed one run, and recorded 28 cutoff rejections. The current result changes only the frozen validation corpus and validation-only execution boundary.",
            "",
            "## Current totals",
            "",
            f"- Eligible / excluded corpus documents: {summary['eligible_corpus_documents']} / {summary['excluded_corpus_documents']}",
            f"- Graph nodes selected / skipped: {summary['selected_nodes']} / {summary['skipped_nodes']}",
            f"- Evidence Claims: {summary['evidence_claims_created']}",
            f"- NodeForecastRuns: {summary['node_forecast_runs_created']}",
            f"- ForecastAggregations: {summary['forecast_aggregations_created']}",
            f"- ForecastVersions: {summary['forecast_versions_created']}",
            f"- Cutoff rejections: {summary['cutoff_rejections']}",
            f"- Critical failures: {summary['critical_node_failures']}",
            f"- Queries / fetched documents: {summary['queries_attempted']} / {summary['documents_fetched']}",
            f"- Extraction fallbacks / failures: {summary['extraction_fallbacks']} / {summary['extraction_failures']}",
            f"- Mock model / search calls: {summary['mock_model_calls']} / {summary['mock_search_calls']}",
            f"- Shadow estimated model / search / total cost: {_fmt_cost(summary['shadow_estimated_model_cost_usd'])} / {_fmt_cost(summary['shadow_estimated_search_cost_usd'])} / {_fmt_cost(summary['shadow_estimated_total_cost_usd'])}",
            f"- Actual mock model / search / total cost: {_fmt_cost(summary['actual_mock_model_cost_usd'])} / {_fmt_cost(summary['actual_mock_search_cost_usd'])} / {_fmt_cost(summary['actual_mock_total_cost_usd'])}",
            f"- Total / mean latency: {_fmt_ms(summary['total_latency_ms'])} / {_fmt_ms(summary['mean_latency_ms'])}",
            f"- Retries: {summary['retries']}",
            "",
            "## Per-question execution",
            "",
            "| # | Domain | Status | Selected / skipped | Shadow model | Shadow search | Shadow total | Actual mock total | Claims | Node runs | Aggregation | Version | Cutoff rejects | Critical failures | Latency |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: |",
        ]
    )
    for index, row in enumerate(artifact["rows"], start=1):
        plan = row["research_plan"]
        research = row["research_execution"]
        forecast = row["forecast_execution"]
        operations = row["operations"]
        lines.append(
            f"| {index} | {row['domain']} | {row['status']} | "
            f"{len(plan['selected_nodes'])} / {len(plan['skipped_nodes'])} | "
            f"{_fmt_cost(plan['shadow_estimated_model_cost_usd'])} | "
            f"{_fmt_cost(plan['shadow_estimated_search_cost_usd'])} | "
            f"{_fmt_cost(plan['shadow_estimated_total_cost_usd'])} | "
            f"{_fmt_cost(operations['actual_mock_total_cost_usd'])} | "
            f"{research['evidence_claims_persisted']} | "
            f"{forecast['node_forecast_runs_persisted']} | "
            f"{'yes' if forecast['forecast_aggregation_persisted'] else 'no'} | "
            f"{'yes' if forecast['forecast_version_persisted'] else 'no'} | "
            f"{research['cutoff_rejections']} | {forecast['critical_node_failures']} | "
            f"{_fmt_ms(operations['latency_ms'])} |"
        )

    lines.extend(["", "## Detailed node and corpus audit", ""])
    for index, row in enumerate(artifact["rows"], start=1):
        plan = row["research_plan"]
        research = row["research_execution"]
        forecast = row["forecast_execution"]
        operations = row["operations"]
        node_audit = {item["node_id"]: item for item in research["nodes"]}
        lines.extend(
            [
                f"### {index}. {row['question']}",
                "",
                f"- Evaluation question ID: `{row['evaluation_question_id']}`",
                f"- Forecast run ID: `{row['forecast_run_id']}`",
                f"- Eligible / excluded corpus documents: {len(row['corpus']['eligible_documents'])} / {len(row['corpus']['excluded_documents'])}",
                f"- Selected / skipped nodes: {len(plan['selected_nodes'])} / {len(plan['skipped_nodes'])}",
                f"- Worker concurrency: {plan['selected_worker_concurrency']}",
                f"- Shadow estimated model / search / total: {_fmt_cost(plan['shadow_estimated_model_cost_usd'])} / {_fmt_cost(plan['shadow_estimated_search_cost_usd'])} / {_fmt_cost(plan['shadow_estimated_total_cost_usd'])}",
                f"- Actual mock model / search / total: {_fmt_cost(operations['actual_mock_model_cost_usd'])} / {_fmt_cost(operations['actual_mock_search_cost_usd'])} / {_fmt_cost(operations['actual_mock_total_cost_usd'])}",
                f"- Claims / node runs: {research['evidence_claims_persisted']} / {forecast['node_forecast_runs_persisted']}",
                f"- Final probability: {forecast['final_probability'] if forecast['final_probability'] is not None else 'not produced'}",
                f"- Reduced confidence: {'yes' if forecast['reduced_confidence'] else 'no'}",
                f"- Retries: {operations['retries']}",
                "",
                "| Node | Type | Critical | Selected | Priority | Searches | Claims | Forecast | Failure |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
            ]
        )
        for node in plan["all_graph_nodes"]:
            node_id = str(node["id"])
            audit = node_audit.get(node_id, {})
            lines.append(
                f"| {node['question']} | {node['node_type']} | "
                f"{'yes' if node['critical'] else 'no'} | "
                f"{'yes' if node_id in plan['selected_nodes'] else 'no'} | "
                f"{float(plan['priority_scores'].get(node_id, 0)):.6f} | "
                f"{plan['allocated_search_count_by_node'].get(node_id, 0)} | "
                f"{audit.get('claims_created', 0)} | "
                f"{'yes' if audit.get('node_forecast_created') else 'no'} | "
                f"{audit.get('failure_code') or 'none'} |"
            )
        lines.extend(["", "Corpus query rankings:", ""])
        for query in row["corpus"]["query_rankings"]:
            ranked = ", ".join(
                f"{item['document_id']}={float(item['score']):.3f}"
                for item in query["ranked_documents"]
            )
            lines.append(f"- Query: {query['query']}")
            lines.append(f"  - Ranking: {ranked}")
        lines.append("")

    lines.extend(
        [
            "## Fourteen-condition pass/fail gate",
            "",
            "| # | Condition | Result | Evidence |",
            "| ---: | --- | --- | --- |",
        ]
    )
    for gate in artifact["gate_results"]:
        lines.append(
            f"| {gate['condition']} | {gate['description']} | "
            f"{'PASS' if gate['passed'] else 'FAIL'} | {gate['evidence']} |"
        )
    lines.extend(
        [
            "",
            "## Integrity confirmations",
            "",
            "- Production historical cutoff rules changed: no.",
            "- Wayback verification rules changed: no.",
            "- Forecast Contracts, questions, outcomes, graph generation, weights, planner ranking, limits, prompts, extractor, node forecaster, aggregation, profiles, benchmark methodology, scoring, or calibration changed: no.",
            "- Live OpenAI or Tavily calls: none.",
            "- API keys loaded by the validation: none.",
            "- Full pilot benchmark executed: no.",
            "- Accuracy evaluated: no.",
            "- Automatic rerun: no.",
            "",
            "## Limitations",
            "",
            "- The corpus is synthetic and outcome-blind; it validates workflow mechanics, not retrieval quality or forecast accuracy.",
            "- Shadow pricing estimates workload selection against a frozen target identity, but execution uses zero-cost mocks.",
            "- Five questions cover a narrow execution surface and cannot support comparative forecasting claims.",
            "- Query relevance uses deterministic lexical matching and is not a production retrieval system.",
            "",
            REQUIRED_NOTICE,
            "",
        ]
    )
    return "\n".join(lines)


def write_cutoff_consistent_validation_artifacts(
    artifact: dict[str, Any],
    *,
    artifact_path: Path = DEFAULT_ARTIFACT_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
) -> None:
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(
        render_cutoff_consistent_validation_report(artifact),
        encoding="utf-8",
    )
