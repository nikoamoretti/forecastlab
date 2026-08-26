from __future__ import annotations

import json
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
from forecastlab.hashing import canonical_json, redact_secrets, sha256_text
from forecastlab.pricing import load_pricing, pricing_hash
from forecastlab.profiles import effective_profile, load_profile, profile_hash
from forecastlab.prompts import load_prompt_bundle
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.config import ROOT
from forecastlab_api.forecast_experiments import (
    _ensure_forecast_contract,
    _resolution_contract,
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
)
from forecastlab_api.persist import save_contract
from forecastlab_api.pilot_benchmark import (
    PILOT_DATASET_NAME,
    PILOT_DATASET_VERSION,
    import_pilot_benchmark,
    load_pilot_rows,
    validate_pilot_release,
)
from forecastlab_api.pipeline import (
    apply_execution_limits,
    create_run_record,
    execute_run,
    provider_settings_from_secrets,
)
from forecastlab_api.secrets import load_secrets

VALIDATION_PROFILE_ID = "graph_forecaster_v1"
# The preserved validation artifact was executed under profile v5. New validation
# freezes use the current private-V1 profile without rewriting that history.
VALIDATION_PROFILE_VERSION = 5
ACTIVE_VALIDATION_PROFILE_VERSION = 9
VALIDATION_SCHEMA_VERSION = 1
VALIDATION_SELECTION_RULE = "first_rows_by_domain_in_pilot_manifest_v1"
VALIDATION_DOMAIN_COUNTS = {
    "economics": 2,
    "business": 1,
    "technology": 1,
    "regulation": 1,
}
PILOT_V1_DATASET_HASH = (
    "c9cd748c1114eff5d16321ecf7d78b18be4a0b65b49ff7719aa5276e302f7888"
)
PREVIOUS_GRAPH_BENCHMARK = {
    "profile_version": 4,
    "assigned_questions": 20,
    "completed_questions": 10,
    "failed_questions": 10,
    "completion_rate": 0.5,
    "failure_summary": "All ten failed runs were classified as evidence failures.",
}
DEFAULT_ARTIFACT_PATH = (
    ROOT / "artifacts" / "graph_execution_validation" / "validation_results.json"
)
DEFAULT_REPORT_PATH = ROOT / "docs" / "GRAPH_EXECUTION_VALIDATION_REPORT.md"


class GraphValidationError(RuntimeError):
    """The evaluation-only graph validation failed a reproducibility or safety gate."""


def selected_pilot_questions() -> list[dict[str, Any]]:
    """Select the first requested domain rows without looking at outcomes."""

    canonical = validate_pilot_release(load_pilot_rows())
    selected: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for item in canonical:
        domain = str(item["domain"])
        if counts[domain] >= VALIDATION_DOMAIN_COUNTS.get(domain, 0):
            continue
        selected.append(item)
        counts[domain] += 1
    if dict(counts) != VALIDATION_DOMAIN_COUNTS or len(selected) != 5:
        raise GraphValidationError("graph_validation_selection_invalid")
    return selected


