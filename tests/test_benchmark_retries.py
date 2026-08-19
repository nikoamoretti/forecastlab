from __future__ import annotations

import time
from datetime import timedelta
from io import BytesIO

import pytest
from sqlalchemy import func, select

from forecastlab.engine import run_forecast_engine
from forecastlab.errors import StructuredOutputError, TransientProviderError, classify_http_status
from forecastlab.providers.base import ChatResult
from forecastlab.providers.mock import SAMPLE_QUESTION, MockModelProvider, MockSearchProvider
from forecastlab.schemas import ModelUsage
from forecastlab_api.experiments import CRASH_HOOKS, InjectedCrash, execute_benchmark_task
from forecastlab_api.models import (
    BenchmarkResult,
    BenchmarkTask,
    EvidenceItem,
    ForecastRun,
    ForecastVersion,
    Job,
    Question,
    ResearchTrack,
    WorkerHeartbeat,
)
from forecastlab_api.worker import drain_jobs, process_once


def _csv_row(question: str = "Will retry identity series happen before 2026-01-01?") -> bytes:
    header = (
        "question,forecast_date,resolution_date,outcome,resolution_source,category,provenance,"
        "is_synthetic,exact_yes,exact_no,resolution_deadline,authoritative_source\n"
    )
    row = (
        f"{question},2024-08-01,2026-01-01,1,https://fixtures.forecastlab.local/retry,test,retry-test,"
        "true,Yes if the fixture series happens,No if it does not,2026-01-01,"
        "https://fixtures.forecastlab.local/retry\n"
    )
    return (header + row).encode("utf-8")


def _import_one(client, name: str, question: str | None = None) -> dict:
    response = client.post(
        "/api/benchmarks/import",
        files={"file": (f"{name}.csv", BytesIO(_csv_row(question or name)), "text/csv")},
        data={"name": name},
    )
    assert response.status_code == 200
    return response.json()


def _create_experiment(client, *, name: str, profiles: list[str] | None = None, question: str | None = None) -> dict:
    imported = _import_one(client, name, question=question)
    created = client.post(
        "/api/experiments",
        json={
            "dataset_id": imported["dataset_id"],
            "profile_ids": profiles or ["single_agent_equal_budget_v1"],
        },
    )
    assert created.status_code == 200
    return created.json()


def _task_for(session, experiment_id: str, profile_id: str | None = None) -> BenchmarkTask:
    stmt = select(BenchmarkTask).where(BenchmarkTask.experiment_id == experiment_id)
    if profile_id:
        stmt = stmt.where(BenchmarkTask.profile_id == profile_id)
    task = session.scalars(stmt).first()
    assert task is not None
    return task


def _assert_identity(session, task_id: str) -> None:
    task = session.get(BenchmarkTask, task_id)
    assert task is not None
    questions = session.scalars(select(Question).where(Question.notes == f"benchmark-task:{task_id}")).all()
    runs = session.scalars(select(ForecastRun).where(ForecastRun.benchmark_task_id == task_id)).all()
    versions = session.scalars(select(ForecastVersion).where(ForecastVersion.run_id == task.run_id)).all() if task.run_id else []
    results = session.scalars(select(BenchmarkResult).where(BenchmarkResult.benchmark_task_id == task_id)).all()
    assert len(questions) == 1
    assert len(runs) == 1
    assert task.question_id == questions[0].id
    assert task.run_id == runs[0].id
    assert runs[0].question_id == questions[0].id
    if task.run_id:
        tracks = session.scalars(select(ResearchTrack).where(ResearchTrack.run_id == task.run_id)).all()
        evidence = session.scalars(select(EvidenceItem).where(EvidenceItem.run_id == task.run_id)).all()
        track_types = [item.track_type for item in tracks]
        assert len(track_types) == len(set(track_types))
        evidence_ids = [item.id for item in evidence]
        assert len(evidence_ids) == len(set(evidence_ids))
    assert len(versions) <= 1
    assert len(results) <= 1


def _run_with_crash(session, task: BenchmarkTask, phase: str) -> None:
    def boom(name: str) -> None:
        raise InjectedCrash(name)

    CRASH_HOOKS[phase] = boom
    try:
        with pytest.raises(InjectedCrash):
            execute_benchmark_task(session, task)
    finally:
        CRASH_HOOKS.pop(phase, None)


