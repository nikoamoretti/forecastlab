from datetime import timedelta
from threading import Thread

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from forecastlab.errors import PermanentProviderError, TransientProviderError
from forecastlab.timeutil import utcnow
from forecastlab_api.db import Base
from forecastlab_api.jobs import (
    claim_next_job,
    enqueue_job,
    error_category,
    is_transient,
    recover_stale_jobs,
    retry_job,
)
from forecastlab_api.models import Job


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/jobs.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_job_idempotency_and_stale_recovery(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        first = enqueue_job(session, job_type="forecast_run", payload={"run_id": "abc"}, idempotency_key="run:abc")
        second = enqueue_job(session, job_type="forecast_run", payload={"run_id": "abc"}, idempotency_key="run:abc")
        session.commit()
        assert first.id == second.id
        claimed = claim_next_job(session)
        session.commit()
        assert claimed is not None
        claimed.heartbeat_at = utcnow() - timedelta(minutes=10)
        claimed.lease_expires_at = utcnow() - timedelta(minutes=1)
        session.commit()
        recovered = recover_stale_jobs(session)
        session.commit()
        assert recovered >= 1
        row = session.get(Job, claimed.id)
        assert row is not None
        assert row.status in {"pending", "failed"}


def test_two_workers_cannot_claim_same_job(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        enqueue_job(session, job_type="forecast_run", payload={"run_id": "one"}, idempotency_key="run:one")
        session.commit()
    claimed: list[str | None] = []

    def worker() -> None:
        with SessionLocal() as session:
            job = claim_next_job(session)
            session.commit()
            claimed.append(job.id if job else None)

    threads = [Thread(target=worker), Thread(target=worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    winners = [item for item in claimed if item]
    assert len(winners) == 1


def test_fresh_heartbeat_is_not_stale(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        enqueue_job(session, job_type="forecast_run", payload={"run_id": "hb"}, idempotency_key="run:hb")
        job = claim_next_job(session)
        session.commit()
        assert job is not None
        job.heartbeat_at = utcnow()
        job.lease_expires_at = utcnow() + timedelta(seconds=180)
        session.commit()
        assert recover_stale_jobs(session) == 0


def test_retry_classification() -> None:
    assert is_transient(TransientProviderError("timeout"))
    assert not is_transient(PermanentProviderError("no"))
    assert error_category(TransientProviderError("429")) == "TransientProviderError"


def test_retry_exhaustion_marks_failed(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        job = enqueue_job(session, job_type="forecast_run", payload={"run_id": "x"}, idempotency_key="run:x")
        job.max_attempts = 1
        job.attempts = 1
        session.commit()
        retry_job(session, job, error="timeout", category="TransientProviderError")
        session.commit()
        row = session.get(Job, job.id)
        assert row is not None
        assert row.status == "failed"
