"""Conflict panel features, targets, models and scoring rules."""

from __future__ import annotations

import numpy as np
import pytest
from tests.conflict_support import synthetic_panel

from forecastlab.conflict.features import (
    FEATURE_NAMES,
    MONTHS_SINCE_CAP,
    build_feature_cube,
    future_values,
    months_since_fatality,
    trailing_sum,
)
from forecastlab.conflict.forecaster import PanelConfig, PanelForecaster
from forecastlab.conflict.models import (
    climatology_count_atoms,
    climatology_probabilities,
    enforce_monotone,
    fit_horizon_model,
    fit_logistic,
    log1p_bin_edges,
    persistence_probabilities,
    sigmoid,
    weighted_mean,
    weighted_quantiles,
)
from forecastlab.conflict.months import format_month
from forecastlab.conflict.scoring import (
    brier_score,
    calibration_table,
    cluster_bootstrap_skill,
    crps_weighted_atoms,
    log_loss,
    murphy_decomposition,
    roc_auc,
    skill_score,
)

CONFIG = PanelConfig(train_start="2002-01", horizons=(1, 3, 6, 12))


def test_trailing_windows_and_months_since_fatality() -> None:
    series = np.array([[0, 2, 0, 0, 5, 0]], dtype=float)
    valid = np.ones_like(series, dtype=bool)
    assert trailing_sum(series, 1).tolist() == [[0, 2, 0, 0, 5, 0]]
    assert trailing_sum(series, 3).tolist() == [[0, 2, 2, 2, 5, 5]]
    assert months_since_fatality(series, valid).tolist() == [[MONTHS_SINCE_CAP, 0, 1, 2, 0, 1]]
    assert months_since_fatality(np.zeros((1, 3)), np.ones((1, 3), dtype=bool), cap=5).tolist() == [[5, 5, 5]]


def test_feature_values_for_a_hand_built_series() -> None:
    series = np.zeros((2, 30))
    series[0, 28] = 9  # country 0: one violent month
    series[1, 29] = 1
    valid = np.ones_like(series, dtype=bool)
    cube = build_feature_cube(series, valid, ("Europe", "Europe"))
    names = list(FEATURE_NAMES)
    at = cube[0, 29]
    assert at[names.index("log1p_fatalities_1m")] == 0.0
    assert at[names.index("log1p_fatalities_3m")] == pytest.approx(np.log1p(9))
    assert at[names.index("log1p_months_since_fatality")] == pytest.approx(np.log1p(1))
    assert at[names.index("share_violent_months_12m")] == pytest.approx(1 / 12)
    # The regional mean excludes the country itself: country 1 had log1p(1) over 12 months.
    assert at[names.index("region_mean_log1p_fatalities_12m")] == pytest.approx(np.log1p(1))
    assert at[names.index("global_share_violent_months_12m")] == pytest.approx(2 / 24)


def test_features_at_origin_use_only_data_up_to_the_origin() -> None:
    panel = synthetic_panel()
    series = panel.series("total").astype(float)
    full = build_feature_cube(series, panel.valid, panel.regions)
    for origin_index in (30, 80, panel.n_months - 2):
        truncated = build_feature_cube(series[:, : origin_index + 1], panel.valid[:, : origin_index + 1], panel.regions)
        np.testing.assert_array_equal(full[:, : origin_index + 1], truncated)
        altered = series.copy()
        altered[:, origin_index + 1 :] = 10_000
        np.testing.assert_array_equal(full[:, : origin_index + 1], build_feature_cube(altered, panel.valid, panel.regions)[:, : origin_index + 1])


def test_targets_are_the_single_month_t_plus_h() -> None:
    series = np.arange(12, dtype=float).reshape(1, 12)
    valid = np.ones_like(series, dtype=bool)
    valid[0, 7] = False
    target, observed = future_values(series, valid, 3)
    assert target[0, 2] == 5.0 and observed[0, 2]
    assert not observed[0, 4]  # month 7 is not observed
    assert not observed[0, 9:].any()  # beyond the panel
    with pytest.raises(ValueError):
        future_values(series, valid, 0)


def test_training_rows_have_outcomes_observed_by_the_origin() -> None:
    panel = synthetic_panel()
    forecaster = PanelForecaster(panel, CONFIG)
    origin_index = 100
    for horizon in CONFIG.horizons:
        mask = forecaster.training_mask(origin_index, horizon)
        rows = np.argwhere(mask)
        assert rows.size
        assert rows[:, 1].max() + horizon == origin_index
        assert rows[:, 1].min() == forecaster.train_start_index
        assert panel.valid[rows[:, 0], rows[:, 1]].all()
        assert panel.valid[rows[:, 0], rows[:, 1] + horizon].all()