@pytest.mark.parametrize(
    "phase",
    [
        "after_task_question_identity",
        "after_run_creation",
        "after_forecast_version",
        "after_benchmark_result",
        "after_task_completion_before_counts",
    ],
)
def test_crash_boundaries_recover_without_duplicates(client, phase) -> None:
    from forecastlab.providers.mock import MockModelProvider
    from forecastlab_api.db import SessionLocal

    created = _create_experiment(client, name=f"crash_{phase}")
    experiment_id = created["id"]
    calls = {"n": 0}
    original = MockModelProvider.complete_json

    def spy(self, **kwargs):
        calls["n"] += 1
        return original(self, **kwargs)

    MockModelProvider.complete_json = spy  # type: ignore[method-assign]
    try:
        with SessionLocal() as session:
            task = _task_for(session, experiment_id)
            task_id = task.id
            _run_with_crash(session, task, phase)
        after_crash = calls["n"]
        with SessionLocal() as session:
            task = session.get(BenchmarkTask, task_id)
            assert task is not None
            execute_benchmark_task(session, task)
            session.commit()
            _assert_identity(session, task_id)
            task = session.get(BenchmarkTask, task_id)
            assert task is not None
            assert task.status in {"completed", "failed"}
            experiment = task.experiment_id
            from forecastlab_api.models import BenchmarkExperiment

            row = session.get(BenchmarkExperiment, experiment)
            assert row is not None
            assert row.status in {"completed", "completed_with_failures", "failed"}
        if phase in {"after_forecast_version", "after_benchmark_result", "after_task_completion_before_counts"}:
            assert calls["n"] == after_crash
    finally:
        MockModelProvider.complete_json = original  # type: ignore[method-assign]


