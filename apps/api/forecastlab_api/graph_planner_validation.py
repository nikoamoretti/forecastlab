from __future__ import annotations

import json
import subprocess
import threading
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
from forecastlab.graph_execution import GraphNodeForecastResult, run_graph_node_forecasts
from forecastlab.hashing import canonical_json, redact_secrets, sha256_text
from forecastlab.pricing import load_pricing, pricing_hash
from forecastlab.profiles import effective_profile, load_profile, profile_hash
from forecastlab.prompts import load_prompt_bundle
from forecastlab.providers.mock import MockModelProvider, MockSearchProvider
from forecastlab.schemas import SearchHit
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.config import ROOT
from forecastlab_api.forecast_experiments import (
    _ensure_forecast_contract,
    _resolution_contract,
)
from forecastlab_api.graph_executor import (
    CRITICAL_IMPORTANCE_THRESHOLD,
    GraphForecastExecutor,
)
from forecastlab_api.models import (
    EvaluationQuestion,
    EvidenceClaimRow,
    EvidenceItem,
    ForecastAggregationRow,
    ForecastContractRow,
    ForecastGraphRow,
    ForecastNodeRunRow,
    ForecastRun,
    ForecastRunAttempt,
    ForecastVersion,
    GraphExecutionFailureRow,
    ProviderCallLedger,
    Question,
    ResearchPlanRow,
)
from forecastlab_api.persist import save_contract
from forecastlab_api.pilot_benchmark import import_pilot_benchmark
from forecastlab_api.pipeline import apply_execution_limits, create_run_record
from forecastlab_api.usage_ledger import PersistentUsageLedger, apply_totals_to_run

PLANNER_SOURCE_COMMIT = "83f1ff8fbcebbf90ed631e0823ece154bd651df2"
PREVIOUS_VALIDATION_ID = "4a5c23e0-0cf6-463b-a608-a1ce65c88e63"
PILOT_V1_DATASET_HASH = "c9cd748c1114eff5d16321ecf7d78b18be4a0b65b49ff7719aa5276e302f7888"
VALIDATION_PROFILE_ID = "graph_forecaster_v1"
VALIDATION_PROFILE_VERSION = 5
VALIDATION_SCHEMA_VERSION = 1
VALIDATION_NAME = "Graph Research Planner Validation"
VALIDATION_NOTE_PREFIX = "graph-research-planner-validation:"
PER_QUESTION_COST_CEILING_USD = 0.25

PREVIOUS_ARTIFACT_PATH = ROOT / "artifacts" / "graph_execution_validation" / "validation_results.json"
DEFAULT_ARTIFACT_PATH = ROOT / "artifacts" / "graph_research_planner_validation" / "validation_results.json"
DEFAULT_REPORT_PATH = ROOT / "docs" / "GRAPH_RESEARCH_PLANNER_VALIDATION_REPORT.md"

PREVIOUS_QUESTION_IDS_BY_HASH = {
    "5a67ed2ad06d47cc4512030b6cc156b5165bb73dc01b8921fc18ddcc4d646436": ("94f806bf-fc0b-41da-84bd-f9ce9792b8e2"),
    "ac9fec2d3a84cecf18c74c24ce6e541369607a0dddc6084b3fb8161cfaa02ee9": ("67ca0f85-ee8f-4371-b5b3-0b9cc2f9bd56"),
    "055be860e87d48b5535aa1bc699423329ec964a240ec17b604cf8b46dc43b134": ("dfda5684-99a3-492b-89d3-316be4bedc23"),
    "7fed57c605c9c0844062871cf1976d1925bf910034fc2f899e7ef0c32ef00475": ("e93a736f-df6d-4fbf-a833-8afa2c557aa6"),
    "f90f4cff669931ce53694ce5e521444ae1fab760cb05c38feb83e0e016d6db17": ("292e0bf3-3b58-43bb-b877-61412481e2d1"),
}

VALIDATION_ONLY_PATHS = frozenset(
    {
        "apps/api/forecastlab_api/graph_planner_validation.py",
        "scripts/run_graph_research_planner_validation.py",
        "tests/test_graph_research_planner_validation.py",
    }
)

_CUTOFF_REASONS = {
    "claim_after_cutoff",
    "final_snapshot_after_as_of",
    "no_eligible_historical_snapshot",
    "published_after_as_of",
    "snapshot_after_as_of",
    "unverifiable_as_of",
}


class GraphPlannerValidationError(RuntimeError):
    """The planner validation failed a reproducibility or safety preflight."""


