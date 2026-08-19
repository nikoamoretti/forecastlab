from __future__ import annotations

import json
import time
from datetime import timedelta

from sqlalchemy import select

from forecastlab.logging import setup_logging
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.experiments import execute_benchmark_task, fail_job_relatives
from forecastlab_api.jobs import (
    claim_next_job,
    error_category,
    finish_job,
    is_transient,
    recover_stale_jobs,
    retry_job,
    touch_worker,
)
from forecastlab_api.migrate import apply_schema
from forecastlab_api.models import BenchmarkTask, ForecastRun, Job, Watch
from forecastlab_api.pipeline import execute_run
from forecastlab_api.seed import seed_sample_question, seed_synthetic_benchmarks
from forecastlab_api.watches import check_watch


def process_once() -> bool:
    from forecastlab_api.db import SessionLocal

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
                stale = last_checked is None or utcnow() - last_checked >= timedelta(seconds=watch.poll_seconds)
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
            elif job.job_type == "benchmark_task":
                task = session.get(BenchmarkTask, payload["task_id"])
                if task is None:
                    raise RuntimeError("benchmark_task_not_found")
                execute_benchmark_task(session, task, job=job)
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
            from forecastlab_api.db import SessionLocal as RetrySessionLocal

            with RetrySessionLocal() as retry_session:
                failed = retry_session.get(Job, job.id)
                if failed:
                    category = error_category(exc)
                    message = str(exc)[:500]
                    if is_transient(exc):
                        outcome = retry_job(retry_session, failed, error=message, category=category)
                        if outcome == "exhausted":
                            fail_job_relatives(retry_session, failed, error=message, category=category)
                    else:
                        finish_job(retry_session, failed, ok=False, error=message, category=category)
                        fail_job_relatives(retry_session, failed, error=message, category=category)
                    retry_session.commit()
            return True


def drain_jobs(*, max_steps: int = 200) -> int:
    processed = 0
    for _ in range(max_steps):
        if not process_once():
            break
        processed += 1
    return processed


def main() -> None:
    from forecastlab_api.db import SessionLocal

    setup_logging()
    apply_schema()
    with SessionLocal() as session:
        seed_sample_question(session)
        seed_synthetic_benchmarks(session)
        session.commit()
    while True:
        worked = process_once()
        time.sleep(0.4 if worked else 1.0)


if __name__ == "__main__":
    main()
