"""Rolling-origin backtest of the panel models and its scoreboard.

For every origin month ``t`` and horizon ``h`` with an observed target month ``t + h``,
the models are refitted on rows whose outcome month is ``<= t`` and scored on month
``t + h``. Results are stored per country-forecast and summarised by threshold, horizon
and country slice.
"""

from __future__ import annotations

import csv
import gzip
import io
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from forecastlab.conflict.forecaster import OriginForecast, PanelConfig, PanelForecaster
from forecastlab.conflict.models import weighted_mean, weighted_quantiles
from forecastlab.conflict.months import format_month, parse_month
from forecastlab.conflict.panel import FRANCE_COUNTRY_ID, Panel
from forecastlab.conflict.scoring import (
    binary_metrics,
    calibration_table,
    cluster_bootstrap_skill,
    crps_weighted_atoms,
    murphy_decomposition,
    skill_score,
)

MODELS = ("logit", "climatology", "persistence")
INTERVAL_LEVELS = (0.05, 0.5, 0.95)
INT_COLUMNS = ("origin", "horizon", "target_month", "country_id")


def _round(value: Any, digits: int = 6) -> Any:
    if value is None:
        return None
    if isinstance(value, float | np.floating):
        number = float(value)
        if not np.isfinite(number):
            return None
        return float(f"{number:.{digits}g}")
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, dict):
        return {key: _round(item, digits) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_round(item, digits) for item in value]
    return value


