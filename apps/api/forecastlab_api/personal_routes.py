from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forecastlab.macro import MacroSpec
from forecastlab_api.config import settings
from forecastlab_api.db import get_db
from forecastlab_api.models import ForecastRun, ForecastVersion, Job, PersonalForecast, ProspectiveCohort, Question
from forecastlab_api.personal_forecasts import create_draft, draft_out, launch_draft, prepare_draft
from forecastlab_api.prospective import (
    CohortIn,
    cohort_report,
    create_cohort,
    freeze_cohort,
    launch_cohort,
    record_outcome,
)

router = APIRouter()


class DraftIn(BaseModel):
    question: str = Field(default="", max_length=5000)
    mode: Literal["demo", "live", "backtest"] = "live"
    as_of: datetime | None = None
    request_key: str = Field(min_length=8, max_length=128)
    macro: MacroSpec | None = None


class LaunchIn(BaseModel):
    review: dict[str, Any] = Field(default_factory=dict)


class ReviewIn(BaseModel):
    reviewed_by: str = Field(min_length=1, max_length=128)


class OutcomeIn(BaseModel):
    outcome: Literal[0, 1] | None
    source_url: str = Field(min_length=10, max_length=2048)
    evidence: str = Field(min_length=1, max_length=10000)
    confirmed_by: str = Field(min_length=1, max_length=128)


@router.get("/api/question-suggestions")
def get_suggestions(db: Session = Depends(get_db)) -> dict:
    from forecastlab_api.question_suggestions import suggestions
    return suggestions(db)


@router.post("/api/question-suggestions/{suggestion_id}/draft", status_code=201)
def post_suggestion_draft(suggestion_id: str, db: Session = Depends(get_db)) -> dict:
    from forecastlab_api.question_suggestions import suggestion_draft
    run = suggestion_draft(db, suggestion_id)
    if settings.embedded_worker and run.status == "preparing":
        prepare_draft(db, run)
    db.expire_all()
    return draft_out(db, run.id)


@router.post("/api/forecast-drafts", status_code=201)
def post_draft(body: DraftIn, db: Session = Depends(get_db)) -> dict:
    if not body.question.strip() and body.macro is None:
        raise HTTPException(422, "Question or macro specification required")
    run = create_draft(db, **body.model_dump(exclude={"macro"}), macro=body.macro)
    if settings.embedded_worker and run.status == "preparing":
        prepare_draft(db, run)
    db.expire_all()
    return draft_out(db, run.id)


@router.get("/api/forecast-drafts/{run_id}")
def get_draft(run_id: str, db: Session = Depends(get_db)) -> dict:
    return draft_out(db, run_id)


@router.post("/api/forecast-drafts/{run_id}/launch")
def post_launch(run_id: str, body: LaunchIn, db: Session = Depends(get_db)) -> dict:
    run = launch_draft(db, run_id, body.review)
    if settings.embedded_worker and run.status == "pending":
        from forecastlab_api.pipeline import execute_run
        execute_run(db, run)
    return draft_out(db, run.id)


@router.get("/api/forecast-summaries")
def summaries(offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
              include_fixtures: bool = False, db: Session = Depends(get_db)) -> dict:
    from forecastlab_api.autopilot_models import ManagedQuestion, OutcomeProposal, QuestionAdjudication
    # Rank first, paginate second. Fetch summary columns only, never full report
    # trees or per-question queries.
    order_time = func.coalesce(PersonalForecast.created_at, Job.created_at, ForecastRun.started_at, ForecastRun.finished_at)
    ranked = select(ForecastRun.id.label("run_id"), ForecastRun.question_id, order_time.label("run_created_at"),
        func.count().over(partition_by=ForecastRun.question_id).label("latest_version"),
        func.row_number().over(partition_by=ForecastRun.question_id, order_by=(order_time.desc(), ForecastRun.id.desc())).label("rank")
    ).outerjoin(PersonalForecast, PersonalForecast.run_id == ForecastRun.id).outerjoin(Job, Job.id == ForecastRun.job_id).subquery()
    query = select(Question.id, Question.original_text, Question.created_at, Question.stale,
        ForecastRun.id.label("run_id"), ForecastRun.profile_id, ForecastRun.mode, ForecastRun.status,
        ForecastRun.finished_at, ForecastRun.total_cost_usd.label("cost_usd"),
        ranked.c.run_created_at,
        ranked.c.latest_version,
        ManagedQuestion.status.label("automation_status"), ManagedQuestion.release_event,
        ManagedQuestion.last_checked_at.label("sources_checked_at"),
        select(OutcomeProposal.id).where(OutcomeProposal.question_id == Question.id,
            ~select(QuestionAdjudication.id).where(QuestionAdjudication.question_id == Question.id).correlate(Question).exists())
            .correlate(Question).exists().label("pending_outcome"),
        PersonalForecast.outcome_status, ForecastVersion.ensemble_probability.label("probability"),
    ).outerjoin(ranked, (ranked.c.question_id == Question.id) & (ranked.c.rank == 1)).outerjoin(
        ManagedQuestion, ManagedQuestion.question_id == Question.id)
    query = query.outerjoin(ForecastRun, ForecastRun.id == ranked.c.run_id).outerjoin(
        PersonalForecast, PersonalForecast.run_id == ForecastRun.id).outerjoin(
        ForecastVersion, ForecastVersion.run_id == ForecastRun.id).where(Question.is_benchmark.is_(False))
    if not include_fixtures:
        query = query.where(Question.requested_mode != "demo", func.coalesce(ForecastRun.synthetic_fixture_run, False).is_(False))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(query.order_by(Question.created_at.desc(), Question.id).offset(offset).limit(limit)).mappings().all()
    return {"items": [dict(row) for row in rows], "total": total, "offset": offset, "limit": limit}


