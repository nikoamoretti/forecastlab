"""Offline tests for the deterministic statistical baseline (statistical_baseline_v1). No network."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from forecastlab.macro import (
    LIVE_MONTHLY_CACHE,
    MacroObservation,
    MacroSnapshot,
    MacroSpec,
    fetch_macro,
    normalize_observations,
)
from forecastlab.root_event import digest
from forecastlab.statistical_baseline import (
    MIN_ERRORS,
    RULE_VERSION,
    BaselineUnavailable,
    baseline_forecast,
    forecast_from_snapshot,
    fred_history_options,
    horizon,
    index_period,
    period_index,
    round_published,
)

RELEASE = datetime(2040, 2, 5, 13, 30, tzinfo=UTC)


def _month(start: str, offset: int) -> str:
    year, month = map(int, start.split("-"))
    index = year * 12 + month - 1 + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _observations(indicator: str, rows: list[tuple[str, float]]) -> list[MacroObservation]:
    return [MacroObservation(series_id="TEST", period=period, value=value, units="u", seasonal_adjustment="sa",
                             source_url="https://example.test/series", available_at=datetime(2039, 12, 1, tzinfo=UTC),
                             vintage="test", revision_basis="test") for period, value in rows]


def _levels(start_value: str, changes: list[str], *, first: str = "2030-01") -> list[tuple[str, float]]:
    """Monthly levels built with exact decimals, delivered as floats like the adapter's values."""
    level = Decimal(start_value)
    rows = [(first, float(level))]
    for offset, change in enumerate(changes, start=1):
        level += Decimal(change)
        rows.append((_month(first, offset), float(level)))
    return rows


# 119 one-month changes: 60 unchanged, 30 up 0.1, 29 down 0.1 (interleaved), ending at 4.2.
CHANGES = (["0", "0.1", "0", "-0.1"] * 30)[:119]
assert CHANGES.count("0") == 60 and CHANGES.count("0.1") == 30 and CHANGES.count("-0.1") == 29
UNRATE_ROWS = _levels(str(Decimal("4.2") - sum(Decimal(c) for c in CHANGES)), CHANGES)
assert UNRATE_ROWS[-1] == ("2039-12", 4.2)


def unrate_spec(threshold: float = 4.2, comparison: str = "gt", period: str = "2040-01") -> MacroSpec:
    year, month = map(int, _month(period, 1).split("-"))
    return MacroSpec(indicator="unemployment", observation_period=period, threshold=threshold,
                     comparison=comparison, release_at=datetime(year, month, 5, 13, 30, tzinfo=UTC))


# --- The rule ------------------------------------------------------------------------------------

def test_probability_is_monotone_non_increasing_in_the_threshold_for_gt():
    observations = _observations("unemployment", UNRATE_ROWS)
    thresholds = [3.5 + step / 20 for step in range(31)]  # 3.5 .. 5.0 in 0.05 steps
    gt = [baseline_forecast(unrate_spec(t), observations) for t in thresholds]
    probabilities = [item["probability"] for item in gt]
    assert all(a >= b for a, b in zip(probabilities, probabilities[1:], strict=False))
    assert probabilities[0] == 0.98 and probabilities[-1] == 0.02
    lt = [baseline_forecast(unrate_spec(t, "lt"), observations)["probability"] for t in thresholds]
    assert all(a <= b for a, b in zip(lt, lt[1:], strict=False))
    for threshold in thresholds:
        above = baseline_forecast(unrate_spec(threshold, "gt"), observations)
        at_most = baseline_forecast(unrate_spec(threshold, "le"), observations)
        # Complementary questions are coherent before the shared clipping bounds.
        assert above["raw_probability"] + at_most["raw_probability"] == pytest.approx(1.0)


def test_equality_with_the_threshold_uses_the_published_precision():
    # Starting from 4.2: "above 4.2%" excludes the 60 unchanged outcomes, "at least 4.2%" includes them.
    observations = _observations("unemployment", UNRATE_ROWS)
    above = baseline_forecast(unrate_spec(4.2, "gt"), observations)
    assert (above["k"], above["n"]) == (30, 119)
    assert above["probability"] == pytest.approx(30.5 / 120)
    at_least = baseline_forecast(unrate_spec(4.2, "ge"), observations)
    assert at_least["k"] == 90 and at_least["probability"] == pytest.approx(90.5 / 120)
    # Floating-point sums such as 4.2 + (4.2 - 4.1) are 4.300000000000001; the rounded sample is exactly 4.3.
    assert 4.2 + (4.2 - 4.1) != 4.3
    assert baseline_forecast(unrate_spec(4.3, "gt"), observations)["k"] == 0
    assert baseline_forecast(unrate_spec(4.3, "ge"), observations)["k"] == 30
    assert above["mode"] == 4.2 and above["distribution"] == [
        {"value": 4.1, "count": 29, "share": 29 / 119}, {"value": 4.2, "count": 60, "share": 60 / 119},
        {"value": 4.3, "count": 30, "share": 30 / 119}]
    assert above["quantiles"] == {"5": 4.1, "10": 4.1, "25": 4.2, "50": 4.2, "75": 4.3, "90": 4.3, "95": 4.3}
    assert above["interval_80"] == [4.1, 4.3]
    assert above["rationale"] == (
        "Over the last 120 months of official data (2030-01 to 2039-12), the U.S. unemployment rate series has 119 "
        "changes over 1 month. Adding each of those changes to the latest value, 4.2% for December 2039, puts the January "
        "2040 figure above 4.2% in 30 of 119 cases. The (k + 0.5) / (n + 1) rule gives a probability of 25% that "
        "the January 2040 figure is above 4.2%. The most likely published value is 4.2%, and the 80% range is 4.1% "
        "to 4.3%.")


