from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from tests.test_benchmark_retries import _create_experiment, _task_for

from forecastlab.errors import TransientProviderError
from forecastlab.providers import mock as mock_mod
from forecastlab.providers.mock import MockModelProvider
from forecastlab.schemas import ModelUsage
from forecastlab_api.models import (
    BenchmarkResult,
    BenchmarkTask,
    ForecastRun,
    ForecastRunAttempt,
    Job,
    ProviderCallLedger,
)
from forecastlab_api.usage_ledger import PersistentUsageLedger
from forecastlab_api.worker import drain_jobs, process_once


def _costly_usage(model: str, content: str) -> ModelUsage:
    tokens = max(32, len(content) // 4)
    return ModelUsage(
        prompt_tokens=tokens,
        completion_tokens=tokens,
        cost_usd=0.05,
        latency_ms=12,
        model=model,
        provider="mock",
        cost_source="estimated",
    )


def test_cumulative_retry_spending_survives_rollback(client, monkeypatch) -> None:
    from forecastlab_api.db import SessionLocal

    monkeypatch.setattr("forecastlab_api.jobs.TRANSIENT_BACKOFF", timedelta(0))
    monkeypatch.setattr(mock_mod, "_usage", _costly_usage)
    created = _create_experiment(
        client,
        name="cumulative_spend",
        profiles=["three_track_equal_budget_v1"],
    )
    original = MockModelProvider.complete_json
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise TransientProviderError("later_call_failed")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(MockModelProvider, "complete_json", flaky)
    assert process_once() is True
    with SessionLocal() as session:
        task = _task_for(session, created["id"])
        assert task.run_id
        ledger = PersistentUsageLedger(SessionLocal)
        totals = ledger.totals(task.run_id)
        assert totals.model_cost_usd >= 0.10
        assert totals.provider_request_count >= 2
        first_cost = totals.total_cost_usd
        assert session.scalars(select(ForecastRunAttempt).where(ForecastRunAttempt.run_id == task.run_id)).all()
        assert session.scalars(select(ProviderCallLedger).where(ProviderCallLedger.run_id == task.run_id)).all()
        run_id = task.run_id
    assert process_once() is True
    with SessionLocal() as session:
        run = session.get(ForecastRun, run_id)
        assert run is not None
        totals = PersistentUsageLedger(SessionLocal).totals(run.id)
        assert totals.total_cost_usd >= first_cost
        assert totals.run_attempt_count >= 2
        assert run.cost_usd == totals.total_cost_usd
        result = session.scalar(select(BenchmarkResult).where(BenchmarkResult.run_id == run.id))
        assert result is not None
        assert result.cost_usd == totals.total_cost_usd


def test_exhausted_failed_run_keeps_cost(client, monkeypatch) -> None:
    from forecastlab_api.db import SessionLocal

    monkeypatch.setattr("forecastlab_api.jobs.TRANSIENT_BACKOFF", timedelta(0))
    monkeypatch.setattr(mock_mod, "_usage", _costly_usage)
    created = _create_experiment(client, name="exhausted_cost", profiles=["single_agent_equal_budget_v1"])
    original = MockModelProvider.complete_json

    def always_fail(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise TransientProviderError("nope")

    monkeypatch.setattr(MockModelProvider, "complete_json", always_fail)
    with SessionLocal() as session:
        task = _task_for(session, created["id"])
        job_row = session.scalars(select(Job).where(Job.payload_json.contains(task.id))).one()
        job_row.max_attempts = 2
        session.commit()
        task_id = task.id
    drain_jobs(max_steps=10)
    with SessionLocal() as session:
        task = session.get(BenchmarkTask, task_id)
        assert task is not None and task.run_id
        run = session.get(ForecastRun, task.run_id)
        assert run is not None
        totals = PersistentUsageLedger(SessionLocal).totals(run.id)
        assert totals.total_cost_usd > 0
        assert run.cost_usd == totals.total_cost_usd
        result = session.scalar(select(BenchmarkResult).where(BenchmarkResult.benchmark_task_id == task.id))
        assert result is not None
        assert result.failed is True
        assert result.cost_usd == totals.total_cost_usd


def test_search_cost_in_run_and_benchmark(client, monkeypatch) -> None:
    from forecastlab.providers.mock import MockSearchProvider
    from forecastlab_api.db import SessionLocal

    original = MockSearchProvider.search

    def paid_search(self, query: str, *, max_results: int = 5):
        hits = original(self, query, max_results=max_results)
        if self.ledger is not None and self.run_id:
            entry = self.ledger.reserve(
                run_id=self.run_id,
                run_attempt_id=self.run_attempt_id,
                logical_call_id=f"search-{query[:12]}",
                physical_attempt_number=2,
                stage="search-extra",
                provider_type="search",
                provider="tavily",
                model="tavily-search",
                reserved_input_tokens=0,
                reserved_output_tokens=0,
                reserved_cost_usd=0.008,
            )
            self.ledger.reconcile(entry.id, ModelUsage(cost_usd=0.008, cost_source="estimated"))
        return hits

    monkeypatch.setattr(MockSearchProvider, "search", paid_search)
    created = _create_experiment(client, name="search_cost", profiles=["single_agent_equal_budget_v1"])
    drain_jobs(max_steps=10)
    with SessionLocal() as session:
        task = _task_for(session, created["id"])
        run = session.get(ForecastRun, task.run_id)
        assert run is not None
        assert run.search_cost_usd >= 0.008
        assert run.total_cost_usd >= run.model_cost_usd + run.search_cost_usd - 1e-9
        result = session.scalar(select(BenchmarkResult).where(BenchmarkResult.benchmark_task_id == task.id))
        assert result is not None
        assert result.cost_usd == run.total_cost_usd
