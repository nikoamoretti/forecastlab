from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forecastlab.evaluation import brier_score, log_loss, mean, median, reliability_bins
from forecastlab.hashing import import_hash
from forecastlab.logging import configure_logging
from forecastlab.profiles import list_profiles
from forecastlab.providers.mock import SAMPLE_QUESTION
from forecastlab.schemas import ResolutionContract, SettingsPublic, SettingsUpdate
from forecastlab.timeutil import as_utc, parse_datetime, utcnow
from forecastlab_api.config import settings
from forecastlab_api.db import Base, SessionLocal, engine, get_db
from forecastlab_api.demo import demo_payload_hash, get_indicator, simulate_indicator
from forecastlab_api.jobs import recover_stale_jobs
from forecastlab_api.models import (
    BenchmarkQuestion,
    BenchmarkResult,
    EvidenceItem,
    ForecastRun,
    ForecastVersion,
    Question,
    ResearchTrack,
    Watch,
    WatchEvent,
    WorkerHeartbeat,
)
from forecastlab_api.persist import save_contract
from forecastlab_api.pipeline import create_run, execute_run, operationalize_question
from forecastlab_api.secrets import public_settings, update_settings
from forecastlab_api.seed import seed_sample_question, seed_synthetic_benchmarks
from forecastlab_api.watches import attach_demo_watch, check_watch

configure_logging(settings.log_level)
app = FastAPI(title="ForecastLab", version="0.1.0")
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


class RunIn(BaseModel):
    profile_id: str = "three_track_ensemble"
    mode: str = "demo"
    as_of: datetime | None = None


class WatchIn(BaseModel):
    endpoint_url: str
    endpoint_type: str = "json"
    json_path: str | None = "$.value"
    poll_seconds: int = 300
    auto_rerun: bool = False


class SimulateIn(BaseModel):
    value: float


def _row(model: Any) -> dict[str, Any]:
    data = {c.name: getattr(model, c.name) for c in model.__table__.columns}
    for key, value in list(data.items()):
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return data


def _contract_out(contract: Any) -> dict[str, Any] | None:
    if contract is None:
        return None
    payload = _row(contract)
    try:
        payload["fallback_sources"] = json.loads(payload.get("fallback_sources_json") or "[]")
    except json.JSONDecodeError:
        payload["fallback_sources"] = []
    return payload


def _question_out(session: Session, question: Question) -> dict[str, Any]:
    versions = session.scalars(
        select(ForecastVersion)
        .where(ForecastVersion.question_id == question.id)
        .order_by(ForecastVersion.created_at.desc())
    ).all()
    latest = versions[0] if versions else None
    runs = session.scalars(select(ForecastRun).where(ForecastRun.question_id == question.id)).all()
    runs = sorted(runs, key=lambda item: item.started_at or item.finished_at or utcnow(), reverse=True)
    watches = session.scalars(select(Watch).where(Watch.question_id == question.id)).all()
    return {
        "id": question.id,
        "original_text": question.original_text,
        "normalized_text": question.normalized_text,
        "status": question.status,
        "stale": question.stale,
        "notes": question.notes,
        "forecast_deadline": question.forecast_deadline.isoformat() if question.forecast_deadline else None,
        "created_at": question.created_at.isoformat(),
        "latest_probability": latest.ensemble_probability if latest else None,
        "previous_probability": versions[1].ensemble_probability if len(versions) > 1 else None,
        "version_count": len(versions),
        "contract": _contract_out(question.contract),
        "runs": [_row(run) for run in runs],
        "versions": [_row(version) for version in versions],
        "watches": [_row(watch) for watch in watches],
    }


@app.on_event("startup")
def startup() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "local").mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        recover_stale_jobs(session)
        seed_sample_question(session)
        seed_synthetic_benchmarks(session)
        session.commit()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "forecastlab-api"}


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
    return {
        "mode": pub.mode,
        "model_provider": pub.model_provider,
        "model_name": pub.model_name,
        "model_api_key_set": pub.model_api_key_set,
        "search_provider": pub.search_provider,
        "search_api_key_set": pub.search_api_key_set,
    }


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)) -> dict[str, Any]:
    questions = db.scalars(select(Question).order_by(Question.created_at.desc())).all()
    events = db.scalars(select(WatchEvent).order_by(WatchEvent.created_at.desc()).limit(8)).all()
    bench = db.scalars(select(BenchmarkResult)).all()
    bench_questions = db.scalar(select(func.count()).select_from(BenchmarkQuestion)) or 0
    return {
        "sample_question": SAMPLE_QUESTION,
        "questions": [_question_out(db, item) for item in questions],
        "watch_events": [_row(event) for event in events],
        "benchmark_count": len(bench),
        "benchmark_question_count": int(bench_questions),
        "synthetic_benchmarks": True,
        "mode": public_settings().mode,
    }


