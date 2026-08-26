from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forecastlab.contracts import ForecastContractError
from forecastlab.errors import ConfigurationError, StructuredOutputError
from forecastlab.evaluation_releases import (
    EvaluationProviderIdentity,
    EvaluationReleaseQuestionInput,
    EvaluationSplit,
)
from forecastlab.evidence_claims import EvidenceClaimError
from forecastlab.execution import readiness, resolve_execution_context
from forecastlab.graphs import ForecastGraphError
from forecastlab.historical_evidence_releases import (
    HistoricalEvidenceDocumentInput,
    HistoricalEvidencePacketInput,
)
from forecastlab.logging import configure_logging
from forecastlab.profiles import list_profiles, load_profile, profile_hash
from forecastlab.providers.mock import SAMPLE_QUESTION
from forecastlab.schemas import ResolutionContract, SettingsPublic, SettingsUpdate
from forecastlab.ssrf import UnsafeURLError
from forecastlab.timeutil import as_utc, utcnow
from forecastlab.version import __version__
from forecastlab_api.aggregations import forecast_aggregation_from_row
from forecastlab_api.config import settings
from forecastlab_api.contracts import (
    apply_forecast_contract_review,
    approve_forecast_contract,
    build_question_compiler,
    forecast_contract_from_row,
    store_forecast_contract,
)
from forecastlab_api.db import SessionLocal, get_db
from forecastlab_api.demo import demo_payload_hash, get_indicator, simulate_indicator
from forecastlab_api.evaluation_datasets import (
    EvaluationDatasetValidationError,
    freeze_evaluation_dataset,
    import_evaluation_dataset,
    parse_evaluation_dataset_file,
    review_evaluation_dataset,
    serialize_evaluation_dataset,
)
from forecastlab_api.evaluation_releases import (
    EvaluationReleaseValidationError,
    create_evaluation_release,
    freeze_evaluation_release,
    get_blinded_execution_manifest,
    release_audit,
    review_evaluation_release,
    serialize_evaluation_release,
)
from forecastlab_api.evidence_claims import evidence_claim_from_row, evidence_for_node
from forecastlab_api.evidence_sufficiency import evidence_sufficiency_from_row
from forecastlab_api.experiments import (
    DEFAULT_EXPERIMENT_PROFILES,
    V1_EVALUATION_NOTICE,
    create_experiment,
    create_v1_evaluation,
    current_builtin_dataset,
    ensure_dataset,
    experiment_progress,
    experiment_summary,
    serialize_dataset,
    v1_evaluation_workflow,
)
from forecastlab_api.forecast_analysis import (
    build_forecast_research_analysis,
    classify_forecast_failure,
    failures_for_forecast_experiment_run,
    serialize_forecast_failure,
    update_forecast_failure_annotation,
)
from forecastlab_api.forecast_experiments import (
    CONTROLLED_FORECAST_PROFILES,
    create_forecast_experiment,
    forecast_experiment_progress,
    forecast_experiment_report,
)
from forecastlab_api.graphs import (
    build_graph_generator,
    forecast_graph_from_row,
    forecast_node_from_row,
    store_forecast_graph,
)
from forecastlab_api.historical_evidence_releases import (
    HistoricalEvidenceReleaseValidationError,
    create_historical_evidence_release,
    freeze_historical_evidence_release,
    get_historical_evidence_execution_manifest,
    review_historical_evidence_release,
    serialize_historical_evidence_release,
    verify_release_bundle,
)
from forecastlab_api.jobs import recover_stale_jobs
from forecastlab_api.material_node_coverage import material_node_coverage_from_row
from forecastlab_api.migrate import apply_schema
from forecastlab_api.models import (
    BenchmarkDataset,
    BenchmarkExperiment,
    BenchmarkQuestion,
    BenchmarkResult,
    EvaluationDataset,
    EvaluationRelease,
    EvidenceClaimRow,
    EvidenceItem,
    ForecastContractRow,
    ForecastExperiment,
    ForecastExperimentRun,
    ForecastGraphRow,
    ForecastNodeRow,
    ForecastNodeRunRow,
    ForecastRun,
    ForecastRunAttempt,
    ForecastVersion,
    GraphExecutionFailureRow,
    HistoricalEvidenceRelease,
    ProviderCallLedger,
    Question,
    ResearchPlanRow,
    ResearchTrack,
    Watch,
    WatchEvent,
    WorkerHeartbeat,
)
from forecastlab_api.persist import save_contract
from forecastlab_api.pipeline import create_run, execute_run, operationalize_question, provider_settings_from_secrets
from forecastlab_api.probes import test_model_connection, test_search_connection
from forecastlab_api.reports import build_v1_report, v1_report_markdown
from forecastlab_api.research_plans import research_plan_from_row
from forecastlab_api.scenario_synthesis import scenario_synthesis_from_row
from forecastlab_api.secrets import public_settings, update_settings
from forecastlab_api.seed import seed_sample_question, seed_synthetic_benchmarks, seed_v1_evaluation_benchmarks
from forecastlab_api.v1_execution import approved_contract_for_question, node_runs_for_run
from forecastlab_api.watches import attach_demo_watch, check_watch, validate_user_watch

configure_logging(settings.log_level)
app = FastAPI(title="ForecastLab", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin, "http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateQuestionIn(BaseModel):
    question: str
    notes: str | None = None
    forecast_deadline: datetime | None = None
    resolution_source: str | None = None
    profile_id: str = "three_track_ensemble"
    mode: str = "demo"
    as_of: datetime | None = None
    start: bool = False


class ContractIn(BaseModel):
    exact_yes: str
    exact_no: str
    resolution_deadline: datetime
    authoritative_source: str
    fallback_sources: list[str] = Field(default_factory=list)
    geography: str | None = None
    units: str | None = None
    ambiguity_notes: str = ""
    cancellation_conditions: str = ""
    resolver_risk_notes: str = ""


class GenerateContractIn(BaseModel):
    question: str
    mode: str = "demo"
    profile_id: str = "three_track_ensemble"
    as_of: datetime | None = None
    created_by: str = "user"


class ApproveContractIn(BaseModel):
    normalized_question: str | None = None
    yes_condition: str | None = None
    no_condition: str | None = None
    resolution_date: datetime | None = None
    authoritative_source: str | None = None
    fallback_sources: list[str] | None = None
    resolution_method: str | None = None
    ambiguity_notes: str | None = None
    cancellation_conditions: str | None = None
    resolver_risk_notes: str | None = None
    forecast_type: str | None = None
    geography: str | None = None
    units: str | None = None
    domain: str | None = None
    initial_reference_class: str | None = None
    suggested_drivers: list[str] | None = None
    known_dependencies: list[str] | None = None


class RunIn(BaseModel):
    profile_id: str = "three_track_ensemble"
    mode: str = "demo"
    as_of: datetime | None = None


class ExecuteV1In(BaseModel):
    mode: str = "demo"
    as_of: datetime | None = None


class WatchIn(BaseModel):
    endpoint_url: str
    endpoint_type: str = "json"
    json_path: str | None = "$.value"
    poll_seconds: int = 300
    auto_rerun: bool | None = None


class SimulateIn(BaseModel):
    value: float


class ExperimentIn(BaseModel):
    dataset_id: str
    profile_ids: list[str] = Field(default_factory=lambda: list(DEFAULT_EXPERIMENT_PROFILES))


class ForecastExperimentIn(BaseModel):
    dataset_id: str | None = None
    evaluation_release_id: str | None = None
    historical_evidence_release_id: str | None = None
    evaluation_split: EvaluationSplit | None = None
    profile_ids: list[str] = Field(
        default_factory=lambda: list(CONTROLLED_FORECAST_PROFILES)
    )
    synthetic_test: bool = False


class EvaluationReleaseIn(BaseModel):
    name: str
    version: str
    development_dataset_id: str
    validation_dataset_id: str
    test_dataset_id: str
    questions: list[EvaluationReleaseQuestionInput]
    provider_identity: EvaluationProviderIdentity
    correction_of_release_id: str | None = None
    correction_summary: str | None = None


class HistoricalEvidenceReleaseIn(BaseModel):
    evaluation_release_id: str
    name: str
    version: str
    documents: list[HistoricalEvidenceDocumentInput]
    packets: list[HistoricalEvidencePacketInput]
    correction_of_release_id: str | None = None
    correction_summary: str | None = None


class HistoricalEvidenceBundleRootIn(BaseModel):
    bundle_root: str


class ForecastFailureIn(BaseModel):
    category: str
    annotation: str
    created_by: str = "internal_reviewer"


class ForecastFailureAnnotationIn(BaseModel):
    annotation: str


@app.exception_handler(ConfigurationError)
def configuration_error_handler(_request, exc: ConfigurationError) -> JSONResponse:
    content: dict[str, Any] = {"detail": str(exc), "reasons": exc.reasons}
    if exc.details is not None:
        content["preflight"] = exc.details
    return JSONResponse(status_code=422, content=content)


@app.exception_handler(ForecastContractError)
def forecast_contract_error_handler(_request, exc: ForecastContractError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc), "reasons": exc.reasons})


