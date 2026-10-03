"""Live panel forecasts from the latest data, and scoring them once outcomes are published.

The forecast artifact stores, for every country and horizon, the threshold probabilities
and the parameters of the count distribution, so ``score_live_forecasts`` can score the
same forecasts with the backtest's scoring functions when a newer UCDP Candidate file
covers the target month.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import platform
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from forecastlab.conflict.backtest import _round
from forecastlab.conflict.forecaster import COUNT_QUANTILE_LEVELS, MODEL_VERSION, PanelConfig, PanelForecaster
from forecastlab.conflict.models import log1p_bin_edges, weighted_mean, weighted_quantiles
from forecastlab.conflict.months import format_month, parse_month
from forecastlab.conflict.panel import FRANCE_COUNTRY_ID, Panel
from forecastlab.conflict.scoring import brier_score, crps_weighted_atoms, log_loss, skill_score
from forecastlab.version import __version__

PACKAGE_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = PACKAGE_DIR.parents[3]
ARTIFACT_KIND = "conflict_panel_live_forecast"
CSV_FIELDS = (
    "country_id", "iso3", "country", "region", "origin", "horizon", "target_month",
    "p_ge1", "p_ge25", "p_ge100", "climatology_p_ge1", "climatology_p_ge25", "climatology_p_ge100",
    "fatalities_q05", "fatalities_q25", "fatalities_q50", "fatalities_q75", "fatalities_q95",
    "mean_log1p", "fatalities_origin_month", "fatalities_last_12_months",
)


def _git(args: list[str], root: Path) -> str | None:
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def code_identity(root: Path | None = None) -> dict[str, Any]:
    """Git commit, dirty flag and a hash of this package's source, so uncommitted code is identifiable."""
    directory = root or REPOSITORY_ROOT
    digest = hashlib.sha256()
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    status = _git(["status", "--porcelain"], directory)
    return {
        "model_version": MODEL_VERSION,
        "application_version": __version__,
        "git_commit": _git(["rev-parse", "HEAD"], directory),
        "working_tree_dirty": bool(status) if status is not None else None,
        "conflict_package_sha256": digest.hexdigest(),
        "numpy_version": np.__version__,
        "python_version": platform.python_version(),
    }


def data_vintage(panel: Panel) -> dict[str, Any]:
    meta = panel.metadata
    return {
        "last_month": format_month(panel.last_month),
        "built_at": meta.get("built_at"),
        "candidate_status_policy": meta.get("candidate_status_policy"),
        "releases": meta.get("releases", []),
    }


