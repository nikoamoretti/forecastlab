from forecastlab.budget import Budget
from forecastlab.engine import run_forecast_engine
from forecastlab.profiles import load_profile
from forecastlab.providers.base import BudgetExceeded
from forecastlab.providers.mock import SAMPLE_QUESTION, MockModelProvider, MockSearchProvider
from forecastlab.schemas import ForecastProfile


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