@pytest.mark.parametrize(("value", "expected"), [
    (1.25, "1.3"), (0.25, "0.3"), (0.35, "0.4"), (2.95, "3.0"), (2.85, "2.9"), (-1.25, "-1.3"), (3.04, "3.0"),
])
def test_cpi_rounds_half_up_like_bls(value, expected):
    assert round_published("cpi", value) == Decimal(expected)
    # Python's float rounding is not BLS rounding for these ties.
    if value in (1.25, 0.25, 0.35):
        assert f"{round(value, 1)}" != expected


def test_publication_precision_for_each_series():
    assert round_published("unemployment", 4.249999999999) == Decimal("4.2")
    assert round_published("treasury_10y", 4.125) == Decimal("4.13")
    assert round_published("jobless_claims", 218000.5) == Decimal("218001")
    assert round_published("payrolls", 142500) == Decimal("143000")
    assert round_published("payrolls", Decimal("142333.3333")) == Decimal("142000")
    assert round_published("payrolls", -50500) == Decimal("-51000")
    assert str(round_published("cpi", -0.04)) == "0.0"  # no negative zero


def test_horizon_for_monthly_weekly_and_daily_series():
    assert horizon("monthly", "2026-08", "2026-09") == 1
    assert horizon("monthly", "2025-11", "2026-02") == 3
    assert horizon("monthly", "2025-09", "2025-11") == 2  # a missing October stays a gap
    assert horizon("weekly", "2026-09-26", "2026-10-03") == 1
    assert horizon("weekly", "2026-09-26", "2026-10-10") == 2
    assert horizon("daily", "2026-10-01", "2026-10-02") == 1
    assert horizon("daily", "2026-10-02", "2026-10-05") == 1  # Friday to Monday is one weekday
    assert horizon("daily", "2026-10-01", "2026-10-05") == 2
    assert horizon("daily", "2026-12-31", "2027-01-04") == 2
    for cadence, period in (("monthly", "2026-08"), ("weekly", "2026-10-03"), ("daily", "2026-10-05")):
        assert index_period(cadence, period_index(cadence, period)) == period
    with pytest.raises(BaselineUnavailable, match="weekly_period_not_saturday"):
        horizon("weekly", "2026-09-25", "2026-10-03")
    with pytest.raises(BaselineUnavailable, match="daily_period_not_weekday"):
        horizon("daily", "2026-10-03", "2026-10-05")
    with pytest.raises(BaselineUnavailable, match="target_not_after_history"):
        horizon("monthly", "2026-09", "2026-09")


def test_horizon_and_window_drive_the_errors():
    observations = _observations("unemployment", UNRATE_ROWS)
    two_ahead = baseline_forecast(unrate_spec(4.2, "gt", period="2040-02"), observations)
    assert two_ahead["horizon"] == {"h": 2, "unit": "month", "from_period": "2039-12", "to_period": "2040-02"}
    assert two_ahead["n"] == 118
    longer = _observations("unemployment", _levels("4.2", ["0"] * 139, first="2028-05"))
    windowed = baseline_forecast(unrate_spec(), longer)
    assert windowed["window"] == {"max_periods": 120, "unit": "month", "span_periods": 120, "start": "2030-01",
                                  "end": "2039-12", "observations": 120}
    with pytest.raises(BaselineUnavailable, match="observation_not_before_target"):
        baseline_forecast(unrate_spec(period="2039-12"), observations)
    with pytest.raises(BaselineUnavailable, match="history_insufficient"):
        baseline_forecast(unrate_spec(), observations[-MIN_ERRORS:])


def test_missing_months_are_skipped_not_imputed():
    rows = [row for row in UNRATE_ROWS if row[0] != "2035-10"]
    result = baseline_forecast(unrate_spec(), _observations("unemployment", rows))
    assert result["n"] == 117  # both pairs touching the missing month are dropped
    assert result["window"]["observations"] == 119 and result["window"]["span_periods"] == 120