@router.post("/api/prospective/cohorts", status_code=201)
def post_cohort(body: CohortIn, db: Session = Depends(get_db)) -> dict:
    row = create_cohort(db, body)
    return cohort_report(db, row.id)


@router.get("/api/prospective/cohorts")
def list_cohorts(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(ProspectiveCohort).order_by(ProspectiveCohort.created_at.desc()).limit(50)).all()
    return {"cohorts": [{"id": row.id, "name": row.name, "status": row.status, "budget_usd": row.budget_usd} for row in rows]}


@router.get("/api/prospective/cohorts/{cohort_id}")
def get_cohort(cohort_id: str, db: Session = Depends(get_db)) -> dict:
    return cohort_report(db, cohort_id)


@router.post("/api/prospective/cohorts/{cohort_id}/freeze")
def post_freeze(cohort_id: str, body: ReviewIn, db: Session = Depends(get_db)) -> dict:
    freeze_cohort(db, cohort_id, reviewed_by=body.reviewed_by)
    return cohort_report(db, cohort_id)


@router.post("/api/prospective/cohorts/{cohort_id}/launch")
def post_cohort_launch(cohort_id: str, db: Session = Depends(get_db)) -> dict:
    launch_cohort(db, cohort_id)
    return cohort_report(db, cohort_id)


@router.post("/api/prospective/entries/{entry_id}/outcomes", status_code=201)
def post_outcome(entry_id: str, body: OutcomeIn, db: Session = Depends(get_db)) -> dict:
    row = record_outcome(db, entry_id, **body.model_dump())
    return {"id": row.id, "revision": row.revision, "outcome": row.outcome}


@router.post("/api/prospective/cohorts/{cohort_id}/official-outcomes/process")
def process_official_outcomes(cohort_id: str, db: Session = Depends(get_db)) -> dict:
    """Run the narrow official-first-release adjudicator; never calls a model."""
    from forecastlab_api.official_macro_outcomes import process_due_prospective_entries
    cohort = db.get(ProspectiveCohort, cohort_id)
    if cohort is None:
        raise HTTPException(404, "Cohort not found")
    # A cohort-specific request must not process or mutate other cohorts.
    amendments = process_due_prospective_entries(db, cohort_id=cohort_id)
    return {"amendments": [{"id": row.id, "status": row.status, "exception_code": row.exception_code} for row in amendments],
            "report": cohort_report(db, cohort_id)}


@router.post("/api/prospective/cohorts/{cohort_id}/score")
def score_cohort(cohort_id: str, db: Session = Depends(get_db)) -> dict:
    report = cohort_report(db, cohort_id)
    row = db.get(ProspectiveCohort, cohort_id)
    if row is None:
        raise HTTPException(404, "Cohort not found")
    row.status = report["status"]
    db.commit()
    return report


class MacroSettingsIn(BaseModel):
    fred_api_key: str = Field(max_length=256)


@router.get("/api/macro/settings")
def macro_settings() -> dict:
    from forecastlab_api.secrets import load_secrets
    return {"fred_api_key_set": bool(load_secrets().get("fred_api_key")), "secrets_managed_externally": settings.cloud}


@router.patch("/api/macro/settings")
def patch_macro_settings(body: MacroSettingsIn) -> dict:
    if settings.cloud:
        raise HTTPException(422, "Manage the production FRED key in Vercel environment settings")
    from forecastlab_api.secrets import load_secrets, save_secrets
    data = load_secrets()
    data["fred_api_key"] = body.fred_api_key.strip() or None
    save_secrets(data)
    return {"fred_api_key_set": bool(data["fred_api_key"])}
