"""Prospective evaluation alongside the sealed, known-outcome historical system."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forecastlab.evaluation import brier_score, log_loss
from forecastlab.macro import SERIES, MacroSpec
from forecastlab.question_selection import event_key
from forecastlab.root_event import digest
from forecastlab.schemas import ForecastContract
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.autopilot_models import OfficialMacroOutcomeAmendment
from forecastlab_api.config import settings
from forecastlab_api.contracts import approve_forecast_contract, store_forecast_contract
from forecastlab_api.jobs import enqueue_job
from forecastlab_api.models import (
    ForecastContractRow,
    ForecastRun,
    ForecastVersion,
    PersonalForecast,
    ProspectiveAssignment,
    ProspectiveCohort,
    ProspectiveEntry,
    ProspectiveOutcome,
    Question,
)
from forecastlab_api.personal_forecasts import envelope
from forecastlab_api.pipeline import create_run_record, resolve_for_question
from forecastlab_api.question_suggestions import schedule_resolution_evidence, selection_sources

# Methods assigned to newly frozen cohorts.  Reports use the methods recorded
# in each cohort's frozen manifest, so earlier cohorts keep their own set.
# ``statistical_baseline_v1`` is deterministic and costs $0: it adds no model
# or search spend to the cohort budget.
METHODS = ("root_event_ensemble_v1", "single_model_forecaster_v1", "three_track_strict_forecaster_v1",
           "statistical_baseline_v1")


def forecasting_source_hash() -> str:
    from forecastlab_api.config import ROOT
    files = [path for prefix in ("packages/forecasting", "apps/api", "prompts", "configs/forecast_profiles", "configs/pricing")
             for path in (Path(ROOT) / prefix).rglob("*") if path.suffix in {".py", ".txt", ".yaml"}]
    files.extend(Path(ROOT) / name for name in ("uv.lock", "pyproject.toml"))
    return digest({str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(files)})


def check_assignment(session: Session, run: ForecastRun) -> None:
    assignment = session.scalar(select(ProspectiveAssignment).where(ProspectiveAssignment.run_id == run.id))
    if assignment is None:
        return
    entry = session.get(ProspectiveEntry, assignment.entry_id)
    cohort = session.get(ProspectiveCohort, assignment.cohort_id)
    if entry is None or cohort is None:
        raise ValueError("prospective_assignment_missing")
    manifest = json.loads(cohort.manifest_json)
    if cohort.status != "running":
        raise ValueError("prospective_cohort_not_launched")
    if digest(manifest) != cohort.manifest_hash or manifest.get("source_hash") != forecasting_source_hash():
        raise ValueError("prospective_frozen_code_or_manifest_changed")
    frozen_entry = next((item for item in manifest["entries"] if item["entry_id"] == entry.id), None)
    frozen_method = next((item for item in (frozen_entry or {}).get("methods", [])
                          if item["run_id"] == run.id and item["profile_id"] == assignment.profile_id), None)
    personal = session.get(PersonalForecast, run.id)
    if not frozen_entry or not frozen_method or not personal:
        raise ValueError("prospective_frozen_assignment_missing")
    context = json.loads(run.execution_context_json)
    if (json.loads(entry.contract_json) != frozen_entry["contract"]
            or json.loads(personal.contract_json) != frozen_entry["contract"]
            or json.loads(entry.macro_json) != frozen_entry["macro"]
            or json.loads(personal.macro_json) != frozen_entry["macro"]
            or as_utc(entry.cutoff).isoformat() != frozen_entry["cutoff"]
            or entry.release_event != frozen_entry["release_event"]
            or json.loads(personal.profile_json) != frozen_method["profile"]
            or digest(json.loads(personal.prompts_json)) != frozen_method["prompt_hash"]
            or run.profile_id != assignment.profile_id or run.mode != "live" or run.as_of is not None
            or any(context.get(key) != value for key, value in frozen_method["execution_context"].items())):
        raise ValueError("prospective_frozen_assignment_changed")
    if utcnow() >= as_utc(entry.cutoff):
        raise ValueError("prospective_forecast_cutoff_passed")


class CohortQuestionIn(BaseModel):
    macro: MacroSpec
    cutoff: datetime
    release_event: str = Field(min_length=1, max_length=255)

    @field_validator("cutoff")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("cutoff_timezone_required")
        return as_utc(value)


class CohortIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    questions: list[CohortQuestionIn] = Field(min_length=1, max_length=10)
    budget_usd: float = Field(default=150, gt=0, le=150, allow_inf_nan=False)


def create_cohort(session: Session, body: CohortIn) -> ProspectiveCohort:
    if len({digest(q.macro.model_dump(mode="json")) for q in body.questions}) != len(body.questions):
        raise HTTPException(422, "Duplicate macro contracts are not allowed")
    for q in body.questions:
        if not utcnow() < q.cutoff < q.macro.release_at:
            raise HTTPException(422, "Forecast cutoff must be in the future and before the release")
    cohort = ProspectiveCohort(id=str(uuid.uuid4()), name=body.name, budget_usd=body.budget_usd)
    session.add(cohort)
    session.flush()
    for item in body.questions:
        question = Question(id=str(uuid.uuid4()), original_text="Macro cohort question", requested_mode="live",
                            requested_profile_id=METHODS[0], status="draft", is_benchmark=True)
        session.add(question)
        session.flush()
        contract = item.macro.template(question.id, str(uuid.uuid4()))
        store_forecast_contract(session, contract)
        question.original_text = contract.original_question
        question.normalized_text = contract.normalized_question
        question.forecast_deadline = contract.resolution_date
        session.add(ProspectiveEntry(id=str(uuid.uuid4()), cohort_id=cohort.id, question_id=question.id,
            contract_json=contract.model_dump_json(), macro_json=item.macro.model_dump_json(),
            release_event=item.release_event, cutoff=item.cutoff))
    session.commit()
    return cohort


def entries_for(session: Session, cohort_id: str) -> list[ProspectiveEntry]:
    return list(session.scalars(select(ProspectiveEntry).where(ProspectiveEntry.cohort_id == cohort_id)
                               .order_by(ProspectiveEntry.id)).all())


def official_schedule_evidence(entries: list[ProspectiveEntry]) -> dict[str, dict]:
    """Verified official release-schedule evidence for BLS entries, keyed by entry id.

    Autopilot attaches the same deterministically verified schedule (retained
    DOL/BLS documents with hash checks) as each run's ``resolution`` evidence.
    Each value is either ``{"evidence": [...]}`` or ``{"gap": reason}``. FRED
    entries and releases absent from the verified calendar get a gap, so the
    root method keeps withholding unless research supplies resolution evidence.
    """
    specs = {entry.id: MacroSpec.model_validate_json(entry.macro_json) for entry in entries}
    bls = {entry_id: spec for entry_id, spec in specs.items() if SERIES[spec.indicator]["source"] == "bls"}
    result: dict[str, dict] = {entry_id: {"gap": "official_schedule_not_applicable"}
                               for entry_id in specs if entry_id not in bls}
    if not bls:
        return result
    try:
        sources = selection_sources()
    except Exception:  # Network or source failure must not block a freeze.
        return result | {entry_id: {"gap": "official_schedule_sources_unavailable"} for entry_id in bls}
    for entry_id, spec in bls.items():
        family, period = event_key(spec)
        release = next((item for item in sources.releases if item.family == family and item.observation_period == period
                        and as_utc(item.release_at) == as_utc(spec.release_at)), None)
        if release is None:
            result[entry_id] = {"gap": "official_schedule_release_not_verified"}
            continue
        try:
            result[entry_id] = {"evidence": schedule_resolution_evidence(release, sources)}
        except HTTPException:
            result[entry_id] = {"gap": "official_schedule_documents_unverified"}
    return result


def freeze_cohort(session: Session, cohort_id: str, *, reviewed_by: str) -> ProspectiveCohort:
    # Retrieve public schedule documents before taking the write lock.
    pending = session.get(ProspectiveCohort, cohort_id)
    schedules = (official_schedule_evidence(entries_for(session, cohort_id))
                 if settings.cohort_schedule_evidence and pending is not None and pending.status == "draft" else {})
    if session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    cohort = session.scalar(select(ProspectiveCohort).where(ProspectiveCohort.id == cohort_id).with_for_update())
    if not cohort:
        raise HTTPException(404, "Cohort not found")
    if cohort.status != "draft":
        session.commit()
        return cohort
    entries = entries_for(session, cohort.id)
    manifest: dict = {"schema_version": "prospective_v1", "reviewed_by": reviewed_by,
                      "methods": METHODS, "budget_usd": cohort.budget_usd, "entries": [],
                      "source_hash": forecasting_source_hash(),
                      "information_policy": "Live evidence; every forecast must finish before its preregistered cutoff and event release."}
    for entry in entries:
        if as_utc(entry.cutoff) <= utcnow():
            raise HTTPException(422, "Forecast cutoff has already passed; create a new cohort")
        contract = ForecastContract.model_validate_json(entry.contract_json)
        row = session.get(ForecastContractRow, contract.id)
        approve_forecast_contract(session, row)
        contract = contract.model_copy(update={"status": "approved"})
        entry.contract_json = contract.model_dump_json()
        question = session.get(Question, entry.question_id)
        schedule = schedules.get(entry.id, {"gap": "official_schedule_lookup_disabled"})
        methods = []
        for profile_id in METHODS:
            context = resolve_for_question(question, profile_id=profile_id, mode="live")
            run = create_run_record(session, question=question, context=context, as_of=None, enqueue=False)
            run.status = "awaiting_cohort_launch"
            frozen = envelope(session, run, request_key=f"cohort:{cohort.id}:{entry.id}:{profile_id}",
                request_hash=digest({"contract": entry.contract_json, "method": profile_id}), contract=contract,
                macro=MacroSpec.model_validate_json(entry.macro_json))
            frozen_result: dict = {"forecast_cutoff": as_utc(entry.cutoff).isoformat()}
            if schedule.get("evidence"):
                frozen_result["resolution_evidence"] = schedule["evidence"]
            frozen.result_json = json.dumps(frozen_result)
            session.add(ProspectiveAssignment(id=str(uuid.uuid4()), cohort_id=cohort.id,
                entry_id=entry.id, profile_id=profile_id, run_id=run.id))
            methods.append({"profile_id": profile_id, "run_id": run.id,
                "execution_context": context.model_dump(mode="json"),
                "profile": json.loads(frozen.profile_json), "prompt_hash": digest(json.loads(frozen.prompts_json))})
        manifest["entries"].append({"entry_id": entry.id, "contract": contract.model_dump(mode="json"),
            "macro": json.loads(entry.macro_json), "cutoff": as_utc(entry.cutoff).isoformat(),
            "release_event": entry.release_event, "methods": methods,
            "official_schedule": ({"claim_ids": [item["claim_id"] for item in schedule["evidence"]],
                                   "source_urls": [item["url"] for item in schedule["evidence"]],
                                   "source_sha256": [item["artifact"]["sha256"] for item in schedule["evidence"]]}
                                  if schedule.get("evidence") else {"gap": schedule["gap"]})})
    cohort.manifest_json = json.dumps(manifest, sort_keys=True)
    cohort.manifest_hash = digest(manifest)
    cohort.frozen_at = utcnow()
    cohort.status = "frozen"
    session.commit()
    return cohort


def launch_cohort(session: Session, cohort_id: str) -> None:
    if session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    cohort = session.scalar(select(ProspectiveCohort).where(ProspectiveCohort.id == cohort_id).with_for_update())
    if not cohort:
        raise HTTPException(404, "Cohort not found")
    if cohort.status in {"running", "awaiting_resolution", "scored"}:
        session.commit()
        return
    if cohort.status != "frozen" or digest(json.loads(cohort.manifest_json)) != cohort.manifest_hash:
        raise HTTPException(409, "Frozen cohort manifest required")
    if json.loads(cohort.manifest_json).get("source_hash") != forecasting_source_hash():
        raise HTTPException(409, "Forecasting code changed after freeze; create a new cohort")
    if any(as_utc(entry.cutoff) <= utcnow() for entry in entries_for(session, cohort.id)):
        raise HTTPException(422, "Forecast cutoff passed")
    assignments = session.scalars(select(ProspectiveAssignment).where(ProspectiveAssignment.cohort_id == cohort.id)).all()
    for assignment in assignments:
        run = session.get(ForecastRun, assignment.run_id)
        if run is None:
            raise ValueError("prospective_assignment_run_missing")
        run.status = "pending"
        job = enqueue_job(session, job_type="forecast_run", payload={"run_id": run.id},
                          idempotency_key=f"forecast_run:{run.id}")
        session.flush()
        run.job_id = job.id
    cohort.status = "running"
    session.commit()


def record_outcome(session: Session, entry_id: str, *, outcome: int | None, source_url: str,
                   evidence: str, confirmed_by: str) -> ProspectiveOutcome:
    if session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    entry = session.scalar(select(ProspectiveEntry).where(ProspectiveEntry.id == entry_id).with_for_update())
    if entry is None:
        raise HTTPException(404, "Prospective question not found")
    contract = ForecastContract.model_validate_json(entry.contract_json)
    cohort = session.get(ProspectiveCohort, entry.cohort_id)
    if cohort is None or cohort.status == "draft" or utcnow() < as_utc(contract.resolution_date):
        raise HTTPException(409, "Outcome cannot be recorded before resolution")
    if outcome not in (None, 0, 1) or not source_url.startswith("https://") or not evidence.strip() or not confirmed_by.strip():
        raise HTTPException(422, "Outcome, source, evidence and human confirmation are required")
    revision = session.scalar(select(func.max(ProspectiveOutcome.revision)).where(ProspectiveOutcome.entry_id == entry.id)) or 0
    row = ProspectiveOutcome(id=str(uuid.uuid4()), entry_id=entry.id, revision=revision + 1,
        outcome=outcome, source_url=source_url, evidence=evidence, confirmed_by=confirmed_by, created_at=utcnow())
    session.add(row)
    session.commit()
    return row


def cohort_report(session: Session, cohort_id: str) -> dict:
    cohort = session.get(ProspectiveCohort, cohort_id)
    if not cohort:
        raise HTTPException(404, "Cohort not found")
    questions = []
    for entry in entries_for(session, cohort.id):
        outcome = session.scalar(select(ProspectiveOutcome).where(ProspectiveOutcome.entry_id == entry.id)
                                 .order_by(ProspectiveOutcome.revision.desc()).limit(1))
        amendment = session.scalar(select(OfficialMacroOutcomeAmendment).where(
            OfficialMacroOutcomeAmendment.prospective_entry_id == entry.id
        ).order_by(OfficialMacroOutcomeAmendment.revision.desc()).limit(1))
        assignments = session.scalars(select(ProspectiveAssignment).where(ProspectiveAssignment.entry_id == entry.id)).all()
        cells = []
        for assignment in assignments:
            run = session.get(ForecastRun, assignment.run_id)
            if run is None:
                raise ValueError("prospective_assignment_run_missing")
            personal = session.get(PersonalForecast, run.id)
            if personal is None:
                raise ValueError("prospective_assignment_envelope_missing")
            version = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run.id))
            on_time = run.finished_at is not None and as_utc(run.finished_at) <= as_utc(entry.cutoff)
            probability = (version.ensemble_probability if version and run.status == "completed" and on_time
                           and personal.outcome_status == "forecasted" else None)
            outcome_value = outcome.outcome if outcome else None
            status = ("missed_cutoff" if run.finished_at and not on_time else
                      (personal.outcome_status or run.status))
            cells.append({"run_id": run.id, "method": assignment.profile_id, "status": status,
                "probability": probability, "cost_usd": run.total_cost_usd,
                "latency_ms": run.latency_ms if run.finished_at else None,
                "brier_score": brier_score(probability, outcome_value) if probability is not None and outcome_value is not None else None,
                "log_loss": log_loss(probability, outcome_value) if probability is not None and outcome_value is not None else None})
        questions.append({"id": entry.id, "question_id": entry.question_id, "contract": json.loads(entry.contract_json),
            "cutoff": as_utc(entry.cutoff).isoformat(), "release_event": entry.release_event, "cells": cells,
            "outcome": None if not outcome else {"value": outcome.outcome, "revision": outcome.revision,
                "source_url": outcome.source_url, "evidence": outcome.evidence, "confirmed_by": outcome.confirmed_by},
            "official_outcome_amendment": None if not amendment else {
                "id": amendment.id, "revision": amendment.revision, "status": amendment.status,
                "policy_version": amendment.policy_version, "source_url": amendment.source_url,
                "source_sha256": amendment.source_sha256, "outcome_known_at": amendment.outcome_known_at,
                "retrieved_at": amendment.retrieved_at, "parser_version": amendment.parser_version,
                "exception_code": amendment.exception_code,
            }})
    cohort_methods = tuple(json.loads(cohort.manifest_json).get("methods") or METHODS) if cohort.manifest_json else METHODS
    matched = [q for q in questions if len(q["cells"]) == len(cohort_methods)
               and all(c["brier_score"] is not None for c in q["cells"])]
    methods = []
    for method in cohort_methods:
        cells = [c for q in questions for c in q["cells"] if c["method"] == method]
        scores = [c for q in matched for c in q["cells"] if c["method"] == method]
        latencies = [c["latency_ms"] for c in cells if c["latency_ms"] is not None]
        methods.append({"method": method, "assigned": len(cells), "forecasted": sum(c["probability"] is not None for c in cells),
            "abstentions": sum(c["status"] == "insufficient_evidence" for c in cells),
            "failures": sum(c["status"] in {"failed", "execution_failed", "missed_cutoff"} for c in cells),
            "matched_resolved_count": len(scores), "cost_usd": sum(c["cost_usd"] for c in cells),
            "mean_latency_ms": sum(latencies) / len(latencies) if latencies else None,
            "forecast_coverage": sum(c["probability"] is not None for c in cells) / len(cells) if cells else None,
            "brier_score": sum(c["brier_score"] for c in scores) / len(scores) if scores else None,
            "log_loss": sum(c["log_loss"] for c in scores) / len(scores) if scores else None})
    cells = [c for q in questions for c in q["cells"]]
    done = bool(cells) and all(c["status"] in {"completed", "forecasted", "insufficient_evidence", "failed", "execution_failed", "missed_cutoff"} for c in cells)
    phase = ("scored" if done and all(q["outcome"] is not None for q in questions)
             else "awaiting_resolution" if done else cohort.status)
    return {"id": cohort.id, "name": cohort.name, "status": phase, "budget_usd": cohort.budget_usd,
        "cost_usd": sum(c["cost_usd"] for c in cells), "manifest_hash": cohort.manifest_hash,
        "questions": questions, "methods": methods, "matched_resolved_count": len(matched),
        "release_event_count": len({q["release_event"] for q in questions}),
        "interpretation": "Operational pilot; small and potentially correlated sample, no accuracy or calibration claim."}
