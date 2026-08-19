from forecastlab.evaluation import brier_score, log_loss, mean, median, reliability_bins


def test_brier_and_log_loss() -> None:
    assert brier_score(0.7, 1) == (0.7 - 1) ** 2
    assert log_loss(0.9, 1) < log_loss(0.1, 1)
    assert mean([1.0, 3.0]) == 2.0
    assert median([1.0, 3.0, 2.0]) == 2.0


def test_reliability_requires_twenty_points() -> None:
    small = reliability_bins([(0.2, 0), (0.8, 1)])
    assert small["available"] is False
    assert small["sample_count"] == 2
    pairs = [(0.1, 0)] * 10 + [(0.9, 1)] * 10
    large = reliability_bins(pairs)
    assert large["available"] is True
    assert large["sample_count"] == 20
