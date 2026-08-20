from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from forecastlab.ledger import InMemoryUsageLedger
from forecastlab.schemas import ModelUsage
from forecastlab_api.models import ForecastRun, ForecastRunAttempt, Question
from forecastlab_api.usage_ledger import PersistentUsageLedger


def _usage(cost: float, prompt: int, completion: int) -> ModelUsage:
    return ModelUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_usd=cost,
        cost_source="estimated",
        request_id="req",
    )


def _reserve(ledger, *, run_id: str, attempt_id: str, logical: str, cost: float, prompt: int, completion: int, provider_type: str = "model"):
    entry = ledger.reserve(
        run_id=run_id,
        run_attempt_id=attempt_id,
        logical_call_id=logical,
        physical_attempt_number=1,
        stage="plan" if provider_type == "model" else "search",
        provider_type=provider_type,
        provider="xai" if provider_type == "model" else "tavily",
        model="grok-test" if provider_type == "model" else "tavily-search",
        reserved_input_tokens=prompt,
        reserved_output_tokens=completion,
        reserved_cost_usd=cost,
    )
    ledger.reconcile(entry.id, _usage(cost, prompt, completion))
    return entry


def test_in_memory_attempt_totals_are_attempt_specific() -> None:
    ledger = InMemoryUsageLedger()
    first = ledger.begin_attempt(run_id="run", job_id="j1", attempt_number=1)
    _reserve(ledger, run_id="run", attempt_id=first.id, logical="m1", cost=0.10, prompt=20, completion=8)
    _reserve(ledger, run_id="run", attempt_id=first.id, logical="s1", cost=0.008, prompt=0, completion=0, provider_type="search")
    ledger.finish_attempt(first.id, status="failed")
    second = ledger.begin_attempt(run_id="run", job_id="j2", attempt_number=2)
    _reserve(ledger, run_id="run", attempt_id=second.id, logical="m2", cost=0.07, prompt=12, completion=6)
    ledger.finish_attempt(second.id, status="completed")
    attempt_1 = ledger.attempt_totals("run", first.id)
    attempt_2 = ledger.attempt_totals("run", second.id)
    cumulative = ledger.totals("run")
    assert attempt_1.total_cost_usd != pytest.approx(cumulative.total_cost_usd)
    assert attempt_2.total_cost_usd != pytest.approx(cumulative.total_cost_usd)
    assert attempt_1.total_cost_usd + attempt_2.total_cost_usd == pytest.approx(cumulative.total_cost_usd)
    assert attempt_1.model_cost_usd + attempt_2.model_cost_usd == pytest.approx(cumulative.model_cost_usd)
    assert attempt_1.search_cost_usd + attempt_2.search_cost_usd == pytest.approx(cumulative.search_cost_usd)
    assert attempt_1.prompt_tokens + attempt_2.prompt_tokens == cumulative.prompt_tokens
    assert attempt_1.completion_tokens + attempt_2.completion_tokens == cumulative.completion_tokens
    assert attempt_1.search_calls + attempt_2.search_calls == cumulative.search_calls
    assert attempt_1.provider_request_count + attempt_2.provider_request_count == cumulative.provider_request_count


def test_persistent_attempt_totals_sum_to_run_lifetime(client) -> None:
    from forecastlab_api.db import SessionLocal

    with SessionLocal() as session:
        question = Question(id=str(uuid.uuid4()), original_text="attempt split", status="draft")
        run = ForecastRun(
            id=str(uuid.uuid4()),
            question_id=question.id,
            profile_id="live_smoke_v1",
            mode="demo",
            status="running",
        )
        session.add_all([question, run])
        session.commit()
        run_id = run.id

    ledger = PersistentUsageLedger(SessionLocal)
    first = ledger.begin_attempt(run_id=run_id, job_id="j1", attempt_number=1)
    _reserve(ledger, run_id=run_id, attempt_id=first.id, logical="m1", cost=0.11, prompt=30, completion=10)
    _reserve(ledger, run_id=run_id, attempt_id=first.id, logical="s1", cost=0.008, prompt=0, completion=0, provider_type="search")
    ledger.finish_attempt(first.id, status="failed")
    second = ledger.begin_attempt(run_id=run_id, job_id="j2", attempt_number=2)
    _reserve(ledger, run_id=run_id, attempt_id=second.id, logical="m2", cost=0.04, prompt=8, completion=3)
    ledger.finish_attempt(second.id, status="completed")

    with SessionLocal() as session:
        run = session.get(ForecastRun, run_id)
        assert run is not None
        attempts = session.scalars(select(ForecastRunAttempt).where(ForecastRunAttempt.run_id == run_id).order_by(ForecastRunAttempt.attempt_number)).all()
        assert len(attempts) == 2
        first_row, second_row = attempts
        assert first_row.total_cost_usd != pytest.approx(run.total_cost_usd)
        assert second_row.total_cost_usd != pytest.approx(run.total_cost_usd)
        assert first_row.total_cost_usd + second_row.total_cost_usd == pytest.approx(run.total_cost_usd)
        assert first_row.model_cost_usd + second_row.model_cost_usd == pytest.approx(run.model_cost_usd)
        assert first_row.search_cost_usd + second_row.search_cost_usd == pytest.approx(run.search_cost_usd)
        assert first_row.prompt_tokens + second_row.prompt_tokens == run.prompt_tokens
        assert first_row.completion_tokens + second_row.completion_tokens == run.completion_tokens
        assert first_row.search_calls + second_row.search_calls == 1
        assert first_row.provider_request_count + second_row.provider_request_count == run.provider_request_count
        assert run.provider_request_count == 3