def _public_provider_snapshot(
    context: Any,
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


def freeze_graph_validation(
    *,
    settings_data: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Freeze selection, profile, providers, prompts, budget, and code before execution."""

    selected = selected_pilot_questions()
    provider_settings = settings_data or provider_settings_from_secrets(load_secrets())
    profile = load_profile(VALIDATION_PROFILE_ID)
    if profile.version != ACTIVE_VALIDATION_PROFILE_VERSION:
        raise GraphValidationError("graph_validation_profile_version_mismatch")
    bundle = load_prompt_bundle()
    catalog = load_pricing()
    contexts: list[Any] = []
    for item in selected:
        contexts.append(
            resolve_execution_context(
                requested_mode="backtest",
                profile_id=VALIDATION_PROFILE_ID,
                settings=provider_settings,
                synthetic_fixture_run=False,
                as_of=as_utc(datetime.fromisoformat(str(item["forecast_date"]))),
                # The runtime ledger still enforces Settings' lower hard ceiling.
                allow_estimate_over_ceiling=True,
            )
        )
    first_context = contexts[0]
    if first_context.model_is_mock or first_context.search_is_mock:
        raise GraphValidationError("graph_validation_real_providers_required")
    if any(
        context.effective_max_cost_usd != first_context.effective_max_cost_usd
        for context in contexts[1:]
    ):
        raise GraphValidationError("graph_validation_budget_mismatch")

    identity = build_environment_identity(
        prompt_bundle_hash=sha256_text(canonical_json(bundle.hashes())),
        profile_hashes={VALIDATION_PROFILE_ID: profile_hash(profile)},
        pricing_catalog=catalog,
    )
    selection = [
        {
            "question_hash": item["question_hash"],
            "question": item["question"],
            "forecast_date": item["forecast_date"],
            "evidence_cutoff": item["forecast_date"],
            "resolution_date": item["resolution_date"],
            "resolution_source": item["resolution_source"],
            "domain": item["domain"],
            "category": item.get("category"),
        }
        for item in selected
    ]
    frozen = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "validation_name": "Graph Execution Validation",
        "validation_id": str(uuid.uuid4()),
        "created_at": as_utc(created_at or utcnow()).isoformat(),
        "dataset": {
            "name": PILOT_DATASET_NAME,
            "version": PILOT_DATASET_VERSION,
            "hash": PILOT_V1_DATASET_HASH,
            "selection_rule": VALIDATION_SELECTION_RULE,
            "selected_question_count": len(selection),
            "selected_question_hash": sha256_text(canonical_json(selection)),
            "questions": selection,
        },
        "profile": {
            "id": profile.id,
            "version": profile.version,
            "hash": profile_hash(profile),
            "snapshot": profile.model_dump(mode="json"),
        },
        "provider": _public_provider_snapshot(first_context, provider_settings),
        "budget": {
            "hard_per_question_cost_ceiling_usd": first_context.effective_max_cost_usd,
            "hard_validation_cost_ceiling_usd": round(
                first_context.effective_max_cost_usd * len(selection), 12
            ),
            "estimated_upper_bound_per_question_usd": (
                first_context.estimated_upper_bound_cost_usd
            ),
            "estimate_over_ceiling_acknowledged": bool(
                first_context.estimate_exceeds_ceiling
            ),
            "max_model_calls": first_context.effective_max_model_calls,
            "max_search_calls": first_context.effective_max_search_calls,
            "max_fetched_documents": first_context.effective_max_fetched_documents,
            "max_tokens": first_context.effective_max_tokens,
            "max_wall_clock_seconds": first_context.effective_max_wall_clock_seconds,
        },
        "prompts": {
            "versions": bundle.versions(),
            "hashes": bundle.hashes(),
            "bundle_hash": sha256_text(canonical_json(bundle.hashes())),
        },
        "pricing_hash": pricing_hash(catalog=catalog),
        "contexts": {
            item["question_hash"]: {
                "configuration_hash": context.configuration_hash,
                "evidence_cutoff": item["forecast_date"],
            }
            for item, context in zip(selected, contexts, strict=True)
        },
        "code": identity,
        "execution_policy": {
            "whole_run_attempts_per_question": 1,
            "automatic_whole_run_retry": False,
            "provider_transient_retry_policy": "existing_provider_policy_max_3_physical_attempts",
            "accuracy_metrics": "not_computed",
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
        notes=f"graph-execution-validation:{validation_id}:{item.question_hash}",
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


def collect_graph_run_metrics(
    session: Session,
    *,
    run_id: str,
    manifest: dict[str, Any],
    wall_latency_ms: int,
    execution_error: str | None,
) -> dict[str, Any]:
    """Collect execution-only measures; outcomes and accuracy are deliberately absent."""

    run = session.get(ForecastRun, run_id)
    if run is None:
        raise GraphValidationError("graph_validation_run_missing")
    graph = _graph_for_run(session, run)
    node_runs = session.scalars(
        select(ForecastNodeRunRow).where(
            ForecastNodeRunRow.forecast_run_id == run.id
        )
    ).all()
    failures = session.scalars(
        select(GraphExecutionFailureRow).where(
            GraphExecutionFailureRow.forecast_run_id == run.id
        )
    ).all()
    claims = session.scalars(
        select(EvidenceClaimRow)
        .join(EvidenceItem, EvidenceClaimRow.evidence_item_id == EvidenceItem.id)
        .where(EvidenceItem.run_id == run.id)
    ).all()
    aggregation = session.scalar(
        select(ForecastAggregationRow).where(
            ForecastAggregationRow.forecast_run_id == run.id
        )
    )
    version = session.scalar(
        select(ForecastVersion).where(ForecastVersion.run_id == run.id)
    )
    attempts = session.scalars(
        select(ForecastRunAttempt).where(ForecastRunAttempt.run_id == run.id)
    ).all()
    provider_calls = session.scalars(
        select(ProviderCallLedger).where(ProviderCallLedger.run_id == run.id)
    ).all()
    total_nodes = len(graph.nodes) if graph is not None else 0
    failed_node_ids = {
        str(failure.node_id) for failure in failures if failure.node_id is not None
    }
    evidence_node_ids = {str(claim.forecast_node_id) for claim in claims}
    failure_categories = Counter(failure.error_code for failure in failures)
    if execution_error and not failure_categories:
        failure_categories["operational_failure"] += 1
    physical_retries = sum(call.physical_attempt_number > 1 for call in provider_calls)
    whole_run_retries = max(0, len(attempts) - 1)
    completed = bool(
        run.status == "completed"
        and version is not None
        and aggregation is not None
    )
    return {
        "question_hash": manifest["question_hash"],
        "question": manifest["question"],
        "domain": manifest["domain"],
        "category": manifest.get("category"),
        "forecast_date": manifest["forecast_date"],
        "resolution_date": manifest["resolution_date"],
        "forecast_run_id": run.id,
        "status": "completed" if completed else "failed",
        "total_nodes": total_nodes,
        "successful_nodes": len({node_run.node_id for node_run in node_runs}),
        "failed_nodes": len(failed_node_ids),
        "failed_node_ids": sorted(failed_node_ids),
        "evidence_claims_created": len(claims),
        "nodes_with_evidence_claims": len(evidence_node_ids),
        "evidence_success_rate": (
            round(len(evidence_node_ids) / total_nodes, 12) if total_nodes else None
        ),
        "node_forecasts_created": len(node_runs),
        "final_aggregation_created": aggregation is not None,
        "forecast_version_created": version is not None,
        "latency_ms": wall_latency_ms,
        "cost_usd": float(run.total_cost_usd or run.cost_usd or 0.0),
        "cost_source": run.cost_source,
        "whole_run_attempts": len(attempts),
        "whole_run_retries": whole_run_retries,
        "provider_requests": len(provider_calls),
        "physical_provider_retries": physical_retries,
        "failed_provider_attempts": sum(call.status == "failed" for call in provider_calls),
        "failure_categories": dict(sorted(failure_categories.items())),
        "execution_error": redact_secrets(execution_error) if execution_error else None,
    }


def summarize_graph_validation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    assigned = len(rows)
    completed = sum(row["status"] == "completed" for row in rows)
    total_nodes = sum(int(row["total_nodes"]) for row in rows)
    evidence_nodes = sum(int(row["nodes_with_evidence_claims"]) for row in rows)
    categories: Counter[str] = Counter()
    for row in rows:
        categories.update(row.get("failure_categories") or {})
    latencies = [int(row["latency_ms"]) for row in rows]
    return {
        "assigned_questions": assigned,
        "completed_questions": completed,
        "failed_questions": assigned - completed,
        "completion_rate": round(completed / assigned, 12) if assigned else None,
        "total_nodes": total_nodes,
        "successful_nodes": sum(int(row["successful_nodes"]) for row in rows),
        "failed_nodes": sum(int(row["failed_nodes"]) for row in rows),
        "evidence_claims_created": sum(
            int(row["evidence_claims_created"]) for row in rows
        ),
        "node_forecasts_created": sum(
            int(row["node_forecasts_created"]) for row in rows
        ),
        "final_aggregations_created": sum(
            bool(row["final_aggregation_created"]) for row in rows
        ),
        "forecast_versions_created": sum(
            bool(row["forecast_version_created"]) for row in rows
        ),
        "evidence_success_rate": (
            round(evidence_nodes / total_nodes, 12) if total_nodes else None
        ),
        "total_latency_ms": sum(latencies),
        "mean_latency_ms": (
            round(sum(latencies) / assigned, 3) if assigned else None
        ),
        "total_cost_usd": round(sum(float(row["cost_usd"]) for row in rows), 12),
        "whole_run_retries": sum(int(row["whole_run_retries"]) for row in rows),
        "physical_provider_retries": sum(
            int(row["physical_provider_retries"]) for row in rows
        ),
        "failed_provider_attempts": sum(
            int(row["failed_provider_attempts"]) for row in rows
        ),
        "failure_categories": dict(sorted(categories.items())),
    }


def execute_graph_validation(
    session_factory: Callable[[], Session],
    frozen: dict[str, Any],
) -> dict[str, Any]:
    """Execute each selected graph question once, with no whole-run retry."""

    expected_hash = frozen.get("configuration_hash")
    unhashed = dict(frozen)
    unhashed.pop("configuration_hash", None)
    if expected_hash != configuration_hash(unhashed):
        raise GraphValidationError("graph_validation_configuration_hash_mismatch")
    if working_tree_dirty():
        raise GraphValidationError("graph_validation_requires_clean_worktree")
    if frozen.get("profile", {}).get("version") != ACTIVE_VALIDATION_PROFILE_VERSION:
        raise GraphValidationError("graph_validation_profile_version_mismatch")

    with session_factory() as session:
        dataset = import_pilot_benchmark(session)
        session.commit()
        if dataset.hash != PILOT_V1_DATASET_HASH:
            raise GraphValidationError("graph_validation_pilot_hash_mismatch")

    settings_data = provider_settings_from_secrets(load_secrets())
    profile = load_profile(VALIDATION_PROFILE_ID)
    bundle = load_prompt_bundle()
    catalog = load_pricing()
    rows: list[dict[str, Any]] = []
    validation_id = str(frozen["validation_id"])

    for manifest in frozen["dataset"]["questions"]:
        run_id: str | None = None
        started = time.perf_counter()
        error: str | None = None
        with session_factory() as session:
            item = session.scalar(
                select(EvaluationQuestion).where(
                    EvaluationQuestion.question_hash == manifest["question_hash"]
                )
            )
            if item is None:
                raise GraphValidationError("graph_validation_question_missing")
            context = resolve_execution_context(
                requested_mode="backtest",
                profile_id=VALIDATION_PROFILE_ID,
                settings=settings_data,
                synthetic_fixture_run=False,
                as_of=as_utc(item.forecast_date),
                allow_estimate_over_ceiling=True,
            )
            run = _prepare_run(
                session,
                item=item,
                validation_id=validation_id,
                context=context,
            )
            run_id = run.id
            try:
                execute_run(
                    session,
                    run,
                    profile=apply_execution_limits(
                        effective_profile(
                            profile,
                            user_max_cost_usd=context.effective_max_cost_usd,
                        ),
                        context,
                    ),
                    prompt_bundle=bundle,
                    model_timeout=context.model_timeout_seconds,
                    pricing_catalog=catalog,
                )
            except Exception as exc:  # the validation must retain every terminal result
                error = f"{exc.__class__.__name__}:{redact_secrets(str(exc))}"
                session.rollback()
                failed_run = session.get(ForecastRun, run_id)
                if failed_run is not None and failed_run.status != "failed":
                    failed_run.status = "failed"
                    failed_run.error_stage = failed_run.error_stage or "validation_execution"
                    failed_run.error_message = error
                    failed_run.finished_at = utcnow()
                    session.commit()
        wall_latency_ms = int((time.perf_counter() - started) * 1000)
        assert run_id is not None
        with session_factory() as session:
            rows.append(
                collect_graph_run_metrics(
                    session,
                    run_id=run_id,
                    manifest=manifest,
                    wall_latency_ms=wall_latency_ms,
                    execution_error=error,
                )
            )

    return {
        "artifact_schema_version": VALIDATION_SCHEMA_VERSION,
        "validation_id": validation_id,
        "validation_name": frozen["validation_name"],
        "created_at": frozen["created_at"],
        "completed_at": utcnow().isoformat(),
        "freeze": frozen,
        "previous_state": PREVIOUS_GRAPH_BENCHMARK,
        "summary": summarize_graph_validation(rows),
        "rows": rows,
        "claim_boundary": (
            "Execution reliability only. No forecast-accuracy metric or superiority claim "
            "is computed from this five-question validation."
        ),
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def render_graph_validation_report(artifact: dict[str, Any]) -> str:
    summary = artifact["summary"]
    previous = artifact["previous_state"]
    frozen = artifact["freeze"]
    provider = frozen["provider"]
    budget = frozen["budget"]
    lines = [
        "# Graph Execution Validation Report",
        "",
        "## Scope",
        "",
        (
            "This is a five-question execution-reliability validation of "
            f"`graph_forecaster_v1` version {frozen['profile']['version']}. "
            "It does not compare forecast accuracy."
        ),
        "",
        f"- Validation ID: `{artifact['validation_id']}`",
        f"- Pilot dataset hash: `{frozen['dataset']['hash']}`",
        f"- Execution code commit: `{frozen['code']['git_commit']}`",
        f"- Configuration hash: `{frozen['configuration_hash']}`",
        f"- Model: `{provider['model_provider']} / {provider['model']}`",
        f"- Search: `{provider['search_provider']}`",
        f"- Evidence policy: `{provider['evidence_policy']}`",
        (
            "- Hard cost ceiling: "
            f"${budget['hard_per_question_cost_ceiling_usd']:.2f}/question, "
            f"${budget['hard_validation_cost_ceiling_usd']:.2f} total"
        ),
        "- Selection rule: first two economics rows and first business, technology, and regulation rows in the frozen pilot manifest",
        "",
        "## Previous state and current validation",
        "",
        "| Measure | Previous graph benchmark | Current validation |",
        "| --- | ---: | ---: |",
        f"| Questions | {previous['assigned_questions']} | {summary['assigned_questions']} |",
        f"| Completed | {previous['completed_questions']} | {summary['completed_questions']} |",
        f"| Failed | {previous['failed_questions']} | {summary['failed_questions']} |",
        f"| Completion rate | {_percent(previous['completion_rate'])} | {_percent(summary['completion_rate'])} |",
        "",
        (
            "The previous benchmark's ten failures were evidence failures. The current "
            f"sample is smaller and uses profile version {frozen['profile']['version']}, "
            "so this is a descriptive "
            "execution comparison only."
        ),
        "",
        "## Current execution measures",
        "",
        f"- Successful nodes: {summary['successful_nodes']} / {summary['total_nodes']}",
        f"- Failed nodes: {summary['failed_nodes']}",
        f"- Evidence Claims created: {summary['evidence_claims_created']}",
        f"- Evidence success rate: {_percent(summary['evidence_success_rate'])}",
        f"- Node forecasts created: {summary['node_forecasts_created']}",
        f"- Final aggregations created: {summary['final_aggregations_created']}",
        f"- Forecast Versions created: {summary['forecast_versions_created']}",
        f"- Total latency: {summary['total_latency_ms']} ms",
        f"- Mean latency: {summary['mean_latency_ms']} ms/question",
        f"- Total measured cost: ${summary['total_cost_usd']:.6f}",
        f"- Whole-run retries: {summary['whole_run_retries']}",
        f"- Physical provider retries: {summary['physical_provider_retries']}",
        f"- Failed provider attempts: {summary['failed_provider_attempts']}",
        "",
        "## Question-level execution audit",
        "",
        "| Domain | Question | Status | Successful / total nodes | Failed nodes | Claims | Node forecasts | Aggregation | Latency ms | Cost USD | Retries | Failure categories |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for row in artifact["rows"]:
        categories = ", ".join(
            f"{key} ({value})" for key, value in row["failure_categories"].items()
        ) or "none"
        retries = row["whole_run_retries"] + row["physical_provider_retries"]
        lines.append(
            "| "
            f"{row['domain']} | {row['question']} | {row['status']} | "
            f"{row['successful_nodes']} / {row['total_nodes']} | {row['failed_nodes']} | "
            f"{row['evidence_claims_created']} | {row['node_forecasts_created']} | "
            f"{'yes' if row['final_aggregation_created'] else 'no'} | "
            f"{row['latency_ms']} | {row['cost_usd']:.6f} | {retries} | {categories} |"
        )
    lines.extend(
        [
            "",
            "## Evidence failures",
            "",
        ]
    )
    if summary["failure_categories"]:
        lines.extend(
            f"- `{category}`: {count}"
            for category, count in summary["failure_categories"].items()
        )
    else:
        lines.append("No graph execution failure categories were recorded.")
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- Five questions are enough to test the execution path, not forecasting quality or statistical reliability.",
            "- Historical retrieval depends on search indexing and verified archive availability at each forecast cutoff.",
            "- Cost is ledger-derived and may be estimated where a provider does not report billing data.",
            "- No failed question was automatically rerun, and the full 20-question pilot benchmark was not executed.",
            "",
            artifact["claim_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def write_graph_validation_artifacts(
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
    report_path.write_text(render_graph_validation_report(artifact), encoding="utf-8")