class RecordingMockSearchProvider(MockSearchProvider):
    """The normal mock search provider plus a validation-only request audit."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def calls(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._calls]

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        hits = super().search(query, max_results=max_results)
        with self._lock:
            self._calls.append(
                {
                    "query": query,
                    "max_results": max_results,
                    "urls": [hit.url for hit in hits],
                }
            )
        return hits


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


def _previous_artifact() -> dict[str, Any]:
    if not PREVIOUS_ARTIFACT_PATH.exists():
        raise GraphPlannerValidationError("previous_validation_artifact_missing")
    artifact = json.loads(PREVIOUS_ARTIFACT_PATH.read_text(encoding="utf-8"))
    if artifact.get("validation_id") != PREVIOUS_VALIDATION_ID:
        raise GraphPlannerValidationError("previous_validation_id_mismatch")
    if artifact.get("freeze", {}).get("dataset", {}).get("hash") != PILOT_V1_DATASET_HASH:
        raise GraphPlannerValidationError("previous_validation_dataset_hash_mismatch")
    rows = artifact.get("rows") or []
    if len(rows) != 5:
        raise GraphPlannerValidationError("previous_validation_question_count_mismatch")
    return artifact


def previous_question_manifests() -> list[dict[str, Any]]:
    """Return the prior validation selection in its original order."""

    artifact = _previous_artifact()
    manifests = artifact["freeze"]["dataset"]["questions"]
    row_hashes = [str(row["question_hash"]) for row in artifact["rows"]]
    manifest_hashes = [str(item["question_hash"]) for item in manifests]
    if manifest_hashes != row_hashes:
        raise GraphPlannerValidationError("previous_validation_question_order_mismatch")
    if set(manifest_hashes) != set(PREVIOUS_QUESTION_IDS_BY_HASH):
        raise GraphPlannerValidationError("previous_validation_question_identity_mismatch")
    return [dict(item) for item in manifests]


def _changed_files_since_planner() -> list[str]:
    try:
        result = subprocess.run(
            [
                "git",
                "diff",
                "--name-only",
                f"{PLANNER_SOURCE_COMMIT}..HEAD",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GraphPlannerValidationError("validation_git_scope_unavailable") from exc
    if result.returncode != 0:
        raise GraphPlannerValidationError("validation_git_scope_unavailable")
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


def validation_already_started(session: Session) -> bool:
    return (
        session.scalar(select(Question.id).where(Question.notes.like(f"{VALIDATION_NOTE_PREFIX}%")).limit(1))
        is not None
    )


def freeze_graph_planner_validation(
    session: Session,
    *,
    expected_question_ids: dict[str, str] | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Freeze the exact previous selection with mock-only execution settings."""

    dataset = import_pilot_benchmark(session)
    session.commit()
    if dataset.hash != PILOT_V1_DATASET_HASH:
        raise GraphPlannerValidationError("planner_validation_pilot_hash_mismatch")

    expected_ids = expected_question_ids or PREVIOUS_QUESTION_IDS_BY_HASH
    manifests = previous_question_manifests()
    selected: list[tuple[dict[str, Any], EvaluationQuestion]] = []
    for manifest in manifests:
        question_hash = str(manifest["question_hash"])
        expected_id = expected_ids.get(question_hash)
        if not expected_id:
            raise GraphPlannerValidationError("planner_validation_question_id_missing")
        item = session.get(EvaluationQuestion, expected_id)
        if item is None or item.question_hash != question_hash:
            raise GraphPlannerValidationError("planner_validation_question_id_mismatch")
        if item.dataset_id != dataset.id or item.question != manifest["question"]:
            raise GraphPlannerValidationError("planner_validation_question_content_mismatch")
        selected.append((manifest, item))

    profile = load_profile(VALIDATION_PROFILE_ID)
    if profile.version != VALIDATION_PROFILE_VERSION:
        raise GraphPlannerValidationError("planner_validation_profile_version_mismatch")
    provider_settings = _mock_provider_settings()
    contexts = [
        resolve_execution_context(
            requested_mode="backtest",
            profile_id=VALIDATION_PROFILE_ID,
            settings=provider_settings,
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
        raise GraphPlannerValidationError("planner_validation_mock_only_context_required")
    if any(context.effective_max_cost_usd != PER_QUESTION_COST_CEILING_USD for context in contexts):
        raise GraphPlannerValidationError("planner_validation_budget_mismatch")

    bundle = load_prompt_bundle()
    catalog = load_pricing()
    identity = build_environment_identity(
        prompt_bundle_hash=sha256_text(canonical_json(bundle.hashes())),
        profile_hashes={VALIDATION_PROFILE_ID: profile_hash(profile)},
        pricing_catalog=catalog,
    )
    changed_files = _changed_files_since_planner()
    unexpected_changes = sorted(set(changed_files) - VALIDATION_ONLY_PATHS)
    selection = [
        {
            **manifest,
            "evaluation_question_id": item.id,
        }
        for manifest, item in selected
    ]
    frozen: dict[str, Any] = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "validation_name": VALIDATION_NAME,
        "validation_id": str(uuid.uuid4()),
        "created_at": as_utc(created_at or utcnow()).isoformat(),
        "source_validation": {
            "validation_id": PREVIOUS_VALIDATION_ID,
            "artifact_path": str(PREVIOUS_ARTIFACT_PATH.relative_to(ROOT)),
            "artifact_hash": sha256_text(PREVIOUS_ARTIFACT_PATH.read_text(encoding="utf-8")),
        },
        "dataset": {
            "name": dataset.name,
            "version": dataset.version,
            "hash": dataset.hash,
            "selected_question_count": len(selection),
            "selected_question_hash": sha256_text(canonical_json(selection)),
            "questions": selection,
            "selection_policy": "exact IDs and order from the previous five-question validation",
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
            "search_api_key_set": False,
            "evidence_policy": contexts[0].evidence_policy,
            "fixture_evidence_allowed": True,
            "live_provider_calls_authorized": False,
        },
        "budget": {
            "hard_per_question_cost_ceiling_usd": PER_QUESTION_COST_CEILING_USD,
            "hard_validation_cost_ceiling_usd": round(PER_QUESTION_COST_CEILING_USD * len(selection), 12),
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
            "planner_source_commit": PLANNER_SOURCE_COMMIT,
        },
        "validation_scope": {
            "changed_files_since_planner_source": changed_files,
            "allowed_validation_only_paths": sorted(VALIDATION_ONLY_PATHS),
            "unexpected_nonvalidation_changes": unexpected_changes,
            "core_behavior_modified": bool(unexpected_changes),
        },
        "execution_policy": {
            "whole_run_attempts_per_question": 1,
            "automatic_whole_run_retry": False,
            "provider_transient_retries": 0,
            "accuracy_metrics": "not_computed",
            "full_pilot_executed": False,
        },
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
        notes=(f"{VALIDATION_NOTE_PREFIX}{validation_id}:{item.id}:{item.question_hash}"),
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


def _graph_for_run(session: Session, run: ForecastRun) -> ForecastGraphRow | None:
    try:
        context = json.loads(run.execution_context_json or "{}")
    except json.JSONDecodeError:
        context = {}
    graph_id = context.get("forecast_graph_id")
    if graph_id:
        graph = session.get(ForecastGraphRow, graph_id)
        if graph is not None:
            return graph
    contract = session.scalar(
        select(ForecastContractRow)
        .where(ForecastContractRow.question_id == run.question_id)
        .order_by(ForecastContractRow.version.desc())
        .limit(1)
    )
    if contract is None:
        return None
    return session.scalar(
        select(ForecastGraphRow)
        .where(ForecastGraphRow.contract_id == contract.id)
        .order_by(ForecastGraphRow.version.desc())
        .limit(1)
    )


def _provider_ledger_payload(rows: list[ProviderCallLedger]) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "logical_call_id": row.logical_call_id,
            "physical_attempt_number": row.physical_attempt_number,
            "stage": row.stage,
            "provider_type": row.provider_type,
            "provider": row.provider,
            "model": row.model,
            "status": row.status,
            "actual_prompt_tokens": row.actual_prompt_tokens,
            "actual_completion_tokens": row.actual_completion_tokens,
            "actual_cost_usd": float(row.actual_cost_usd if row.actual_cost_usd is not None else row.reserved_cost_usd),
            "cost_source": row.cost_source,
        }
        for row in rows
    ]