def test_payroll_point_is_the_trailing_three_month_mean_of_changes():
    changes = [100000, 200000, 150000] * 40  # 120 monthly changes in jobs, ending 2039-12
    rows = [(_month("2030-01", offset), float(value)) for offset, value in enumerate(changes)]
    spec = MacroSpec(indicator="payrolls", observation_period="2040-01", threshold=150000, release_at=RELEASE)
    result = baseline_forecast(spec, _observations("payrolls", rows))
    assert result["point_rule"] == "trailing_3_month_mean_of_monthly_changes"
    assert result["point_forecast"] == 150000.0
    # Every trailing mean is 150,000, so the errors are -50,000, +50,000 or 0 (118 origins).
    assert result["n"] == 117
    assert {item["value"] for item in result["distribution"]} == {100000, 150000, 200000}
    assert result["k"] == 39 and result["mode"] == 150000
    assert "the average of three monthly payroll changes" in result["rationale"]
    assert "+150,000 jobs (2039-10, 2039-11, 2039-12)" in result["rationale"]

    uneven = rows[:-3] + [("2039-10", 30000.0), ("2039-11", 0.0), ("2039-12", -10000.0)]
    point = baseline_forecast(spec.model_copy(update={"threshold": 0}), _observations("payrolls", uneven))
    assert point["point_forecast"] == pytest.approx(20000 / 3)
    assert all(item["value"] % 1000 == 0 for item in point["distribution"])
    with pytest.raises(BaselineUnavailable, match="point_inputs_missing"):
        baseline_forecast(spec, _observations("payrolls", [row for row in rows if row[0] != "2039-11"]))


def test_weekly_and_daily_series_use_their_own_grids():
    saturday = date(2026, 9, 26)
    weekly = [((saturday - timedelta(weeks=i)).isoformat(), 200000.0 + 1000 * (i % 3)) for i in range(156)]
    claims = MacroSpec(indicator="jobless_claims", observation_period="2026-10-10", threshold=200000,
                       release_at=datetime(2026, 10, 15, 12, 30, tzinfo=UTC))
    result = baseline_forecast(claims, _observations("jobless_claims", weekly))
    assert result["horizon"]["h"] == 2 and result["horizon"]["unit"] == "week" and result["n"] == 154
    assert "for the week ending 2026-10-10" in result["rationale"]
    days, day = [], date(2026, 10, 1)
    while len(days) < 520:
        if day.weekday() < 5:
            days.append((day.isoformat(), 4.0 + (0.01 if len(days) % 2 else 0.0)))
        day -= timedelta(days=1)
    days = [row for row in days if row[0] != "2026-09-07"]  # a market holiday has no value
    yields = MacroSpec(indicator="treasury_10y", observation_period="2026-10-05", threshold=4.0,
                       release_at=datetime(2026, 10, 6, 21, tzinfo=UTC))
    daily = baseline_forecast(yields, _observations("treasury_10y", days))
    assert daily["horizon"] == {"h": 2, "unit": "weekday", "from_period": "2026-10-01", "to_period": "2026-10-05"}
    assert daily["window"]["observations"] == 519
    assert all(round(item["value"], 2) == item["value"] for item in daily["distribution"])


# --- Data path -------------------------------------------------------------------------------------

def _weekly_csv(end: date, count: int) -> bytes:
    rows = [f"{(end - timedelta(weeks=i)).isoformat()},{200000 + i}" for i in reversed(range(count))]
    return ("observation_date,ICSA\n" + "\n".join(rows) + "\n").encode()


def test_fred_history_options_only_widen_the_baseline_fetch(monkeypatch):
    monkeypatch.setattr("forecastlab.macro.utcnow", lambda: datetime(2026, 10, 2, 23, tzinfo=UTC))
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=_weekly_csv(date(2026, 9, 26), 300),
                              headers={"content-type": "application/csv"})
    spec = MacroSpec(indicator="jobless_claims", observation_period="2026-10-03", threshold=200000,
                     release_at=datetime(2026, 10, 8, 12, 30, tzinfo=UTC))
    options = fred_history_options("jobless_claims")
    assert options == {"fred_history_limit": 156, "fred_lookback_days": 7 * 156 + 21}
    assert fred_history_options("treasury_10y") == {"fred_history_limit": 520, "fred_lookback_days": 749}
    assert fred_history_options("unemployment") == {}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        default = fetch_macro(spec, client=client)
        widened = fetch_macro(spec, client=client, **options)
    assert len(default.observations) == 104 and len(widened.observations) == 156
    assert f"cosd={(date(2026, 10, 2) - timedelta(days=770)).isoformat()}" in seen[0]
    assert f"cosd={(date(2026, 10, 2) - timedelta(days=7 * 156 + 21)).isoformat()}" in seen[1]
    result = forecast_from_snapshot(spec, widened)
    assert result["window"]["observations"] == 156 and result["snapshot"]["raw_hash"] == widened.raw_hash
    assert result["snapshot"]["request_url"] == seen[1]


# --- Execution through execute_run -------------------------------------------------------------------

LIVE_SETTINGS = {"model_provider": "openai", "model_name": "gpt-5-mini", "model_api_key": "test-not-real",
                 "search_provider": "tavily", "search_api_key": "test-not-real", "max_cost_usd": 5}


class _ForbiddenProvider:
    name = "forbidden"
    model = "forbidden"

    def __getattr__(self, item):
        raise AssertionError(f"statistical baseline must not call a provider ({item})")


