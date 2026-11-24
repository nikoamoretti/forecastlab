"""Owner and internal interfaces deliberately use separate authentication paths."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.official_releases import official_correction_evidence_url
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api import autopilot
from forecastlab_api.artifact_store import get_bytes
from forecastlab_api.auth import owner
from forecastlab_api.autopilot_models import (
    AppSetting,
    ExecutionLease,
    InboxEvent,
    ManagedQuestion,
    OutcomeProposal,
    QuestionAdjudication,
)
from forecastlab_api.autopilot_outcomes import confirm, metrics, proposal_list
from forecastlab_api.autopilot_store import insert_once, notify, state
from forecastlab_api.config import settings
from forecastlab_api.db import get_db

router = APIRouter()


@router.get("/api/autopilot")
def get_dashboard(db: Session = Depends(get_db)):
    return autopilot.dashboard(db)


@router.put("/api/autopilot/policy")
def approve_policy(body: autopilot.PolicyConfig, request: Request, db: Session = Depends(get_db)):
    policy = autopilot.approve_policy(db, body, approved_by=owner(request))
    return {"revision": policy.revision, "fingerprint": policy.fingerprint, "enabled": False}


class EnableBody(BaseModel):
    enabled: bool


@router.post("/api/autopilot/enable")
def enable(body: EnableBody, db: Session = Depends(get_db)):
    autopilot.set_enabled(db, body.enabled)
    return {"enabled": body.enabled}


@router.post("/api/autopilot/qualify")
def qualify(db: Session = Depends(get_db)):
    return autopilot.qualification_dispatch(db)


@router.get("/api/autopilot/inbox")
def inbox(offset: int = 0, db: Session = Depends(get_db)):
    rows = db.scalars(select(InboxEvent).order_by(InboxEvent.created_at.desc()).offset(max(0, offset)).limit(50)).all()
    return [{"id": r.id, "kind": r.kind, "title": r.title, "detail": r.detail, "question_id": r.question_id,
             "created_at": r.created_at, "read_at": r.read_at} for r in rows]


@router.post("/api/autopilot/inbox/{event_id}/read")
def mark_read(event_id: str, db: Session = Depends(get_db)):
    row = db.get(InboxEvent, event_id)
    if not row:
        raise HTTPException(404, "Inbox event not found")
    row.read_at = row.read_at or utcnow()
    return {"read_at": row.read_at}


@router.get("/api/autopilot/outcomes")
def outcomes(db: Session = Depends(get_db)):
    return proposal_list(db)


@router.post("/api/autopilot/outcomes/{proposal_id}/confirm")
def confirm_outcome(proposal_id: str, request: Request, db: Session = Depends(get_db)):
    row = confirm(db, proposal_id, confirmed_by=owner(request))
    return {"adjudication_id": row.id, "revision": row.revision, "outcome": row.outcome}


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal[0, 1] | None
    reason: str = Field(min_length=10, max_length=3000)
    evidence_url: str = Field(max_length=1000)

    @field_validator("evidence_url")
    @classmethod
    def official_release_url(cls, value: str) -> str:
        if not official_correction_evidence_url(value):
            raise ValueError("evidence_url must be an official BLS page or DOL economic-data release")
        return value


@router.post("/api/autopilot/outcomes/{proposal_id}/corrections")
def correct_outcome(proposal_id: str, body: Correction, request: Request, db: Session = Depends(get_db)):
    row = confirm(db, proposal_id, confirmed_by=owner(request), correction=body.model_dump())
    return {"adjudication_id": row.id, "revision": row.revision, "outcome": row.outcome}


@router.get("/api/autopilot/questions/{question_id}/adjudications")
def adjudications(question_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(select(QuestionAdjudication).where(QuestionAdjudication.question_id == question_id)
        .order_by(QuestionAdjudication.revision)).all()
    return [{"id": r.id, "revision": r.revision, "outcome": r.outcome, "confirmed_by": r.confirmed_by,
             "created_at": r.created_at, "evidence": json.loads(r.evidence_json)} for r in rows]


@router.get("/api/autopilot/outcomes/{proposal_id}/source")
def outcome_source(proposal_id: str, db: Session = Depends(get_db)):
    row = db.get(OutcomeProposal, proposal_id)
    if not row:
        raise HTTPException(404, "Outcome proposal not found")
    artifact = json.loads(row.payload_json)["artifact"]
    pdf = artifact.get("content_type", "").split(";", 1)[0] == "application/pdf"
    return Response(get_bytes(artifact), media_type="application/pdf" if pdf else "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="official-release.{"pdf" if pdf else "html"}"', "Cache-Control": "private, no-store"})


@router.get("/api/autopilot/metrics")
def get_metrics(db: Session = Depends(get_db)):
    return metrics(db)


class ScheduleReview(BaseModel):
    action: Literal["resume_unchanged", "cancel"]


@router.post("/api/autopilot/questions/{question_id}/schedule-review")
def schedule_review(question_id: str, body: ScheduleReview, db: Session = Depends(get_db)):
    row = db.get(ManagedQuestion, question_id)
    if not row or row.status != "schedule_review":
        raise HTTPException(409, "Question is not awaiting schedule review")
    if body.action == "resume_unchanged":
        sources = autopilot.database_selection_sources()
        if not autopilot._calendar_matches(row, sources):
            raise HTTPException(409, "The schedule still differs; cancel this event and review a new manual question")
    row.status = "active" if body.action == "resume_unchanged" else "cancelled"
    return {"status": row.status}


@router.get("/internal/cron")
def cron():
    if not settings.production:
        return {"status": "disabled_outside_production"}
    from forecastlab_api.backup import daily_backup
    backup = daily_backup()
    try:
        result = autopilot.reconcile()
    except Exception as exc:
        # Source checks must not starve an already persisted forecast job.
        from forecastlab_api.db import SessionLocal
        with SessionLocal() as session:
            notify(session, "scheduler:" + utcnow().strftime("%Y-%m-%d"), "incident", "Source checks need attention",
                f"Scheduler error: {type(exc).__name__}. Queued work remains recoverable.")
            session.commit()
        result = {"status": "source_check_failed", "error_category": type(exc).__name__}
    from forecastlab_api.worker import process_once
    return {**result, "backup": backup, "processed": process_once()}


@router.post("/internal/process")
def process():
    if settings.cloud and not settings.production:
        raise HTTPException(409, "Preview execution is disabled")
    from forecastlab_api.worker import process_once
    return {"processed": process_once()}


@router.get("/internal/readiness")
def internal_readiness(db: Session = Depends(get_db)):
    from sqlalchemy import text
    current = state(db)
    return {"schema_revision": db.execute(text("SELECT version_num FROM alembic_version")).scalar_one(),
        "automatic_spending_enabled": current.enabled, "qualification_enabled": current.qualification_enabled,
        "owner_configured": bool(settings.owner_github_id),
        "oauth_configured": bool(settings.github_client_id and settings.github_client_secret),
        "restore_verified": bool(json.loads(current.restore_receipt_json).get("verified")),
        "release_revision": settings.deployment_revision}


@router.post("/internal/source-preflight")
def source_preflight():
    if not settings.production:
        raise HTTPException(409, "Preflight requires the production service")
    sources = autopilot.database_selection_sources()
    from forecastlab.question_selection import choose_questions
    candidates, selection_gaps = choose_questions(sources.releases, sources.snapshots, now=utcnow())
    return {"checked_at": sources.checked_at, "gaps": sources.gaps + selection_gaps, "release_count": len(sources.releases),
        "indicators": sorted(sources.snapshots), "retained_calendars": len(sources.documents),
        "source_diagnostics": sources.diagnostics,
        "selectable_questions": [{"indicator": c.macro.indicator, "period": c.macro.observation_period,
            "release_at": c.macro.release_at, "schedule_basis": c.schedule.schedule_basis} for c in candidates],
        "paid_provider_calls": 0}


@router.post("/internal/release/pause")
def release_pause(db: Session = Depends(get_db)):
    from forecastlab_api.models import Job
    insert_once(db, ExecutionLease, {"key": "paid_worker", "generation": 0})
    lease = db.scalar(select(ExecutionLease).where(ExecutionLease.key == "paid_worker").with_for_update())
    current = state(db, lock=True)
    control = db.get(AppSetting, "release_control")
    previous = json.loads(control.value_json) if control else {}
    was_enabled = previous.get("was_enabled", False) if previous.get("dispatch_paused") else current.enabled
    pause_reason = previous.get("pause_reason", "") if previous.get("dispatch_paused") else current.pause_reason
    db.merge(AppSetting(key="release_control", value_json=json.dumps({"dispatch_paused": True,
        "was_enabled": was_enabled, "pause_reason": pause_reason, "paused_at": utcnow().isoformat()})))
    current.enabled = current.qualification_enabled = False
    if not previous.get("dispatch_paused") or current.pause_reason == "Release maintenance":
        current.pause_reason = "Release maintenance"
    running = list(db.scalars(select(Job.id).where(Job.status == "running")))
    leased = bool(lease and lease.expires_at and as_utc(lease.expires_at) > utcnow())
    return {"paused": True, "drained": not running and not leased, "running_job_ids": running}


@router.post("/internal/release/complete")
def release_complete(db: Session = Depends(get_db)):
    lease = db.scalar(select(ExecutionLease).where(ExecutionLease.key == "paid_worker").with_for_update())
    if lease and lease.expires_at and as_utc(lease.expires_at) > utcnow():
        raise HTTPException(409, "Wait for active execution to drain")
    control = db.get(AppSetting, "release_control")
    previous = json.loads(control.value_json) if control else {}
    current = state(db, lock=True)
    if previous.get("dispatch_paused") and current.pause_reason == "Release maintenance":
        gaps = autopilot.enable_gaps(db) if previous.get("was_enabled") else []
        if previous.get("was_enabled") and not gaps:
            current.enabled, current.pause_reason = True, ""
        else:
            current.pause_reason = "; ".join(gaps) or previous.get("pause_reason") or "Autopilot remains paused"
    db.merge(AppSetting(key="release_control", value_json=json.dumps({"dispatch_paused": False,
        "revision": settings.deployment_revision, "completed_at": utcnow().isoformat()})))
    return {"dispatch_paused": False, "automatic_spending_enabled": current.enabled}
