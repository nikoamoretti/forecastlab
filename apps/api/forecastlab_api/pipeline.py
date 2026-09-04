from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.engine import operationalize_only, run_forecast_engine
from forecastlab.errors import ConfigurationError, GraphForecastExecutionError
from forecastlab.execution import ExecutionContext, resolve_execution_context
from forecastlab.graph_execution import run_graph_forecast_engine
from forecastlab.pricing import load_pricing
from forecastlab.profiles import load_profile
from forecastlab.prompts import PromptBundle
from forecastlab.providers.base import SearchProvider
from forecastlab.providers.factory import build_model_provider
from forecastlab.providers.search import build_search_provider
from forecastlab.run_cache import DocumentStore, RunCache
from forecastlab.schemas import ForecastProfile, ResolutionContract
from forecastlab.single_model import run_single_model_forecast
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.config import settings
from forecastlab_api.jobs import enqueue_job, heartbeat, touch_job_lease, touch_worker_standalone
from forecastlab_api.models import ForecastRun, ForecastRunAttempt, ForecastVersion, PersonalForecast, Question
from forecastlab_api.persist import persist_engine_result, save_contract
from forecastlab_api.secrets import load_secrets
from forecastlab_api.usage_ledger import PersistentUsageLedger, apply_totals_to_run


def provider_settings_from_secrets(data: dict | None = None) -> dict:
    secrets = data or load_secrets()
    return {
        "model_provider": secrets.get("model_provider") or "mock",
        "model_name": secrets.get("model_name") or "mock-forecast-v1",
        "model_base_url": secrets.get("model_base_url"),
        "model_api_key": secrets.get("model_api_key"),
        "search_provider": secrets.get("search_provider") or "mock",
        "search_api_key": secrets.get("search_api_key"),
        "max_cost_usd": float(secrets.get("max_cost_usd") or 5.0),
        "model_timeout_seconds": float(secrets.get("model_timeout_seconds") or 60.0),
    }


def apply_execution_limits(profile: ForecastProfile, context: ExecutionContext) -> ForecastProfile:
    return profile.model_copy(
        update={
            "max_estimated_cost_usd": context.effective_max_cost_usd,
            "max_tokens": context.effective_max_tokens,
            "max_model_calls": context.effective_max_model_calls,
            "max_search_calls": context.effective_max_search_calls,
            "max_fetched_documents": context.effective_max_fetched_documents,
            "max_wall_clock_seconds": context.effective_max_wall_clock_seconds,
        }
    )


def apply_execution_context(run: ForecastRun, context: ExecutionContext) -> None:
    run.execution_context_json = context.model_dump_json()
    run.configuration_hash = context.configuration_hash
    run.evidence_policy = context.evidence_policy
    run.fixture_evidence_used = context.fixture_evidence_used
    run.code_commit = context.code_commit
    run.synthetic_fixture_run = context.synthetic_fixture_run
    run.profile_id = context.profile_id
    run.mode = context.effective_mode
    run.provider_json = json.dumps(
        {
            "model_provider": context.model_provider,
            "model_name": context.model_name,
            "model_is_mock": context.model_is_mock,
            "search_provider": context.search_provider,
            "search_is_mock": context.search_is_mock,
        }
    )


def resolve_for_question(
    question: Question,
    *,
    profile_id: str | None = None,
    mode: str | None = None,
    as_of: datetime | None = None,
    synthetic_fixture_run: bool = False,
    settings_data: dict | None = None,
) -> ExecutionContext:
    return resolve_execution_context(
        requested_mode=mode or question.requested_mode or "demo",  # type: ignore[arg-type]
        profile_id=profile_id or question.requested_profile_id or "three_track_ensemble",
        settings=provider_settings_from_secrets(settings_data),
        synthetic_fixture_run=synthetic_fixture_run,
        as_of=as_of if as_of is not None else question.requested_as_of,
    )


