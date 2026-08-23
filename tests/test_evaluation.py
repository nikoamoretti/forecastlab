import pytest

from forecastlab.evaluation import (
    BOOTSTRAP_SEED,
    bootstrap_mean_ci,
    brier_score,
    log_loss,
    mean,
    median,
    paired_profile_comparison,
    reliability_bins,
)


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
    assert "not a calibration claim" in str(large["message"]).lower() or "display" in str(large).lower()


def test_paired_bootstrap_is_deterministic() -> None:
    left = {
        "q1": {"brier": 0.1, "log_loss": 0.2, "cost_usd": 1.0, "latency_ms": 10, "evidence_coverage": 0.8},
        "q2": {"brier": 0.2, "log_loss": 0.3, "cost_usd": 1.0, "latency_ms": 12, "evidence_coverage": 0.9},
        "q3": {"brier": 0.3, "log_loss": 0.4, "cost_usd": 1.2, "latency_ms": 11, "evidence_coverage": 1.0},
    }
    right = {
        "q1": {"brier": 0.2, "log_loss": 0.3, "cost_usd": 2.0, "latency_ms": 20, "evidence_coverage": 0.7},
        "q2": {"brier": 0.2, "log_loss": 0.3, "cost_usd": 2.0, "latency_ms": 22, "evidence_coverage": 0.8},
        "q3": {"brier": 0.4, "log_loss": 0.5, "cost_usd": 2.2, "latency_ms": 21, "evidence_coverage": 0.9},
    }
    first = paired_profile_comparison(left, right, left_id="a", right_id="b")
    second = paired_profile_comparison(left, right, left_id="a", right_id="b")
    assert first["paired_brier_bootstrap"] == second["paired_brier_bootstrap"]
    assert first["paired_brier_bootstrap"]["seed"] == BOOTSTRAP_SEED
    assert first["mean_evidence_coverage_difference"] == pytest.approx(0.1)
    again = bootstrap_mean_ci([-0.1, 0.0, -0.1])
    assert again == bootstrap_mean_ci([-0.1, 0.0, -0.1])
