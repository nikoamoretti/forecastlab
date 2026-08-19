from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from forecastlab.engine import operationalize_only, run_forecast_engine
from forecastlab.providers.factory import build_model_provider
from forecastlab.providers.search import build_search_provider
from forecastlab.schemas import ResolutionContract
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.config import settings
from forecastlab_api.jobs import enqueue_job, heartbeat
from forecastlab_api.models import ForecastRun, Question
from forecastlab_api.persist import persist_engine_result, save_contract
from forecastlab_api.secrets import load_secrets


def start_run(
    session: Session,
    *,
    question: Question,
    profile_id: str,
    mode: str,
    as_of: datetime | None,
) -> ForecastRun:
    run = ForecastRun(
        id=str(uuid.uuid4()),
        question_id=question.id,
        profile_id=profile_id,
        mode=mode,
        as_of=as_of,
        status="pending",
        progress_stage="queued",
        progress_message="Waiting for worker",
    )
    session.add(run)
    session.flush()
    job = enqueue_job(
        session,
        job_type="forecast_run",
        payload={"run_id": run.id},
        idempotency_key=f"forecast_run:{run.id}",
    )
    run.job_id = job.id
    question.status = "running"
    return run


def execute_run(session: Session, run: ForecastRun, job=None) -> None:
    secrets = load_secrets()
    question = run.question
    model = build_model_provider(
        provider=str(secrets.get("model_provider") or "mock"),
        api_key=secrets.get("model_api_key"),
        base_url=secrets.get("model_base_url"),
        model=str(secrets.get("model_name") or "mock-forecast-v1"),
        timeout=float(secrets.get("model_timeout_seconds") or 60),
    )
    search = build_search_provider(
        str(secrets.get("search_provider") or "mock"),
        secrets.get("search_api_key"),
    )
    contract = None
    if question.contract:
        contract = ResolutionContract(
            exact_yes=question.contract.exact_yes,
            exact_no=question.contract.exact_no,
            resolution_deadline=question.contract.resolution_deadline,
            authoritative_source=question.contract.authoritative_source,
            fallback_sources=json.loads(question.contract.fallback_sources_json or "[]"),
            geography=question.contract.geography,
            units=question.contract.units,
            ambiguity_notes=question.contract.ambiguity_notes,
            cancellation_conditions=question.contract.cancellation_conditions,
            resolver_risk_notes=question.contract.resolver_risk_notes,
        )
    run.started_at = utcnow()
    run.status = "running"
    run.provider_json = json.dumps(
        {
            "model_provider": getattr(model, "name", "unknown"),
            "search_provider": getattr(search, "name", "unknown"),
            "model_name": secrets.get("model_name"),
        }
    )

    def progress(stage: str, message: str, pct: float, extra=None) -> None:
        run.progress_stage = stage
        run.progress_message = message
        run.progress_pct = pct
        if job is not None:
            heartbeat(session, job, stage=stage, message=message, pct=pct)
        session.commit()

    try:
        result = run_forecast_engine(
            question=question.original_text,
            contract=contract,
            profile_id=run.profile_id,
            mode=run.mode,
            as_of=run.as_of,
            model=model,
            search=search,
            allow_local_fixtures=settings.allow_local_fixtures,
            progress=progress,
        )
        persist_engine_result(session, run, result)
        if run.started_at:
            run.latency_ms = int((utcnow() - as_utc(run.started_at)).total_seconds() * 1000)
        session.commit()
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)[:500]
        run.error_stage = run.progress_stage
        run.finished_at = utcnow()
        session.commit()
        raise


def operationalize_question(session: Session, question: Question) -> ResolutionContract:
    secrets = load_secrets()
    model = build_model_provider(
        provider=str(secrets.get("model_provider") or "mock"),
        api_key=secrets.get("model_api_key"),
        base_url=secrets.get("model_base_url"),
        model=str(secrets.get("model_name") or "mock-forecast-v1"),
        timeout=float(secrets.get("model_timeout_seconds") or 60),
    )
    contract = operationalize_only(question=question.original_text, model=model)
    save_contract(session, question, contract)
    return contract


create_run = start_run