HEARTBEAT_INTERVAL_SECONDS = 8.0


def create_run_record(
    session: Session,
    *,
    question: Question,
    context: ExecutionContext,
    as_of: datetime | None,
    enqueue: bool = True,
    benchmark_task_id: str | None = None,
) -> ForecastRun:
    run = ForecastRun(
        id=str(uuid.uuid4()),
        question_id=question.id,
        profile_id=context.profile_id,
        mode=context.effective_mode,
        as_of=as_of,
        status="pending",
        progress_stage="queued",
        progress_message="Waiting for worker",
        benchmark_task_id=benchmark_task_id,
    )
    apply_execution_context(run, context)
    session.add(run)
    session.flush()
    if not question.is_benchmark:
        # Manual evidence is attached to a specific new run without creating a
        # claim. Graph research may later submit it to the ordinary extractor.
        from forecastlab_api.manual_evidence import attach_manual_evidence_to_run

        attach_manual_evidence_to_run(session, run=run)
    if enqueue:
        job = enqueue_job(
            session,
            job_type="forecast_run",
            payload={"run_id": run.id},
            idempotency_key=f"forecast_run:{run.id}",
        )
        run.job_id = job.id
    if not question.is_benchmark:
        question.status = "running"
    return run


def start_run(
    session: Session,
    *,
    question: Question,
    profile_id: str,
    mode: str,
    as_of: datetime | None,
    synthetic_fixture_run: bool = False,
    enqueue: bool = True,
) -> ForecastRun:
    context = resolve_for_question(
        question,
        profile_id=profile_id,
        mode=mode,
        as_of=as_of,
        synthetic_fixture_run=synthetic_fixture_run,
    )
    return create_run_record(session, question=question, context=context, as_of=as_of, enqueue=enqueue)