@app.exception_handler(ForecastGraphError)
def forecast_graph_error_handler(_request, exc: ForecastGraphError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc), "reasons": exc.reasons})


@app.exception_handler(EvidenceClaimError)
def evidence_claim_error_handler(_request, exc: EvidenceClaimError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc), "reasons": exc.reasons})


@app.exception_handler(StructuredOutputError)
def structured_output_error_handler(_request, exc: StructuredOutputError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc), "reasons": [str(exc)]})


def _row(model: Any) -> dict[str, Any]:
    data = {c.name: getattr(model, c.name) for c in model.__table__.columns}
    for key, value in list(data.items()):
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return data


def _graph_failure_out(row: GraphExecutionFailureRow) -> dict[str, Any]:
    payload = _row(row)
    for source_key, output_key, fallback in (
        ("research_plan_json", "research_plan", {}),
        ("queries_attempted_json", "queries_attempted", []),
        ("sources_checked_json", "sources_checked", []),
    ):
        try:
            payload[output_key] = json.loads(payload.get(source_key) or json.dumps(fallback))
        except json.JSONDecodeError:
            payload[output_key] = fallback
    return payload


def _contract_out(contract: Any) -> dict[str, Any] | None:
    if contract is None:
        return None
    payload = _row(contract)
    try:
        payload["fallback_sources"] = json.loads(payload.get("fallback_sources_json") or "[]")
    except json.JSONDecodeError:
        payload["fallback_sources"] = []
    return payload


def _run_order_time(run: ForecastRun) -> datetime:
    value = run.started_at or run.finished_at
    return as_utc(value) if value is not None else utcnow()


def _is_graph_profile(profile_id: str) -> bool:
    try:
        return load_profile(profile_id).execution_strategy == "graph_nodes"
    except (FileNotFoundError, ValueError):
        return False


def _question_out(session: Session, question: Question) -> dict[str, Any]:
    versions = session.scalars(
        select(ForecastVersion)
        .where(ForecastVersion.question_id == question.id)
        .order_by(ForecastVersion.created_at.desc())
    ).all()
    latest = versions[0] if versions else None
    runs = session.scalars(select(ForecastRun).where(ForecastRun.question_id == question.id)).all()
    runs = sorted(runs, key=_run_order_time, reverse=True)
    watches = session.scalars(select(Watch).where(Watch.question_id == question.id)).all()
    forecast_contract = session.scalar(
        select(ForecastContractRow)
        .where(ForecastContractRow.question_id == question.id)
        .order_by(ForecastContractRow.version.desc())
        .limit(1)
    )
    version_payloads: list[dict[str, Any]] = []
    for version in versions:
        version_payload = _row(version)
        version_payload["profile_id"] = version.run.profile_id
        version_payloads.append(version_payload)
    return {
        "id": question.id,
        "original_text": question.original_text,
        "normalized_text": question.normalized_text,
        "status": question.status,
        "stale": question.stale,
        "notes": question.notes,
        "forecast_deadline": question.forecast_deadline.isoformat() if question.forecast_deadline else None,
        "created_at": question.created_at.isoformat(),
        "requested_mode": question.requested_mode,
        "requested_profile_id": question.requested_profile_id,
        "requested_as_of": question.requested_as_of.isoformat() if question.requested_as_of else None,
        "is_benchmark": question.is_benchmark,
        "latest_probability": latest.ensemble_probability if latest else None,
        "previous_probability": versions[1].ensemble_probability if len(versions) > 1 else None,
        "version_count": len(versions),
        "contract": _contract_out(question.contract),
        "forecast_contract": (
            forecast_contract_from_row(forecast_contract).model_dump(mode="json")
            if forecast_contract is not None
            else None
        ),
        "runs": [_row(run) for run in runs],
        "versions": version_payloads,
        "watches": [_row(watch) for watch in watches],
        "watcher_policy": "Changes mark the forecast stale. Reruns require user action.",
    }


@app.on_event("startup")
def startup() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "local").mkdir(parents=True, exist_ok=True)
    apply_schema()
    with SessionLocal() as session:
        recover_stale_jobs(session)
        seed_sample_question(session)
        seed_synthetic_benchmarks(session)
        seed_v1_evaluation_benchmarks(session)
        session.commit()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "forecastlab-api", "version": __version__}


@app.get("/api/meta")
def api_meta() -> dict[str, str]:
    return {"application_version": __version__, "service": "forecastlab-api"}


@app.get("/health/db")
def health_db(db: Session = Depends(get_db)) -> dict[str, str]:
    db.scalar(select(Question.id).limit(1))
    return {"status": "ok"}