def test_forecast_at_origin_ignores_all_later_data() -> None:
    panel = synthetic_panel()
    origin = panel.first_month + 110
    reference = PanelForecaster(panel, CONFIG)
    truncated = PanelForecaster(panel.truncated(origin), CONFIG)
    altered_panel = panel.truncated(panel.last_month)
    for name in altered_panel.counts:
        altered_panel.counts[name][:, 111:] = 0
    altered = PanelForecaster(altered_panel, CONFIG)
    for horizon in (1, 12):
        expected = reference.forecast(110, horizon)
        for other in (truncated, altered):
            got = other.forecast(110, horizon)
            np.testing.assert_allclose(got.logit, expected.logit, rtol=0, atol=1e-12)
            np.testing.assert_allclose(got.climatology, expected.climatology, rtol=0, atol=0)
            np.testing.assert_allclose(got.model_atoms, expected.model_atoms, rtol=0, atol=1e-12)
    assert format_month(origin) == "2009-03"


def test_logistic_regression_recovers_known_coefficients() -> None:
    rng = np.random.default_rng(3)
    x = rng.normal(size=(20_000, 2))
    design = np.column_stack([np.ones(len(x)), x])
    truth = np.array([-1.0, 1.5, -0.7])
    y = (rng.random(len(x)) < sigmoid(design @ truth)).astype(float)
    beta = fit_logistic(design, y, l2=0.0)
    np.testing.assert_allclose(beta, truth, atol=0.08)
    # A strong penalty shrinks slopes but not the intercept.
    shrunk = fit_logistic(design, y, l2=1e6)
    assert np.all(np.abs(shrunk[1:]) < 0.01)
    # Separable data still converge to finite coefficients under the penalty.
    separable = fit_logistic(design[:200], (x[:200, 0] > 0).astype(float), l2=1.0)
    assert np.all(np.isfinite(separable))


def test_threshold_probabilities_are_monotone() -> None:
    rng = np.random.default_rng(0)
    raw = rng.random((500, 3))
    fixed = enforce_monotone(raw)
    assert np.all(fixed[:, 0] >= fixed[:, 1]) and np.all(fixed[:, 1] >= fixed[:, 2])
    assert np.array_equal(fixed[:, 0], raw[:, 0])
    panel = synthetic_panel()
    forecaster = PanelForecaster(panel, CONFIG)
    for horizon in CONFIG.horizons:
        forecast = forecaster.forecast(120, horizon)
        assert np.all(np.diff(forecast.logit, axis=1) <= 1e-15)
        assert np.all(np.diff(forecast.climatology, axis=1) <= 1e-15)
        assert np.all(np.diff(forecast.persistence, axis=1) <= 0)


def test_count_distribution_matches_threshold_probabilities() -> None:
    panel = synthetic_panel()
    forecaster = PanelForecaster(panel, CONFIG)
    forecast = forecaster.forecast(120, 3)
    atoms, weights = forecast.model_atoms, forecast.model_weights
    np.testing.assert_allclose(weights.sum(axis=1), 1.0, atol=1e-12)
    for k, threshold in enumerate(CONFIG.thresholds):
        mass = np.sum(weights * (atoms >= np.log1p(threshold) - 1e-12), axis=1)
        np.testing.assert_allclose(mass, forecast.logit[:, k], atol=1e-12)
    assert np.all(atoms >= 0)
    edges = log1p_bin_edges((1, 25, 100))
    assert edges[0] == (pytest.approx(np.log1p(1)), pytest.approx(np.log1p(24)))
    assert edges[2][1] == np.inf
    with pytest.raises(ValueError):
        fit_horizon_model(np.zeros((3, 2)), np.zeros(3), horizon=1, thresholds=(25, 100))


def test_climatology_and_persistence() -> None:
    series = np.array([[0, 30, 0, 120], [0, 0, 0, 0]], dtype=float)
    valid = np.array([[True, True, True, True], [False, False, True, True]])
    clim = climatology_probabilities(series, valid, 3, (1, 25, 100))
    np.testing.assert_allclose(clim[0], [(2 + 0.5) / 5, (2 + 0.5) / 5, (1 + 0.5) / 5])
    np.testing.assert_allclose(clim[1], [0.5 / 3] * 3)
    assert persistence_probabilities(series, 3, (1, 25, 100)).tolist() == [[1, 1, 1], [0, 0, 0]]
    assert persistence_probabilities(series, 1, (1, 25, 100)).tolist() == [[1, 1, 0], [0, 0, 0]]
    atoms = climatology_count_atoms(series, valid, 3, 4)
    assert sorted(atoms[0].tolist()) == pytest.approx(sorted(np.log1p([0, 30, 0, 120]).tolist()))
    assert atoms[1].tolist() == [0, 0, 0, 0]