def _is_cutoff_rejection(record: dict[str, Any]) -> bool:
    reason = str(record.get("rejection_reason") or record.get("reason") or "")
    return bool(
        record.get("outcome") == "cutoff_rejection"
        or reason in _CUTOFF_REASONS
        or reason.startswith("final_snapshot_after_as_of")
    )


def _node_execution_audit(
    result: GraphNodeForecastResult | None,
    search_calls: list[dict[str, Any]],
    ledger_rows: list[ProviderCallLedger],
) -> list[dict[str, Any]]:
    if result is None:
        return []
    calls_by_query: dict[str, list[str]] = {}
    for call in search_calls:
        calls_by_query.setdefault(str(call["query"]), []).extend(str(url) for url in call["urls"])
    output: list[dict[str, Any]] = []
    for execution in result.nodes:
        queries = list(execution.queries_attempted)
        urls = list(dict.fromkeys(url for query in queries for url in calls_by_query.get(query, [])))
        fetched_records = [
            *execution.evidence,
            *[item for item in execution.rejected if item.get("rejection_reason") != "no_eligible_historical_snapshot"],
        ]
        sources = list(execution.sources_checked)
        extraction_rows = [row for row in ledger_rows if row.stage == f"extract_claims:{execution.node.id}"]
        cutoff_rejections = sum(_is_cutoff_rejection(item) for item in sources)
        if not sources:
            cutoff_rejections = sum(_is_cutoff_rejection(item) for item in execution.rejected)
        output.append(
            {
                "node_id": execution.node.id,
                "question": execution.node.question,
                "node_type": execution.node.node_type,
                "importance_weight": execution.node.importance_weight,
                "dependencies": execution.node.dependencies,
                "critical": (execution.node.importance_weight >= CRITICAL_IMPORTANCE_THRESHOLD),
                "research_selected": execution.research_selected,
                "skip_reason": execution.skip_reason,
                "queries_attempted": queries,
                "urls_discovered": urls,
                "sources_checked": sources,
                "documents_fetched": len(fetched_records),
                "cutoff_rejections": cutoff_rejections,
                "extraction_attempts": len(extraction_rows),
                "smaller_chunk_retries": sum(item.get("outcome") == "claims_created_smaller_chunk" for item in sources),
                "document_level_fallbacks": sum(
                    item.get("outcome") == "claims_created_document_fallback" for item in sources
                ),
                "extraction_failures": sum(item.get("outcome") == "extraction_failure" for item in sources),
                "evidence_records": len(execution.evidence),
                "rejected_records": len(execution.rejected),
                "claims_created": len(execution.claims),
                "node_forecast_created": execution.node_run is not None,
                "failure_code": execution.error,
                "failure_stage": execution.error_stage,
                "failure_detail": (redact_secrets(execution.error_detail) if execution.error_detail else None),
            }
        )
    return output