@app.get("/api/profiles")
def profiles() -> list[dict[str, Any]]:
    return [item.model_dump() for item in list_profiles()]


@app.post("/api/questions")
def create_question(body: CreateQuestionIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    question = Question(
        id=str(uuid.uuid4()),
        original_text=body.question.strip(),
        notes=body.notes,
        forecast_deadline=body.forecast_deadline,
        status="draft",
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
    run = create_run(db, question=question, profile_id=body.profile_id, mode=body.mode, as_of=body.as_of)
    if settings.embedded_worker:
        execute_run(db, run)
        db.refresh(run)
    db.commit()
    return _row(run)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    run = db.get(ForecastRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    tracks = db.scalars(select(ResearchTrack).where(ResearchTrack.run_id == run.id)).all()
    evidence = db.scalars(select(EvidenceItem).where(EvidenceItem.run_id == run.id)).all()
    payload = _row(run)
    payload["tracks"] = [_row(track) for track in tracks]
    payload["evidence"] = [_row(item) for item in evidence]
    for track in payload["tracks"]:
        for key in ("plan_json", "key_drivers_json", "counterarguments_json", "unresolved_json"):
            if track.get(key):
                track[key.replace("_json", "")] = json.loads(track[key])
        if "key_drivers" in track and isinstance(track["key_drivers"], list):
            pass
        if "unresolved" in track:
            track["unresolved_uncertainties"] = track["unresolved"]
    if run.aggregation_json:
        payload["aggregation"] = json.loads(run.aggregation_json)
    if run.budget_json:
        payload["budget"] = json.loads(run.budget_json)
    if run.prompt_versions_json:
        payload["prompt_versions"] = json.loads(run.prompt_versions_json)
    return payload


@app.get("/api/questions/{question_id}/report")
def report(question_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(404, "Question not found")
    payload = _question_out(db, question)
    latest_run = payload["runs"][0] if payload["runs"] else None
    if latest_run:
        payload["latest_run"] = get_run(latest_run["id"], db)
    return payload


@app.get("/api/questions/{question_id}/export.md")
def export_md(question_id: str, db: Session = Depends(get_db)) -> PlainTextResponse:
    payload = report(question_id, db)
    latest = payload.get("latest_run") or {}
    lines = [
        "# ForecastLab report",
        "",
        f"**Question:** {payload['original_text']}",
        f"**Status:** {payload['status']}",
        f"**Ensemble probability:** {payload.get('latest_probability')}",
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
    lines.append("## Evidence")
    for item in latest.get("evidence") or []:
        flag = "rejected" if item.get("rejected") else "accepted"
        lines.append(f"- [{flag}] {item.get('title')} — {item.get('url')}")
    return PlainTextResponse("\n".join(lines), media_type="text/markdown")


@app.get("/api/questions/{question_id}/export.json")
def export_json(question_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return report(question_id, db)


@app.post("/api/questions/{question_id}/watches")
def add_watch(question_id: str, body: WatchIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(404, "Question not found")
    watch = Watch(
        id=str(uuid.uuid4()),
        question_id=question.id,
        endpoint_url=body.endpoint_url,
        endpoint_type=body.endpoint_type,
        json_path=body.json_path,
        poll_seconds=body.poll_seconds,
        auto_rerun=body.auto_rerun,
        status="active",
    )
    db.add(watch)
    db.commit()
    return _row(watch)


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


def _benchmark_fields(row: dict[str, Any]) -> dict[str, str]:
    return {
        "question": str(row["question"]).strip(),
        "forecast_date": str(row["forecast_date"]),
        "resolution_date": str(row["resolution_date"]),
        "outcome": str(row["outcome"]),
        "resolution_source": str(row["resolution_source"]),
    }


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


@app.post("/api/benchmarks/import")
async def import_benchmarks(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    raw = (await file.read()).decode("utf-8")
    try:
        rows = _parse_benchmark_file(raw, file.filename or "benchmarks.csv")
    except Exception as exc:
        raise HTTPException(400, f"Could not parse file: {exc}") from exc
    created = 0
    duplicates = 0
    errors: list[str] = []
    for index, row in enumerate(rows, start=1):
        try:
            fields = _benchmark_fields(row)
            digest = import_hash(fields)
            if db.scalar(select(BenchmarkQuestion).where(BenchmarkQuestion.import_hash == digest)):
                duplicates += 1
                continue
            forecast_date = parse_datetime(fields["forecast_date"])
            resolution_date = parse_datetime(fields["resolution_date"])
            if forecast_date is None or resolution_date is None:
                raise ValueError("forecast_date and resolution_date are required")
            db.add(
                BenchmarkQuestion(
                    id=str(uuid.uuid4()),
                    question=fields["question"],
                    forecast_date=forecast_date,
                    resolution_date=resolution_date,
                    outcome=int(fields["outcome"]),
                    resolution_source=fields["resolution_source"],
                    category=str(row.get("category") or "uncategorized"),
                    provenance=str(row.get("provenance") or "user_import"),
                    import_hash=digest,
                    is_synthetic=str(row.get("is_synthetic", "")).lower() == "true",
                )
            )
            created += 1
        except Exception as exc:
            errors.append(f"row {index}: {exc}")
    db.commit()
    return {"created": created, "duplicates": duplicates, "errors": errors, "synthetic_label": True}


@app.post("/api/benchmarks/run")
def run_benchmarks(db: Session = Depends(get_db)) -> dict[str, Any]:
    questions = db.scalars(select(BenchmarkQuestion)).all()
    if not questions:
        raise HTTPException(400, "No benchmark questions imported")
    created = 0
    for item in questions:
        for profile_id in ("single_agent_baseline", "three_track_ensemble"):
            q = Question(
                id=str(uuid.uuid4()),
                original_text=item.question,
                notes=f"benchmark:{item.id}",
                status="draft",
            )
            db.add(q)
            db.flush()
            run = create_run(db, question=q, profile_id=profile_id, mode="demo", as_of=item.forecast_date)
            execute_run(db, run)
            db.refresh(run)
            latest = db.scalar(
                select(ForecastVersion)
                .where(ForecastVersion.run_id == run.id)
                .order_by(ForecastVersion.created_at.desc())
            )
            prob = latest.ensemble_probability if latest else None
            failed = prob is None
            db.add(
                BenchmarkResult(
                    id=str(uuid.uuid4()),
                    benchmark_question_id=item.id,
                    run_id=run.id,
                    profile_id=profile_id,
                    probability=prob,
                    brier=None if failed else brier_score(prob or 0, item.outcome),
                    log_loss_value=None if failed else log_loss(prob or 0, item.outcome),
                    cost_usd=run.cost_usd,
                    latency_ms=run.latency_ms,
                    failed=failed,
                )
            )
            created += 1
    db.commit()
    return {"created": created}


@app.get("/api/benchmarks/summary")
def benchmark_summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(BenchmarkResult)).all()
    by_profile: dict[str, list[BenchmarkResult]] = {}
    for row in rows:
        by_profile.setdefault(row.profile_id, []).append(row)
    profiles_out = []
    pairs: list[tuple[float, int]] = []
    questions = {item.id: item for item in db.scalars(select(BenchmarkQuestion)).all()}
    categories: dict[str, list[float]] = {}
    for profile_id, items in by_profile.items():
        briers = [item.brier for item in items if item.brier is not None]
        losses = [item.log_loss_value for item in items if item.log_loss_value is not None]
        costs = [item.cost_usd for item in items]
        lats = [item.latency_ms for item in items]
        failures = [item for item in items if item.failed]
        mean_brier = mean(briers)
        mean_cost = mean(costs)
        profiles_out.append(
            {
                "profile_id": profile_id,
                "n": len(items),
                "brier": mean_brier,
                "log_loss": mean(losses),
                "mean_cost_usd": mean_cost,
                "median_cost_usd": median(costs),
                "mean_latency_ms": mean([float(x) for x in lats]),
                "failure_rate": len(failures) / len(items) if items else None,
                "brier_per_dollar": None if not mean_cost else (mean_brier / mean_cost if mean_brier is not None else None),
            }
        )
        for item in items:
            question = questions.get(item.benchmark_question_id)
            if item.probability is not None and question is not None:
                pairs.append((item.probability, question.outcome))
            if item.brier is not None and question is not None:
                categories.setdefault(f"{profile_id}:{question.category}", []).append(item.brier)
    question_rows = []
    for item in rows:
        question = questions.get(item.benchmark_question_id)
        question_rows.append(
            {
                **_row(item),
                "question": question.question if question else None,
                "category": question.category if question else None,
                "outcome": question.outcome if question else None,
            }
        )
    return {
        "synthetic": True,
        "profiles": profiles_out,
        "rows": question_rows,
        "by_category": [
            {"key": key, "n": len(values), "brier": mean(values)} for key, values in sorted(categories.items())
        ],
        "profile_configs": [item.model_dump() for item in list_profiles()],
        "reliability": reliability_bins(pairs),
        "note": "Fixture outcomes are synthetic. This is not a claim about real-world calibration.",
    }


def create_app() -> FastAPI:
    return app