def make_live_forecast(
    panel: Panel,
    config: PanelConfig | None = None,
    *,
    origin: int | None = None,
    top_n: int = 10,
    focus_country: int = FRANCE_COUNTRY_ID,
    root: Path | None = None,
) -> dict[str, Any]:
    """Forecasts for every panel country and horizon from data through ``origin``."""
    config = config or PanelConfig()
    origin = panel.last_month if origin is None else origin
    forecaster = PanelForecaster(panel, config)
    index = forecaster.origin_index(origin)
    series = forecaster.series
    rows: list[dict[str, Any]] = []
    models: dict[str, Any] = {}
    next_month: dict[int, dict[str, float]] = {}
    for horizon in config.horizons:
        forecast = forecaster.forecast(index, horizon)
        model = forecast.model
        quantiles = weighted_quantiles(forecast.model_atoms, forecast.model_weights, COUNT_QUANTILE_LEVELS)
        mean_log1p = weighted_mean(forecast.model_atoms, forecast.model_weights)
        locations = model.standardizer.design(forecaster.cube[forecast.rows, index, :]) @ model.bin_coefs.T
        clim_mean = forecast.climatology_atoms.mean(axis=1)
        clim_median = np.quantile(forecast.climatology_atoms, 0.5, axis=1, method="inverted_cdf")
        models[str(horizon)] = {
            "n_train": model.n_train,
            "train_positives": list(model.train_positives),
            "training_outcomes_through": format_month(origin),
            "bin_edges_log1p": [[low, None if not np.isfinite(high) else high] for low, high in model.bin_edges],
            "bin_residual_quantiles": model.bin_residual_quantiles.tolist(),
        }
        for j, c in enumerate(forecast.rows):
            row = {
                "country_id": int(panel.country_ids[c]),
                "iso3": panel.iso3[c],
                "country": panel.names[c],
                "region": panel.regions[c],
                "origin": format_month(origin),
                "horizon": horizon,
                "target_month": format_month(origin + horizon),
                **{f"p_ge{t}": float(forecast.logit[j, k]) for k, t in enumerate(config.thresholds)},
                **{f"climatology_p_ge{t}": float(forecast.climatology[j, k]) for k, t in enumerate(config.thresholds)},
                **{
                    f"fatalities_q{int(round(level * 100)):02d}": float(np.expm1(quantiles[j, q]))
                    for q, level in enumerate(COUNT_QUANTILE_LEVELS)
                },
                "mean_log1p": float(mean_log1p[j]),
                "bin_location_log1p": [float(value) for value in locations[j]],
                "climatology_mean_log1p": float(clim_mean[j]),
                "climatology_median_log1p": float(clim_median[j]),
                "fatalities_origin_month": int(series[c, index]),
                "fatalities_last_12_months": int(series[c, max(0, index - 11) : index + 1].sum()),
            }
            rows.append(row)
            if horizon == config.horizons[0]:
                next_month[int(panel.country_ids[c])] = row

    previous: dict[int, dict[str, float]] = {}
    if index >= 1:
        prior = forecaster.forecast(index - 1, config.horizons[0])
        for j, c in enumerate(prior.rows):
            previous[int(panel.country_ids[c])] = {
                f"p_ge{t}": float(prior.logit[j, k]) for k, t in enumerate(config.thresholds)
            }
    risers = []
    for country_id, row in next_month.items():
        before = previous.get(country_id)
        if before is None:
            continue
        risers.append(
            {
                "country_id": country_id,
                "country": row["country"],
                "iso3": row["iso3"],
                "p_ge25": row["p_ge25"],
                "p_ge25_previous_origin": before["p_ge25"],
                "change_p_ge25": row["p_ge25"] - before["p_ge25"],
                "p_ge1": row["p_ge1"],
                "change_p_ge1": row["p_ge1"] - before["p_ge1"],
                "fatalities_origin_month": row["fatalities_origin_month"],
            }
        )
    risers.sort(key=lambda item: (-item["change_p_ge25"], -item["change_p_ge1"], item["country_id"]))
    top_level = sorted(next_month.values(), key=lambda item: (-item["p_ge25"], item["country_id"]))[:top_n]
    payload = {
        "kind": ARTIFACT_KIND,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "origin": format_month(origin),
        "horizons": {str(h): format_month(origin + h) for h in config.horizons},
        "conventions": {
            "origin": "last month of data used; features use months <= origin",
            "horizon": "h targets the fatalities in the single calendar month origin + h",
            "series": f"{config.series}: UCDP best-estimate fatalities, all three violence types summed"
            if config.series == "total"
            else config.series,
            "probabilities": "P(fatalities >= k) for k in 1, 25, 100; monotone across k",
            "risers": "change in next-month P(>=25) versus the next-month forecast from the previous origin, "
            "recomputed from the current data truncated at that origin",
        },
        "data_vintage": data_vintage(panel),
        "code": code_identity(root),
        "config": config.as_dict(),
        "models": models,
        "top_risers": [_round(item) for item in risers[:top_n]],
        "top_next_month_p_ge25": [
            _round({key: item[key] for key in ("country_id", "country", "iso3", "p_ge1", "p_ge25", "p_ge100", "fatalities_origin_month")})
            for item in top_level
        ],
        "focus_country": [_round(row) for row in rows if row["country_id"] == focus_country],
        "forecasts": [_round(row) for row in rows],
    }
    return payload