def collect_graph_planner_run_metrics(
    session: Session,
    *,
    run_id: str,
    manifest: dict[str, Any],
    wall_latency_ms: int,
    execution_error: str | None,
    node_result: GraphNodeForecastResult | None,
    search_calls: list[dict[str, Any]],
    per_question_ceiling: float,
) -> dict[str, Any]:
    run = session.get(ForecastRun, run_id)
    if run is None:
        raise GraphPlannerValidationError("planner_validation_run_missing")
    graph = _graph_for_run(session, run)
    plan_row = session.scalar(select(ResearchPlanRow).where(ResearchPlanRow.forecast_run_id == run.id))
    node_runs = session.scalars(select(ForecastNodeRunRow).where(ForecastNodeRunRow.forecast_run_id == run.id)).all()
    claims = session.scalars(
        select(EvidenceClaimRow)
        .join(EvidenceItem, EvidenceClaimRow.evidence_item_id == EvidenceItem.id)
        .where(EvidenceItem.run_id == run.id)
    ).all()
    failures = session.scalars(
        select(GraphExecutionFailureRow).where(GraphExecutionFailureRow.forecast_run_id == run.id)
    ).all()
    aggregation = session.scalar(select(ForecastAggregationRow).where(ForecastAggregationRow.forecast_run_id == run.id))
    version = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run.id))
    attempts = session.scalars(select(ForecastRunAttempt).where(ForecastRunAttempt.run_id == run.id)).all()
    ledger_rows = session.scalars(
        select(ProviderCallLedger)
        .where(ProviderCallLedger.run_id == run.id)
        .order_by(
            ProviderCallLedger.request_started_at,
            ProviderCallLedger.id,
        )
    ).all()

    selected_nodes = json.loads(plan_row.selected_nodes_json) if plan_row else []
    skipped_nodes = json.loads(plan_row.skipped_nodes_json) if plan_row else []
    priority_scores = json.loads(plan_row.priority_scores_json) if plan_row else {}
    allocation = json.loads(plan_row.budget_allocation_json) if plan_row else {}
    score_total = sum(float(value) for value in priority_scores.values())
    normalized_scores = (
        {node_id: round(float(score) / score_total, 12) for node_id, score in priority_scores.items()}
        if score_total > 0
        else {}
    )
    per_node_allocations = allocation.get("per_node") or {}
    allocated_searches_by_node = {
        node_id: int(values.get("searches") or 0) for node_id, values in per_node_allocations.items()
    }
    node_audit = _node_execution_audit(node_result, search_calls, ledger_rows)
    node_audit_by_id = {item["node_id"]: item for item in node_audit}
    graph_nodes = [
        {
            "id": node.id,
            "question": node.question,
            "node_type": node.node_type,
            "importance_weight": node.importance_weight,
            "dependencies": list(node.dependencies),
            "critical": node.importance_weight >= CRITICAL_IMPORTANCE_THRESHOLD,
        }
        for node in (node_result.graph.nodes if node_result is not None else [])
    ]
    if not graph_nodes and graph is not None:
        graph_nodes = [
            {
                "id": node.id,
                "question": node.question,
                "node_type": node.node_type,
                "importance_weight": node.importance_weight,
                "dependencies": json.loads(node.dependencies_json or "[]"),
                "critical": node.importance_weight >= CRITICAL_IMPORTANCE_THRESHOLD,
            }
            for node in graph.nodes
        ]
    critical_ids = {str(node["id"]) for node in graph_nodes if bool(node["critical"])}
    failed_node_ids = {str(failure.node_id) for failure in failures if failure.node_id is not None}
    if node_result is not None:
        failed_node_ids.update(
            execution.node.id
            for execution in node_result.nodes
            if execution.research_selected and execution.node_run is None
        )
    critical_failure_ids = sorted(failed_node_ids & critical_ids)
    successful_node_ids = {row.node_id for row in node_runs}
    selected_failed_ids = sorted(set(selected_nodes) - successful_node_ids)
    noncritical_exclusions = sorted(set(selected_failed_ids) - critical_ids)
    failure_categories = Counter(failure.error_code for failure in failures)
    if execution_error and not failure_categories:
        failure_categories["operational_failure"] += 1
    actual_cost = float(run.total_cost_usd or run.cost_usd or 0.0)
    model_calls = sum(row.provider_type == "model" for row in ledger_rows)
    search_request_count = sum(row.provider_type == "search" for row in ledger_rows)
    live_provider_rows = [row for row in ledger_rows if row.provider != "mock"]
    budget_exceeded_events = int(failure_categories.get("budget_exceeded", 0))
    if execution_error and "BudgetExceeded" in execution_error:
        budget_exceeded_events += 1
    selected_audits = [node_audit_by_id[node_id] for node_id in selected_nodes if node_id in node_audit_by_id]
    queries_within_allocation = all(
        len(item["queries_attempted"]) <= allocated_searches_by_node.get(str(item["node_id"]), 0)
        for item in selected_audits
    )
    plan_limits = allocation.get("limits") or {}
    max_claims_by_node = {
        node_id: int(values.get("max_evidence_claims") or 0) for node_id, values in per_node_allocations.items()
    }
    context_snapshot = json.loads(run.execution_context_json or "{}")
    limits_respected = bool(
        len(selected_nodes) <= int(plan_limits.get("maximum_researched_nodes") or 0)
        and search_request_count <= sum(allocated_searches_by_node.values())
        and search_request_count <= int(context_snapshot.get("effective_max_search_calls", 0))
        and sum(int(item["documents_fetched"]) for item in selected_audits)
        <= int(context_snapshot.get("effective_max_fetched_documents", 0))
        and len(claims) <= int(plan_limits.get("maximum_evidence_claims_total") or 0)
        and all(
            int(item["claims_created"]) <= max_claims_by_node.get(str(item["node_id"]), 0) for item in selected_audits
        )
        and queries_within_allocation
    )
    completed = bool(run.status == "completed" and version is not None and aggregation is not None)
    reliability = context_snapshot.get("graph_research_reliability") or {}
    return {
        "evaluation_question_id": manifest["evaluation_question_id"],
        "question_hash": manifest["question_hash"],
        "question": manifest["question"],
        "domain": manifest["domain"],
        "category": manifest.get("category"),
        "forecast_date": manifest["forecast_date"],
        "evidence_cutoff": manifest["evidence_cutoff"],
        "resolution_date": manifest["resolution_date"],
        "forecast_run_id": run.id,
        "status": "completed" if completed else "failed",
        "research_plan": {
            "id": plan_row.id if plan_row else None,
            "all_graph_nodes": graph_nodes,
            "selected_nodes": selected_nodes,
            "skipped_nodes": skipped_nodes,
            "critical_nodes": sorted(critical_ids),
            "priority_scores": priority_scores,
            "normalized_priority_scores": normalized_scores,
            "allocated_search_count": sum(allocated_searches_by_node.values()),
            "allocated_search_count_by_node": allocated_searches_by_node,
            "estimated_pre_execution_cost_usd": float(allocation.get("estimated_total_cost_usd") or 0.0),
            "selected_worker_concurrency": int(allocation.get("parallel_research_workers") or 0),
            "budget_allocation": allocation,
        },
        "research_execution": {
            "nodes": node_audit,
            "queries_attempted": sum(len(item["queries_attempted"]) for item in node_audit),
            "queries_attempted_by_node": {str(item["node_id"]): item["queries_attempted"] for item in node_audit},
            "urls_discovered": len({url for item in node_audit for url in item["urls_discovered"]}),
            "urls_discovered_by_node": {str(item["node_id"]): item["urls_discovered"] for item in node_audit},
            "documents_fetched": sum(int(item["documents_fetched"]) for item in node_audit),
            "cutoff_rejections": sum(int(item["cutoff_rejections"]) for item in node_audit),
            "extraction_attempts": sum(int(item["extraction_attempts"]) for item in node_audit),
            "smaller_chunk_retries": sum(int(item["smaller_chunk_retries"]) for item in node_audit),
            "document_level_fallbacks": sum(int(item["document_level_fallbacks"]) for item in node_audit),
            "extraction_failures": sum(int(item["extraction_failures"]) for item in node_audit),
            "evidence_claims_persisted": len(claims),
        },
        "forecast_execution": {
            "selected_nodes_successfully_researched": len(set(selected_nodes) & successful_node_ids),
            "selected_nodes_failed": len(selected_failed_ids),
            "selected_node_failure_ids": selected_failed_ids,
            "critical_node_failures": len(critical_failure_ids),
            "critical_node_failure_ids": critical_failure_ids,
            "noncritical_node_exclusions": len(noncritical_exclusions),
            "noncritical_node_exclusion_ids": noncritical_exclusions,
            "node_forecast_runs_persisted": len(node_runs),
            "forecast_aggregation_persisted": aggregation is not None,
            "forecast_version_persisted": version is not None,
            "final_probability": (
                float(version.ensemble_probability)
                if version is not None and version.ensemble_probability is not None
                else None
            ),
            "reduced_confidence": reliability.get("impact") == "reduced_confidence",
            "reliability": reliability,
        },
        "operations": {
            "actual_model_calls": model_calls,
            "actual_search_calls": search_request_count,
            "provider_ledger_row_count": len(ledger_rows),
            "provider_ledger_rows": _provider_ledger_payload(ledger_rows),
            "live_provider_call_count": len(live_provider_rows),
            "actual_cost_usd": actual_cost,
            "budget_remaining_usd": round(max(0.0, per_question_ceiling - actual_cost), 12),
            "budget_exceeded_events": budget_exceeded_events,
            "latency_ms": wall_latency_ms,
            "whole_run_attempts": len(attempts),
            "whole_run_retries": max(0, len(attempts) - 1),
            "physical_provider_retries": sum(row.physical_attempt_number > 1 for row in ledger_rows),
            "failed_provider_attempts": sum(row.status == "failed" for row in ledger_rows),
            "provider_names": sorted({row.provider for row in ledger_rows}),
        },
        "failure_categories": dict(sorted(failure_categories.items())),
        "search_and_evidence_limits_respected": limits_respected,
        "execution_error": redact_secrets(execution_error) if execution_error else None,
    }