def _no_providers_or_network(monkeypatch):
    from forecastlab_api import pipeline
    monkeypatch.setattr(pipeline, "build_model_provider", lambda **_: _ForbiddenProvider())
    monkeypatch.setattr(pipeline, "build_search_provider", lambda *_a, **_k: _ForbiddenProvider())

    def offline(*_args, **_kwargs):
        raise AssertionError("network access is not allowed in this test")
    # Real sockets only: the in-process TestClient uses its own transport.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", offline)


def _scripted_snapshot(rows=UNRATE_ROWS, *, indicator="unemployment") -> MacroSnapshot:
    retrieved = datetime.now(UTC) - timedelta(minutes=5)
    payload = {"scripted": True, "rows": rows}
    observations = normalize_observations(indicator, dict(rows), available_at=retrieved, vintage=retrieved.isoformat(),
        source_url="https://data.bls.gov/timeseries/LNS14000000",
        revision_basis="latest_observed_revisions_not_first_release")
    return MacroSnapshot(indicator=indicator, retrieved_at=retrieved, observations=observations,
                         raw_payload=payload, raw_hash=digest(payload))


def _live_resolve(monkeypatch):
    from forecastlab.execution import resolve_execution_context
    from forecastlab_api import prospective

    def resolve(question, *, profile_id, mode):
        return resolve_execution_context(requested_mode="live", profile_id=profile_id, settings=LIVE_SETTINGS)
    monkeypatch.setattr(prospective, "resolve_for_question", resolve)


def _freeze_cohort(client, macro: MacroSpec, *, budget_usd: float = 1) -> dict:
    cutoff = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    cohort = client.post("/api/prospective/cohorts", json={"name": "Baseline", "budget_usd": budget_usd,
        "questions": [{"macro": macro.model_dump(mode="json"), "cutoff": cutoff, "release_event": "release"}]})
    assert cohort.status_code == 201, cohort.text
    frozen = client.post(f"/api/prospective/cohorts/{cohort.json()['id']}/freeze", json={"reviewed_by": "Test reviewer"})
    assert frozen.status_code == 200, frozen.text
    return frozen.json()


def test_profile_is_zero_cost_and_needs_no_model_or_search():
    from forecastlab.execution import estimate_workload, resolve_execution_context
    from forecastlab.profiles import load_profile
    profile = load_profile("statistical_baseline_v1")
    assert profile.execution_strategy == "statistical_baseline"
    assert (profile.max_model_calls, profile.max_search_calls, profile.max_tokens) == (0, 0, 0)
    assert profile.max_estimated_cost_usd == 0.0 and profile.max_fetched_documents == 1
    assert estimate_workload(profile) == {"tracks": 0, "subquestions": 0, "planned_model_calls": 0,
                                          "planned_search_calls": 0, "planned_fetches": 1, "planned_max_tokens": 0}
    context = resolve_execution_context(requested_mode="live", profile_id=profile.id, settings=LIVE_SETTINGS)
    assert context.estimated_upper_bound_cost_usd == 0.0 and not context.estimate_exceeds_ceiling
    assert context.effective_max_cost_usd == 0.0


