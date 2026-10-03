"""Fit and forecast the panel models at a single origin month.

Conventions (see docs/CONFLICT_PANEL_V1.md):

* the origin ``t`` is the last month of data a forecast may use;
* horizon ``h`` targets the fatalities in the single calendar month ``t + h``;
* the logistic and count models for origin ``t`` are fitted only on rows ``(country, s)``
  with ``s + h <= t``, so every outcome used in fitting was observed by month ``t``,
  and every feature uses months ``<= s``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from forecastlab.conflict.features import FEATURE_NAMES, build_feature_cube, future_values
from forecastlab.conflict.models import (
    HorizonModel,
    climatology_count_atoms,
    climatology_probabilities,
    fit_horizon_model,
    persistence_probabilities,
    weighted_mean,
    weighted_quantiles,
)
from forecastlab.conflict.months import format_month, parse_month
from forecastlab.conflict.panel import Panel

MODEL_VERSION = "conflict_panel_v1"
COUNT_QUANTILE_LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)


@dataclass(frozen=True)
class PanelConfig:
    series: str = "total"
    thresholds: tuple[int, ...] = (1, 25, 100)
    horizons: tuple[int, ...] = (1, 3, 6, 12)
    train_start: str = "1990-12"
    l2: float = 1.0
    bin_atoms: int = 20
    climatology_atoms: int = 61
    climatology_prior: float = 0.5

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["model_version"] = MODEL_VERSION
        payload["features"] = list(FEATURE_NAMES)
        return payload


@dataclass
class OriginForecast:
    """Forecasts from all models for one origin and horizon.

    Probability arrays have one column per threshold; ``*_atoms``/``*_weights`` describe
    predictive distributions of ``log1p(fatalities)``.
    """

    origin: int
    horizon: int
    rows: np.ndarray
    logit: np.ndarray
    logit_raw: np.ndarray
    climatology: np.ndarray
    persistence: np.ndarray
    model_atoms: np.ndarray
    model_weights: np.ndarray
    climatology_atoms: np.ndarray
    origin_value: np.ndarray
    model: HorizonModel

    @property
    def target_month(self) -> int:
        return self.origin + self.horizon

    def model_count_summary(self) -> dict[str, np.ndarray]:
        quantiles = weighted_quantiles(self.model_atoms, self.model_weights, COUNT_QUANTILE_LEVELS)
        summary = {"mean_log1p": weighted_mean(self.model_atoms, self.model_weights)}
        for j, level in enumerate(COUNT_QUANTILE_LEVELS):
            summary[f"q{int(round(level * 100)):02d}_log1p"] = quantiles[:, j]
        return summary


class PanelForecaster:
    """Precomputes causal features once, then fits and forecasts at any origin."""

    def __init__(self, panel: Panel, config: PanelConfig | None = None):
        self.panel = panel
        self.config = config or PanelConfig()
        self.series = panel.series(self.config.series).astype(np.float64)
        self.valid = panel.valid
        self.cube = build_feature_cube(self.series, self.valid, panel.regions)
        start = parse_month(self.config.train_start) - panel.first_month
        self.train_start_index = max(0, start)
        self._targets = {h: future_values(self.series, self.valid, h) for h in self.config.horizons}

    def origin_index(self, origin: int) -> int:
        return self.panel.month_index(origin)

    def training_mask(self, origin_index: int, horizon: int) -> np.ndarray:
        """Rows usable to fit a model at ``origin_index``: outcome month ``s + h <= origin``."""
        target, observed = self._target(horizon)
        mask = np.zeros_like(self.valid)
        last_row = origin_index - horizon
        if last_row >= self.train_start_index:
            window = slice(self.train_start_index, last_row + 1)
            mask[:, window] = self.valid[:, window] & observed[:, window]
        return mask

    def _target(self, horizon: int) -> tuple[np.ndarray, np.ndarray]:
        if horizon not in self._targets:
            self._targets[horizon] = future_values(self.series, self.valid, horizon)
        return self._targets[horizon]

    def fit(self, origin_index: int, horizon: int, *, warm_start: HorizonModel | None = None) -> HorizonModel:
        mask = self.training_mask(origin_index, horizon)
        target, _ = self._target(horizon)
        return fit_horizon_model(
            self.cube[mask],
            target[mask],
            horizon=horizon,
            thresholds=self.config.thresholds,
            l2=self.config.l2,
            bin_atoms=self.config.bin_atoms,
            warm_start=warm_start,
        )

    def forecast(
        self,
        origin_index: int,
        horizon: int,
        *,
        model: HorizonModel | None = None,
        warm_start: HorizonModel | None = None,
        require_observed_target: bool = False,
    ) -> OriginForecast:
        """Forecast every country that exists at the origin (and, optionally, at ``t + h``)."""
        if model is None:
            model = self.fit(origin_index, horizon, warm_start=warm_start)
        rows_mask = self.valid[:, origin_index].copy()
        if require_observed_target:
            _, observed = self._target(horizon)
            rows_mask &= observed[:, origin_index]
        rows = np.flatnonzero(rows_mask)
        features = self.cube[rows, origin_index, :]
        raw = model.raw_probabilities(features)
        probabilities = model.probabilities(features)
        atoms, weights = model.count_distribution(features, probabilities)
        thresholds = self.config.thresholds
        clim = climatology_probabilities(
            self.series, self.valid, origin_index, thresholds, prior=self.config.climatology_prior
        )[rows]
        clim_atoms = climatology_count_atoms(self.series[rows], self.valid[rows], origin_index, self.config.climatology_atoms)
        persistence = persistence_probabilities(self.series, origin_index, thresholds)[rows]
        return OriginForecast(
            origin=self.panel.first_month + origin_index,
            horizon=horizon,
            rows=rows,
            logit=probabilities,
            logit_raw=raw,
            climatology=clim,
            persistence=persistence,
            model_atoms=atoms,
            model_weights=weights,
            climatology_atoms=clim_atoms,
            origin_value=np.log1p(self.series[rows, origin_index]),
            model=model,
        )

    def outcomes(self, forecast: OriginForecast) -> np.ndarray:
        """Observed fatalities in the target month for the forecast rows."""
        target, observed = self._target(forecast.horizon)
        index = forecast.origin - self.panel.first_month
        if not np.all(observed[forecast.rows, index]):
            raise ValueError(f"target month {format_month(forecast.target_month)} not observed for all rows")
        return target[forecast.rows, index]