@app.get("/health/worker")
def health_worker(db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(WorkerHeartbeat, "worker")
    if row is None:
        return {"status": "missing", "fresh": False}
    last_seen = as_utc(row.last_seen_at)
    fresh = utcnow() - last_seen <= timedelta(seconds=20)
    return {"status": row.status, "last_seen_at": last_seen.isoformat(), "fresh": fresh}


@app.get("/health/providers")
def health_providers() -> dict[str, Any]:
    pub = public_settings()
    ready = readiness(provider_settings_from_secrets())
    return {
        "mode": pub.mode,
        "model_provider": pub.model_provider,
        "model_name": pub.model_name,
        "model_api_key_set": pub.model_api_key_set,
        "search_provider": pub.search_provider,
        "search_api_key_set": pub.search_api_key_set,
        **ready,
    }


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)) -> dict[str, Any]:
    questions = db.scalars(
        select(Question).where(Question.is_benchmark.is_(False)).order_by(Question.created_at.desc())
    ).all()
    events = db.scalars(select(WatchEvent).order_by(WatchEvent.created_at.desc()).limit(8)).all()
    bench = db.scalars(select(BenchmarkResult)).all()
    bench_questions = db.scalar(select(func.count()).select_from(BenchmarkQuestion)) or 0
    synthetic = db.scalar(select(BenchmarkDataset).where(BenchmarkDataset.is_synthetic.is_(True)))
    return {
        "sample_question": SAMPLE_QUESTION,
        "questions": [_question_out(db, item) for item in questions],
        "watch_events": [_row(event) for event in events],
        "benchmark_count": len(bench),
        "benchmark_question_count": int(bench_questions),
        "synthetic_benchmarks": synthetic is not None,
        "mode": public_settings().mode,
    }


@app.get("/api/profiles")
def profiles() -> list[dict[str, Any]]:
    return [{**item.model_dump(), "profile_hash": profile_hash(item)} for item in list_profiles()]


