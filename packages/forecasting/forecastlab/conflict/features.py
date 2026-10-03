"""Causal features and targets for the country-month panel.

A forecast at origin month ``t`` may use only data for months ``<= t``. Every feature
for month index ``s`` below is computed from columns ``0..s`` of the fatality series,
so features are identical whether or not later months exist in the panel. The forecast
target for horizon ``h`` is the fatality count in the single month ``t + h``.
"""

from __future__ import annotations

import numpy as np

FEATURE_NAMES = (
    "log1p_fatalities_1m",
    "log1p_fatalities_3m",
    "log1p_fatalities_6m",
    "log1p_fatalities_12m",
    "log1p_fatalities_24m",
    "log1p_months_since_fatality",
    "share_violent_months_12m",
    "region_mean_log1p_fatalities_12m",
    "global_share_violent_months_12m",
)
SUM_WINDOWS = (1, 3, 6, 12, 24)
MONTHS_SINCE_CAP = 120


def trailing_sum(values: np.ndarray, window: int) -> np.ndarray:
    """Sum over months ``s - window + 1 .. s`` (fewer at the start of the panel)."""
    cumulative = np.concatenate([np.zeros((values.shape[0], 1)), np.cumsum(values, axis=1)], axis=1)
    upper = np.arange(1, values.shape[1] + 1)
    lower = np.maximum(upper - window, 0)
    return cumulative[:, upper] - cumulative[:, lower]


def months_since_fatality(series: np.ndarray, valid: np.ndarray, cap: int = MONTHS_SINCE_CAP) -> np.ndarray:
    """Months since the latest month with at least one fatality (0 if the current month has one).

    Countries with no such month in the last ``cap`` months, or in the data so far, get ``cap``.
    """
    n_countries, n_months = series.shape
    last = np.full(n_countries, -(10**6), dtype=np.int64)
    out = np.empty((n_countries, n_months), dtype=np.float64)
    for s in range(n_months):
        hit = (series[:, s] >= 1) & valid[:, s]
        last[hit] = s
        out[:, s] = np.minimum(s - last, cap)
    return out


def build_feature_cube(series: np.ndarray, valid: np.ndarray, regions: tuple[str, ...]) -> np.ndarray:
    """Features for every country and month, shape ``(n_countries, n_months, n_features)``."""
    values = np.where(valid, series, 0).astype(np.float64)
    violent = ((values >= 1) & valid).astype(np.float64)
    valid_f = valid.astype(np.float64)
    features: list[np.ndarray] = [np.log1p(trailing_sum(values, window)) for window in SUM_WINDOWS]
    features.append(np.log1p(months_since_fatality(values, valid)))
    violent_12 = trailing_sum(violent, 12)
    valid_12 = trailing_sum(valid_f, 12)
    features.append(violent_12 / np.maximum(valid_12, 1.0))

    log_12 = np.log1p(trailing_sum(values, 12)) * valid_f
    region_mean = np.zeros_like(log_12)
    region_labels = np.array(regions)
    for region in sorted(set(regions)):
        members = region_labels == region
        total = log_12[members].sum(axis=0)
        count = valid_f[members].sum(axis=0)
        own = log_12[members]
        own_valid = valid_f[members]
        others = np.maximum(count[None, :] - own_valid, 0.0)
        region_mean[members] = np.where(others > 0, (total[None, :] - own) / np.maximum(others, 1.0), 0.0)
    features.append(region_mean)

    global_share = violent_12.sum(axis=0) / np.maximum(valid_12.sum(axis=0), 1.0)
    features.append(np.broadcast_to(global_share, values.shape).copy())
    cube = np.stack(features, axis=2)
    if cube.shape[2] != len(FEATURE_NAMES):
        raise AssertionError("feature list out of sync")
    return cube


def future_values(series: np.ndarray, valid: np.ndarray, horizon: int) -> tuple[np.ndarray, np.ndarray]:
    """Target values ``series[:, s + horizon]`` and a mask of where that month is observed."""
    if horizon < 1:
        raise ValueError("horizon must be at least one month")
    n_countries, n_months = series.shape
    target = np.zeros((n_countries, n_months), dtype=np.float64)
    observed = np.zeros((n_countries, n_months), dtype=bool)
    if horizon < n_months:
        target[:, : n_months - horizon] = series[:, horizon:]
        observed[:, : n_months - horizon] = valid[:, horizon:]
    return target, observed
