from __future__ import annotations

import json
import time
from datetime import timedelta

from sqlalchemy import select

from forecastlab.logging import setup_logging
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.db import Base, SessionLocal, engine
from forecastlab_api.jobs import claim_next_job, finish_job, recover_stale_jobs, touch_worker
from forecastlab_api.models import ForecastRun, Watch
from forecastlab_api.pipeline import execute_run
from forecastlab_api.seed import seed_sample_question, seed_synthetic_benchmarks
from forecastlab_api.watches import check_watch


def process_once() -> bool:
    with SessionLocal() as session:
        touch_worker(session, "idle")
        recover_stale_jobs(session)
        job = claim_next_job(session)
        session.commit()
        if job is None:
            due = session.scalars(select(Watch).where(Watch.status == "active")).all()
            ran = False
            for watch in due:
                last_checked = as_utc(watch.last_checked_at) if watch.last_checked_at else None
                stale = last_checked is None or utcnow() - last_checked >= timedelta(
                    seconds=watch.poll_seconds
                )
                if stale:
                    check_watch(session, watch)
                    ran = True
            if ran:
                session.commit()
            return False
        touch_worker(session, "running")
        try:
            payload = json.loads(job.payload_json)
            if job.job_type == "forecast_run":
                run = session.get(ForecastRun, payload["run_id"])
                if run is None:
                    raise RuntimeError("run_not_found")
                execute_run(session, run, job=job)
            elif job.job_type == "watch_check":
                watch = session.get(Watch, payload["watch_id"])
                if watch:
                    check_watch(session, watch)
            else:
                raise RuntimeError(f"unknown_job:{job.job_type}")
            finish_job(session, job, ok=True)
            session.commit()
            return True
        except Exception as exc:
            session.rollback()
            with SessionLocal() as retry_session:
                failed = retry_session.get(type(job), job.id)
                if failed:
                    finish_job(retry_session, failed, ok=False, error=str(exc)[:500])
                    run_id = json.loads(failed.payload_json).get("run_id")
                    if run_id:
                        run = retry_session.get(ForecastRun, run_id)
                        if run:
                            run.status = "failed"
                            run.error_message = str(exc)[:500]
                    retry_session.commit()
            return True


def main() -> None:
    setup_logging()
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        seed_sample_question(session)
        seed_synthetic_benchmarks(session)
        session.commit()
    while True:
        worked = process_once()
        time.sleep(0.4 if worked else 1.0)


if __name__ == "__main__":
    main()