def execute_run(
    session: Session,
    run: ForecastRun,
    job=None,
    *,
    profile: ForecastProfile | None = None,
    prompt_bundle: PromptBundle | None = None,
    model_timeout: float | None = None,
    pricing_catalog: dict | None = None,
    search_provider_override: SearchProvider | None = None,
    document_store: DocumentStore | None = None,
) -> None:
    secrets = load_secrets()
    question = run.question
    personal = session.get(PersonalForecast, run.id)
    if personal is not None:
        profile = ForecastProfile.model_validate_json(personal.profile_json)
        prompt_bundle = PromptBundle.model_validate_json(personal.prompts_json)
    existing_version = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run.id))
    if existing_version is not None:
        run.status = "completed"
        run.finished_at = run.finished_at or utcnow()
        run.progress_pct = 100
        run.progress_stage = "report"
        run.progress_message = "Probability withheld; review evidence gaps" if personal and personal.outcome_status == "insufficient_evidence" else "Forecast ready"
        session.commit()
        return
    if personal:
        from forecastlab_api.prospective import check_assignment
        check_assignment(session, run)
    raw_context = json.loads(run.execution_context_json or "{}")
    if raw_context:
        context = ExecutionContext.model_validate(raw_context)
    else:
        context = resolve_for_question(
            question,
            profile_id=run.profile_id,
            mode=run.mode,
            as_of=as_utc(run.as_of) if run.as_of else None,
            synthetic_fixture_run=run.synthetic_fixture_run,
            settings_data=secrets,
        )
        apply_execution_context(run, context)
    if context.effective_mode == "live" and context.model_is_mock:
        raise ConfigurationError(["live_run_mock_model_violation"])
    if context.effective_mode == "live" and context.search_is_mock:
        raise ConfigurationError(["live_run_mock_search_violation"])
    timeout = (
        model_timeout
        if model_timeout is not None
        else float(context.model_timeout_seconds or secrets.get("model_timeout_seconds") or 60)
    )
    catalog = pricing_catalog if pricing_catalog is not None else load_pricing()
    if profile is None:
        profile = apply_execution_limits(load_profile(context.profile_id), context)
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
    if personal and personal.contract_json != "{}":
        from forecastlab.schemas import ForecastContract
        contract = ForecastContract.model_validate_json(personal.contract_json).to_resolution_contract()
    run.started_at = run.started_at or utcnow()
    run.status = "running"
    if personal:
        run.error_stage = None
        run.error_message = None
        personal.outcome_status = None
    session.commit()
    from forecastlab_api.db import SessionLocal

    ledger = PersistentUsageLedger(
        SessionLocal,
        max_cost_usd=profile.max_estimated_cost_usd,
        max_tokens=profile.max_tokens,
    )
    attempt_number = job.attempts if job is not None else max(1, int(run.run_attempt_count or 0) + 1)
    attempt = ledger.begin_attempt(run_id=run.id, job_id=job.id if job is not None else None, attempt_number=attempt_number)
    model = build_model_provider(
        provider=context.model_provider,
        api_key=secrets.get("model_api_key"),
        base_url=context.model_base_url,
        model=context.model_name,
        timeout=timeout,
        execution=context,
        ledger=ledger,
        run_id=run.id,
        run_attempt_id=attempt.id,
        pricing_catalog=catalog,
    )
    search = search_provider_override or build_search_provider(
        context.search_provider,
        secrets.get("search_api_key"),
        execution=context,
        ledger=ledger,
        run_id=run.id,
        run_attempt_id=attempt.id,
        pricing_catalog=catalog,
    )
    frozen_cache = (
        RunCache.create(
            run_id=run.id,
            model_provider=context.model_provider,
            search_provider=search.name,
            mode=context.effective_mode,
            as_of=as_utc(run.as_of) if run.as_of else None,
            configuration_hash=context.configuration_hash,
            document_store=document_store,
        )
        if document_store is not None
        else None
    )
    stop_heartbeat = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_heartbeat.wait(HEARTBEAT_INTERVAL_SECONDS):
            touch_worker_standalone("running")
            if job is not None:
                touch_job_lease(job.id)

    worker = threading.Thread(target=heartbeat_loop, daemon=True)
    worker.start()

    def progress(stage: str, message: str, pct: float, extra=None) -> None:
        run.progress_stage = stage
        run.progress_message = message
        run.progress_pct = pct
        if job is not None:
            heartbeat(session, job, stage=stage, message=message, pct=pct)
        session.commit()

    prior_elapsed = 0.0
    if run.started_at:
        prior_elapsed = max(0.0, (utcnow() - as_utc(run.started_at)).total_seconds())
    if personal:
        preparations = session.scalars(select(ForecastRunAttempt).where(
            ForecastRunAttempt.run_id == run.id, ForecastRunAttempt.attempt_number < 0)).all()
        prior_elapsed += sum(max(0, (as_utc(a.completed_at) - as_utc(a.started_at)).total_seconds())
                             for a in preparations if a.completed_at is not None)
        # Finish the read transaction before concurrent provider-ledger writes.
        session.commit()
    try:
        if profile.execution_strategy == "root_event_ensemble_v1":
            from forecastlab_api.root_executor import execute_root_forecast
            execute_root_forecast(session, run=run, profile=profile, context=context, model=model,
                search=search, ledger=ledger, progress=progress, cache=frozen_cache,
                prior_elapsed_seconds=prior_elapsed)
            fixture_evidence_used = bool(context.fixture_evidence_allowed)
            forecast_contract_id = personal.contract_id if personal else None
            forecast_graph_id = json.loads(run.execution_context_json).get("forecast_graph_id")
        elif profile.execution_strategy == "graph_nodes":
            if profile.graph_aggregation_enabled:
                from forecastlab_api.graph_executor import GraphForecastExecutor

                progress("graph", "Resolving the approved Forecast Contract and Forecast Graph", 0.08)
                GraphForecastExecutor(
                    session,
                    run=run,
                    profile=profile,
                    execution=context,
                    model=model,
                    search=search,
                    allow_local_fixtures=(
                        context.fixture_evidence_allowed and settings.allow_local_fixtures
                    ),
                    progress=progress,
                    prompt_bundle=prompt_bundle,
                    ledger=ledger,
                    pricing_catalog=catalog,
                    prior_elapsed_seconds=prior_elapsed,
                    cache=frozen_cache,
                ).execute()
                fixture_evidence_used = run.fixture_evidence_used
                stored_context = json.loads(run.execution_context_json or "{}")
                forecast_contract_id = stored_context.get("forecast_contract_id")
                forecast_graph_id = stored_context.get("forecast_graph_id")
            else:
                from forecastlab_api.research_plans import store_research_plan
                from forecastlab_api.v1_execution import (
                    ensure_execution_graph,
                    persist_graph_engine_result,
                    persist_node_research,
                    record_execution_graph_resolution,
                )

                progress("graph", "Resolving the approved Forecast Contract and Forecast Graph", 0.08)
                graph_resolution = ensure_execution_graph(
                    session,
                    question=question,
                    model=model,
                    max_output_tokens=profile.max_output_tokens_per_call,
                    prompt_bundle=prompt_bundle,
                )
                forecast_contract = graph_resolution.contract
                forecast_graph = graph_resolution.graph
                record_execution_graph_resolution(run, graph_resolution)
                session.commit()
                result = run_graph_forecast_engine(
                    contract=forecast_contract,
                    graph=forecast_graph,
                    profile_id=context.profile_id,
                    mode=context.effective_mode,
                    as_of=as_utc(run.as_of) if run.as_of else None,
                    model=model,
                    search=search,
                    allow_local_fixtures=context.fixture_evidence_allowed and settings.allow_local_fixtures,
                    progress=progress,
                    profile=profile,
                    execution=context,
                    prompt_bundle=prompt_bundle,
                    cache=frozen_cache,
                    run_id=run.id,
                    ledger=ledger,
                    pricing_catalog=catalog,
                    prior_elapsed_seconds=prior_elapsed,
                    persist_research=lambda _node, evidence, rejected, claims: persist_node_research(
                        session,
                        run=run,
                        evidence=evidence,
                        rejected=rejected,
                        claims=claims,
                    ),
                    persist_research_plan=lambda plan: store_research_plan(
                        session,
                        plan,
                    ),
                )
                persist_graph_engine_result(session, run, result)
                fixture_evidence_used = bool(result.fixture_evidence_used)
                forecast_contract_id = result.contract.id
                forecast_graph_id = result.graph.id
        elif profile.execution_strategy == "single_model":
            from forecastlab_api.contracts import forecast_contract_from_row
            from forecastlab_api.v1_execution import approved_contract_for_question

            progress("contract", "Resolving the approved Forecast Contract", 0.08)
            forecast_contract = forecast_contract_from_row(
                approved_contract_for_question(session, question.id)
            )
            if personal and personal.contract_json != "{}":
                from forecastlab.schemas import ForecastContract
                forecast_contract = ForecastContract.model_validate_json(personal.contract_json)
            result = run_single_model_forecast(
                contract=forecast_contract,
                profile_id=context.profile_id,
                mode=context.effective_mode,
                as_of=as_utc(run.as_of) if run.as_of else None,
                model=model,
                search=search,
                allow_local_fixtures=(
                    context.fixture_evidence_allowed and settings.allow_local_fixtures
                ),
                progress=progress,
                profile=profile,
                execution=context,
                prompt_bundle=prompt_bundle,
                cache=frozen_cache,
                run_id=run.id,
                ledger=ledger,
                pricing_catalog=catalog,
                prior_elapsed_seconds=prior_elapsed,
            )
            persist_engine_result(session, run, result)
            fixture_evidence_used = bool(result.fixture_evidence_used)
            forecast_contract_id = forecast_contract.id
            forecast_graph_id = None
        else:
            result = run_forecast_engine(
                question=question.original_text,
                contract=contract,
                profile_id=context.profile_id,
                mode=context.effective_mode,
                as_of=as_utc(run.as_of) if run.as_of else None,
                model=model,
                search=search,
                allow_local_fixtures=context.fixture_evidence_allowed and settings.allow_local_fixtures,
                progress=progress,
                profile=profile,
                execution=context,
                prompt_bundle=prompt_bundle,
                cache=frozen_cache,
                run_id=run.id,
                ledger=ledger,
                pricing_catalog=catalog,
                prior_elapsed_seconds=prior_elapsed,
            )
            persist_engine_result(session, run, result)
            fixture_evidence_used = bool(result.fixture_evidence_used)
            forecast_contract_id = None
            forecast_graph_id = None
        apply_totals_to_run(run, ledger.totals(run.id))
        snapshot = context.model_dump(mode="json")
        try:
            persisted_snapshot = json.loads(run.execution_context_json or "{}")
        except json.JSONDecodeError:
            persisted_snapshot = {}
        snapshot.update(persisted_snapshot)
        snapshot["fixture_evidence_used"] = fixture_evidence_used
        if profile.execution_strategy == "graph_nodes":
            snapshot["forecast_contract_id"] = forecast_contract_id
            snapshot["forecast_graph_id"] = forecast_graph_id
        elif profile.execution_strategy == "single_model":
            snapshot["forecast_contract_id"] = forecast_contract_id
        run.execution_context_json = json.dumps(snapshot, sort_keys=True)
        run.fixture_evidence_used = fixture_evidence_used
        if personal and profile.execution_strategy != "root_event_ensemble_v1":
            version = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run.id))
            personal.outcome_status = "forecasted" if version and version.ensemble_probability is not None else "insufficient_evidence"
            personal.result_json = json.dumps({**json.loads(personal.result_json), "schema_version": "personal_forecast_v1", "outcome_status": personal.outcome_status,
                "probability": version.ensemble_probability if version else None, "contract": json.loads(personal.contract_json)})
        if run.started_at:
            run.latency_ms = int((utcnow() - as_utc(run.started_at)).total_seconds() * 1000)
        session.commit()
        ledger.finish_attempt(attempt.id, status="completed")
    except Exception as exc:
        if personal:
            personal.outcome_status = "execution_failed"
            run.status = "failed"
            run.error_stage = type(exc).__name__
            run.error_message = str(exc)[:500]
            run.finished_at = utcnow()
            run.latency_ms = int((utcnow() - as_utc(run.started_at)).total_seconds() * 1000)
            session.commit()
        ledger.finish_attempt(
            attempt.id,
            status="failed",
            error_category=exc.__class__.__name__,
            error_message=str(exc),
        )
        apply_totals_to_run(run, ledger.totals(run.id))
        if isinstance(exc, GraphForecastExecutionError):
            if run.finished_at is None:
                run.finished_at = utcnow()
            if run.started_at is not None:
                run.latency_ms = max(
                    1,
                    int(
                        (as_utc(run.finished_at) - as_utc(run.started_at)).total_seconds()
                        * 1000
                    ),
                )
            session.commit()
        raise
    finally:
        stop_heartbeat.set()


def operationalize_question(session: Session, question: Question) -> ResolutionContract:
    secrets = load_secrets()
    context = resolve_for_question(question, settings_data=secrets)
    model = build_model_provider(
        provider=context.model_provider,
        api_key=secrets.get("model_api_key"),
        base_url=context.model_base_url or secrets.get("model_base_url"),
        model=context.model_name,
        timeout=float(secrets.get("model_timeout_seconds") or 60),
        execution=context,
    )
    contract = operationalize_only(question=question.original_text, model=model)
    save_contract(session, question, contract)
    return contract


create_run = start_run
