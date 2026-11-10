from __future__ import annotations

import pytest

from forecastlab_api.learned_model import BOUNDS, fit_learned_model, learned_probability


def test_with_no_results_the_model_is_the_market_price() -> None:
    fit = fit_learned_model([])
    assert fit == {"intercept": 0.0, "slope": 1.0, "weights": {}}
    assert learned_probability(0.3, 0.9, "Tech", fit) == pytest.approx(0.3)
    # Prices outside the bounds are read as the bound.
    assert learned_probability(0.001, 0.9, "Tech", fit) == pytest.approx(BOUNDS[0])
    assert learned_probability(0.999, 0.1, "Tech", fit) == pytest.approx(BOUNDS[1])


def test_it_learns_which_topic_to_follow_claude_on() -> None:
    # The market says 50% and Claude 80% on both topics. On Tech yes happens 80% of the time (Claude is right), on
    # Politics half the time (the market is right). World pins the constant: no disagreement, and the price holds.
    rows = ([(0.5, 0.8, "Tech", 0 if n % 5 == 0 else 1) for n in range(200)]
            + [(0.5, 0.8, "Politics", n % 2) for n in range(200)]
            + [(0.5, 0.5, "World", n % 2) for n in range(200)])
    fit = fit_learned_model(rows)
    assert fit["weights"]["Tech"] > 0.8 and abs(fit["weights"]["Politics"]) < 0.1 and abs(fit["intercept"]) < 0.1
    assert learned_probability(0.5, 0.8, "Tech", fit) == pytest.approx(0.8, abs=0.03)
    assert learned_probability(0.5, 0.8, "Politics", fit) == pytest.approx(0.5, abs=0.03)
    # A topic with no results has no weight: Claude's disagreement changes nothing there.
    assert "Crypto" not in fit["weights"]
    assert learned_probability(0.3, 0.9, "Crypto", fit) == learned_probability(0.3, 0.3, "Crypto", fit)


def test_it_learns_when_yes_happens_more_often_than_priced_but_not_all_the_way() -> None:
    fit = fit_learned_model([(0.5, 0.5, "World", 1 if n % 10 < 7 else 0) for n in range(300)])
    assert 0.65 < learned_probability(0.5, 0.5, "World", fit) < 0.70


def test_a_short_record_moves_it_less_than_a_long_one() -> None:
    pattern = [(0.5, 0.9, "Tech", 0), (0.5, 0.9, "Tech", 1), (0.5, 0.9, "Tech", 1), (0.5, 0.9, "Tech", 1), (0.5, 0.9, "Tech", 1)]
    short = learned_probability(0.5, 0.9, "Tech", fit_learned_model(pattern))
    long = learned_probability(0.5, 0.9, "Tech", fit_learned_model(pattern * 60))
    assert 0.5 < short < long < 0.81


def test_the_fit_is_the_same_whatever_the_order_of_the_results() -> None:
    rows = [(0.2 + 0.06 * (n % 11), 0.1 + 0.07 * (n % 12), ("Tech", "Politics", "Economy")[n % 3], (n * 7) % 3 == 0) for n in range(150)]
    first, second = fit_learned_model(rows), fit_learned_model(rows[::-1])
    assert fit_learned_model(rows) == first
    assert second["intercept"] == pytest.approx(first["intercept"]) and second["slope"] == pytest.approx(first["slope"])
    assert second["weights"] == pytest.approx(first["weights"])
    assert list(first["weights"]) == ["Economy", "Politics", "Tech"]