@dataclass
class BacktestRecords:
    """One row per (origin, horizon, country) forecast with its outcome."""

    thresholds: tuple[int, ...]
    columns: dict[str, np.ndarray]
    fits: list[dict[str, Any]] = field(default_factory=list)

    def __len__(self) -> int:
        return int(len(self.columns["origin"]))

    def prob(self, model: str, threshold: int, *, raw: bool = False) -> np.ndarray:
        prefix = "p_raw" if raw else f"p_{model}"
        return self.columns[f"{prefix}_ge{threshold}"]

    def subset(self, mask: np.ndarray) -> BacktestRecords:
        return BacktestRecords(self.thresholds, {key: values[mask] for key, values in self.columns.items()}, self.fits)

    def save_csv_gz(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        names = list(self.columns)
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(names)
        formatted = []
        for name in names:
            values = self.columns[name]
            if name in INT_COLUMNS:
                formatted.append([format_month(v) if name in ("origin", "target_month") else str(int(v)) for v in values])
            else:
                formatted.append([f"{float(v):.6g}" for v in values])
        writer.writerows(zip(*formatted, strict=True))
        partial = path.with_name(path.name + ".part")
        with gzip.open(partial, "wt", encoding="utf-8", newline="") as handle:
            handle.write(buffer.getvalue())
        os.replace(partial, path)


def _equal_weights(atoms: np.ndarray) -> np.ndarray:
    return np.full(atoms.shape, 1.0 / atoms.shape[1])


def _forecast_columns(forecast: OriginForecast, outcome: np.ndarray, forecaster: PanelForecaster) -> dict[str, np.ndarray]:
    thresholds = forecaster.config.thresholds
    n = len(forecast.rows)
    log_outcome = np.log1p(outcome)
    columns: dict[str, np.ndarray] = {
        "origin": np.full(n, forecast.origin),
        "horizon": np.full(n, forecast.horizon),
        "target_month": np.full(n, forecast.target_month),
        "country_id": forecaster.panel.country_ids[forecast.rows],
        "y": outcome,
        "y_origin": np.expm1(forecast.origin_value),
    }
    for k, threshold in enumerate(thresholds):
        columns[f"p_logit_ge{threshold}"] = forecast.logit[:, k]
        columns[f"p_raw_ge{threshold}"] = forecast.logit_raw[:, k]
        columns[f"p_climatology_ge{threshold}"] = forecast.climatology[:, k]
        columns[f"p_persistence_ge{threshold}"] = forecast.persistence[:, k]
    model_quantiles = weighted_quantiles(forecast.model_atoms, forecast.model_weights, INTERVAL_LEVELS)
    clim_weights = _equal_weights(forecast.climatology_atoms)
    clim_quantiles = weighted_quantiles(forecast.climatology_atoms, clim_weights, INTERVAL_LEVELS)
    columns.update(
        {
            "logit_mean_log1p": weighted_mean(forecast.model_atoms, forecast.model_weights),
            "logit_q05_log1p": model_quantiles[:, 0],
            "logit_q50_log1p": model_quantiles[:, 1],
            "logit_q95_log1p": model_quantiles[:, 2],
            "logit_crps_log1p": crps_weighted_atoms(forecast.model_atoms, forecast.model_weights, log_outcome),
            "climatology_mean_log1p": forecast.climatology_atoms.mean(axis=1),
            "climatology_q05_log1p": clim_quantiles[:, 0],
            "climatology_q50_log1p": clim_quantiles[:, 1],
            "climatology_q95_log1p": clim_quantiles[:, 2],
            "climatology_crps_log1p": crps_weighted_atoms(forecast.climatology_atoms, clim_weights, log_outcome),
            "persistence_mean_log1p": forecast.origin_value,
            "persistence_q05_log1p": forecast.origin_value,
            "persistence_q50_log1p": forecast.origin_value,
            "persistence_q95_log1p": forecast.origin_value,
            "persistence_crps_log1p": np.abs(forecast.origin_value - log_outcome),
        }
    )
    return columns


def run_backtest(
    panel: Panel,
    config: PanelConfig | None = None,
    *,
    first_origin: int,
    last_origin: int | None = None,
    origins: Sequence[int] | None = None,
    progress: Callable[[str], None] | None = None,
) -> BacktestRecords:
    """Refit and forecast at every origin from ``first_origin``; score only observed targets."""
    config = config or PanelConfig()
    forecaster = PanelForecaster(panel, config)
    last_allowed = panel.last_month if last_origin is None else min(last_origin, panel.last_month)
    candidate_origins = sorted(set(origins)) if origins is not None else list(range(first_origin, last_allowed + 1))
    chunks: list[dict[str, np.ndarray]] = []
    fits: list[dict[str, Any]] = []
    for horizon in config.horizons:
        warm = None
        for origin in candidate_origins:
            if origin < first_origin or origin > last_allowed or origin + horizon > panel.last_month:
                continue
            if origin < panel.first_month:
                continue
            index = forecaster.origin_index(origin)
            forecast = forecaster.forecast(index, horizon, warm_start=warm, require_observed_target=True)
            warm = forecast.model
            outcome = forecaster.outcomes(forecast)
            chunks.append(_forecast_columns(forecast, outcome, forecaster))
            fits.append(
                {
                    "origin": format_month(origin),
                    "horizon": horizon,
                    "n_train": forecast.model.n_train,
                    "train_positives": list(forecast.model.train_positives),
                    "latest_training_outcome_month": format_month(origin),
                }
            )
        if progress is not None:
            progress(f"horizon {horizon}: {sum(1 for fit in fits if fit['horizon'] == horizon)} origins")
    if not chunks:
        raise ValueError("no scorable origins in the requested range")
    columns = {key: np.concatenate([chunk[key] for chunk in chunks]) for key in chunks[0]}
    return BacktestRecords(thresholds=config.thresholds, columns=columns, fits=fits)


def top_countries(panel: Panel, series: str, first_month: int, last_month: int, n: int) -> list[int]:
    """Countries with the most best-estimate fatalities over ``first_month..last_month``."""
    start = panel.month_index(first_month)
    stop = panel.month_index(last_month) + 1
    totals = np.where(panel.valid[:, start:stop], panel.series(series)[:, start:stop], 0).sum(axis=1)
    order = np.argsort(-totals, kind="stable")[:n]
    return [int(panel.country_ids[i]) for i in order if totals[i] > 0]


def _binary_block(records: BacktestRecords, threshold: int, *, bootstrap: int) -> dict[str, Any]:
    outcome = (records.columns["y"] >= threshold).astype(np.float64)
    metrics = {model: binary_metrics(records.prob(model, threshold), outcome) for model in MODELS}
    reference_brier = metrics["climatology"]["brier"]
    reference_log_loss = metrics["climatology"]["log_loss"]
    block: dict[str, Any] = {
        "n": int(outcome.size),
        "events": int(outcome.sum()),
        "observed_rate": float(outcome.mean()) if outcome.size else None,
        "models": metrics,
        "brier_skill_vs_climatology": {
            model: skill_score(metrics[model]["brier"], reference_brier) for model in ("logit", "persistence")
        },
        "log_loss_skill_vs_climatology": {
            model: skill_score(metrics[model]["log_loss"], reference_log_loss) for model in ("logit", "persistence")
        },
    }
    if bootstrap and outcome.size:
        interval = cluster_bootstrap_skill(
            (records.prob("logit", threshold) - outcome) ** 2,
            (records.prob("climatology", threshold) - outcome) ** 2,
            records.columns["country_id"],
            replicates=bootstrap,
        )
        block["logit_brier_skill_95ci_country_bootstrap"] = list(interval) if interval else None
    return block


def _count_block(records: BacktestRecords) -> dict[str, Any]:
    log_outcome = np.log1p(records.columns["y"])
    out: dict[str, Any] = {"n": len(records)}
    for model in MODELS:
        mean = records.columns[f"{model}_mean_log1p"]
        median = records.columns[f"{model}_q50_log1p"]
        low = records.columns[f"{model}_q05_log1p"]
        high = records.columns[f"{model}_q95_log1p"]
        out[model] = {
            "mae_log1p_median": float(np.mean(np.abs(median - log_outcome))) if len(records) else None,
            "mse_log1p_mean": float(np.mean((mean - log_outcome) ** 2)) if len(records) else None,
            "crps_log1p": float(np.mean(records.columns[f"{model}_crps_log1p"])) if len(records) else None,
            "coverage_90": float(np.mean((log_outcome >= low - 1e-9) & (log_outcome <= high + 1e-9)))
            if len(records)
            else None,
        }
    reference = out["climatology"]["crps_log1p"]
    out["crps_skill_vs_climatology"] = {
        model: skill_score(out[model]["crps_log1p"], reference) if reference is not None else None
        for model in ("logit", "persistence")
    }
    return out


def summarize_backtest(
    records: BacktestRecords,
    panel: Panel,
    config: PanelConfig,
    *,
    top_n: int = 20,
    focus_country: int = FRANCE_COUNTRY_ID,
    bootstrap: int = 1000,
) -> dict[str, Any]:
    """Scoreboard: overall, top-N countries by fatalities, and one focus country."""
    origins = records.columns["origin"]
    targets = records.columns["target_month"]
    first_target, last_target = int(targets.min()), int(targets.max())
    top = top_countries(panel, config.series, first_target, last_target, top_n)
    country = records.columns["country_id"]
    slices = {
        "overall": np.ones(len(records), dtype=bool),
        f"top{top_n}": np.isin(country, top),
        "france" if focus_country == FRANCE_COUNTRY_ID else f"country_{focus_country}": country == focus_country,
    }
    by_slice: dict[str, Any] = {}
    for name, mask in slices.items():
        horizons: dict[str, Any] = {}
        for horizon in config.horizons:
            subset = records.subset(mask & (records.columns["horizon"] == horizon))
            if not len(subset):
                continue
            horizons[str(horizon)] = {
                "origins": [format_month(int(subset.columns["origin"].min())), format_month(int(subset.columns["origin"].max()))],
                "thresholds": {
                    str(threshold): _binary_block(subset, threshold, bootstrap=bootstrap if name != "france" else 0)
                    for threshold in config.thresholds
                },
                "count": _count_block(subset),
            }
        by_slice[name] = horizons

    calibration: dict[str, Any] = {}
    for horizon in config.horizons:
        subset = records.subset(records.columns["horizon"] == horizon)
        if not len(subset):
            continue
        per_threshold = {}
        for threshold in config.thresholds:
            outcome = (subset.columns["y"] >= threshold).astype(np.float64)
            per_threshold[str(threshold)] = {
                "logit_table": calibration_table(subset.prob("logit", threshold), outcome),
                "murphy": {model: murphy_decomposition(subset.prob(model, threshold), outcome) for model in ("logit", "climatology")},
            }
        calibration[str(horizon)] = per_threshold

    raw = np.stack([records.prob("logit", t, raw=True) for t in config.thresholds], axis=1)
    violations = np.maximum(raw[:, 1:] - raw[:, :-1], 0.0)
    focus = records.subset((country == focus_country) & (records.columns["horizon"] == config.horizons[0]))
    positive = focus.columns["y"] >= 1
    focus_detail = [
        {
            "target_month": format_month(int(month)),
            "fatalities": int(value),
            **{f"p_logit_ge{t}": float(focus.prob("logit", t)[i]) for t in config.thresholds},
            **{f"p_climatology_ge{t}": float(focus.prob("climatology", t)[i]) for t in config.thresholds},
        }
        for i, (month, value) in enumerate(zip(focus.columns["target_month"], focus.columns["y"], strict=True))
        if positive[i]
    ]
    names = {int(cid): panel.names[i] for i, cid in enumerate(panel.country_ids)}
    summary = {
        "config": config.as_dict(),
        "records": len(records),
        "origins": [format_month(int(origins.min())), format_month(int(origins.max()))],
        "target_months": [format_month(first_target), format_month(last_target)],
        "countries": int(np.unique(country).size),
        f"top{top_n}_countries": [{"country_id": cid, "country": names[cid]} for cid in top],
        "slices": by_slice,
        "calibration_overall": calibration,
        "monotonicity": {
            "rows_with_raw_violation": int(np.sum(np.any(violations > 1e-12, axis=1))),
            "share_rows_with_raw_violation": float(np.mean(np.any(violations > 1e-12, axis=1))),
            "max_raw_violation": float(violations.max()) if violations.size else 0.0,
            "rule": "P(>=25) = min(P(>=25), P(>=1)); P(>=100) = min(P(>=100), P(>=25))",
        },
        "focus_country_positive_months_h1": {"country_id": focus_country, "months": focus_detail},
        "training_rows": {
            "first_fit": records.fits[0] if records.fits else None,
            "last_fit": records.fits[-1] if records.fits else None,
        },
    }
    return _round(summary)


def headline_table(summary: dict[str, Any], *, horizons: Sequence[int] = (1, 12)) -> list[dict[str, Any]]:
    """Brier skill of the logistic baseline vs climatology by slice, threshold and horizon."""
    rows = []
    for slice_name, by_horizon in summary["slices"].items():
        for horizon in horizons:
            block = by_horizon.get(str(horizon))
            if block is None:
                continue
            for threshold, metrics in block["thresholds"].items():
                rows.append(
                    {
                        "slice": slice_name,
                        "horizon": horizon,
                        "threshold": int(threshold),
                        "n": metrics["n"],
                        "events": metrics["events"],
                        "bss_logit": metrics["brier_skill_vs_climatology"]["logit"],
                        "bss_persistence": metrics["brier_skill_vs_climatology"]["persistence"],
                        "bss_logit_95ci": metrics.get("logit_brier_skill_95ci_country_bootstrap"),
                        "brier_logit": metrics["models"]["logit"]["brier"],
                        "brier_climatology": metrics["models"]["climatology"]["brier"],
                        "auc_logit": metrics["models"]["logit"]["auc"],
                    }
                )
    return rows


def parse_origin(label: str | None, default: int) -> int:
    return default if label is None else parse_month(label)