def summarize_graph_planner_validation(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    latencies = [int(row["operations"]["latency_ms"]) for row in rows]
    categories: Counter[str] = Counter()
    for row in rows:
        categories.update(row.get("failure_categories") or {})
    return {
        "assigned_questions": len(rows),
        "completed_questions": sum(row["status"] == "completed" for row in rows),
        "failed_questions": sum(row["status"] != "completed" for row in rows),
        "forecast_versions_created": sum(bool(row["forecast_execution"]["forecast_version_persisted"]) for row in rows),
        "forecast_aggregations_created": sum(
            bool(row["forecast_execution"]["forecast_aggregation_persisted"]) for row in rows
        ),
        "node_forecast_runs_created": sum(
            int(row["forecast_execution"]["node_forecast_runs_persisted"]) for row in rows
        ),
        "evidence_claims_created": sum(int(row["research_execution"]["evidence_claims_persisted"]) for row in rows),
        "all_graph_nodes": sum(len(row["research_plan"]["all_graph_nodes"]) for row in rows),
        "selected_nodes": sum(len(row["research_plan"]["selected_nodes"]) for row in rows),
        "skipped_nodes": sum(len(row["research_plan"]["skipped_nodes"]) for row in rows),
        "successful_selected_nodes": sum(
            int(row["forecast_execution"]["selected_nodes_successfully_researched"]) for row in rows
        ),
        "failed_selected_nodes": sum(int(row["forecast_execution"]["selected_nodes_failed"]) for row in rows),
        "critical_node_failures": sum(int(row["forecast_execution"]["critical_node_failures"]) for row in rows),
        "queries_attempted": sum(int(row["research_execution"]["queries_attempted"]) for row in rows),
        "urls_discovered": sum(int(row["research_execution"]["urls_discovered"]) for row in rows),
        "documents_fetched": sum(int(row["research_execution"]["documents_fetched"]) for row in rows),
        "cutoff_rejections": sum(int(row["research_execution"]["cutoff_rejections"]) for row in rows),
        "extraction_attempts": sum(int(row["research_execution"]["extraction_attempts"]) for row in rows),
        "smaller_chunk_retries": sum(int(row["research_execution"]["smaller_chunk_retries"]) for row in rows),
        "document_level_fallbacks": sum(int(row["research_execution"]["document_level_fallbacks"]) for row in rows),
        "extraction_failures": sum(int(row["research_execution"]["extraction_failures"]) for row in rows),
        "actual_model_calls": sum(int(row["operations"]["actual_model_calls"]) for row in rows),
        "actual_search_calls": sum(int(row["operations"]["actual_search_calls"]) for row in rows),
        "provider_ledger_rows": sum(int(row["operations"]["provider_ledger_row_count"]) for row in rows),
        "live_provider_calls": sum(int(row["operations"]["live_provider_call_count"]) for row in rows),
        "budget_exceeded_runs": sum(int(row["operations"]["budget_exceeded_events"]) > 0 for row in rows),
        "total_estimated_pre_execution_cost_usd": round(
            sum(float(row["research_plan"]["estimated_pre_execution_cost_usd"]) for row in rows),
            12,
        ),
        "total_actual_cost_usd": round(
            sum(float(row["operations"]["actual_cost_usd"]) for row in rows),
            12,
        ),
        "total_latency_ms": sum(latencies),
        "mean_latency_ms": (round(sum(latencies) / len(latencies), 3) if latencies else None),
        "whole_run_retries": sum(int(row["operations"]["whole_run_retries"]) for row in rows),
        "physical_provider_retries": sum(int(row["operations"]["physical_provider_retries"]) for row in rows),
        "failure_categories": dict(sorted(categories.items())),
    }


def evaluate_validation_gates(
    rows: list[dict[str, Any]],
    frozen: dict[str, Any],
) -> list[dict[str, Any]]:
    summary = summarize_graph_planner_validation(rows)
    ceiling = float(frozen["budget"]["hard_per_question_cost_ceiling_usd"])
    gates = [
        (
            "Five of five graph runs produce a ForecastVersion",
            summary["forecast_versions_created"] == 5,
            f"{summary['forecast_versions_created']}/5",
        ),
        (
            "Five of five graph runs produce a ForecastAggregation",
            summary["forecast_aggregations_created"] == 5,
            f"{summary['forecast_aggregations_created']}/5",
        ),
        (
            "Every run produces at least one NodeForecastRun",
            all(row["forecast_execution"]["node_forecast_runs_persisted"] > 0 for row in rows) and len(rows) == 5,
            ", ".join(str(row["forecast_execution"]["node_forecast_runs_persisted"]) for row in rows),
        ),
        (
            "Every run produces at least one accepted EvidenceClaim",
            all(row["research_execution"]["evidence_claims_persisted"] > 0 for row in rows) and len(rows) == 5,
            ", ".join(str(row["research_execution"]["evidence_claims_persisted"]) for row in rows),
        ),
        (
            "No run ends with run-level budget_exceeded",
            summary["budget_exceeded_runs"] == 0,
            str(summary["budget_exceeded_runs"]),
        ),
        (
            "No critical selected node fails",
            summary["critical_node_failures"] == 0,
            str(summary["critical_node_failures"]),
        ),
        (
            "Every run remains within the $0.25 lifetime ceiling",
            all(float(row["operations"]["actual_cost_usd"]) <= ceiling + 1e-12 for row in rows) and len(rows) == 5,
            ", ".join(f"${float(row['operations']['actual_cost_usd']):.6f}" for row in rows),
        ),
        (
            "The planner selects no more than eight nodes per run",
            all(len(row["research_plan"]["selected_nodes"]) <= 8 for row in rows) and len(rows) == 5,
            ", ".join(str(len(row["research_plan"]["selected_nodes"])) for row in rows),
        ),
        (
            "Search and evidence limits remain within the committed profile and plan",
            all(row["search_and_evidence_limits_respected"] for row in rows) and len(rows) == 5,
            ", ".join("within" if row["search_and_evidence_limits_respected"] else "exceeded" for row in rows),
        ),
        (
            "No live OpenAI or Tavily request occurs",
            summary["live_provider_calls"] == 0
            and frozen["provider"]["model_provider"] == "mock"
            and frozen["provider"]["search_provider"] == "mock",
            f"live ledger rows: {summary['live_provider_calls']}",
        ),
        (
            "No forecasting or research behavior is changed during validation",
            not frozen["validation_scope"]["core_behavior_modified"],
            (
                "validation-only files"
                if not frozen["validation_scope"]["core_behavior_modified"]
                else ", ".join(frozen["validation_scope"]["unexpected_nonvalidation_changes"])
            ),
        ),
    ]
    return [
        {
            "condition": index,
            "description": description,
            "passed": passed,
            "evidence": evidence,
        }
        for index, (description, passed, evidence) in enumerate(gates, start=1)
    ]


def _previous_state() -> dict[str, Any]:
    artifact = _previous_artifact()
    summary = artifact["summary"]
    return {
        "validation_id": PREVIOUS_VALIDATION_ID,
        "forecasts_completed": int(summary["completed_questions"]),
        "assigned_questions": int(summary["assigned_questions"]),
        "successful_nodes": int(summary["successful_nodes"]),
        "failed_nodes": int(summary["failed_nodes"]),
        "budget_exceeded_runs": int(summary["failure_categories"].get("budget_exceeded", 0)),
        "extraction_failures": int(summary["failure_categories"].get("extraction_failure", 0)),
        "nodes_skipped_after_budget_stop": int(summary["failure_categories"].get("not_run_after_budget_stop", 0)),
        "evidence_claims": int(summary["evidence_claims_created"]),
        "node_forecasts": int(summary["node_forecasts_created"]),
        "aggregations": int(summary["final_aggregations_created"]),
        "forecast_versions": int(summary["forecast_versions_created"]),
        "total_cost_usd": float(summary["total_cost_usd"]),
        "mean_latency_ms": float(summary["mean_latency_ms"]),
    }


def _execute_question_once(
    session_factory: Callable[[], Session],
    *,
    frozen: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    run_id: str | None = None
    node_result: GraphNodeForecastResult | None = None
    execution_error: str | None = None
    search_calls: list[dict[str, Any]] = []
    started = time.perf_counter()
    catalog = load_pricing()
    bundle = load_prompt_bundle()
    profile = load_profile(VALIDATION_PROFILE_ID)

    with session_factory() as session:
        item = session.get(EvaluationQuestion, manifest["evaluation_question_id"])
        if item is None or item.question_hash != manifest["question_hash"]:
            raise GraphPlannerValidationError("planner_validation_question_missing")
        context = resolve_execution_context(
            requested_mode="backtest",
            profile_id=VALIDATION_PROFILE_ID,
            settings=_mock_provider_settings(),
            synthetic_fixture_run=True,
            as_of=as_utc(item.forecast_date),
        )
        expected_context = frozen["contexts"][manifest["question_hash"]]
        if context.configuration_hash != expected_context["configuration_hash"]:
            raise GraphPlannerValidationError("planner_validation_context_drift")
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
        search = RecordingMockSearchProvider(
            ledger=ledger,
            run_id=run.id,
            run_attempt_id=attempt.id,
        )
        holder: dict[str, GraphNodeForecastResult] = {}

        def audited_node_runner(**kwargs: Any) -> GraphNodeForecastResult:
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
        except Exception as exc:  # retain the first terminal result for every question
            execution_error = f"{exc.__class__.__name__}:{redact_secrets(str(exc))}"
            session.rollback()
            failed_run = session.get(ForecastRun, run.id)
            if failed_run is not None and failed_run.status != "failed":
                failed_run.status = "failed"
                failed_run.error_stage = failed_run.error_stage or "validation_execution"
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
        search_calls = search.calls
        session.expire_all()
        persisted_run = session.get(ForecastRun, run.id)
        if persisted_run is None:
            raise GraphPlannerValidationError("planner_validation_run_disappeared")
        apply_totals_to_run(persisted_run, ledger.totals(run.id))
        persisted_run.latency_ms = int((time.perf_counter() - started) * 1000)
        session.commit()

    wall_latency_ms = int((time.perf_counter() - started) * 1000)
    assert run_id is not None
    with session_factory() as session:
        return collect_graph_planner_run_metrics(
            session,
            run_id=run_id,
            manifest=manifest,
            wall_latency_ms=wall_latency_ms,
            execution_error=execution_error,
            node_result=node_result,
            search_calls=search_calls,
            per_question_ceiling=float(frozen["budget"]["hard_per_question_cost_ceiling_usd"]),
        )


def execute_graph_planner_validation(
    session_factory: Callable[[], Session],
    frozen: dict[str, Any],
) -> dict[str, Any]:
    """Execute the exact five mock-only questions once with no run retry."""

    expected_hash = frozen.get("configuration_hash")
    unhashed = dict(frozen)
    unhashed.pop("configuration_hash", None)
    if expected_hash != configuration_hash(unhashed):
        raise GraphPlannerValidationError("planner_validation_configuration_hash_mismatch")
    if working_tree_dirty():
        raise GraphPlannerValidationError("planner_validation_requires_clean_worktree")
    if frozen.get("code", {}).get("working_tree_dirty"):
        raise GraphPlannerValidationError("planner_validation_frozen_dirty_worktree")
    if frozen.get("profile", {}).get("version") != VALIDATION_PROFILE_VERSION:
        raise GraphPlannerValidationError("planner_validation_profile_version_mismatch")
    provider = frozen.get("provider") or {}
    if provider.get("model_provider") != "mock" or provider.get("search_provider") != "mock":
        raise GraphPlannerValidationError("planner_validation_live_provider_forbidden")
    if provider.get("model_api_key_set") or provider.get("search_api_key_set"):
        raise GraphPlannerValidationError("planner_validation_provider_key_forbidden")
    if frozen.get("validation_scope", {}).get("core_behavior_modified"):
        raise GraphPlannerValidationError("planner_validation_core_behavior_changed")

    with session_factory() as session:
        dataset = import_pilot_benchmark(session)
        session.commit()
        if dataset.hash != PILOT_V1_DATASET_HASH:
            raise GraphPlannerValidationError("planner_validation_pilot_hash_mismatch")
        if validation_already_started(session):
            raise GraphPlannerValidationError("planner_validation_already_started")

    rows: list[dict[str, Any]] = []
    for manifest in frozen["dataset"]["questions"]:
        rows.append(
            _execute_question_once(
                session_factory,
                frozen=frozen,
                manifest=manifest,
            )
        )

    gates = evaluate_validation_gates(rows, frozen)
    return {
        "artifact_schema_version": VALIDATION_SCHEMA_VERSION,
        "validation_id": frozen["validation_id"],
        "validation_name": VALIDATION_NAME,
        "created_at": frozen["created_at"],
        "completed_at": utcnow().isoformat(),
        "freeze": frozen,
        "previous_state": _previous_state(),
        "summary": summarize_graph_planner_validation(rows),
        "rows": rows,
        "gate_results": gates,
        "passed": all(gate["passed"] for gate in gates),
        "claim_boundary": (
            "Execution reliability only. This validation does not compute forecast accuracy, "
            "does not tune behavior, and does not run the full pilot benchmark."
        ),
    }


def _fmt_ms(value: float | int | None) -> str:
    return "n/a" if value is None else f"{float(value):.1f} ms"


def _fmt_cost(value: float | int | None) -> str:
    return "n/a" if value is None else f"${float(value):.6f}"


def render_graph_planner_validation_report(artifact: dict[str, Any]) -> str:
    frozen = artifact["freeze"]
    summary = artifact["summary"]
    previous = artifact["previous_state"]
    lines = [
        "# Graph Research Planner Validation Report",
        "",
        "## Result",
        "",
        f"- Overall gate: **{'PASS' if artifact['passed'] else 'FAIL'}**",
        f"- Validation ID: `{artifact['validation_id']}`",
        f"- Planner source commit: `{frozen['code']['planner_source_commit']}`",
        f"- Validation harness commit: `{frozen['code']['git_commit']}`",
        f"- Graph profile: `{frozen['profile']['id']}` version {frozen['profile']['version']}",
        f"- Dataset hash: `{frozen['dataset']['hash']}`",
        "- Providers: built-in mock model and built-in mock search only",
        "- Live OpenAI/Tavily authorization: none",
        "- Whole-run retries: disabled",
        "",
        "This is an execution-reliability validation. The first execution result below is retained without tuning or rerun.",
        "",
        "## Exact reused questions",
        "",
        "| Order | Evaluation question ID | Domain | Forecast cutoff | Question |",
        "| ---: | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(frozen["dataset"]["questions"], start=1):
        lines.append(
            f"| {index} | `{item['evaluation_question_id']}` | {item['domain']} | "
            f"{item['evidence_cutoff']} | {item['question']} |"
        )

    lines.extend(
        [
            "",
            "## Previous versus current",
            "",
            "| Measure | Previous validation | Current validation |",
            "| --- | ---: | ---: |",
            f"| Forecasts completed | {previous['forecasts_completed']}/{previous['assigned_questions']} | {summary['completed_questions']}/{summary['assigned_questions']} |",
            f"| Successful nodes | {previous['successful_nodes']} | {summary['successful_selected_nodes']} |",
            f"| Failed nodes | {previous['failed_nodes']} | {summary['failed_selected_nodes']} |",
            f"| Budget-exceeded runs | {previous['budget_exceeded_runs']} | {summary['budget_exceeded_runs']} |",
            f"| Extraction failures | {previous['extraction_failures']} | {summary['extraction_failures']} |",
            f"| Nodes skipped after budget stop | {previous['nodes_skipped_after_budget_stop']} | 0 |",
            f"| Evidence Claims | {previous['evidence_claims']} | {summary['evidence_claims_created']} |",
            f"| NodeForecastRuns | {previous['node_forecasts']} | {summary['node_forecast_runs_created']} |",
            f"| ForecastAggregations | {previous['aggregations']} | {summary['forecast_aggregations_created']} |",
            f"| ForecastVersions | {previous['forecast_versions']} | {summary['forecast_versions_created']} |",
            f"| Total cost | {_fmt_cost(previous['total_cost_usd'])} | {_fmt_cost(summary['total_actual_cost_usd'])} |",
            f"| Mean latency | {_fmt_ms(previous['mean_latency_ms'])} | {_fmt_ms(summary['mean_latency_ms'])} |",
            "",
            "## Current totals",
            "",
            f"- All graph nodes: {summary['all_graph_nodes']}",
            f"- Selected nodes: {summary['selected_nodes']}",
            f"- Skipped nodes: {summary['skipped_nodes']}",
            f"- Critical-node failures: {summary['critical_node_failures']}",
            f"- Queries attempted: {summary['queries_attempted']}",
            f"- URLs discovered: {summary['urls_discovered']}",
            f"- Documents fetched: {summary['documents_fetched']}",
            f"- Cutoff rejections: {summary['cutoff_rejections']}",
            f"- Extraction attempts: {summary['extraction_attempts']}",
            f"- Smaller-chunk retries: {summary['smaller_chunk_retries']}",
            f"- Document-level fallbacks: {summary['document_level_fallbacks']}",
            f"- Extraction failures: {summary['extraction_failures']}",
            f"- Actual mock model calls: {summary['actual_model_calls']}",
            f"- Actual mock search calls: {summary['actual_search_calls']}",
            f"- Provider ledger rows: {summary['provider_ledger_rows']}",
            f"- Live provider calls: {summary['live_provider_calls']}",
            f"- Estimated pre-execution cost: {_fmt_cost(summary['total_estimated_pre_execution_cost_usd'])}",
            f"- Actual cost: {_fmt_cost(summary['total_actual_cost_usd'])}",
            f"- Mean latency: {_fmt_ms(summary['mean_latency_ms'])}",
            "",
            "## Question-level result",
            "",
            "| # | Domain | Status | Selected / skipped | Critical failures | Claims | Node runs | Aggregation | Version | Est. cost | Actual cost | Latency |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: |",
        ]
    )
    for index, row in enumerate(artifact["rows"], start=1):
        plan = row["research_plan"]
        forecast = row["forecast_execution"]
        research = row["research_execution"]
        operations = row["operations"]
        lines.append(
            f"| {index} | {row['domain']} | {row['status']} | "
            f"{len(plan['selected_nodes'])} / {len(plan['skipped_nodes'])} | "
            f"{forecast['critical_node_failures']} | {research['evidence_claims_persisted']} | "
            f"{forecast['node_forecast_runs_persisted']} | "
            f"{'yes' if forecast['forecast_aggregation_persisted'] else 'no'} | "
            f"{'yes' if forecast['forecast_version_persisted'] else 'no'} | "
            f"{_fmt_cost(plan['estimated_pre_execution_cost_usd'])} | "
            f"{_fmt_cost(operations['actual_cost_usd'])} | "
            f"{_fmt_ms(operations['latency_ms'])} |"
        )

    lines.extend(["", "## Detailed execution audit", ""])
    for index, row in enumerate(artifact["rows"], start=1):
        plan = row["research_plan"]
        research = row["research_execution"]
        forecast = row["forecast_execution"]
        operations = row["operations"]
        lines.extend(
            [
                f"### {index}. {row['question']}",
                "",
                f"- Evaluation question ID: `{row['evaluation_question_id']}`",
                f"- Forecast run ID: `{row['forecast_run_id']}`",
                f"- Selected / skipped nodes: {len(plan['selected_nodes'])} / {len(plan['skipped_nodes'])}",
                f"- Allocated searches: {plan['allocated_search_count']}",
                f"- Estimated pre-execution cost: {_fmt_cost(plan['estimated_pre_execution_cost_usd'])}",
                f"- Selected worker concurrency: {plan['selected_worker_concurrency']}",
                f"- Claims / node runs: {research['evidence_claims_persisted']} / {forecast['node_forecast_runs_persisted']}",
                f"- Final probability: {forecast['final_probability'] if forecast['final_probability'] is not None else 'not produced'}",
                f"- Reduced confidence: {'yes' if forecast['reduced_confidence'] else 'no'}",
                f"- Actual model / search calls: {operations['actual_model_calls']} / {operations['actual_search_calls']}",
                f"- Actual cost / remaining budget: {_fmt_cost(operations['actual_cost_usd'])} / {_fmt_cost(operations['budget_remaining_usd'])}",
                f"- Budget-exceeded events: {operations['budget_exceeded_events']}",
                f"- Whole-run / physical retries: {operations['whole_run_retries']} / {operations['physical_provider_retries']}",
                "",
                "| Node | Type | Critical | Selected | Raw priority | Normalized priority | Searches allocated | Claims | Forecast | Failure |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
            ]
        )
        node_audit = {item["node_id"]: item for item in research["nodes"]}
        for node in plan["all_graph_nodes"]:
            node_id = str(node["id"])
            audit = node_audit.get(node_id, {})
            lines.append(
                f"| {node['question']} | {node['node_type']} | "
                f"{'yes' if node['critical'] else 'no'} | "
                f"{'yes' if node_id in plan['selected_nodes'] else 'no'} | "
                f"{float(plan['priority_scores'].get(node_id, 0)):.6f} | "
                f"{float(plan['normalized_priority_scores'].get(node_id, 0)):.6f} | "
                f"{plan['allocated_search_count_by_node'].get(node_id, 0)} | "
                f"{audit.get('claims_created', 0)} | "
                f"{'yes' if audit.get('node_forecast_created') else 'no'} | "
                f"{audit.get('failure_code') or 'none'} |"
            )
        lines.extend(["", "Queries and discovered URLs:", ""])
        for audit in research["nodes"]:
            if not audit["research_selected"]:
                continue
            lines.append(f"- `{audit['node_id']}` {audit['question']}")
            for query in audit["queries_attempted"]:
                lines.append(f"  - Query: {query}")
            for url in audit["urls_discovered"]:
                lines.append(f"  - URL: {url}")
            lines.append(
                "  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: "
                f"{audit['documents_fetched']} / {audit['cutoff_rejections']} / "
                f"{audit['extraction_attempts']} / {audit['smaller_chunk_retries']} / "
                f"{audit['document_level_fallbacks']} / {audit['extraction_failures']}"
            )
        if row["failure_categories"]:
            lines.extend(
                [
                    "",
                    "Failure categories: "
                    + ", ".join(f"`{name}` ({count})" for name, count in row["failure_categories"].items()),
                ]
            )
        lines.append("")

    lines.extend(
        [
            "## Pass/fail gate",
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
            "- Forecasting and research behavior changed during validation: no.",
            "- Validation-only harness/report/artifact changes: yes.",
            f"- Unexpected non-validation files: {', '.join(frozen['validation_scope']['unexpected_nonvalidation_changes']) or 'none'}.",
            f"- Live provider ledger rows: {summary['live_provider_calls']}.",
            "- Full 20-question pilot benchmark executed: no.",
            "- Automatic validation rerun: no.",
            "",
            "## Limitations",
            "",
            "- Built-in mock providers validate execution mechanics and cutoff enforcement, not real-world retrieval coverage or forecast quality.",
            "- Five questions measure a narrow reliability surface and do not support accuracy conclusions.",
            "- Mock-provider monetary cost is zero; the planner estimate still records the pre-execution budget decision.",
            "- Latency is diagnostic and is not part of the pass/fail gate.",
            "",
            artifact["claim_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def write_graph_planner_validation_artifacts(
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
        render_graph_planner_validation_report(artifact),
        encoding="utf-8",
    )