def test_transient_retry_reuses_run(client, monkeypatch) -> None:
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.pipeline import execute_run

    monkeypatch.setattr("forecastlab_api.jobs.TRANSIENT_BACKOFF", timedelta(0))
    created = _create_experiment(client, name="transient_reuse")
    experiment_id = created["id"]
    original = execute_run
    state = {"calls": 0}

    def flaky(session, run, job=None, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise TransientProviderError("simulated_timeout")
        return original(session, run, job=job, **kwargs)

    monkeypatch.setattr("forecastlab_api.pipeline.execute_run", flaky)
    monkeypatch.setattr("forecastlab_api.worker.execute_run", flaky)
    monkeypatch.setattr("forecastlab_api.experiments.execute_run", flaky)
    first = process_once()
    assert first is True
    with SessionLocal() as session:
        task = _task_for(session, experiment_id)
        job = session.scalars(select(Job).where(Job.payload_json.contains(task.id))).one()
        assert job.status == "pending"
        assert job.attempts == 1
        run_id = task.run_id
        assert run_id
    assert process_once() is True
    with SessionLocal() as session:
        task = _task_for(session, experiment_id)
        _assert_identity(session, task.id)
        assert task.run_id == run_id
        assert task.status == "completed"
        results = session.scalars(select(BenchmarkResult).where(BenchmarkResult.benchmark_task_id == task.id)).all()
        assert len(results) == 1
        assert results[0].failed is False
        job = session.scalars(select(Job).where(Job.payload_json.contains(task.id))).one()
        assert job.status == "completed"


def test_exhausted_retry_marks_graph_failed(client, monkeypatch) -> None:
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import BenchmarkExperiment

    monkeypatch.setattr("forecastlab_api.jobs.TRANSIENT_BACKOFF", timedelta(0))
    created = _create_experiment(client, name="exhausted_retry")
    experiment_id = created["id"]
    with SessionLocal() as session:
        task = _task_for(session, experiment_id)
        job = session.scalars(select(Job).where(Job.payload_json.contains(task.id))).one()
        job.max_attempts = 2
        session.commit()
        task_id = task.id
        job_id = job.id

    def always_fail(*_args, **_kwargs):
        raise TransientProviderError("always")

    monkeypatch.setattr("forecastlab_api.experiments.execute_run", always_fail)
    monkeypatch.setattr("forecastlab_api.pipeline.execute_run", always_fail)
    drain_jobs(max_steps=10)
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        task = session.get(BenchmarkTask, task_id)
        experiment = session.get(BenchmarkExperiment, experiment_id)
        assert job is not None and task is not None and experiment is not None
        run = session.get(ForecastRun, task.run_id) if task.run_id else None
        assert job.status == "failed"
        assert task.status == "failed"
        assert run is not None
        assert run.status == "failed"
        assert experiment.status == "failed"
        results = session.scalars(select(BenchmarkResult).where(BenchmarkResult.benchmark_task_id == task.id)).all()
        assert len(results) == 1
        assert results[0].failed is True


def test_mixed_experiment_completed_with_failures(client, monkeypatch) -> None:
    from forecastlab.errors import ConfigurationError
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import BenchmarkExperiment
    from forecastlab_api.pipeline import execute_run

    created = _create_experiment(
        client,
        name="mixed_terminal",
        profiles=["single_agent_equal_budget_v1", "three_track_equal_budget_v1"],
    )
    original = execute_run

    def one_fails(session, run, job=None, **kwargs):
        if run.profile_id == "three_track_equal_budget_v1":
            raise ConfigurationError(["forced_fail"])
        return original(session, run, job=job, **kwargs)

    monkeypatch.setattr("forecastlab_api.experiments.execute_run", one_fails)
    drain_jobs(max_steps=20)
    summary = client.get(f"/api/experiments/{created['id']}/summary").json()
    assert summary["progress"]["status"] == "completed_with_failures"
    assert summary["progress"]["completed_tasks"] == 1
    assert summary["progress"]["failed_tasks"] == 1
    assert summary["profiles"]
    with SessionLocal() as session:
        experiment = session.get(BenchmarkExperiment, created["id"])
        assert experiment is not None
        assert experiment.status == "completed_with_failures"
        tasks = session.scalars(select(BenchmarkTask).where(BenchmarkTask.experiment_id == experiment.id)).all()
        assert {item.status for item in tasks} == {"completed", "failed"}
        assert session.scalar(select(func.count()).select_from(BenchmarkResult)) == 2


def test_worker_health_stays_fresh_during_long_call(client, monkeypatch) -> None:
    from forecastlab.providers.mock import MockModelProvider
    from forecastlab_api.db import SessionLocal

    monkeypatch.setattr("forecastlab_api.pipeline.HEARTBEAT_INTERVAL_SECONDS", 0.05)
    original = MockModelProvider.complete_json
    standalone_calls = {"n": 0}
    from forecastlab_api import pipeline as pipeline_mod

    orig_touch = pipeline_mod.touch_worker_standalone

    def slow(self, **kwargs):
        time.sleep(0.18)
        return original(self, **kwargs)

    def counted(status: str = "running") -> None:
        standalone_calls["n"] += 1
        orig_touch(status)

    monkeypatch.setattr(MockModelProvider, "complete_json", slow)
    monkeypatch.setattr(pipeline_mod, "touch_worker_standalone", counted)
    created = _create_experiment(client, name="heartbeat_fresh")
    drain_jobs(max_steps=10)
    assert standalone_calls["n"] >= 1
    with SessionLocal() as session:
        row = session.get(WorkerHeartbeat, "worker")
        assert row is not None
        summary = client.get(f"/api/experiments/{created['id']}").json()
        assert summary["status"] == "completed"


def test_track_reraises_transient_errors() -> None:
    class Boom(MockModelProvider):
        def complete_json(self, **kwargs):
            if kwargs.get("schema_name") == "track_forecast":
                raise TransientProviderError("HTTP 429")
            return super().complete_json(**kwargs)

    with pytest.raises(TransientProviderError):
        run_forecast_engine(
            question=SAMPLE_QUESTION,
            contract=None,
            profile_id="three_track_ensemble",
            mode="demo",
            as_of=None,
            model=Boom(),
            search=MockSearchProvider(),
            allow_local_fixtures=True,
        )


def test_partial_track_local_failure_is_preserved() -> None:
    class LocalFail(MockModelProvider):
        def complete_json(self, **kwargs):
            user = kwargs.get("user") or ""
            if kwargs.get("schema_name") == "track_forecast" and "base_rate" in user:
                raise RuntimeError("track_local_permanent")
            return super().complete_json(**kwargs)

    result = run_forecast_engine(
        question=SAMPLE_QUESTION,
        contract=None,
        profile_id="three_track_ensemble",
        mode="demo",
        as_of=None,
        model=LocalFail(),
        search=MockSearchProvider(),
        allow_local_fixtures=True,
    )
    assert result.partial is True
    assert result.aggregation.ensemble_probability is not None
    failed = [track for track in result.tracks if track.error]
    ok = [track for track in result.tracks if track.forecast is not None]
    assert failed
    assert ok


def test_invalid_structured_output_raises() -> None:
    class Bad(MockModelProvider):
        def complete_json(self, **kwargs):
            if kwargs.get("schema_name") == "resolution_contract":
                usage = ModelUsage(prompt_tokens=1, completion_tokens=1, cost_usd=0.0, latency_ms=1, model="x", provider="mock")
                return ChatResult(content="{}", parsed={"not": "a contract"}, usage=usage)
            return super().complete_json(**kwargs)

    with pytest.raises(StructuredOutputError):
        run_forecast_engine(
            question=SAMPLE_QUESTION,
            contract=None,
            profile_id="single_agent_baseline",
            mode="demo",
            as_of=None,
            model=Bad(),
            search=MockSearchProvider(),
            allow_local_fixtures=True,
        )


def test_http_timeout_and_5xx_are_transient() -> None:
    assert classify_http_status(408) is TransientProviderError
    assert classify_http_status(429) is TransientProviderError
    assert classify_http_status(500) is TransientProviderError
    assert classify_http_status(503) is TransientProviderError


def test_retry_job_reports_outcome(tmp_path) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from forecastlab_api.db import Base
    from forecastlab_api.jobs import enqueue_job, retry_job

    engine = create_engine(f"sqlite:///{tmp_path}/retry-outcome.db")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        job = enqueue_job(session, job_type="forecast_run", payload={"run_id": "z"}, idempotency_key="run:z")
        job.attempts = 1
        job.max_attempts = 3
        session.commit()
        assert retry_job(session, job, error="timeout", category="TransientProviderError") == "rescheduled"
        job.attempts = 3
        assert retry_job(session, job, error="timeout", category="TransientProviderError") == "exhausted"
        session.commit()
        assert job.status == "failed"
