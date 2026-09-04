"""Durable preparation and single-review launch for the personal profile."""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forecastlab.budget import Budget
from forecastlab.budgeted_provider import BudgetedModelProvider
from forecastlab.contracts import QuestionCompiler
from forecastlab.deadline import ExecutionDeadline, check_deadline
from forecastlab.execution import ExecutionContext
from forecastlab.macro import MacroSpec
from forecastlab.profiles import load_profile
from forecastlab.prompts import PromptBundle, load_prompt_bundle
from forecastlab.providers.factory import build_model_provider
from forecastlab.root_event import contract_hash, digest
from forecastlab.schemas import ForecastContract, ForecastProfile
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.contracts import (
    apply_forecast_contract_review,
    approve_forecast_contract,
    forecast_contract_from_row,
    store_forecast_contract,
)
from forecastlab_api.jobs import enqueue_job
from forecastlab_api.models import ForecastContractRow, ForecastRun, ForecastRunAttempt, PersonalForecast, Question
from forecastlab_api.pipeline import apply_execution_limits, create_run_record, resolve_for_question
from forecastlab_api.secrets import load_secrets
from forecastlab_api.usage_ledger import PersistentUsageLedger

PROFILE = "root_event_ensemble_v1"


def envelope(session: Session, run: ForecastRun, *, request_key: str, request_hash: str,
             contract: ForecastContract | None = None, macro: MacroSpec | None = None) -> PersonalForecast:
    context = ExecutionContext.model_validate_json(run.execution_context_json)
    row = PersonalForecast(run_id=run.id, request_key=request_key, request_hash=request_hash,
        contract_id=contract.id if contract else None,
        contract_json=contract.model_dump_json() if contract else "{}",
        profile_json=apply_execution_limits(load_profile(run.profile_id), context).model_dump_json(),
        prompts_json=load_prompt_bundle().model_dump_json(), macro_json=macro.model_dump_json() if macro else "{}")
    session.add(row)
    return row


def draft_out(session: Session, run_id: str) -> dict:
    row = session.get(PersonalForecast, run_id)
    run = session.get(ForecastRun, run_id)
    if not row or not run:
        raise HTTPException(404, "Forecast draft not found")
    contract_row = session.get(ForecastContractRow, row.contract_id) if row.contract_id else None
    return {"run_id": run.id, "question_id": run.question_id, "status": run.status,
            "progress_message": run.progress_message, "error": run.error_message,
            "contract": forecast_contract_from_row(contract_row).model_dump(mode="json") if contract_row else None,
            "macro": json.loads(row.macro_json), "outcome_status": row.outcome_status,
            "result": json.loads(row.result_json), "cost_usd": run.total_cost_usd,
            "max_cost_usd": ForecastProfile.model_validate_json(row.profile_json).max_estimated_cost_usd}


def create_draft(session: Session, *, question: str, mode: str, as_of: datetime | None,
                 request_key: str, macro: MacroSpec | None = None) -> ForecastRun:
    fingerprint = digest({"question": question, "mode": mode, "as_of": as_of.isoformat() if as_of else None,
                          "macro": macro.model_dump(mode="json") if macro else None})
    existing = session.scalar(select(PersonalForecast).where(PersonalForecast.request_key == request_key))
    if existing:
        if existing.request_hash != fingerprint:
            raise HTTPException(409, "Request key already belongs to different inputs")
        prior = session.get(ForecastRun, existing.run_id)
        if prior is None:
            raise ValueError("draft_run_missing")
        return prior
    item = Question(id=str(uuid.uuid4()), original_text=question or "Macro forecast draft",
                    requested_mode=mode, requested_profile_id=PROFILE, requested_as_of=as_of, status="draft")
    session.add(item)
    session.flush()
    context = resolve_for_question(item)
    run = create_run_record(session, question=item, context=context, as_of=as_of, enqueue=False)
    run.status = "preparing"
    run.progress_stage = "preparation"
    run.progress_message = "Preparing the question for review"
    item.status = "draft"
    envelope(session, run, request_key=request_key, request_hash=fingerprint, macro=macro)
    job = enqueue_job(session, job_type="forecast_prepare", payload={"run_id": run.id},
                      idempotency_key=f"forecast_prepare:{run.id}")
    session.flush()
    run.job_id = job.id
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if session.scalar(select(PersonalForecast).where(PersonalForecast.request_key == request_key)) is None:
            raise
        return create_draft(session, question=question, mode=mode, as_of=as_of, request_key=request_key, macro=macro)
    return run


