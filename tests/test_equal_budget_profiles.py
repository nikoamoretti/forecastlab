from forecastlab.profiles import load_profile


def test_equal_budget_profiles_share_ceilings() -> None:
    left = load_profile("single_agent_equal_budget_v1")
    right = load_profile("three_track_equal_budget_v1")
    assert left.max_search_calls == right.max_search_calls
    assert left.max_fetched_documents == right.max_fetched_documents
    assert left.max_tokens == right.max_tokens
    assert left.max_estimated_cost_usd == right.max_estimated_cost_usd
    assert left.max_wall_clock_seconds == right.max_wall_clock_seconds
    assert left.max_model_calls == right.max_model_calls