def test_weighted_quantiles_and_mean() -> None:
    atoms = np.array([[0.0, 1.0, 2.0, 3.0]])
    weights = np.array([[0.5, 0.2, 0.2, 0.1]])
    assert weighted_quantiles(atoms, weights, (0.25, 0.5, 0.6, 0.95)).tolist() == [[0.0, 0.0, 1.0, 3.0]]
    assert weighted_mean(atoms, weights)[0] == pytest.approx(0.2 + 0.4 + 0.3)


def test_brier_log_loss_and_skill() -> None:
    p = np.array([0.2, 0.8, 0.5])
    y = np.array([0, 1, 1])
    assert brier_score(p, y) == pytest.approx((0.04 + 0.04 + 0.25) / 3)
    assert log_loss(p, y) == pytest.approx(-(np.log(0.8) + np.log(0.8) + np.log(0.5)) / 3)
    assert log_loss(np.array([0.0]), np.array([1])) == pytest.approx(-np.log(1e-4))
    assert skill_score(0.05, 0.1) == pytest.approx(0.5)
    assert skill_score(0.1, 0.0) is None
    with pytest.raises(ValueError):
        brier_score(np.array([1.2]), np.array([1]))
    with pytest.raises(ValueError):
        brier_score(np.array([0.2]), np.array([2]))


def test_auc_handles_ties_and_single_class() -> None:
    assert roc_auc(np.array([0.1, 0.2, 0.8, 0.9]), np.array([0, 0, 1, 1])) == 1.0
    assert roc_auc(np.array([0.9, 0.8, 0.2, 0.1]), np.array([0, 0, 1, 1])) == 0.0
    assert roc_auc(np.full(4, 0.3), np.array([0, 1, 0, 1])) == 0.5
    assert roc_auc(np.array([0.1, 0.2]), np.array([0, 0])) is None


def test_calibration_bins_and_murphy_decomposition() -> None:
    p = np.array([0.05, 0.05, 0.15, 0.95, 1.0, 0.55])
    y = np.array([0, 1, 0, 1, 1, 0])
    table = calibration_table(p, y)
    assert len(table) == 10 and sum(row["n"] for row in table) == 6
    assert table[0] == {"bin": "0.0-0.1", "n": 2, "mean_forecast": pytest.approx(0.05), "observed_rate": 0.5, "events": 1}
    assert table[9]["n"] == 2 and table[9]["observed_rate"] == 1.0  # p = 1.0 falls in the top bin
    assert table[3]["n"] == 0 and table[3]["mean_forecast"] is None
    binned = np.repeat([0.1, 0.7], 50)
    outcome = np.concatenate([np.zeros(45), np.ones(5), np.ones(35), np.zeros(15)])
    parts = murphy_decomposition(binned, outcome)
    assert parts["reliability"] - parts["resolution"] + parts["uncertainty"] == pytest.approx(brier_score(binned, outcome))
    assert parts["reliability"] == pytest.approx(0.0)


def test_crps_of_discrete_distributions() -> None:
    point = crps_weighted_atoms(np.array([[2.0]]), np.array([[1.0]]), np.array([5.0]))
    assert point[0] == pytest.approx(3.0)
    two_point = crps_weighted_atoms(np.array([[0.0, 1.0]]), np.array([[0.5, 0.5]]), np.array([0.0]))
    assert two_point[0] == pytest.approx(0.25)
    rng = np.random.default_rng(5)
    atoms = rng.normal(size=(1, 7))
    weights = rng.random((1, 7))
    weights /= weights.sum()
    observed = 0.3
    grid = np.linspace(-6, 6, 120_001)
    cdf = (weights[0][None, :] * (atoms[0][None, :] <= grid[:, None])).sum(axis=1)
    numeric = np.trapezoid((cdf - (grid >= observed)) ** 2, grid)
    assert crps_weighted_atoms(atoms, weights, np.array([observed]))[0] == pytest.approx(numeric, abs=1e-3)


def test_cluster_bootstrap_interval() -> None:
    rng = np.random.default_rng(1)
    clusters = np.repeat(np.arange(20), 30)
    reference = rng.random(600)
    score = reference * 0.5
    low, high = cluster_bootstrap_skill(score, reference, clusters, replicates=200)
    assert low == pytest.approx(0.5) and high == pytest.approx(0.5)
    noisy = cluster_bootstrap_skill(rng.random(600), reference, clusters, replicates=200)
    assert noisy is not None and noisy[0] < noisy[1]
    assert cluster_bootstrap_skill(score, reference, np.zeros(600), replicates=10) is None