def prepare_draft(session: Session, run: ForecastRun, job=None) -> None:
    from forecastlab_api.db import SessionLocal
    record = session.get(PersonalForecast, run.id)
    if record is None:
        raise ValueError("personal_envelope_required")
    if record.contract_id:
        return
    context = ExecutionContext.model_validate_json(run.execution_context_json)
    profile = ForecastProfile.model_validate_json(record.profile_json)
    ledger = PersistentUsageLedger(SessionLocal, max_cost_usd=profile.max_estimated_cost_usd, max_tokens=profile.max_tokens)
    attempt = ledger.begin_attempt(run_id=run.id, job_id=run.job_id,
                                   attempt_number=-max(1, job.attempts if job else 1))
    preparations = session.scalars(select(ForecastRunAttempt).where(
        ForecastRunAttempt.run_id == run.id, ForecastRunAttempt.attempt_number < 0,
        ForecastRunAttempt.id != attempt.id)).all()
    prior_elapsed = sum(max(0, (as_utc(a.completed_at or utcnow()) - as_utc(a.started_at)).total_seconds())
                        for a in preparations)
    session.commit()
    ledger.deadline = ExecutionDeadline.after(profile.max_wall_clock_seconds - prior_elapsed)
    try:
        check_deadline(ledger, "prepare_contract")
        macro = MacroSpec.model_validate_json(record.macro_json) if record.macro_json != "{}" else None
        if macro:
            contract = macro.template(run.question_id, str(uuid.uuid4()))
        else:
            secrets = load_secrets()
            model = build_model_provider(provider=context.model_provider, api_key=secrets.get("model_api_key"),
                base_url=context.model_base_url, model=context.model_name, execution=context,
                timeout=context.model_timeout_seconds, ledger=ledger, run_id=run.id, run_attempt_id=attempt.id)
            budget = Budget.from_persisted(profile, ledger.totals(run.id), provider=context.model_provider,
                                           model=context.model_name, search_provider=context.search_provider,
                                           prior_elapsed_seconds=prior_elapsed)
            contract = QuestionCompiler(BudgetedModelProvider(model, budget, stage="prepare_contract"),
                prompt_bundle=PromptBundle.model_validate_json(record.prompts_json)).compile(
                run.question.original_text, question_id=run.question_id)
        check_deadline(ledger, "persist_prepared_contract")
        row = store_forecast_contract(session, contract)
        record.contract_id = row.id
        run.question.original_text = contract.original_question
        run.question.normalized_text = contract.normalized_question
        run.question.forecast_deadline = contract.resolution_date
        run.status = "awaiting_review"
        run.error_stage = None
        run.error_message = None
        record.outcome_status = None
        run.progress_message = "Review the event, deadline, and resolver"
        session.commit()
        ledger.finish_attempt(attempt.id, status="completed")
    except Exception as exc:
        session.rollback()
        ledger.finish_attempt(attempt.id, status="failed", error_category=type(exc).__name__, error_message=type(exc).__name__)
        run.status = "failed"
        run.error_stage = "preparation"
        run.error_message = f"{type(exc).__name__}: {str(exc)[:300]}"
        run.progress_message = "Question preparation failed; costs retained"
        record.outcome_status = "execution_failed"
        session.commit()
        raise


def launch_draft(session: Session, run_id: str, review: dict) -> ForecastRun:
    # The launch guard and job insertion share a transaction across processes.
    if session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    run = session.scalar(select(ForecastRun).where(ForecastRun.id == run_id).with_for_update())
    record = session.get(PersonalForecast, run_id)
    if run is None or record is None:
        raise HTTPException(404, "Forecast draft not found")
    if run.status in {"pending", "running", "completed"} and record.contract_json != "{}":
        session.commit()
        return run
    if run.status != "awaiting_review" or not record.contract_id:
        raise HTTPException(409, "Draft is not ready for review")
    row = session.get(ForecastContractRow, record.contract_id)
    if row is None:
        raise HTTPException(409, "Draft contract is missing")
    editable = {"normalized_question", "yes_condition", "no_condition", "resolution_date", "authoritative_source",
                "fallback_sources", "resolution_method", "ambiguity_notes", "cancellation_conditions", "resolver_risk_notes",
                "initial_reference_class", "suggested_drivers", "known_dependencies", "domain", "geography", "units"}
    if set(review) - editable:
        raise HTTPException(422, "Review contains non-editable contract fields")
    if record.macro_json != "{}" and review:
        current = forecast_contract_from_row(row).model_dump(mode="json")
        if any(value != current.get(key) for key, value in review.items()):
            raise HTTPException(422, "Change macro inputs in a new draft to keep measurement and contract consistent")
    try:
        reviewed = ForecastContract.model_validate({**forecast_contract_from_row(row).model_dump(), **review})
    except ValueError as exc:
        raise HTTPException(422, "Invalid contract review") from exc
    if reviewed.resolution_date is None or reviewed.resolution_date.tzinfo is None:
        raise HTTPException(422, "Resolution timestamp with timezone required")
    apply_forecast_contract_review(row, {key: getattr(reviewed, key) for key in review})
    approve_forecast_contract(session, row)
    contract = forecast_contract_from_row(row)
    if contract.resolution_date is None or (run.mode == "live" and contract.resolution_date <= utcnow()):
        raise HTTPException(422, "Live forecasts require a future resolution time")
    record.contract_json = contract.model_dump_json()
    snapshot = json.loads(run.execution_context_json)
    snapshot.update({"forecast_contract_id": contract.id, "root_contract_hash": contract_hash(contract)})
    run.execution_context_json = json.dumps(snapshot)
    run.status = "pending"
    run.progress_stage = "queued"
    run.progress_message = "Question approved; research queued"
    run.question.status = "running"
    job = enqueue_job(session, job_type="forecast_run", payload={"run_id": run.id},
                      idempotency_key=f"forecast_run:{run.id}")
    session.flush()
    run.job_id = job.id
    session.commit()
    return run


def rerun(session: Session, question: Question, *, mode: str, as_of: datetime | None) -> ForecastRun:
    from forecastlab_api.v1_execution import approved_contract_for_question
    contract = forecast_contract_from_row(approved_contract_for_question(session, question.id))
    if contract.resolution_date is None or (mode == "live" and contract.resolution_date <= utcnow()):
        raise HTTPException(422, "Live forecasts require a future resolution time")
    prior = session.scalar(select(PersonalForecast).join(ForecastRun).where(ForecastRun.question_id == question.id)
                           .order_by(PersonalForecast.created_at.desc()))
    macro = MacroSpec.model_validate_json(prior.macro_json) if prior and prior.macro_json != "{}" else None
    context = resolve_for_question(question, profile_id=PROFILE, mode=mode, as_of=as_of)
    run = create_run_record(session, question=question, context=context, as_of=as_of)
    envelope(session, run, request_key=f"rerun:{run.id}", request_hash=contract_hash(contract), contract=contract, macro=macro)
    session.commit()
    return run