def test_cohort_run_through_execute_run_is_deterministic_free_and_on_time(client, monkeypatch):
    from sqlalchemy import select

    from forecastlab_api import baseline_executor, prospective
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import (
        ForecastRun,
        ForecastRunAttempt,
        ForecastVersion,
        PersonalForecast,
        ProviderCallLedger,
    )
    from forecastlab_api.pipeline import execute_run
    from forecastlab_api.usage_ledger import PersistentUsageLedger

    _live_resolve(monkeypatch)
    data = _freeze_cohort(client, unrate_spec(4.2))
    cells = {cell["method"]: cell for cell in data["questions"][0]["cells"]}
    assert set(cells) == set(prospective.METHODS) and len(cells) == 4
    assert client.post(f"/api/prospective/cohorts/{data['id']}/launch").status_code == 200
    # Spend most of the $1 cohort budget elsewhere: the $0 baseline must still run.
    ledger = PersistentUsageLedger(SessionLocal, max_cost_usd=5)
    ledger.reserve(run_id=cells["root_event_ensemble_v1"]["run_id"], run_attempt_id=None, logical_call_id="spend",
        physical_attempt_number=1, stage="test", provider_type="model", provider="test", model="test",
        reserved_input_tokens=1, reserved_output_tokens=1, reserved_cost_usd=0.99)
    calls = []

    def scripted_fetch(spec, **kwargs):
        calls.append(kwargs)
        return _scripted_snapshot()
    monkeypatch.setattr(baseline_executor, "fetch_macro", scripted_fetch)
    _no_providers_or_network(monkeypatch)
    run_id = cells["statistical_baseline_v1"]["run_id"]
    with SessionLocal() as session:
        execute_run(session, session.get(ForecastRun, run_id))
    with SessionLocal() as session:
        run = session.get(ForecastRun, run_id)
        version = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run_id))
        personal = session.get(PersonalForecast, run_id)
        result = json.loads(personal.result_json)
        assert run.status == "completed" and personal.outcome_status == "forecasted"
        assert version.ensemble_probability == pytest.approx(30.5 / 120) == result["probability"]
        assert run.total_cost_usd == 0 and run.provider_request_count == 0 and run.latency_ms is not None
        assert session.scalars(select(ProviderCallLedger).where(ProviderCallLedger.run_id == run_id)).all() == []
        attempts = session.scalars(select(ForecastRunAttempt).where(ForecastRunAttempt.run_id == run_id)).all()
        assert [attempt.status for attempt in attempts] == ["completed"]
        assert json.loads(run.budget_json)["fetches"] == 1 and json.loads(run.budget_json)["model_calls"] == 0
    assert len(calls) == 1 and set(calls[0]) == {"as_of", "fred_api_key", "timeout", "cache"}
    assert calls[0]["as_of"] is None and 0 < calls[0]["timeout"] <= 120
    assert calls[0]["cache"] is LIVE_MONTHLY_CACHE  # shared with the root method's live monthly fetch
    baseline = result["statistical_baseline"]
    assert baseline["rule_version"] == RULE_VERSION and (baseline["n"], baseline["k"]) == (119, 30)
    assert baseline["horizon"]["h"] == 1 and baseline["window"]["start"] == "2030-01"
    assert baseline["last_observation"]["period"] == "2039-12" and baseline["last_observation"]["value"] == 4.2
    assert baseline["snapshot"]["raw_hash"] == _scripted_snapshot().raw_hash
    assert baseline["snapshot"]["source_url"] == "https://data.bls.gov/timeseries/LNS14000000"
    assert baseline["snapshot"]["retrieved_at"] and baseline["mode"] == 4.2 and baseline["quantiles"]["50"] == 4.2
    assert result["model_calls"] == result["search_calls"] == 0 and result["evidence_gaps"] == []
    assert baseline["rationale"].startswith("Over the last 120 months of official data (2030-01 to 2039-12)")
    assert result["aggregation"]["rationale"] == baseline["rationale"]
    report = client.get(f"/api/prospective/cohorts/{data['id']}").json()
    method = next(m for m in report["methods"] if m["method"] == "statistical_baseline_v1")
    assert method["forecasted"] == 1 and method["cost_usd"] == 0 and method["mean_latency_ms"] is not None
    cell = next(c for c in report["questions"][0]["cells"] if c["method"] == "statistical_baseline_v1")
    assert cell["probability"] == pytest.approx(30.5 / 120) and cell["status"] == "forecasted"
    # A repeated job is idempotent: the stored version is kept, nothing is refetched.
    with SessionLocal() as session:
        execute_run(session, session.get(ForecastRun, run_id))
    assert len(calls) == 1


def test_frozen_assignment_checks_still_apply(client, monkeypatch):
    from forecastlab_api import prospective
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ForecastRun, PersonalForecast
    from forecastlab_api.pipeline import execute_run

    _live_resolve(monkeypatch)
    data = _freeze_cohort(client, unrate_spec(4.2))
    run_id = next(c["run_id"] for c in data["questions"][0]["cells"] if c["method"] == "statistical_baseline_v1")
    _no_providers_or_network(monkeypatch)
    with SessionLocal() as session, pytest.raises(ValueError, match="prospective_cohort_not_launched"):
        execute_run(session, session.get(ForecastRun, run_id))
    client.post(f"/api/prospective/cohorts/{data['id']}/launch")
    monkeypatch.setattr(prospective, "forecasting_source_hash", lambda: "changed-source")
    with SessionLocal() as session, pytest.raises(ValueError, match="frozen_code_or_manifest_changed"):
        execute_run(session, session.get(ForecastRun, run_id))
    with SessionLocal() as session:
        assert session.get(PersonalForecast, run_id).outcome_status is None


def test_late_completion_is_an_execution_failure_not_a_forecast(client, monkeypatch):
    from forecastlab import deadline
    from forecastlab_api import baseline_executor
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ForecastRun, PersonalForecast
    from forecastlab_api.pipeline import execute_run

    _live_resolve(monkeypatch)
    data = _freeze_cohort(client, unrate_spec(4.2))
    run_id = next(c["run_id"] for c in data["questions"][0]["cells"] if c["method"] == "statistical_baseline_v1")
    client.post(f"/api/prospective/cohorts/{data['id']}/launch")
    clock = [1000.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: clock[0])

    def slow_fetch(spec, **kwargs):
        clock[0] += 121  # past the profile's 120-second execution budget
        return _scripted_snapshot()
    monkeypatch.setattr(baseline_executor, "fetch_macro", slow_fetch)
    _no_providers_or_network(monkeypatch)
    with SessionLocal() as session, pytest.raises(Exception, match="max_wall_clock_seconds"):
        execute_run(session, session.get(ForecastRun, run_id))
    with SessionLocal() as session:
        run = session.get(ForecastRun, run_id)
        assert run.status == "failed" and session.get(PersonalForecast, run_id).outcome_status == "execution_failed"
        assert run.total_cost_usd == 0