@app.post("/api/contracts/generate")
def generate_contract(body: GenerateContractIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    if body.mode not in {"demo", "live", "backtest"}:
        raise HTTPException(422, "Unknown mode")
    question_id = str(uuid.uuid4())
    compiler = build_question_compiler(mode=body.mode, profile_id=body.profile_id, as_of=body.as_of)
    contract = compiler.compile(
        body.question,
        question_id=question_id,
        created_by=body.created_by,
    )
    question = Question(
        id=question_id,
        original_text=contract.original_question,
        normalized_text=contract.normalized_question,
        forecast_deadline=contract.resolution_date,
        status="draft",
        requested_mode=body.mode,
        requested_profile_id=body.profile_id,
        requested_as_of=body.as_of,
        is_benchmark=False,
    )
    db.add(question)
    db.flush()
    if body.mode == "demo":
        attach_demo_watch(db, question)
    row = store_forecast_contract(db, contract)
    db.commit()
    return forecast_contract_from_row(row).model_dump(mode="json")


@app.get("/api/contracts/{contract_id}")
def get_contract(contract_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(ForecastContractRow, contract_id)
    if row is None:
        raise HTTPException(404, "Forecast Contract not found")
    return forecast_contract_from_row(row).model_dump(mode="json")


@app.post("/api/contracts/{contract_id}/approve")
def approve_contract(
    contract_id: str,
    body: ApproveContractIn | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.get(ForecastContractRow, contract_id)
    if row is None:
        raise HTTPException(404, "Forecast Contract not found")
    if body is not None:
        apply_forecast_contract_review(row, body.model_dump(exclude_unset=True))
    approve_forecast_contract(db, row)
    db.commit()
    return forecast_contract_from_row(row).model_dump(mode="json")


@app.post("/api/contracts/{contract_id}/graph")
def generate_graph(contract_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    contract_row = db.get(ForecastContractRow, contract_id)
    if contract_row is None:
        raise HTTPException(404, "Forecast Contract not found")
    if contract_row.status != "approved":
        raise ForecastGraphError(
            ["approved_forecast_contract_required"],
            "Approve the Forecast Contract before generating a Forecast Graph",
        )

    existing = db.scalar(
        select(ForecastGraphRow)
        .where(
            ForecastGraphRow.contract_id == contract_id,
            ForecastGraphRow.status == "approved",
        )
        .order_by(ForecastGraphRow.version.desc())
        .limit(1)
    )
    if existing is not None:
        return forecast_graph_from_row(existing).model_dump(mode="json")

    latest_version = db.scalar(
        select(func.max(ForecastGraphRow.version)).where(ForecastGraphRow.contract_id == contract_id)
    )
    generator = build_graph_generator(contract_row.question)
    generation_result = generator.generate(
        forecast_contract_from_row(contract_row),
        version=int(latest_version or 0) + 1,
    )
    row = store_forecast_graph(db, generation_result.graph)
    db.commit()
    return forecast_graph_from_row(row).model_dump(mode="json")


@app.get("/api/graphs/{graph_id}")
def get_graph(graph_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(ForecastGraphRow, graph_id)
    if row is None:
        raise HTTPException(404, "Forecast Graph not found")
    return forecast_graph_from_row(row).model_dump(mode="json")


@app.get("/api/nodes/{node_id}/evidence")
def get_node_evidence(node_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    node = db.get(ForecastNodeRow, node_id)
    if node is None:
        raise HTTPException(404, "Forecast Node not found")
    return {
        "node": forecast_node_from_row(node).model_dump(mode="json"),
        "claims": [claim.model_dump(mode="json") for claim in evidence_for_node(db, node_id)],
    }


@app.get("/api/evidence/{evidence_id}")
def get_evidence(evidence_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(EvidenceClaimRow, evidence_id)
    if row is None:
        raise HTTPException(404, "Evidence Claim not found")
    return evidence_claim_from_row(row).model_dump(mode="json")


@app.get("/api/execution/preview")
def execution_preview(profile_id: str = "three_track_ensemble", mode: str = "demo", as_of: str | None = None) -> dict[str, Any]:
    ready = readiness(provider_settings_from_secrets())
    payload: dict[str, Any] = {"readiness": ready, "mode": mode, "ready": ready.get(mode, {}).get("ready", False)}
    if mode == "backtest" and not as_of:
        payload["ready"] = False
        payload["reasons"] = ["as_of_required"]
        payload["context"] = None
        return payload
    try:
        context = resolve_execution_context(
            requested_mode=mode,  # type: ignore[arg-type]
            profile_id=profile_id,
            settings=provider_settings_from_secrets(),
            as_of=datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None,
        )
        payload["ready"] = True
        payload["reasons"] = []
        payload["context"] = context.model_dump(mode="json")
    except ConfigurationError as exc:
        payload["ready"] = False
        payload["reasons"] = exc.reasons
        payload["detail"] = str(exc)
        if exc.details is not None:
            payload["preflight"] = exc.details
        payload["context"] = None
    return payload


@app.post("/api/questions")
def create_question(body: CreateQuestionIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    if body.mode not in {"demo", "live", "backtest"}:
        raise HTTPException(422, "Unknown mode")
    question = Question(
        id=str(uuid.uuid4()),
        original_text=body.question.strip(),
        notes=body.notes,
        forecast_deadline=body.forecast_deadline,
        status="draft",
        requested_mode=body.mode,
        requested_profile_id=body.profile_id,
        requested_as_of=body.as_of,
        is_benchmark=False,
    )
    db.add(question)
    db.flush()
    if body.resolution_source:
        note = f"Preferred resolution source: {body.resolution_source}"
        question.notes = f"{body.notes}\n{note}".strip() if body.notes else note
    if body.mode == "demo":
        attach_demo_watch(db, question)
    if body.start:
        run = create_run(db, question=question, profile_id=body.profile_id, mode=body.mode, as_of=body.as_of)
        if settings.embedded_worker:
            execute_run(db, run)
    db.commit()
    return _question_out(db, question)


@app.get("/api/questions/{question_id}")
def get_question(question_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(404, "Question not found")
    return _question_out(db, question)


@app.put("/api/questions/{question_id}/contract")
def put_contract(question_id: str, body: ContractIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(404, "Question not found")
    save_contract(db, question, ResolutionContract.model_validate(body.model_dump()))
    db.commit()
    return _question_out(db, question)


@app.post("/api/questions/{question_id}/operationalize")
def operationalize(question_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(404, "Question not found")
    contract = operationalize_question(db, question)
    if question.notes and "Preferred resolution source:" in question.notes:
        preferred = question.notes.split("Preferred resolution source:", 1)[1].strip().splitlines()[0].strip()
        if preferred:
            contract.authoritative_source = preferred
            save_contract(db, question, contract)
    db.commit()
    return _question_out(db, question)


@app.post("/api/questions/{question_id}/runs")
def post_run(question_id: str, body: RunIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(404, "Question not found")
    requested_profile = load_profile(body.profile_id)
    if requested_profile.execution_strategy == "graph_nodes":
        approved_contract_for_question(db, question.id)
    elif requested_profile.execution_strategy == "single_model":
        approved_contract_for_question(db, question.id)
    elif question.forecast_contracts:
        approved_contracts = [contract for contract in question.forecast_contracts if contract.status == "approved"]
        if not approved_contracts:
            raise ForecastContractError(
                ["approved_forecast_contract_required"],
                "Approve the Forecast Contract before research begins",
            )
        if not any(
            graph.status == "approved"
            for contract in approved_contracts
            for graph in contract.forecast_graphs
        ):
            raise ForecastGraphError(
                ["approved_forecast_graph_required"],
                "Generate an approved Forecast Graph before research begins",
            )
    question.requested_mode = body.mode
    question.requested_profile_id = body.profile_id
    question.requested_as_of = body.as_of
    run = create_run(db, question=question, profile_id=body.profile_id, mode=body.mode, as_of=body.as_of)
    if settings.embedded_worker:
        execute_run(db, run)
        db.refresh(run)
    db.commit()
    return _row(run)


def _start_v1_run(
    forecast_id: str,
    body: ExecuteV1In,
    db: Session,
) -> ForecastRun:
    question = db.get(Question, forecast_id)
    if question is None:
        raise HTTPException(404, "Forecast not found")
    if body.mode not in {"demo", "live", "backtest"}:
        raise HTTPException(422, "Unknown mode")
    approved_contract_for_question(db, question.id)
    question.requested_mode = body.mode
    question.requested_profile_id = "graph_forecaster_v1"
    question.requested_as_of = body.as_of
    run = create_run(
        db,
        question=question,
        profile_id="graph_forecaster_v1",
        mode=body.mode,
        as_of=body.as_of,
    )
    if settings.embedded_worker:
        execute_run(db, run)
        db.refresh(run)
    db.commit()
    return run


@app.post("/api/forecasts/{forecast_id}/execute-v1")
def execute_forecast_v1(
    forecast_id: str,
    body: ExecuteV1In | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = _start_v1_run(forecast_id, body or ExecuteV1In(), db)
    return _row(run)


@app.post("/api/forecasts/{forecast_id}/execute-graph")
def execute_graph_forecast(
    forecast_id: str,
    body: ExecuteV1In | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Start the complete graph_forecaster_v1 execution pipeline."""

    run = _start_v1_run(forecast_id, body or ExecuteV1In(), db)
    return _row(run)


@app.post("/api/forecasts/{forecast_id}/node-runs")
def post_forecast_node_runs(
    forecast_id: str,
    body: ExecuteV1In | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Start graph execution and return its auditable node runs."""

    run = _start_v1_run(forecast_id, body or ExecuteV1In(), db)
    return {
        "forecast_id": forecast_id,
        "run_id": run.id,
        "profile_id": run.profile_id,
        "status": run.status,
        "node_runs": [item.model_dump(mode="json") for item in node_runs_for_run(db, run.id)],
    }


@app.get("/api/forecasts/{forecast_id}/node-runs")
def get_forecast_node_runs(forecast_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    question = db.get(Question, forecast_id)
    if question is None:
        raise HTTPException(404, "Forecast not found")
    candidate_runs = db.scalars(
        select(ForecastRun)
        .where(ForecastRun.question_id == forecast_id)
        .order_by(ForecastRun.started_at.desc(), ForecastRun.id.desc())
    ).all()
    run = next(
        (
            candidate
            for candidate in candidate_runs
            if _is_graph_profile(candidate.profile_id)
        ),
        None,
    )
    if run is None:
        return {
            "forecast_id": forecast_id,
            "run_id": None,
            "profile_id": "graph_forecaster_v1",
            "status": "not_started",
            "node_runs": [],
        }
    return {
        "forecast_id": forecast_id,
        "run_id": run.id,
        "profile_id": run.profile_id,
        "status": run.status,
        "node_runs": [item.model_dump(mode="json") for item in node_runs_for_run(db, run.id)],
    }


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    run = db.get(ForecastRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    tracks = db.scalars(select(ResearchTrack).where(ResearchTrack.run_id == run.id)).all()
    evidence = db.scalars(select(EvidenceItem).where(EvidenceItem.run_id == run.id)).all()
    claims = db.scalars(
        select(EvidenceClaimRow)
        .join(EvidenceItem, EvidenceClaimRow.evidence_item_id == EvidenceItem.id)
        .where(EvidenceItem.run_id == run.id)
        .order_by(EvidenceClaimRow.source_available_at.desc(), EvidenceClaimRow.id)
    ).all()
    node_runs = db.scalars(
        select(ForecastNodeRunRow)
        .where(ForecastNodeRunRow.forecast_run_id == run.id)
        .order_by(ForecastNodeRunRow.created_at, ForecastNodeRunRow.id)
    ).all()
    graph_failures = db.scalars(
        select(GraphExecutionFailureRow)
        .where(GraphExecutionFailureRow.forecast_run_id == run.id)
        .order_by(GraphExecutionFailureRow.created_at, GraphExecutionFailureRow.id)
    ).all()
    research_plan = db.scalar(
        select(ResearchPlanRow).where(ResearchPlanRow.forecast_run_id == run.id)
    )
    payload = _row(run)
    payload["tracks"] = [_row(track) for track in tracks]
    payload["evidence"] = [_row(item) for item in evidence]
    payload["evidence_claims"] = [evidence_claim_from_row(item).model_dump(mode="json") for item in claims]
    payload["node_runs"] = [item.model_dump(mode="json") for item in node_runs_for_run(db, run.id)]
    payload["graph_execution_failures"] = [
        _graph_failure_out(item) for item in graph_failures
    ]
    payload["research_plan"] = (
        research_plan_from_row(research_plan).model_dump(mode="json")
        if research_plan is not None
        else None
    )
    graph_row = node_runs[0].node.graph if node_runs else None
    if graph_row is None and run.execution_context_json:
        try:
            graph_id = json.loads(run.execution_context_json).get("forecast_graph_id")
        except json.JSONDecodeError:
            graph_id = None
        if graph_id:
            graph_row = db.get(ForecastGraphRow, graph_id)
    if graph_row is not None:
        contract_row = graph_row.contract
        payload["forecast_contract"] = forecast_contract_from_row(contract_row).model_dump(mode="json")
        payload["forecast_graph"] = forecast_graph_from_row(graph_row).model_dump(mode="json")
    for track in payload["tracks"]:
        for key in ("plan_json", "key_drivers_json", "counterarguments_json", "unresolved_json"):
            if track.get(key):
                track[key.replace("_json", "")] = json.loads(track[key])
        if "unresolved" in track:
            track["unresolved_uncertainties"] = track["unresolved"]
    if run.aggregation_json:
        payload["aggregation"] = json.loads(run.aggregation_json)
    if run.aggregation is not None:
        payload["forecast_aggregation"] = forecast_aggregation_from_row(run.aggregation).model_dump(
            mode="json"
        )
    if run.evidence_sufficiency_assessment is not None:
        payload["evidence_sufficiency_assessment"] = evidence_sufficiency_from_row(
            run.evidence_sufficiency_assessment
        ).model_dump(mode="json")
    if run.material_node_coverage_assessment is not None:
        payload["material_node_coverage_assessment"] = (
            material_node_coverage_from_row(
                run.material_node_coverage_assessment
            ).model_dump(mode="json")
        )
    if run.scenario_synthesis is not None:
        payload["scenario_synthesis"] = scenario_synthesis_from_row(
            run.scenario_synthesis
        ).model_dump(mode="json")
    if run.budget_json:
        payload["budget"] = json.loads(run.budget_json)
    if run.prompt_versions_json:
        payload["prompt_versions"] = json.loads(run.prompt_versions_json)
    if run.execution_context_json:
        try:
            payload["execution_context"] = json.loads(run.execution_context_json)
        except json.JSONDecodeError:
            payload["execution_context"] = {}
    if run.provider_json:
        try:
            payload["providers"] = json.loads(run.provider_json)
        except json.JSONDecodeError:
            payload["providers"] = {}
    attempts = db.scalars(select(ForecastRunAttempt).where(ForecastRunAttempt.run_id == run.id)).all()
    ledger = db.scalars(select(ProviderCallLedger).where(ProviderCallLedger.run_id == run.id)).all()
    payload["run_attempts"] = [_row(item) for item in attempts]
    payload["provider_call_ledger"] = [_row(item) for item in ledger]
    payload["model_cost_usd"] = run.model_cost_usd
    payload["search_cost_usd"] = run.search_cost_usd
    payload["failed_attempt_cost_usd"] = run.failed_attempt_cost_usd
    payload["total_cost_usd"] = run.total_cost_usd or run.cost_usd
    payload["cost_usd"] = run.total_cost_usd or run.cost_usd
    v1_report = build_v1_report(payload)
    if v1_report is not None:
        payload["v1_report"] = v1_report
    return payload


@app.get("/api/forecasts/{forecast_id}/graph-report")
def graph_forecast_report(forecast_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    question = db.get(Question, forecast_id)
    if question is None:
        raise HTTPException(404, "Forecast not found")
    candidate_runs = db.scalars(
        select(ForecastRun)
        .where(ForecastRun.question_id == forecast_id)
        .order_by(ForecastRun.started_at.desc(), ForecastRun.id.desc())
    ).all()
    run = next(
        (candidate for candidate in candidate_runs if _is_graph_profile(candidate.profile_id)),
        None,
    )
    if run is None:
        raise HTTPException(404, "Graph forecast has not been executed")
    payload = get_run(run.id, db)
    report_payload = payload.get("v1_report")
    return {
        "forecast_id": forecast_id,
        "run_id": run.id,
        "profile_id": run.profile_id,
        "status": run.status,
        "final_probability": (
            report_payload.get("final_probability") if report_payload is not None else None
        ),
        "report": report_payload,
        "failures": payload.get("graph_execution_failures") or [],
    }


@app.get("/api/questions/{question_id}/report")
def report(question_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(404, "Question not found")
    payload = _question_out(db, question)
    latest_run = payload["runs"][0] if payload["runs"] else None
    if latest_run:
        payload["latest_run"] = get_run(latest_run["id"], db)
        payload["v1_report"] = payload["latest_run"].get("v1_report")
    return payload


@app.get("/api/questions/{question_id}/export.md")
def export_md(question_id: str, db: Session = Depends(get_db)) -> PlainTextResponse:
    payload = report(question_id, db)
    latest = payload.get("latest_run") or {}
    context = latest.get("execution_context") or {}
    lines = [
        "# ForecastLab report",
        "",
        f"**Question:** {payload['original_text']}",
        f"**Status:** {payload['status']}",
        f"**Ensemble probability:** {payload.get('latest_probability')}",
        f"**Mode:** {context.get('effective_mode') or latest.get('mode')}",
        f"**Model:** {context.get('model_provider')} / {context.get('model_name')}",
        f"**Search:** {context.get('search_provider')}",
        f"**Profile:** {context.get('profile_id')} {context.get('configuration_hash')}",
        "",
        "## Resolution contract",
        json.dumps(payload.get("contract"), indent=2),
        "",
        "## Aggregation",
        json.dumps(latest.get("aggregation"), indent=2),
        "",
        "## Tracks",
    ]
    for track in latest.get("tracks") or []:
        lines.append(f"### {track.get('track_type')}")
        lines.append(f"Probability: {track.get('probability')}")
        lines.append(track.get("reasoning_summary") or "")
        lines.append("")
    if latest.get("v1_report"):
        lines.extend(v1_report_markdown(latest["v1_report"]))
        lines.append("")
    if latest.get("node_runs"):
        lines.append("## Forecast Graph node runs")
        for node_run in latest["node_runs"]:
            lines.append(f"### Node {node_run.get('node_id')}")
            lines.append(f"Probability: {node_run.get('probability')}")
            lines.append(f"Confidence: {node_run.get('confidence')}")
            lines.append(f"Raw importance weight: {node_run.get('raw_importance_weight')}")
            lines.append(f"Dependency factor: {node_run.get('dependency_factor')}")
            lines.append(f"Normalized weight: {node_run.get('normalized_weight')}")
            lines.append(f"Probability contribution: {node_run.get('probability_contribution')}")
            lines.append(node_run.get("reasoning") or "")
            lines.append("")
    lines.append("## Evidence")
    for item in latest.get("evidence") or []:
        flag = "rejected" if item.get("rejected") else "accepted"
        lines.append(f"- [{flag}] {item.get('title')} — {item.get('url')}")
    if latest.get("evidence_claims"):
        lines.append("")
        lines.append("## Evidence Claims")
        for claim in latest["evidence_claims"]:
            lines.append(f"- [{claim.get('id')}] {claim.get('claim')} — {claim.get('source_url')}")
            lines.append(
                "  - Publication date: "
                f"{claim.get('publication_date') or 'unavailable'} "
                f"(verified={bool(claim.get('publication_date_verified'))})"
            )
            lines.append(
                f"  - Source available at: {claim.get('source_available_at')} "
                f"via {claim.get('temporal_basis')}"
            )
            lines.append(
                f"  - Retrieved at: {claim.get('retrieval_date')} · "
                f"cutoff verified={bool(claim.get('cutoff_verified'))}"
            )
            lines.append(
                "  - Deterministic provenance: "
                f"{claim.get('source_class')} / {claim.get('extraction_method')} / "
                f"{claim.get('source_host') or 'host unavailable'}"
            )
    return PlainTextResponse("\n".join(lines), media_type="text/markdown")


@app.get("/api/questions/{question_id}/export.json")
def export_json(question_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return report(question_id, db)


@app.post("/api/questions/{question_id}/watches")
def add_watch(question_id: str, body: WatchIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(404, "Question not found")
    try:
        validate_user_watch(endpoint_url=body.endpoint_url, endpoint_type=body.endpoint_type)
    except UnsafeURLError as exc:
        raise HTTPException(400, str(exc)) from exc
    watch = Watch(
        id=str(uuid.uuid4()),
        question_id=question.id,
        endpoint_url=body.endpoint_url,
        endpoint_type=body.endpoint_type,
        json_path=body.json_path,
        poll_seconds=body.poll_seconds,
        auto_rerun=False,
        status="active",
    )
    db.add(watch)
    db.commit()
    return {**_row(watch), "policy": "Changes mark the forecast stale. Reruns require user action."}


@app.post("/api/watches/{watch_id}/check")
def post_watch_check(watch_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    watch = db.get(Watch, watch_id)
    if watch is None:
        raise HTTPException(404, "Watch not found")
    event = check_watch(db, watch)
    db.commit()
    return _row(event)


@app.get("/api/settings", response_model=SettingsPublic)
def get_settings() -> SettingsPublic:
    return public_settings()


@app.put("/api/settings", response_model=SettingsPublic)
def put_settings(body: SettingsUpdate) -> SettingsPublic:
    return update_settings(body)


@app.post("/api/settings/test-model")
def post_test_model() -> dict[str, Any]:
    return test_model_connection()


@app.post("/api/settings/test-search")
def post_test_search() -> dict[str, Any]:
    return test_search_connection()


@app.get("/demo/indicators/{name}")
def demo_indicator(name: str) -> dict[str, Any]:
    try:
        payload = get_indicator(name)
    except KeyError as exc:
        raise HTTPException(404, "Unknown demo indicator") from exc
    return {"name": name, **payload, "hash": demo_payload_hash(name)}


@app.post("/demo/indicators/{name}/simulate")
def demo_simulate(name: str, body: SimulateIn) -> dict[str, Any]:
    try:
        payload = simulate_indicator(name, body.value)
    except KeyError as exc:
        raise HTTPException(404, "Unknown demo indicator") from exc
    return {"name": name, **payload, "hash": demo_payload_hash(name)}


def _parse_benchmark_file(raw: str, filename: str) -> list[dict[str, Any]]:
    stripped = raw.lstrip()
    if filename.lower().endswith(".json") or stripped.startswith(("[", "{")):
        payload = json.loads(raw)
        if isinstance(payload, dict):
            payload = payload.get("questions") or payload.get("rows") or []
        if not isinstance(payload, list):
            raise ValueError("JSON benchmark file must be an array or {questions: [...]}")
        return [item if isinstance(item, dict) else {"question": str(item)} for item in payload]
    return list(csv.DictReader(io.StringIO(raw)))


@app.get("/api/benchmarks/template.csv")
def benchmark_template() -> PlainTextResponse:
    path = Path(__file__).resolve().parents[3] / "fixtures" / "benchmarks" / "import_template.csv"
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/csv")


@app.get("/api/datasets")
def list_datasets(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(BenchmarkDataset).order_by(BenchmarkDataset.created_at.desc())).all()
    return {"datasets": [serialize_dataset(item) for item in rows]}


@app.get("/api/evaluation/datasets")
def list_evaluation_datasets(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(
        select(EvaluationDataset).order_by(
            EvaluationDataset.name,
            EvaluationDataset.version,
            EvaluationDataset.created_at,
        )
    ).all()
    return {
        "datasets": [serialize_evaluation_dataset(item) for item in rows],
        "execution_supported": True,
        "comparison_profiles": list(CONTROLLED_FORECAST_PROFILES),
    }


@app.get("/api/evaluation/datasets/template.csv")
def evaluation_dataset_template() -> PlainTextResponse:
    path = Path(__file__).resolve().parents[3] / "fixtures" / "benchmarks" / "evaluation_template.csv"
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/csv")


@app.post("/api/evaluation/datasets/import", status_code=201)
async def import_evaluation_dataset_file(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    version: str = Form(default="1"),
    description: str = Form(default=""),
    provenance: str = Form(default=""),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        raw = (await file.read()).decode("utf-8-sig")
        rows = parse_evaluation_dataset_file(raw, file.filename or "evaluation.csv")
    except (UnicodeDecodeError, json.JSONDecodeError, EvaluationDatasetValidationError) as exc:
        reasons = (
            exc.reasons
            if isinstance(exc, EvaluationDatasetValidationError)
            else ["invalid_dataset_file"]
        )
        raise HTTPException(
            400,
            detail={"message": "Could not parse evaluation dataset", "reasons": reasons},
        ) from exc
    dataset_name = (name or (file.filename or "evaluation_dataset")).rsplit(".", 1)[0]
    try:
        dataset = import_evaluation_dataset(
            db,
            name=dataset_name,
            version=version,
            description=description,
            provenance=provenance,
            rows=rows,
        )
    except EvaluationDatasetValidationError as exc:
        raise HTTPException(
            400,
            detail={"message": str(exc), "reasons": exc.reasons},
        ) from exc
    db.commit()
    db.refresh(dataset)
    return serialize_evaluation_dataset(dataset, include_questions=True)


@app.post("/api/evaluation/datasets/{dataset_id}/review")
def review_evaluation_dataset_endpoint(
    dataset_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    dataset = db.get(EvaluationDataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "Evaluation dataset not found")
    try:
        review_evaluation_dataset(db, dataset)
    except EvaluationDatasetValidationError as exc:
        raise HTTPException(
            400,
            detail={"message": str(exc), "reasons": exc.reasons},
        ) from exc
    db.commit()
    db.refresh(dataset)
    return serialize_evaluation_dataset(dataset, include_questions=True)


@app.post("/api/evaluation/datasets/{dataset_id}/freeze")
def freeze_evaluation_dataset_endpoint(
    dataset_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    dataset = db.get(EvaluationDataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "Evaluation dataset not found")
    try:
        freeze_evaluation_dataset(db, dataset)
    except EvaluationDatasetValidationError as exc:
        raise HTTPException(
            400,
            detail={"message": str(exc), "reasons": exc.reasons},
        ) from exc
    db.commit()
    db.refresh(dataset)
    return serialize_evaluation_dataset(dataset, include_questions=True)


@app.get("/api/evaluation/datasets/{dataset_id}")
def get_evaluation_dataset(dataset_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    dataset = db.get(EvaluationDataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "Evaluation dataset not found")
    return serialize_evaluation_dataset(dataset, include_questions=True)


@app.get("/api/evaluation/releases")
def list_evaluation_releases(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(
        select(EvaluationRelease).order_by(
            EvaluationRelease.name,
            EvaluationRelease.version,
            EvaluationRelease.created_at,
        )
    ).all()
    return {
        "releases": [serialize_evaluation_release(item) for item in rows],
        "policy_version": "private_v1_real_evaluation_release_v1",
        "real_corpus_populated": False,
    }


@app.post("/api/evaluation/releases", status_code=201)
def post_evaluation_release(
    body: EvaluationReleaseIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        release = create_evaluation_release(
            db,
            name=body.name,
            version=body.version,
            development_dataset_id=body.development_dataset_id,
            validation_dataset_id=body.validation_dataset_id,
            test_dataset_id=body.test_dataset_id,
            questions=body.questions,
            provider_identity=body.provider_identity,
            correction_of_release_id=body.correction_of_release_id,
            correction_summary=body.correction_summary,
        )
    except EvaluationReleaseValidationError as exc:
        raise HTTPException(
            400,
            detail={"message": str(exc), "reasons": exc.reasons},
        ) from exc
    db.commit()
    db.refresh(release)
    return release_audit(db, release)


@app.post("/api/evaluation/releases/{release_id}/review")
def review_evaluation_release_endpoint(
    release_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    release = db.get(EvaluationRelease, release_id)
    if release is None:
        raise HTTPException(404, "Evaluation release not found")
    try:
        review_evaluation_release(db, release)
    except EvaluationReleaseValidationError as exc:
        raise HTTPException(
            400,
            detail={"message": str(exc), "reasons": exc.reasons},
        ) from exc
    db.commit()
    db.refresh(release)
    return release_audit(db, release)


@app.post("/api/evaluation/releases/{release_id}/freeze")
def freeze_evaluation_release_endpoint(
    release_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    release = db.get(EvaluationRelease, release_id)
    if release is None:
        raise HTTPException(404, "Evaluation release not found")
    try:
        freeze_evaluation_release(db, release)
    except EvaluationReleaseValidationError as exc:
        raise HTTPException(
            400,
            detail={"message": str(exc), "reasons": exc.reasons},
        ) from exc
    db.commit()
    db.refresh(release)
    return release_audit(db, release)


@app.get("/api/evaluation/releases/{release_id}/execution-manifest")
def get_evaluation_release_execution_manifest(
    release_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    release = db.get(EvaluationRelease, release_id)
    if release is None:
        raise HTTPException(404, "Evaluation release not found")
    try:
        manifest = get_blinded_execution_manifest(release)
    except EvaluationReleaseValidationError as exc:
        raise HTTPException(
            400,
            detail={"message": str(exc), "reasons": exc.reasons},
        ) from exc
    return manifest.model_dump(mode="json")


@app.get("/api/evaluation/releases/{release_id}")
def get_evaluation_release(
    release_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    release = db.get(EvaluationRelease, release_id)
    if release is None:
        raise HTTPException(404, "Evaluation release not found")
    return release_audit(db, release)


@app.get("/api/evaluation/evidence-releases")
def list_historical_evidence_releases(
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = db.scalars(
        select(HistoricalEvidenceRelease).order_by(
            HistoricalEvidenceRelease.name,
            HistoricalEvidenceRelease.version,
            HistoricalEvidenceRelease.created_at,
        )
    ).all()
    return {
        "releases": [serialize_historical_evidence_release(item) for item in rows],
        "policy_version": "private_v1_historical_evidence_release_v1",
        "repository_bundles_certified_real_corpus": False,
    }


@app.post("/api/evaluation/evidence-releases", status_code=201)
def post_historical_evidence_release(
    body: HistoricalEvidenceReleaseIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        release = create_historical_evidence_release(
            db,
            evaluation_release_id=body.evaluation_release_id,
            name=body.name,
            version=body.version,
            documents=body.documents,
            packets=body.packets,
            correction_of_release_id=body.correction_of_release_id,
            correction_summary=body.correction_summary,
        )
    except HistoricalEvidenceReleaseValidationError as exc:
        raise HTTPException(
            400,
            detail={"message": str(exc), "reasons": exc.reasons},
        ) from exc
    db.commit()
    db.refresh(release)
    return serialize_historical_evidence_release(release, include_audit=True)


@app.post("/api/evaluation/evidence-releases/{release_id}/review")
def review_historical_evidence_release_endpoint(
    release_id: str,
    body: HistoricalEvidenceBundleRootIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    release = db.get(HistoricalEvidenceRelease, release_id)
    if release is None:
        raise HTTPException(404, "Historical evidence release not found")
    try:
        review_historical_evidence_release(
            db, release, bundle_root=Path(body.bundle_root)
        )
    except HistoricalEvidenceReleaseValidationError as exc:
        raise HTTPException(
            400,
            detail={"message": str(exc), "reasons": exc.reasons},
        ) from exc
    db.commit()
    db.refresh(release)
    return serialize_historical_evidence_release(release, include_audit=True)


@app.post("/api/evaluation/evidence-releases/{release_id}/freeze")
def freeze_historical_evidence_release_endpoint(
    release_id: str,
    body: HistoricalEvidenceBundleRootIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    release = db.get(HistoricalEvidenceRelease, release_id)
    if release is None:
        raise HTTPException(404, "Historical evidence release not found")
    try:
        freeze_historical_evidence_release(
            db, release, bundle_root=Path(body.bundle_root)
        )
    except HistoricalEvidenceReleaseValidationError as exc:
        raise HTTPException(
            400,
            detail={"message": str(exc), "reasons": exc.reasons},
        ) from exc
    db.commit()
    db.refresh(release)
    return serialize_historical_evidence_release(release, include_audit=True)


@app.post("/api/evaluation/evidence-releases/{release_id}/verify")
def verify_historical_evidence_release_endpoint(
    release_id: str,
    body: HistoricalEvidenceBundleRootIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    release = db.get(HistoricalEvidenceRelease, release_id)
    if release is None:
        raise HTTPException(404, "Historical evidence release not found")
    try:
        result = verify_release_bundle(release, Path(body.bundle_root))
    except HistoricalEvidenceReleaseValidationError as exc:
        raise HTTPException(
            400,
            detail={"message": str(exc), "reasons": exc.reasons},
        ) from exc
    return result.model_dump(mode="json")


@app.get("/api/evaluation/evidence-releases/{release_id}/execution-manifest")
def get_historical_evidence_execution_manifest_endpoint(
    release_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    release = db.get(HistoricalEvidenceRelease, release_id)
    if release is None:
        raise HTTPException(404, "Historical evidence release not found")
    try:
        manifest = get_historical_evidence_execution_manifest(release)
    except HistoricalEvidenceReleaseValidationError as exc:
        raise HTTPException(
            400,
            detail={"message": str(exc), "reasons": exc.reasons},
        ) from exc
    return manifest.model_dump(mode="json")


@app.get("/api/evaluation/evidence-releases/{release_id}")
def get_historical_evidence_release_endpoint(
    release_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    release = db.get(HistoricalEvidenceRelease, release_id)
    if release is None:
        raise HTTPException(404, "Historical evidence release not found")
    return serialize_historical_evidence_release(release, include_audit=True)


@app.post("/api/forecast-experiments", status_code=201)
def post_forecast_experiment(
    body: ForecastExperimentIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        experiment = create_forecast_experiment(
            db,
            dataset_id=body.dataset_id,
            profile_ids=body.profile_ids,
            synthetic_test=body.synthetic_test,
            evaluation_release_id=body.evaluation_release_id,
            evaluation_split=body.evaluation_split,
            historical_evidence_release_id=body.historical_evidence_release_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return forecast_experiment_progress(db, experiment)


@app.get("/api/forecast-experiments")
def list_forecast_experiments(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(
        select(ForecastExperiment).order_by(ForecastExperiment.created_at.desc())
    ).all()
    return {
        "experiments": [forecast_experiment_progress(db, item) for item in rows],
        "comparison_profiles": list(CONTROLLED_FORECAST_PROFILES),
    }


@app.get("/api/forecast-experiments/{experiment_id}")
def get_forecast_experiment(
    experiment_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    experiment = db.get(ForecastExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, "Forecast experiment not found")
    return forecast_experiment_progress(db, experiment)


@app.get("/api/forecast-experiments/{experiment_id}/report")
def get_forecast_experiment_report(
    experiment_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    experiment = db.get(ForecastExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, "Forecast experiment not found")
    return forecast_experiment_report(db, experiment).model_dump(mode="json")


@app.get("/api/forecast-experiments/{experiment_id}/analysis")
def get_forecast_experiment_analysis(
    experiment_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    experiment = db.get(ForecastExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, "Forecast experiment not found")
    return build_forecast_research_analysis(db, experiment).model_dump(mode="json")


@app.get("/api/forecast-experiment-runs/{run_id}/failures")
def get_forecast_experiment_run_failures(
    run_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    experiment_run = db.get(ForecastExperimentRun, run_id)
    if experiment_run is None:
        raise HTTPException(404, "Forecast experiment run not found")
    return {
        "forecast_experiment_run_id": run_id,
        "failures": [
            serialize_forecast_failure(item)
            for item in failures_for_forecast_experiment_run(db, run_id)
        ],
    }


@app.post("/api/forecast-experiment-runs/{run_id}/failures", status_code=201)
def post_forecast_experiment_run_failure(
    run_id: str,
    body: ForecastFailureIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.get(ForecastExperimentRun, run_id) is None:
        raise HTTPException(404, "Forecast experiment run not found")
    try:
        row = classify_forecast_failure(
            db,
            forecast_experiment_run_id=run_id,
            category=body.category,
            annotation=body.annotation,
            created_by=body.created_by,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return serialize_forecast_failure(row)


@app.patch("/api/forecast-failures/{failure_id}")
def patch_forecast_failure(
    failure_id: str,
    body: ForecastFailureAnnotationIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        row = update_forecast_failure_annotation(
            db,
            failure_id=failure_id,
            annotation=body.annotation,
        )
    except ValueError as exc:
        if str(exc) == "forecast_failure_not_found":
            raise HTTPException(404, "Forecast failure not found") from exc
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return serialize_forecast_failure(row)


@app.post("/api/benchmarks/import")
async def import_benchmarks(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    raw = (await file.read()).decode("utf-8")
    try:
        rows = _parse_benchmark_file(raw, file.filename or "benchmarks.csv")
    except Exception as exc:
        raise HTTPException(400, f"Could not parse file: {exc}") from exc
    dataset_name = (name or (file.filename or "imported_dataset")).rsplit(".", 1)[0]
    try:
        dataset, created, duplicates, errors = ensure_dataset(
            session=db,
            name=dataset_name,
            description=f"Imported from {file.filename}",
            rows=rows,
            provenance="user_import",
            is_synthetic=False,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {
        "created": created,
        "duplicates": duplicates,
        "errors": errors,
        "dataset_id": dataset.id,
        "dataset_name": dataset.name,
        "dataset_hash": dataset.dataset_hash,
        "synthetic": dataset.is_synthetic,
        "is_synthetic": dataset.is_synthetic,
    }


@app.post("/api/experiments")
def post_experiment(body: ExperimentIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        experiment = create_experiment(db, dataset_id=body.dataset_id, profile_ids=body.profile_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ConfigurationError:
        raise
    db.commit()
    return {
        "id": experiment.id,
        "status": experiment.status,
        "total_tasks": experiment.total_tasks,
        "is_synthetic": experiment.is_synthetic,
        "experiment_hash": experiment.experiment_hash,
        "progress": experiment_progress(db, experiment),
    }


@app.get("/api/evaluations/v1")
def get_v1_evaluation_workflow(db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return v1_evaluation_workflow(db)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/evaluations/v1")
def post_v1_evaluation(db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        experiment, workflow = create_v1_evaluation(db)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {
        "id": experiment.id,
        "status": experiment.status,
        "total_tasks": experiment.total_tasks,
        "is_synthetic": experiment.is_synthetic,
        "experiment_hash": experiment.experiment_hash,
        "workflow": workflow,
        "notice": V1_EVALUATION_NOTICE,
        "progress": experiment_progress(db, experiment),
    }


@app.get("/api/experiments")
def list_experiments(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(BenchmarkExperiment).order_by(BenchmarkExperiment.created_at.desc())).all()
    return {
        "experiments": [
            {
                "id": item.id,
                "dataset_id": item.dataset_id,
                "status": item.status,
                "total_tasks": item.total_tasks,
                "completed_tasks": item.completed_tasks,
                "failed_tasks": item.failed_tasks,
                "is_synthetic": item.is_synthetic,
                "experiment_hash": item.experiment_hash,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in rows
        ]
    }


@app.get("/api/experiments/{experiment_id}")
def get_experiment(experiment_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    experiment = db.get(BenchmarkExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, "Experiment not found")
    return experiment_progress(db, experiment)


@app.get("/api/experiments/{experiment_id}/summary")
def get_experiment_summary(experiment_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    experiment = db.get(BenchmarkExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, "Experiment not found")
    return experiment_summary(db, experiment)


@app.get("/api/experiments/{experiment_id}/export.csv")
def export_experiment_csv(experiment_id: str, db: Session = Depends(get_db)) -> PlainTextResponse:
    experiment = db.get(BenchmarkExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, "Experiment not found")
    summary = experiment_summary(db, experiment)
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "question",
            "profile_id",
            "status",
            "probability",
            "outcome",
            "brier",
            "log_loss_value",
            "cost_usd",
            "latency_ms",
            "evidence_coverage",
            "evidence_covered_units",
            "evidence_total_units",
            "failed",
            "partial",
        ],
    )
    writer.writeheader()
    for row in summary.get("rows") or []:
        writer.writerow({key: row.get(key) for key in writer.fieldnames})
    return PlainTextResponse(buffer.getvalue(), media_type="text/csv")


@app.post("/api/benchmarks/run")
def run_benchmarks(db: Session = Depends(get_db)) -> dict[str, Any]:
    dataset = current_builtin_dataset(db)
    if dataset is None:
        raise HTTPException(400, "No current built-in benchmark dataset available")
    experiment = create_experiment(db, dataset_id=dataset.id, profile_ids=list(DEFAULT_EXPERIMENT_PROFILES))
    db.commit()
    return {"experiment_id": experiment.id, "created": experiment.total_tasks, "async": True}


@app.get("/api/benchmarks/summary")
def benchmark_summary(experiment_id: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    experiment = None
    if experiment_id:
        experiment = db.get(BenchmarkExperiment, experiment_id)
    else:
        experiment = db.scalar(select(BenchmarkExperiment).order_by(BenchmarkExperiment.created_at.desc()))
    if experiment is None:
        return {
            "synthetic": False,
            "is_synthetic": False,
            "profiles": [],
            "rows": [],
            "reliability_by_profile": {},
            "paired_comparisons": [],
            "notice": "No experiment has been run yet.",
            "datasets": [serialize_dataset(item) for item in db.scalars(select(BenchmarkDataset)).all()],
            "profile_configs": [item.model_dump() for item in list_profiles()],
        }
    return experiment_summary(db, experiment)


def create_app() -> FastAPI:
    return app
