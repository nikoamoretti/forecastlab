from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forecastlab.contracts import ForecastContractError
from forecastlab.graph_execution import GraphEngineResult
from forecastlab.graphs import GraphGenerator
from forecastlab.prompts import PromptBundle
from forecastlab.providers.base import ModelProvider
from forecastlab.schemas import EvidenceClaim, ForecastContract, ForecastGraph, ForecastNodeRun
from forecastlab.timeutil import parse_datetime, utcnow
from forecastlab_api.contracts import forecast_contract_from_row
from forecastlab_api.evidence_claims import store_evidence_claims
from forecastlab_api.graphs import forecast_graph_from_row, store_forecast_graph
from forecastlab_api.models import (
    EvidenceClaimRow,
    EvidenceItem,
    ForecastContractRow,
    ForecastGraphRow,
    ForecastNodeRunRow,
    ForecastRun,
    ForecastVersion,
    Question,
)
from forecastlab_api.persist import jsonable


def approved_contract_for_question(session: Session, question_id: str) -> ForecastContractRow:
    row = session.scalar(
        select(ForecastContractRow)
        .where(
            ForecastContractRow.question_id == question_id,
            ForecastContractRow.status == "approved",
        )
        .order_by(ForecastContractRow.version.desc())
        .limit(1)
    )
    if row is None:
        raise ForecastContractError(
            ["approved_forecast_contract_required"],
            "Approve the Forecast Contract before V1 forecast execution",
        )
    return row


def ensure_execution_graph(
    session: Session,
    *,
    question: Question,
    model: ModelProvider,
    prompt_bundle: PromptBundle | None = None,
) -> tuple[ForecastContract, ForecastGraph]:
    """Resolve the approved contract and persist its graph before node research starts."""

    contract_row = approved_contract_for_question(session, question.id)
    contract = forecast_contract_from_row(contract_row)
    graph_row = session.scalar(
        select(ForecastGraphRow)
        .where(
            ForecastGraphRow.contract_id == contract.id,
            ForecastGraphRow.status == "approved",
        )
        .order_by(ForecastGraphRow.version.desc())
        .limit(1)
    )
    if graph_row is None:
        latest_version = session.scalar(
            select(func.max(ForecastGraphRow.version)).where(ForecastGraphRow.contract_id == contract.id)
        )
        graph = GraphGenerator(model, prompt_bundle=prompt_bundle).generate(
            contract,
            version=int(latest_version or 0) + 1,
        )
        graph_row = store_forecast_graph(session, graph)
        session.commit()
    return contract, forecast_graph_from_row(graph_row)


def forecast_node_run_from_row(row: ForecastNodeRunRow) -> ForecastNodeRun:
    return ForecastNodeRun(
        id=row.id,
        forecast_run_id=row.forecast_run_id,
        node_id=row.node_id,
        probability=row.probability,
        confidence=row.confidence,
        reasoning=row.reasoning,
        supporting_claim_ids=json.loads(row.supporting_claim_ids_json or "[]"),
        opposing_claim_ids=json.loads(row.opposing_claim_ids_json or "[]"),
        uncertainty=row.uncertainty,
        raw_importance_weight=row.raw_importance_weight,
        dependency_factor=row.dependency_factor,
        normalized_weight=row.normalized_weight,
        probability_contribution=row.probability_contribution,
        created_at=row.created_at,
    )


def node_runs_for_run(session: Session, run_id: str) -> list[ForecastNodeRun]:
    rows = session.scalars(
        select(ForecastNodeRunRow)
        .where(ForecastNodeRunRow.forecast_run_id == run_id)
        .order_by(ForecastNodeRunRow.created_at, ForecastNodeRunRow.id)
    ).all()
    return [forecast_node_run_from_row(row) for row in rows]


def _persist_evidence_item(session: Session, run: ForecastRun, item: dict) -> None:
    if session.get(EvidenceItem, item["id"]) is not None:
        return
    session.add(
        EvidenceItem(
            id=item["id"],
            run_id=run.id,
            track_id=None,
            subquestion=item.get("subquestion"),
            url=item["url"],
            title=item.get("title") or "",
            publisher=item.get("publisher"),
            published_at=parse_datetime(item.get("published_at")),
            retrieved_at=parse_datetime(item.get("retrieved_at")) or utcnow(),
            excerpt=item.get("excerpt") or "",
            content_hash=item.get("content_hash") or "",
            source_class=item.get("source_class") or "secondary",
            as_of_eligible=bool(item.get("as_of_eligible", True)),
            rejected=bool(item.get("rejected")),
            rejection_reason=item.get("rejection_reason"),
            snapshot_url=item.get("snapshot_url") or item.get("final_snapshot_url"),
            snapshot_at=parse_datetime(item.get("snapshot_at") or item.get("final_snapshot_at")),
            requested_snapshot_url=item.get("requested_snapshot_url"),
            requested_snapshot_at=parse_datetime(item.get("requested_snapshot_at")),
            final_snapshot_url=item.get("final_snapshot_url"),
            final_snapshot_at=parse_datetime(item.get("final_snapshot_at")),
            archived_original_url=item.get("archived_original_url"),
            snapshot_verification_status=item.get("snapshot_verification_status"),
            status_code=int(item.get("status_code") or 0),
            published_at_unknown=bool(item.get("published_at_unknown")),
        )
    )


def persist_node_research(
    session: Session,
    *,
    run: ForecastRun,
    evidence: list[dict],
    rejected: list[dict],
    claims: list[EvidenceClaim],
) -> None:
    """Commit provenance before the node forecast can consume its claim context."""

    for item in evidence + rejected:
        _persist_evidence_item(session, run, item)
    session.flush()
    new_claims = [claim for claim in claims if session.get(EvidenceClaimRow, claim.id) is None]
    if new_claims:
        cutoff: datetime | None = run.as_of if run.mode == "backtest" else None
        store_evidence_claims(session, new_claims, cutoff=cutoff)
    session.commit()