def _personal_run(client, *, mode: str, macro: MacroSpec | None, question: str = "", as_of=None):
    """Prepare a personal draft (root method), then create a baseline run with the same contract and macro."""
    import uuid

    from forecastlab.execution import resolve_execution_context
    from forecastlab.schemas import ForecastContract
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import Question
    from forecastlab_api.personal_forecasts import envelope
    from forecastlab_api.pipeline import create_run_record
    body = {"mode": "demo", "request_key": f"baseline-{uuid.uuid4()}", "question": question}
    if macro is not None:
        body["macro"] = macro.model_dump(mode="json")
    draft = client.post("/api/forecast-drafts", json=body).json()
    reviewed = client.post(f"/api/forecast-drafts/{draft['run_id']}/launch", json={"review": {}}).json()
    contract = ForecastContract.model_validate(reviewed["result"]["contract"])
    with SessionLocal() as session:
        item = session.get(Question, draft["question_id"])
        context = resolve_execution_context(requested_mode=mode, profile_id="statistical_baseline_v1",
                                            settings=LIVE_SETTINGS, as_of=as_of)
        run = create_run_record(session, question=item, context=context, as_of=as_of, enqueue=False)
        envelope(session, run, request_key=f"baseline-run-{run.id}", request_hash="baseline", contract=contract,
                 macro=macro)
        session.commit()
        return run.id


def _execute(run_id: str):
    from sqlalchemy import select

    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ForecastRun, ForecastVersion, PersonalForecast
    from forecastlab_api.pipeline import execute_run
    with SessionLocal() as session:
        execute_run(session, session.get(ForecastRun, run_id))
    with SessionLocal() as session:
        version = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run_id))
        personal = session.get(PersonalForecast, run_id)
        return version, personal.outcome_status, json.loads(personal.result_json)


def _forbid_fetch(monkeypatch):
    from forecastlab_api import baseline_executor

    def forbidden(*_args, **_kwargs):
        raise AssertionError("no official-data request expected")
    monkeypatch.setattr(baseline_executor, "fetch_macro", forbidden)


def test_non_macro_questions_withhold_with_a_clear_gap(client, monkeypatch):
    run_id = _personal_run(client, mode="live", macro=None, question="Will a general event happen by 2040?")
    _forbid_fetch(monkeypatch)
    _no_providers_or_network(monkeypatch)
    version, outcome, result = _execute(run_id)
    assert version.ensemble_probability is None and outcome == "insufficient_evidence"
    assert result["evidence_gaps"] == ["statistical_baseline_requires_macro_spec"]
    assert result["probability"] is None and result["statistical_baseline"] is None


def test_demo_mode_is_labeled_and_never_invents_data(client, monkeypatch):
    run_id = _personal_run(client, mode="demo", macro=unrate_spec())
    _forbid_fetch(monkeypatch)
    _no_providers_or_network(monkeypatch)
    version, outcome, result = _execute(run_id)
    assert version.ensemble_probability is None and outcome == "insufficient_evidence"
    assert result["evidence_gaps"] == ["statistical_baseline_demo_mode_no_official_data"]
    assert result["data_basis"] == "demo_mode_no_data"


def test_historical_fred_runs_withhold(client, monkeypatch):
    claims = MacroSpec(indicator="jobless_claims", observation_period="2026-10-03", threshold=200000,
                       release_at=datetime(2026, 10, 8, 12, 30, tzinfo=UTC))
    as_of = datetime(2026, 10, 3, tzinfo=UTC)
    run_id = _personal_run(client, mode="backtest", macro=claims, as_of=as_of)
    _no_providers_or_network(monkeypatch)  # fetch_macro fails closed before any request
    version, outcome, result = _execute(run_id)
    assert version.ensemble_probability is None and outcome == "insufficient_evidence"
    assert result["evidence_gaps"] == ["historical_fred_series_not_supported"]


def test_historical_bls_run_uses_the_vintage_before_the_cutoff(client, monkeypatch):
    from forecastlab_api import baseline_executor
    rows = _levels(str(Decimal("4.2") - sum(Decimal(c) for c in CHANGES)), CHANGES, first="2016-08")
    target = MacroSpec(indicator="unemployment", observation_period="2026-08", threshold=4.2,
                       release_at=datetime(2026, 9, 4, 12, 30, tzinfo=UTC))
    as_of = datetime(2026, 9, 4, tzinfo=UTC)
    run_id = _personal_run(client, mode="backtest", macro=target, as_of=as_of)
    late = _personal_run(client, mode="backtest", macro=target.model_copy(update={"threshold": 4.3}), as_of=as_of)
    seen = []

    def vintage(available: datetime) -> MacroSnapshot:
        snapshot = _scripted_snapshot(rows)  # retrieved now, i.e. after the historical cutoff
        observations = [row.model_copy(update={"available_at": available}) for row in snapshot.observations]
        return snapshot.model_copy(update={"observations": observations})

    def vintage_fetch(spec, **kwargs):
        seen.append(kwargs["as_of"])
        return vintage(datetime(2026, 9, 3, 23, 59, tzinfo=UTC))  # the previous day's ALFRED vintage
    monkeypatch.setattr(baseline_executor, "fetch_macro", vintage_fetch)
    _no_providers_or_network(monkeypatch)
    version, outcome, result = _execute(run_id)
    assert seen == [as_of] and outcome == "forecasted"
    assert version.ensemble_probability == pytest.approx(30.5 / 120)
    assert result["forecast_cutoff"] == as_of.isoformat()

    monkeypatch.setattr(baseline_executor, "fetch_macro",
                        lambda spec, **kwargs: vintage(datetime(2026, 9, 4, 13, tzinfo=UTC)))
    version, outcome, result = _execute(late)  # an observation available after the cutoff withholds
    assert version.ensemble_probability is None and outcome == "insufficient_evidence"
    assert result["evidence_gaps"] == ["macro_observation_period_or_cutoff_mismatch"]