def write_live_artifacts(payload: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    """Write ``live_forecast_<origin>.json`` and a flat CSV of the same forecasts."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"live_forecast_{payload['origin']}"
    json_path = directory / f"{stem}.json"
    csv_path = directory / f"{stem}.csv"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in payload["forecasts"]:
        writer.writerow({key: ("" if row.get(key) is None else row.get(key)) for key in CSV_FIELDS})
    for path, text in ((csv_path, buffer.getvalue()), (json_path, _dump_one_row_per_line(payload))):
        partial = path.with_name(path.name + ".part")
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, path)
    return json_path, csv_path


def _dump_one_row_per_line(payload: Mapping[str, Any]) -> str:
    """JSON with nested metadata indented and each list item of row lists on one line."""
    lines = ["{"]
    items = list(payload.items())
    for position, (key, value) in enumerate(items):
        comma = "," if position < len(items) - 1 else ""
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            rows = ",\n".join("  " + json.dumps(item, separators=(", ", ": ")) for item in value)
            lines.append(f"  {json.dumps(key)}: [\n{rows}\n  ]{comma}")
        else:
            nested = json.dumps(value, indent=1).replace("\n", "\n  ")
            lines.append(f"  {json.dumps(key)}: {nested}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def load_live_forecast(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("kind") != ARTIFACT_KIND:
        raise ValueError(f"{path} is not a live forecast artifact")
    return payload


def _model_atoms(rows: list[dict[str, Any]], model_info: Mapping[str, Any], thresholds: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Rebuild the stored mixture distribution of log1p(fatalities) for each row."""
    residuals = np.array(model_info["bin_residual_quantiles"], dtype=np.float64)
    edges = [(low, np.inf if high is None else high) for low, high in model_info["bin_edges_log1p"]]
    if len(edges) != len(thresholds):
        edges = log1p_bin_edges(tuple(thresholds))
    probabilities = np.array([[row[f"p_ge{t}"] for t in thresholds] for row in rows], dtype=np.float64)
    locations = np.array([row["bin_location_log1p"] for row in rows], dtype=np.float64)
    n_rows, n_atoms = len(rows), residuals.shape[1]
    upper = np.column_stack([probabilities[:, 1:], np.zeros(n_rows)])
    mass = np.clip(probabilities - upper, 0.0, 1.0)
    atoms = [np.zeros((n_rows, 1))]
    weights = [np.clip(1.0 - probabilities[:, :1], 0.0, 1.0)]
    for b, (low, high) in enumerate(edges):
        atoms.append(np.clip(locations[:, b : b + 1] + residuals[b][None, :], low, high))
        weights.append(np.repeat(mass[:, b : b + 1] / n_atoms, n_atoms, axis=1))
    return np.concatenate(atoms, axis=1), np.concatenate(weights, axis=1)


def score_live_forecasts(payload: Mapping[str, Any], panel: Panel) -> dict[str, Any]:
    """Score every stored forecast whose target month the (newer) panel now covers.

    Uses the same scoring functions as the backtest. Target months after the panel's last
    month are reported as pending. Candidate data are preliminary, so scores computed from
    them can change when UCDP publishes the final GED release for that year.
    """
    thresholds = [int(t) for t in payload["config"]["thresholds"]]
    series_name = payload["config"].get("series", "total")
    series = panel.series(series_name)
    position = {int(cid): i for i, cid in enumerate(panel.country_ids)}
    scored: dict[str, Any] = {}
    pending: list[str] = []
    for horizon_label, target_label in payload["horizons"].items():
        target = parse_month(target_label)
        if target > panel.last_month:
            pending.append(target_label)
            continue
        rows = [row for row in payload["forecasts"] if str(row["horizon"]) == horizon_label]
        usable = [row for row in rows if row["country_id"] in position and panel.valid[position[row["country_id"]], target - panel.first_month]]
        if not usable:
            continue
        outcome = np.array([series[position[row["country_id"]], target - panel.first_month] for row in usable], dtype=np.float64)
        origin_value = np.array([row["fatalities_origin_month"] for row in usable], dtype=np.float64)
        log_outcome = np.log1p(outcome)
        block: dict[str, Any] = {"target_month": target_label, "n": len(usable), "thresholds": {}}
        for threshold in thresholds:
            hit = (outcome >= threshold).astype(np.float64)
            forecasts = {
                "logit": np.array([row[f"p_ge{threshold}"] for row in usable]),
                "climatology": np.array([row[f"climatology_p_ge{threshold}"] for row in usable]),
                "persistence": (origin_value >= threshold).astype(np.float64),
            }
            briers = {name: brier_score(values, hit) for name, values in forecasts.items()}
            block["thresholds"][str(threshold)] = {
                "events": int(hit.sum()),
                "brier": briers,
                "log_loss": {name: log_loss(values, hit) for name, values in forecasts.items()},
                "brier_skill_vs_climatology": {
                    name: skill_score(briers[name], briers["climatology"]) for name in ("logit", "persistence")
                },
            }
        atoms, weights = _model_atoms(usable, payload["models"][horizon_label], thresholds)
        median = weighted_quantiles(atoms, weights, (0.5,))[:, 0]
        block["count_log1p"] = {
            "logit": {
                "mae_median": float(np.mean(np.abs(median - log_outcome))),
                "mse_mean": float(np.mean((np.array([row["mean_log1p"] for row in usable]) - log_outcome) ** 2)),
                "crps": float(np.mean(crps_weighted_atoms(atoms, weights, log_outcome))),
            },
            "climatology": {
                "mae_median": float(np.mean(np.abs(np.array([row["climatology_median_log1p"] for row in usable]) - log_outcome))),
                "mse_mean": float(np.mean((np.array([row["climatology_mean_log1p"] for row in usable]) - log_outcome) ** 2)),
            },
            "persistence": {
                "mae_median": float(np.mean(np.abs(np.log1p(origin_value) - log_outcome))),
                "mse_mean": float(np.mean((np.log1p(origin_value) - log_outcome) ** 2)),
                "crps": float(np.mean(np.abs(np.log1p(origin_value) - log_outcome))),
            },
        }
        scored[horizon_label] = block
    return _round(
        {
            "kind": "conflict_panel_live_score",
            "forecast_origin": payload["origin"],
            "scored_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "scoring_data_vintage": data_vintage(panel),
            "scored_horizons": scored,
            "pending_target_months": pending,
            "note": "Outcomes from UCDP Candidate files are preliminary and can change in the final GED release.",
        }
    )