def _apply_result_metadata(run: ForecastRun, result: GraphEngineResult) -> None:
    run.finished_at = utcnow()
    run.cost_usd = float(result.budget.get("total_cost_usd") or result.budget.get("cost_usd") or 0)
    run.total_cost_usd = run.cost_usd
    run.model_cost_usd = float(result.budget.get("model_cost_usd") or 0)
    run.search_cost_usd = float(result.budget.get("search_cost_usd") or 0)
    run.failed_attempt_cost_usd = float(result.budget.get("failed_attempt_cost_usd") or 0)
    run.tokens = int(result.budget.get("tokens") or result.budget.get("total_tokens") or 0)
    run.prompt_tokens = int(result.budget.get("prompt_tokens") or 0)
    run.completion_tokens = int(result.budget.get("completion_tokens") or 0)
    run.total_tokens = int(result.budget.get("total_tokens") or run.tokens)
    run.provider_request_count = int(result.budget.get("provider_request_count") or 0)
    run.cost_source = str(result.budget.get("cost_label") or "estimated")
    run.prompt_versions_json = json.dumps(result.prompt_versions)
    run.budget_json = json.dumps(jsonable(result.budget))
    run.aggregation_json = json.dumps(jsonable(result.aggregation))
    run.disagreement_summary = None
    run.fixture_evidence_used = bool(result.fixture_evidence_used)
    run.progress_pct = 100
    if result.stopped_early:
        run.error_stage = result.stop_stage
        run.error_message = result.stop_reason


def persist_graph_engine_result(
    session: Session,
    run: ForecastRun,
    result: GraphEngineResult,
) -> ForecastVersion | None:
    existing = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run.id))
    if existing is not None:
        run.status = "completed"
        run.finished_at = run.finished_at or utcnow()
        run.progress_pct = 100
        run.progress_stage = "report"
        run.progress_message = "Forecast ready"
        return existing

    _apply_result_metadata(run, result)
    for execution in result.nodes:
        for item in execution.evidence + execution.rejected:
            _persist_evidence_item(session, run, item)
    session.flush()

    all_claims = [
        claim
        for execution in result.nodes
        for claim in execution.claims
        if session.get(EvidenceClaimRow, claim.id) is None
    ]
    if all_claims:
        cutoff: datetime | None = run.as_of if run.mode == "backtest" else None
        store_evidence_claims(session, all_claims, cutoff=cutoff)

    probabilities: dict[str, float | None] = {}
    driver_acc: list[dict] = []
    counter_acc: list[str] = []
    claim_ids: list[str] = []
    for execution in result.nodes:
        node_run = execution.node_run
        probabilities[execution.node.id] = node_run.probability if node_run else None
        claim_ids.extend(claim.id for claim in execution.claims)
        if node_run is None:
            continue
        session.add(
            ForecastNodeRunRow(
                id=node_run.id,
                forecast_run_id=node_run.forecast_run_id,
                node_id=node_run.node_id,
                probability=node_run.probability,
                confidence=node_run.confidence,
                reasoning=node_run.reasoning,
                supporting_claim_ids_json=json.dumps(node_run.supporting_claim_ids),
                opposing_claim_ids_json=json.dumps(node_run.opposing_claim_ids),
                uncertainty=node_run.uncertainty,
                raw_importance_weight=node_run.raw_importance_weight,
                dependency_factor=node_run.dependency_factor,
                normalized_weight=node_run.normalized_weight,
                probability_contribution=node_run.probability_contribution,
                created_at=node_run.created_at,
            )
        )
        cited = [*node_run.supporting_claim_ids, *node_run.opposing_claim_ids]
        driver_acc.append(
            {
                "factor": execution.node.question,
                "direction": "unclear",
                "importance": execution.node.importance_weight,
                "evidence_ids": cited,
                "inference": not cited,
            }
        )
        if node_run.opposing_claim_ids or execution.node.node_type == "adversarial":
            counter_acc.append(node_run.reasoning)

    question = run.question
    if result.aggregation.ensemble_probability is None:
        run.status = "failed"
        run.error_message = run.error_message or result.stop_reason or "no_probability"
        run.progress_stage = "failed"
        run.progress_message = "No probability produced"
        if not question.is_benchmark:
            question.status = "failed"
        return None

    run.status = "completed"
    run.progress_stage = "report"
    run.progress_message = "Partial forecast" if result.partial else "Forecast ready"
    if not question.is_benchmark:
        question.status = "complete"
        question.stale = False

    previous = session.scalar(
        select(ForecastVersion)
        .where(ForecastVersion.question_id == question.id)
        .order_by(ForecastVersion.created_at.desc())
    )
    version = ForecastVersion(
        id=str(uuid.uuid4()),
        question_id=question.id,
        run_id=run.id,
        raw_track_probabilities_json=json.dumps(probabilities),
        ensemble_probability=result.aggregation.ensemble_probability,
        aggregation_json=json.dumps(jsonable(result.aggregation)),
        shrinkage=result.aggregation.shrinkage,
        track_spread=result.aggregation.track_spread,
        key_drivers_json=json.dumps(driver_acc),
        counterarguments_json=json.dumps(counter_acc),
        evidence_ids_json=json.dumps(list(dict.fromkeys(claim_ids))),
        trigger_event="graph_forecaster_v1",
        previous_version_id=previous.id if previous else None,
    )
    session.add(version)
    return version