def test_provider_failure_is_an_execution_failure(client, monkeypatch):
    from forecastlab.errors import PermanentProviderError
    from forecastlab.macro import MacroDataError
    from forecastlab_api import baseline_executor
    run_id = _personal_run(client, mode="live", macro=unrate_spec())

    def failing(spec, **kwargs):
        raise MacroDataError("bls_request_not_succeeded")
    monkeypatch.setattr(baseline_executor, "fetch_macro", failing)
    _no_providers_or_network(monkeypatch)
    with pytest.raises(PermanentProviderError):
        _execute(run_id)
    draft = client.get(f"/api/forecast-drafts/{run_id}").json()
    assert draft["status"] == "failed" and draft["outcome_status"] == "execution_failed"


def test_reruns_endpoint_runs_the_baseline_with_the_personal_envelope(client):
    draft = client.post("/api/forecast-drafts", json={"mode": "demo", "request_key": "baseline-rerun-api",
                                                       "macro": unrate_spec().model_dump(mode="json")}).json()
    client.post(f"/api/forecast-drafts/{draft['run_id']}/launch", json={"review": {}})
    response = client.post(f"/api/questions/{draft['question_id']}/runs",
                           json={"mode": "demo", "profile_id": "statistical_baseline_v1"})
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["profile_id"] == "statistical_baseline_v1" and run["status"] == "completed"
    report = client.get(f"/api/questions/{draft['question_id']}/report").json()
    assert report["personal_report"]["evidence_gaps"] == ["statistical_baseline_demo_mode_no_official_data"]
    assert report["personal_report"]["method"] == "statistical_baseline_v1"


def test_a_run_without_a_personal_envelope_withholds_visibly(client, monkeypatch):
    """For example a Lab benchmark task: no MacroSpec, so the run completes without a probability."""
    import uuid

    from sqlalchemy import select

    from forecastlab.execution import resolve_execution_context
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ForecastRun, ForecastVersion, Question
    from forecastlab_api.pipeline import create_run_record, execute_run

    _forbid_fetch(monkeypatch)
    with SessionLocal() as session:
        question = Question(id=str(uuid.uuid4()), original_text="Synthetic benchmark question", requested_mode="demo",
                            status="draft", is_benchmark=True)
        session.add(question)
        session.flush()
        context = resolve_execution_context(requested_mode="demo", profile_id="statistical_baseline_v1", settings={})
        run = create_run_record(session, question=question, context=context, as_of=None, enqueue=False)
        session.commit()
        execute_run(session, run)
        run = session.get(ForecastRun, run.id)
        version = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run.id))
        assert run.status == "completed" and version.ensemble_probability is None
        assert run.progress_message == "Probability withheld: statistical_baseline_requires_macro_spec"
        assert run.total_cost_usd == 0


# --- Prospective cohorts -----------------------------------------------------------------------------

def test_new_cohorts_get_four_methods_and_frozen_cohorts_keep_theirs(client, monkeypatch):
    from forecastlab_api import prospective
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ProspectiveCohort

    _live_resolve(monkeypatch)
    previous = ("root_event_ensemble_v1", "single_model_forecaster_v1", "three_track_strict_forecaster_v1")
    current = prospective.METHODS
    assert current == (*previous, "statistical_baseline_v1")
    monkeypatch.setattr(prospective, "METHODS", previous)
    earlier = _freeze_cohort(client, unrate_spec(4.2))
    monkeypatch.setattr(prospective, "METHODS", current)
    later = _freeze_cohort(client, unrate_spec(4.3))
    assert [m["method"] for m in later["methods"]] == list(current)
    assert later["budget_usd"] == 1
    with SessionLocal() as session:
        report = prospective.cohort_report(session, earlier["id"])
        earlier_manifest = json.loads(session.get(ProspectiveCohort, earlier["id"]).manifest_json)
        later_manifest = json.loads(session.get(ProspectiveCohort, later["id"]).manifest_json)
    assert [m["method"] for m in report["methods"]] == list(previous)
    assert {m["method"]: m["assigned"] for m in report["methods"]} == dict.fromkeys(previous, 1)
    assert earlier_manifest["methods"] == list(previous) and later_manifest["methods"] == list(current)
    baseline = next(m for m in later_manifest["entries"][0]["methods"] if m["profile_id"] == "statistical_baseline_v1")
    assert baseline["profile"]["max_estimated_cost_usd"] == 0.0
    assert baseline["execution_context"]["estimated_upper_bound_cost_usd"] == 0.0


