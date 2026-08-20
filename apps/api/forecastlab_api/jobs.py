from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from forecastlab.errors import PermanentProviderError, TransientProviderError
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.models import Job, JobEvent, WorkerHeartbeat

RetryOutcome = Literal["rescheduled", "exhausted"]

STALE_AFTER = timedelta(seconds=180)
LEASE_SECONDS = 180
TRANSIENT_BACKOFF = timedelta(seconds=2)


def enqueue_job(session: Session, *, job_type: str, payload: dict, idempotency_key: str) -> Job:
    existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing:
        return existing
    now = utcnow()
    job = Job(
        id=str(uuid.uuid4()),
        job_type=job_type,
        status="pending",
        payload_json=json.dumps(payload),
        idempotency_key=idempotency_key,
        available_at=now,
    )
    session.add(job)
    session.add(
        JobEvent(
            id=str(uuid.uuid4()),
            job_id=job.id,
            stage="queued",
            message="Job queued",
            payload_json="{}",
        )
    )
    return job


def recover_stale_jobs(session: Session) -> int:
    now = utcnow()
    recovered = 0
    running = session.scalars(select(Job).where(Job.status == "running")).all()
    for job in running:
        expires = as_utc(job.lease_expires_at) if job.lease_expires_at else None
        heartbeat = as_utc(job.heartbeat_at or job.started_at) if (job.heartbeat_at or job.started_at) else None
        stale = False
        if expires is not None and expires < now:
            stale = True
        elif heartbeat is None or now - heartbeat > STALE_AFTER:
            stale = True
        if not stale:
            continue
        if job.attempts >= job.max_attempts:
            job.status = "failed"
            job.error = "stale_worker_max_attempts"
            job.error_category = "stale_worker_max_attempts"
            job.finished_at = now
            from forecastlab_api.experiments import fail_job_relatives

            fail_job_relatives(session, job, error="stale_worker_max_attempts", category="stale_worker_max_attempts")
        else:
            job.status = "pending"
            job.error = "recovered_after_stale_heartbeat"
            job.available_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            _reset_task_for_retry(session, job)
        recovered += 1
    return recovered


def claim_next_job(session: Session, *, owner: str = "worker") -> Job | None:
    recover_stale_jobs(session)
    now = utcnow()
    if session.get_bind().dialect.name == "sqlite":
        session.flush()
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        except Exception:
            # SQLAlchemy may already own the connection transaction; the
            # UPDATE ... WHERE status='pending' claim remains the guard.
            pass
    candidate = (
        select(Job.id)
        .where(Job.status == "pending")
        .where((Job.available_at.is_(None)) | (Job.available_at <= now))
        .order_by(Job.created_at.asc())
        .limit(1)
        .scalar_subquery()
    )
    result = session.execute(
        update(Job)
        .where(Job.id == candidate, Job.status == "pending")
        .values(
            status="running",
            attempts=Job.attempts + 1,
            started_at=now,
            heartbeat_at=now,
            lease_owner=owner,
            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
        )
        .returning(Job.id)
    )
    claimed_id = result.scalar_one_or_none()
    if claimed_id is None:
        return None
    return session.get(Job, claimed_id)


def heartbeat(session: Session, job: Job, *, stage: str, message: str, pct: float) -> None:
    now = utcnow()
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    job.progress_stage = stage
    job.progress_message = message
    job.progress_pct = pct
    session.add(
        JobEvent(
            id=str(uuid.uuid4()),
            job_id=job.id,
            stage=stage,
            message=message,
            payload_json=json.dumps({"pct": pct}),
        )
    )


def touch_job_lease(job_id: str, *, owner: str = "worker") -> None:
    from forecastlab_api.db import SessionLocal

    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None or job.status != "running":
            return
        now = utcnow()
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        job.lease_owner = owner
        session.commit()


def finish_job(session: Session, job: Job, *, ok: bool, error: str | None = None, category: str | None = None) -> None:
    job.status = "completed" if ok else "failed"
    job.error = error
    job.error_category = category
    job.finished_at = utcnow()
    job.heartbeat_at = job.finished_at
    job.progress_pct = 100 if ok else job.progress_pct
    session.add(
        JobEvent(
            id=str(uuid.uuid4()),
            job_id=job.id,
            stage="completed" if ok else "failed",
            message=error or "Job completed",
            payload_json=json.dumps({"category": category}),
        )
    )


def retry_job(session: Session, job: Job, *, error: str, category: str) -> RetryOutcome:
    history = []
    try:
        history = json.loads(job.error_history_json or "[]")
    except json.JSONDecodeError:
        history = []
    history.append({"error": error, "category": category, "at": utcnow().isoformat()})
    job.error_history_json = json.dumps(history)
    job.error = error
    job.error_category = category
    if job.attempts >= job.max_attempts:
        finish_job(session, job, ok=False, error=error, category=category)
        return "exhausted"
    job.status = "pending"
    job.available_at = utcnow() + TRANSIENT_BACKOFF
    job.lease_owner = None
    job.lease_expires_at = None
    _reset_task_for_retry(session, job)
    return "rescheduled"


def _reset_task_for_retry(session: Session, job: Job) -> None:
    try:
        payload = json.loads(job.payload_json or "{}")
    except json.JSONDecodeError:
        return
    task_id = payload.get("task_id")
    if not task_id:
        return
    from forecastlab_api.models import BenchmarkTask

    task = session.get(BenchmarkTask, task_id)
    if task is not None and task.status == "running":
        task.status = "pending"


def error_category(exc: Exception) -> str:
    if isinstance(exc, TransientProviderError):
        return "TransientProviderError"
    if isinstance(exc, PermanentProviderError):
        return "PermanentProviderError"
    name = exc.__class__.__name__
    if name in {
        "ConfigurationError",
        "EvidenceIntegrityError",
        "BudgetExceeded",
        "StructuredOutputError",
        "ExperimentEnvironmentMismatch",
    }:
        return name
    return "PermanentProviderError"


def is_transient(exc: Exception) -> bool:
    return isinstance(exc, TransientProviderError)


def touch_worker(session: Session, status: str = "idle") -> None:
    row = session.get(WorkerHeartbeat, "worker")
    if row is None:
        row = WorkerHeartbeat(id="worker", last_seen_at=utcnow(), status=status)
        session.add(row)
    else:
        row.last_seen_at = utcnow()
        row.status = status


def touch_worker_standalone(status: str = "running") -> None:
    from forecastlab_api.db import SessionLocal

    with SessionLocal() as session:
        touch_worker(session, status)
        session.commit()
