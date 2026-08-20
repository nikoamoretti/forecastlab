from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.engine import EngineResult
from forecastlab.schemas import ResolutionContract
from forecastlab.timeutil import parse_datetime, utcnow
from forecastlab_api.models import (
    EvidenceItem,
    ForecastRun,
    ForecastVersion,
    Question,
    ResearchTrack,
    ResolutionContractRow,
    Subquestion,
)


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return jsonable(asdict(value))
    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return str(value)


def save_contract(session: Session, question: Question, contract: ResolutionContract) -> ResolutionContractRow:
    row = question.contract
    payload = {
        "exact_yes": contract.exact_yes,
        "exact_no": contract.exact_no,
        "resolution_deadline": contract.resolution_deadline,
        "authoritative_source": contract.authoritative_source,
        "fallback_sources_json": json.dumps(contract.fallback_sources),
        "geography": contract.geography,
        "units": contract.units,
        "ambiguity_notes": contract.ambiguity_notes,
        "cancellation_conditions": contract.cancellation_conditions,
        "resolver_risk_notes": contract.resolver_risk_notes,
        "updated_at": utcnow(),
    }
    if row is None:
        row = ResolutionContractRow(id=str(uuid.uuid4()), question_id=question.id, version=1, **payload)
        session.add(row)
        question.contract = row
    else:
        for key, value in payload.items():
            setattr(row, key, value)
        row.version += 1
    question.normalized_text = contract.exact_yes
    question.status = "contract_ready"
    return row


def persist_engine_result(session: Session, run: ForecastRun, result: EngineResult) -> ForecastVersion | None:
    existing = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run.id))
    if existing is not None:
        run.status = "completed"
        run.finished_at = run.finished_at or utcnow()
        run.progress_pct = 100
        run.progress_stage = "report"
        run.progress_message = "Forecast ready"
        return existing

    run.finished_at = utcnow()
    run.cost_usd = float(result.budget.get("total_cost_usd") or result.budget.get("cost_usd") or 0)
    run.total_cost_usd = float(result.budget.get("total_cost_usd") or result.budget.get("cost_usd") or 0)
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
    run.disagreement_summary = result.disagreement_summary
    run.fixture_evidence_used = bool(result.fixture_evidence_used)
    run.progress_pct = 100
    if result.stopped_early:
        run.error_stage = result.stop_stage
        run.error_message = result.stop_reason

    question = run.question
    save_contract(session, question, result.contract)

    existing_tracks = {
        item.track_type: item
        for item in session.scalars(select(ResearchTrack).where(ResearchTrack.run_id == run.id)).all()
    }
    existing_evidence = session.scalars(select(EvidenceItem).where(EvidenceItem.run_id == run.id)).first()

    track_probs: dict[str, float | None] = {}
    driver_acc: list[Any] = []
    counter_acc: list[str] = []
    evidence_ids: list[str] = []

    for track in result.tracks:
        track_row = existing_tracks.get(track.track_type)
        if track_row is None:
            track_row = ResearchTrack(
                id=str(uuid.uuid4()),
                run_id=run.id,
                track_type=track.track_type,
                plan_json=json.dumps(jsonable(track.plan) if track.plan else {}),
                probability=track.forecast.probability if track.forecast else None,
                prior_probability=track.forecast.prior_probability if track.forecast else None,
                reasoning_summary=track.forecast.reasoning_summary if track.forecast else None,
                key_drivers_json=json.dumps(jsonable(track.forecast.key_drivers) if track.forecast else []),
                counterarguments_json=json.dumps(track.forecast.counterarguments if track.forecast else []),
                unresolved_json=json.dumps(track.forecast.unresolved_uncertainties if track.forecast else []),
                resolver_risk=track.forecast.resolver_risk if track.forecast else None,
                evidence_quality=track.forecast.evidence_quality if track.forecast else None,
                status="failed" if track.error else "completed",
                error_message=track.error,
                independent=True,
            )
            session.add(track_row)
            session.flush()
            if track.plan:
                for index, sub in enumerate(track.plan.subquestions):
                    session.add(
                        Subquestion(
                            id=str(uuid.uuid4()),
                            track_id=track_row.id,
                            text=sub.text,
                            purpose=sub.purpose,
                            preferred_source_types_json=json.dumps(sub.preferred_source_types),
                            search_queries_json=json.dumps(sub.search_queries),
                            expected_output=sub.expected_output,
                            relationship_to_forecast=sub.relationship_to_forecast,
                            sort_order=index,
                        )
                    )
            if existing_evidence is None:
                for item in track.evidence + track.rejected:
                    evidence_ids.append(item["id"])
                    session.add(
                        EvidenceItem(
                            id=item["id"],
                            run_id=run.id,
                            track_id=track_row.id,
                            subquestion=item.get("subquestion"),
                            url=item["url"],
                            title=item.get("title") or "",
                            publisher=item.get("publisher"),
                            excerpt=item.get("excerpt") or "",
                            content_hash=item.get("content_hash") or "",
                            source_class=item.get("source_class") or "secondary",
                            published_at=parse_datetime(item.get("published_at")),
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
        track_probs[track.track_type] = track.forecast.probability if track.forecast else None
        if track.forecast:
            driver_acc.extend(jsonable(track.forecast.key_drivers))
            counter_acc.extend(track.forecast.counterarguments)

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
        raw_track_probabilities_json=json.dumps(track_probs),
        ensemble_probability=result.aggregation.ensemble_probability,
        aggregation_json=json.dumps(jsonable(result.aggregation)),
        shrinkage=result.aggregation.shrinkage,
        track_spread=result.aggregation.track_spread,
        key_drivers_json=json.dumps(driver_acc),
        counterarguments_json=json.dumps(counter_acc),
        evidence_ids_json=json.dumps(evidence_ids),
        trigger_event="run",
        previous_version_id=previous.id if previous else None,
    )
    session.add(version)
    return version
