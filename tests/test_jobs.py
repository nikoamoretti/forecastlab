from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from forecastlab.timeutil import utcnow
from forecastlab_api.db import Base
from forecastlab_api.jobs import claim_next_job, enqueue_job, recover_stale_jobs
from forecastlab_api.models import Job


def test_job_idempotency_and_stale_recovery(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/jobs.db")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        first = enqueue_job(session, job_type="forecast_run", payload={"run_id": "abc"}, idempotency_key="run:abc")
        second = enqueue_job(session, job_type="forecast_run", payload={"run_id": "abc"}, idempotency_key="run:abc")
        session.commit()
        assert first.id == second.id
        claimed = claim_next_job(session)
        session.commit()
        assert claimed is not None
        claimed.heartbeat_at = utcnow() - timedelta(minutes=10)
        session.commit()
        recovered = recover_stale_jobs(session)
        session.commit()
        assert recovered >= 1
        row = session.get(Job, claimed.id)
        assert row is not None
        assert row.status in {"pending", "failed"}