# --- Backtest script (offline, against a small ALFRED simulator) -------------------------------------

def _fake_alfred(backtest, *, last_target: str, requests: list[int]):
    from urllib.parse import parse_qs, urlsplit

    def level(period: str) -> str:
        return f"{4 + ((backtest.month_index(period) * 7) % 5 - 2) / 10:.1f}"

    def release(period: str) -> date:  # each month is first printed on the 5th of the next month
        return backtest.month_start(backtest.shift_month(period, 1)) + timedelta(days=4)

    first, last = backtest.month_index(backtest.START_MONTH), backtest.month_index(last_target)
    vintages = [release(backtest.month_period(index)) for index in range(first, last + 1)]

    def download(self, url):
        if "downloaddata" in url:
            options = "".join(f'<option value="{v}">{v}</option>' for v in vintages)
            return f'<select id="form_selected_vintage_dates" multiple="multiple">{options}</select>'.encode(), "text/html"
        query = parse_qs(urlsplit(url).query)
        ids, starts, ends, days = (query[key][0].split(",") for key in ("id", "cosd", "coed", "vintage_date"))
        requests.append(len(ids))
        months = sorted({backtest.month_period(index) for start, end in zip(starts, ends, strict=True)
                         for index in range(backtest.month_index(start), backtest.month_index(end) + 1)})
        header = "observation_date," + ",".join(f"{ids[0]}_{day.replace('-', '')}" for day in days)
        rows = [f"{month}-01," + ",".join(level(month) if start[:7] <= month <= end[:7] and release(month) <=
                                          date.fromisoformat(day) else "" for start, end, day in zip(starts, ends, days, strict=True))
                for month in months]
        return ("\n".join([header, *rows]) + "\n").encode(), "application/csv"
    return download, level, release


def test_backtest_uses_the_day_before_the_first_release_and_batches_politely(tmp_path, monkeypatch):
    from scripts import backtest_statistical_baseline as backtest

    requests: list[int] = []
    download, level, release = _fake_alfred(backtest, last_target="2017-05", requests=requests)
    monkeypatch.setattr(backtest.Fetcher, "_download", download)
    fetcher = backtest.Fetcher(tmp_path, offline=False)
    try:
        result = backtest.monthly_backtest(fetcher, "unemployment", date(2017, 6, 30))
    finally:
        fetcher.close()
    summary = result["summary"]
    assert summary["targets_scored"] == 17 and summary["skipped_targets"] == [] and summary["horizons"] == {"1": 17}
    assert requests == [12, 5, 12, 5]  # vintage lines: 17 release days, then 17 previous days
    for row in result["targets"]:
        first = release(row["target"])
        assert row["first_release_vintage"] == first.isoformat()
        assert row["information_vintage"] == (first - timedelta(days=1)).isoformat()
        assert row["first_release_value"] == float(level(row["target"]))
        assert row["last_period"] == backtest.shift_month(row["target"], -1)
        assert row["last_value"] == float(level(row["last_period"]))
    first_target = result["targets"][0]
    assert first_target["window_start"] == "2007-01" and first_target["n"] == 107  # the BLS v1 ten-year window
    assert len(result["questions"]) == 51
    at = [row for row in result["questions"] if row["position"] == "at"]
    assert all(row["outcome"] == int(Decimal(level(row["target"])) > Decimal(str(row["threshold"]))) for row in at)
    assert all(row["persistence_probability"] == 0.02 for row in at)
    table, _ = backtest.reliability([(row["probability"], row["outcome"]) for row in result["questions"]])
    assert len(table) == 10 and sum(item["count"] for item in table) == 51

    monkeypatch.setattr(backtest.Fetcher, "_download", lambda *_: pytest.fail("offline replay must not download"))
    replay = backtest.Fetcher(tmp_path, offline=True)
    try:
        assert backtest.monthly_backtest(replay, "unemployment", date(2017, 6, 30)) == result
    finally:
        replay.close()
    with pytest.raises(backtest.BacktestError, match="offline_cache_miss"):
        backtest.Fetcher(tmp_path / "empty", offline=True).get("https://alfred.stlouisfed.org/x", kind="csv")


def test_backtest_rejects_a_clamped_alfred_vintage(tmp_path, monkeypatch):
    from scripts import backtest_statistical_baseline as backtest

    def clamped(self, url):
        return b"observation_date,UNRATE_20260930\n2026-08-01,4.1\n2026-09-01,4.2\n", "application/csv"
    monkeypatch.setattr(backtest.Fetcher, "_download", clamped)
    fetcher = backtest.Fetcher(tmp_path, offline=False)
    line = (date(2027, 1, 8), date(2026, 8, 1), date(2026, 12, 1))
    with pytest.raises(backtest.BacktestError, match="alfred_header_mismatch"):
        backtest.fetch_vintages(fetcher, "UNRATE", [line])
    fetcher.close()
