from __future__ import annotations

import pytest

from forecastlab.budget import Budget
from forecastlab.errors import TransientProviderError
from forecastlab.ledger import InMemoryUsageLedger, summarize_entries
from forecastlab.physical import run_physical_attempts
from forecastlab.profiles import load_profile
from forecastlab.schemas import ModelUsage


def _usage(cost: float, prompt: int = 10, completion: int = 5) -> ModelUsage:
    return ModelUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_usd=cost,
        cost_source="estimated",
        request_id="req-1",
    )


def test_summarize_includes_failed_and_search() -> None:
    ledger = InMemoryUsageLedger()
    ledger.begin_attempt(run_id="run", job_id="job", attempt_number=1)
    first = ledger.reserve(
        run_id="run",
        run_attempt_id=None,
        logical_call_id="m1",
        physical_attempt_number=1,
        stage="plan",
        provider_type="model",
        provider="mock",
        model="mock",
        reserved_input_tokens=10,
        reserved_output_tokens=5,
        reserved_cost_usd=0.2,
    )
    ledger.reconcile(first.id, _usage(0.15))
    second = ledger.reserve(
        run_id="run",
        run_attempt_id=None,
        logical_call_id="m2",
        physical_attempt_number=1,
        stage="forecast",
        provider_type="model",
        provider="mock",
        model="mock",
        reserved_input_tokens=10,
        reserved_output_tokens=5,
        reserved_cost_usd=0.2,
    )
    ledger.fail(second.id, error_category="TransientProviderError", error_message="boom", usage=_usage(0.18))
    search = ledger.reserve(
        run_id="run",
        run_attempt_id=None,
        logical_call_id="s1",
        physical_attempt_number=1,
        stage="search",
        provider_type="search",
        provider="tavily",
        model=None,
        reserved_input_tokens=0,
        reserved_output_tokens=0,
        reserved_cost_usd=0.008,
    )
    ledger.reconcile(search.id, ModelUsage(cost_usd=0.008, cost_source="estimated"))
    totals = ledger.totals("run")
    assert totals.model_cost_usd == pytest.approx(0.33)
    assert totals.search_cost_usd == pytest.approx(0.008)
    assert totals.failed_attempt_cost_usd == pytest.approx(0.18)
    assert totals.total_cost_usd == pytest.approx(0.338)
    assert totals.provider_request_count == 3


def test_reconcile_replay_is_idempotent() -> None:
    ledger = InMemoryUsageLedger()
    entry = ledger.reserve(
        run_id="run",
        run_attempt_id=None,
        logical_call_id="m1",
        physical_attempt_number=1,
        stage="plan",
        provider_type="model",
        provider="mock",
        model="mock",
        reserved_input_tokens=8,
        reserved_output_tokens=4,
        reserved_cost_usd=0.1,
    )
    ledger.reconcile(entry.id, _usage(0.1))
    ledger.reconcile(entry.id, _usage(0.9))
    totals = ledger.totals("run")
    assert totals.total_cost_usd == 0.1
    assert totals.provider_request_count == 1


def test_budget_from_persisted_does_not_reset() -> None:
    ledger = InMemoryUsageLedger()
    entry = ledger.reserve(
        run_id="run",
        run_attempt_id=None,
        logical_call_id="m1",
        physical_attempt_number=1,
        stage="plan",
        provider_type="model",
        provider="mock",
        model="mock",
        reserved_input_tokens=20,
        reserved_output_tokens=20,
        reserved_cost_usd=0.4,
    )
    ledger.reconcile(entry.id, _usage(0.4, 20, 20))
    profile = load_profile("single_agent_equal_budget_v1")
    profile = profile.model_copy(update={"max_estimated_cost_usd": 0.5, "max_model_calls": 3, "max_tokens": 100})
    budget = Budget.from_persisted(profile, ledger.totals("run"))
    assert budget.state.cost_usd == 0.4
    assert budget.state.model_calls == 1
    try:
        budget.reserve_model_call("next", estimated_input_tokens=20, max_output_tokens=20, estimated_cost_usd=0.2)
        raised = False
    except Exception:
        raised = True
    assert raised


def test_physical_retries_each_appear() -> None:
    ledger = InMemoryUsageLedger()
    attempts = {"n": 0}

    def send(_physical: int):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TransientProviderError(f"fail-{attempts['n']}")
        return "ok", _usage(0.05)

    result = run_physical_attempts(
        ledger=ledger,
        run_id="run",
        run_attempt_id=None,
        logical_call_id="logical-1",
        stage="model",
        provider_type="model",
        provider="openai_compatible",
        model="tiny",
        reserved_input_tokens=1,
        reserved_output_tokens=1,
        reserved_cost_usd=0.05,
        send=send,
        sleep=lambda _s: None,
    )
    assert result == "ok"
    rows = ledger.entries("run")
    assert len(rows) == 3
    assert [row.status for row in rows] == ["failed", "failed", "succeeded"]
    totals = summarize_entries(rows, attempt_count=1)
    assert totals.provider_request_count == 3
    assert totals.failed_attempt_cost_usd > 0
    assert totals.total_cost_usd >= totals.failed_attempt_cost_usd
