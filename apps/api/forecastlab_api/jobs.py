from __future__ import annotations

import json
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.models import Job, JobEvent, WorkerHeartbeat

STALE_AFTER = timedelta(seconds=45)


def enqueue_job(session: Session, *, job_type: str, payload: dict, idempotency_key: str) -> Job:
    existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing:
        return existing
    job = Job(
        id=str(uuid.uuid4()),
        job_type=job_type,
        status="pending",
        payload_json=json.dumps(payload),
        idempotency_key=idempotency_key,
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
        heartbeat = as_utc(job.heartbeat_at or job.started_at) if (job.heartbeat_at or job.started_at) else None
        if heartbeat is None or now - heartbeat > STALE_AFTER:
            if job.attempts >= job.max_attempts:
                job.status = "failed"
                job.error = "stale_worker_max_attempts"
                job.finished_at = now
            else:
                job.status = "pending"
                job.error = "recovered_after_stale_heartbeat"
            recovered += 1
    return recovered


def claim_next_job(session: Session) -> Job | None:
    recover_stale_jobs(session)
    job = session.scalar(select(Job).where(Job.status == "pending").order_by(Job.created_at.asc()).limit(1))
    if job is None:
        return None
    job.status = "running"
    job.attempts += 1
    job.started_at = utcnow()
    job.heartbeat_at = job.started_at
    return job


def heartbeat(session: Session, job: Job, *, stage: str, message: str, pct: float) -> None:
    job.heartbeat_at = utcnow()
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


def finish_job(session: Session, job: Job, *, ok: bool, error: str | None = None) -> None:
    job.status = "completed" if ok else "failed"
    job.error = error
    job.finished_at = utcnow()
    job.heartbeat_at = job.finished_at
    job.progress_pct = 100 if ok else job.progress_pct
    session.add(
        JobEvent(
            id=str(uuid.uuid4()),
            job_id=job.id,
            stage="completed" if ok else "failed",
            message=error or "Job completed",
            payload_json="{}",
        )
    )


def touch_worker(session: Session, status: str = "idle") -> None:
    row = session.get(WorkerHeartbeat, "worker")
    if row is None:
        row = WorkerHeartbeat(id="worker", last_seen_at=utcnow(), status=status)
        session.add(row)
    else:
        row.last_seen_at = utcnow()
        row.status = status


claim_next_job = claim_next_job
finish_job = finish_job
enqueue_job = enqueue_job
recover_stale_jobs = recover_stale_jobs
touch_worker = touch_worker
