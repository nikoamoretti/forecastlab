from forecastlab.budget import Budget
from forecastlab.engine import run_forecast_engine
from forecastlab.errors import BudgetExceeded
from forecastlab.profiles import load_profile
from forecastlab.providers.mock import SAMPLE_QUESTION, MockModelProvider, MockSearchProvider
from forecastlab.schemas import ForecastProfile, ModelUsage, ResolutionContract


def test_budget_ceiling_stops_run() -> None:
    profile = ForecastProfile(
        id="tiny",
        label="tiny",
        description="budget test",
        tracks=["base_rate"],
        subquestions_per_track=1,
        max_model_calls=0,
        max_search_calls=0,
        max_fetched_documents=0,
        max_tokens=1,
        max_estimated_cost_usd=0.0,
        max_wall_clock_seconds=30,
    )
    budget = Budget(profile)
    try:
        budget.add_model_call("operationalize", 10, 0.01)
        raised = False
    except BudgetExceeded:
        raised = True
    assert raised is True


def test_mock_engine_three_tracks() -> None:
    result = run_forecast_engine(
        question=SAMPLE_QUESTION,
        contract=None,
        profile_id="three_track_ensemble",
        mode="demo",
        as_of=None,
        model=MockModelProvider(),
        search=MockSearchProvider(),
        allow_local_fixtures=True,
    )
    assert result.contract.exact_yes
    assert len(result.tracks) == 3
    assert all(track.forecast is not None for track in result.tracks)
    assert result.aggregation.ensemble_probability is not None
    assert 0.02 <= result.aggregation.ensemble_probability <= 0.98
    evidence_urls = {item["url"] for track in result.tracks for item in track.evidence}
    assert evidence_urls
    assert load_profile("three_track_ensemble").id == "three_track_ensemble"
    assert result.budget["reservations"]
    assert all(item["reconciled"] for item in result.budget["reservations"])
    assert all("max_output_tokens" in item for item in result.budget["reservations"])


def _tiny_profile(**overrides) -> ForecastProfile:
    payload = dict(
        id="tiny",
        label="tiny",
        description="budget test",
        tracks=["base_rate"],
        subquestions_per_track=1,
        max_model_calls=8,
        max_search_calls=8,
        max_fetched_documents=8,
        max_tokens=200,
        max_output_tokens_per_call=40,
        max_estimated_cost_usd=1.0,
        max_wall_clock_seconds=30,
    )
    payload.update(overrides)
    return ForecastProfile(**payload)


def test_reserve_rejects_call_that_cannot_fit() -> None:
    budget = Budget(_tiny_profile(max_tokens=50, max_output_tokens_per_call=40))
    try:
        budget.reserve_model_call("operationalize", estimated_input_tokens=30, max_output_tokens=40)
        raised = False
    except BudgetExceeded as exc:
        raised = True
        assert exc.reason == "max_tokens"
    assert raised is True


def test_reserve_happens_before_provider_call() -> None:
    calls = {"n": 0}

    class Spy(MockModelProvider):
        def complete_json(self, **kwargs):
            calls["n"] += 1
            return super().complete_json(**kwargs)

    try:
        run_forecast_engine(
            question=SAMPLE_QUESTION,
            contract=None,
            profile_id="tiny",
            mode="demo",
            as_of=None,
            model=Spy(),
            search=MockSearchProvider(),
            allow_local_fixtures=True,
            profile=_tiny_profile(max_model_calls=0),
        )
        raised = False
    except BudgetExceeded:
        raised = True
    assert raised is True
    assert calls["n"] == 0


def test_reconcile_releases_unused_reservation() -> None:
    budget = Budget(_tiny_profile())
    reservation = budget.reserve_model_call("x", estimated_input_tokens=80, max_output_tokens=40, estimated_cost_usd=0.2)
    assert budget.state.tokens == 120
    budget.reconcile_model_call(
        reservation,
        ModelUsage(prompt_tokens=20, completion_tokens=10, cost_usd=0.05),
    )
    assert budget.state.tokens == 30
    assert abs(budget.state.cost_usd - 0.05) < 1e-9
    assert reservation.cost_source == "provider_reported"
    assert reservation.unused_tokens == 90


def test_missing_usage_keeps_reserved_estimate() -> None:
    budget = Budget(_tiny_profile())
    reservation = budget.reserve_model_call("x", estimated_input_tokens=80, max_output_tokens=40, estimated_cost_usd=0.2)
    budget.reconcile_model_call(reservation, ModelUsage(prompt_tokens=0, completion_tokens=0, cost_usd=0.0))
    assert budget.state.tokens == 120
    assert abs(budget.state.cost_usd - 0.2) < 1e-9
    assert reservation.cost_source == "estimated"
    assert budget.state.cost_is_estimated is True


def test_provider_receives_max_output_tokens() -> None:
    seen: list[int | None] = []

    class Spy(MockModelProvider):
        def complete_json(self, **kwargs):
            seen.append(kwargs.get("max_output_tokens"))
            return super().complete_json(**kwargs)

    contract = ResolutionContract(
        exact_yes="yes",
        exact_no="no",
        resolution_deadline="2027-07-15T00:00:00+00:00",
        authoritative_source="https://fixtures.forecastlab.local/bls-employment-situation",
    )
    run_forecast_engine(
        question=SAMPLE_QUESTION,
        contract=contract,
        profile_id="tiny",
        mode="demo",
        as_of=None,
        model=Spy(),
        search=MockSearchProvider(),
        allow_local_fixtures=True,
        profile=_tiny_profile(max_tokens=50_000),
    )
    assert seen
    assert all(value is not None and value > 0 for value in seen)
    assert seen[0] == 40
