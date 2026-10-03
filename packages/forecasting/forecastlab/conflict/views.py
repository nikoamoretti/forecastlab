"""Benchmark against ViEWS country-month forecasts from the public ViEWS API.

ViEWS ``fatalities00N`` runs forecast state-based (sb) fatalities only. Run
``fatalitiesNNN_YYYY_MM_tKK`` uses data through month YYYY-MM; its first forecast month is
the next month, so step ``s`` targets YYYY-MM + s, the same convention as this panel's
horizon. Each run publishes a point prediction of ``ln(sb + 1)`` and, for ViEWS, the
probability of at least 25 battle-related deaths in the country-month.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np

from forecastlab.conflict.backtest import BacktestRecords, _round
from forecastlab.conflict.countries import ged_country_for_views
from forecastlab.conflict.months import format_month, from_views_month_id, month_ordinal, views_month_id
from forecastlab.conflict.scoring import brier_score, cluster_bootstrap_skill, skill_score
from forecastlab.conflict.sources import RawStore

VIEWS_API = "https://api.viewsforecasting.org/"
_RUN_RE = re.compile(r"^fatalities(\d{3})_(\d{4})_(\d{2})_t(\d{2})$")
_PREDICTORS_RE = re.compile(r"^predictors_fatalities(\d{3})_")
VIEWS_THRESHOLD = 25


@dataclass(frozen=True)
class ViewsRun:
    name: str
    family: int
    origin: int
    attempt: int

    @property
    def ln_field(self) -> str:
        return "sc_cm_sb_main" if self.family == 1 else "main_mean_ln"

    @property
    def dich_field(self) -> str:
        return "sc_cm_sb_dich_main" if self.family == 1 else "main_dich"


def parse_run_name(name: str) -> ViewsRun | None:
    """Monthly ``fatalities`` runs; special runs with month ``00`` are ignored."""
    match = _RUN_RE.match(name)
    if not match:
        return None
    family, year, month, attempt = (int(part) for part in match.groups())
    if not 1 <= month <= 12:
        return None
    return ViewsRun(name=name, family=family, origin=month_ordinal(year, month), attempt=attempt)


def select_runs(names: Iterable[str]) -> tuple[list[ViewsRun], list[str]]:
    """One run per origin month: the newest model family, then the latest attempt."""
    pool = list(names)
    best: dict[int, ViewsRun] = {}
    for name in pool:
        run = parse_run_name(name)
        if run is None:
            continue
        current = best.get(run.origin)
        if current is None or (run.family, run.attempt) > (current.family, current.attempt):
            best[run.origin] = run
    selected = sorted(best.values(), key=lambda run: run.origin)
    chosen = {run.name for run in selected}
    skipped = sorted(name for name in pool if parse_run_name(name) is not None and name not in chosen)
    return selected, skipped


def predictors_dataset(names: Iterable[str]) -> str | None:
    """Newest ViEWS input dataset, which includes ViEWS's own country-month UCDP aggregation."""
    found = [(int(match.group(1)), name) for name in names if (match := _PREDICTORS_RE.match(name))]
    return max(found)[1] if found else None


def forecast_url(run: ViewsRun, field: str, target_months: Sequence[int]) -> str:
    months = "&".join(f"month={views_month_id(month)}" for month in sorted(target_months))
    return f"{VIEWS_API}{run.name}/cm/sb/{field}?pagesize=10000&{months}"


def _cache_name(url: str, prefix: str) -> str:
    return f"{prefix}__{hashlib.sha256(url.encode()).hexdigest()[:16]}.json"


def _read_rows(store: RawStore, url: str, *, client: httpx.Client | None, prefix: str) -> tuple[list[dict[str, Any]], bool]:
    """All ``data`` rows of a (possibly paginated) API response; also whether anything was downloaded."""
    rows: list[dict[str, Any]] = []
    downloaded = False
    page, pages = 1, 1
    while page <= pages:
        page_url = url if page == 1 else f"{url}&page={page}"
        downloaded = downloaded or store.cached(page_url) is None
        record = store.ensure(page_url, client=client, filename=_cache_name(page_url, prefix))
        payload = json.loads(store.path_for(record).read_text(encoding="utf-8"))
        rows.extend(payload.get("data") or [])
        pages = int(payload.get("page_count") or 1)
        page += 1
    return rows, downloaded


def fetch_run_forecasts(
    store: RawStore,
    run: ViewsRun,
    target_months: Sequence[int],
    *,
    client: httpx.Client | None,
    pause: float = 0.2,
) -> dict[tuple[int, int], dict[str, float]]:
    """``(GED country_id, target month) -> {"ln": ..., "p25": ...}`` for one run."""
    out: dict[tuple[int, int], dict[str, float]] = {}
    for key, field in (("ln", run.ln_field), ("p25", run.dich_field)):
        rows, downloaded = _read_rows(store, forecast_url(run, field, target_months), client=client, prefix=f"{run.name}__{field}")
        if downloaded and pause:
            time.sleep(pause)
        for row in rows:
            value = row.get(field)
            if value is None:
                continue
            cell = (ged_country_for_views(int(row["gwcode"])), from_views_month_id(int(row["month_id"])))
            out.setdefault(cell, {})[key] = float(value)
    return out


def fetch_views_outcomes(store: RawStore, dataset: str, *, client: httpx.Client | None) -> dict[tuple[int, int], float]:
    """ViEWS's own country-month sum of UCDP sb best estimates, keyed like the panel."""
    rows, _ = _read_rows(store, f"{VIEWS_API}{dataset}/cm?pagesize=100000", client=client, prefix=dataset)
    out: dict[tuple[int, int], float] = {}
    for row in rows:
        value = row.get("ucdp_ged_sb_best_sum")
        if value is None:
            continue
        out[(ged_country_for_views(int(row["gwcode"])), from_views_month_id(int(row["month_id"])))] = float(value)
    return out


def _pair_block(
    ours: np.ndarray, theirs: np.ndarray, outcome: np.ndarray, clusters: np.ndarray, *, bootstrap: int
) -> dict[str, Any]:
    """Mean squared error of both forecasts (MSE for log counts, Brier for probabilities)."""
    ours_terms, theirs_terms = (ours - outcome) ** 2, (theirs - outcome) ** 2
    interval = cluster_bootstrap_skill(ours_terms, theirs_terms, clusters, replicates=bootstrap) if bootstrap else None
    return {
        "panel_baseline": float(ours_terms.mean()),
        "views": float(theirs_terms.mean()),
        "relative_improvement_vs_views": skill_score(float(ours_terms.mean()), float(theirs_terms.mean())),
        "relative_improvement_95ci_country_bootstrap": list(interval) if interval else None,
    }


def vintage_effect(revised: BacktestRecords, realtime: BacktestRecords, *, thresholds: Sequence[int] = (1, 25)) -> dict[str, Any]:
    """The same baseline fed revised (latest) versus as-of data, on identical forecast rows."""

    def keys(records: BacktestRecords) -> list[tuple[int, int, int]]:
        cols = records.columns
        return list(zip(cols["origin"].tolist(), cols["horizon"].tolist(), cols["country_id"].tolist(), strict=True))

    position = {key: i for i, key in enumerate(keys(revised))}
    pairs = [(position[key], j) for j, key in enumerate(keys(realtime)) if key in position]
    if not pairs:
        return {"rows": 0}
    left = np.array([pair[0] for pair in pairs])
    right = np.array([pair[1] for pair in pairs])
    outcome = revised.columns["y"][left]
    if not np.array_equal(outcome, realtime.columns["y"][right]):
        raise AssertionError("paired rows must share outcomes")
    horizon = revised.columns["horizon"][left]
    out: dict[str, Any] = {}
    for label in ["all", *[str(h) for h in sorted(set(horizon.tolist()))]]:
        mask = np.ones(len(left), dtype=bool) if label == "all" else horizon == int(label)
        y_log = np.log1p(outcome[mask])
        block: dict[str, Any] = {"n": int(mask.sum())}
        for name, records, rows in (("revised_data", revised, left), ("asof_data", realtime, right)):
            selected = rows[mask]
            block[name] = {
                "mse_log1p": float(np.mean((records.columns["logit_mean_log1p"][selected] - y_log) ** 2)),
                **{
                    f"brier_ge{t}": brier_score(records.prob("logit", t)[selected], (outcome[mask] >= t).astype(np.float64))
                    for t in thresholds
                },
            }
        out[label] = block
    return _round(out)


def compare_with_views(
    records: BacktestRecords,
    views: dict[str, dict[tuple[int, int], dict[str, float]]],
    runs: Sequence[ViewsRun],
    *,
    views_outcomes: dict[tuple[int, int], float] | None = None,
    bootstrap: int = 1000,
) -> dict[str, Any]:
    """Score the sb-series baseline and ViEWS on the same country-months and horizons.

    ``records`` must come from a backtest on the ``sb`` series at the ViEWS origins.
    """
    run_by_origin = {run.origin: run for run in runs}
    columns = records.columns
    n = len(records)
    ln = np.full(n, np.nan)
    p25 = np.full(n, np.nan)
    family = np.zeros(n, dtype=np.int64)
    for i in range(n):
        run = run_by_origin.get(int(columns["origin"][i]))
        if run is None:
            continue
        cell = views.get(run.name, {}).get((int(columns["country_id"][i]), int(columns["target_month"][i])))
        if cell is None:
            continue
        ln[i] = cell.get("ln", np.nan)
        p25[i] = cell.get("p25", np.nan)
        family[i] = run.family
    matched = np.isfinite(ln) & np.isfinite(p25)
    outcome_sets: dict[str, np.ndarray] = {"ucdp_panel": columns["y"]}
    if views_outcomes is not None:
        alt = np.array(
            [views_outcomes.get((int(c), int(m)), np.nan) for c, m in zip(columns["country_id"], columns["target_month"], strict=True)]
        )
        outcome_sets["views_aggregation"] = alt
    results: dict[str, Any] = {}
    for outcome_name, outcome_counts in outcome_sets.items():
        per_horizon: dict[str, Any] = {}
        usable_all = matched & np.isfinite(outcome_counts)
        groups = [("all", usable_all)] + [
            (str(h), usable_all & (columns["horizon"] == h)) for h in sorted(set(columns["horizon"].tolist()))
        ]
        for label, mask in groups:
            if not mask.any():
                continue
            y_count = outcome_counts[mask]
            y_log = np.log1p(y_count)
            y_bin = (y_count >= VIEWS_THRESHOLD).astype(np.float64)
            clusters = columns["country_id"][mask]
            clim_log = columns["climatology_mean_log1p"][mask]
            block = {
                "n": int(mask.sum()),
                "countries": int(np.unique(clusters).size),
                "origins": [format_month(int(columns["origin"][mask].min())), format_month(int(columns["origin"][mask].max()))],
                "events_ge25": int(y_bin.sum()),
                "mse_log1p": _pair_block(columns["logit_mean_log1p"][mask], ln[mask], y_log, clusters, bootstrap=bootstrap),
                "brier_ge25": _pair_block(columns[f"p_logit_ge{VIEWS_THRESHOLD}"][mask], p25[mask], y_bin, clusters, bootstrap=bootstrap),
                "reference": {
                    "mse_log1p_climatology": float(np.mean((clim_log - y_log) ** 2)),
                    "mse_log1p_zero_forecast": float(np.mean(y_log**2)),
                    "mse_log1p_persistence": float(np.mean((columns["persistence_mean_log1p"][mask] - y_log) ** 2)),
                    "brier_ge25_climatology": brier_score(columns[f"p_climatology_ge{VIEWS_THRESHOLD}"][mask], y_bin),
                },
                "views_by_family": {
                    f"fatalities{fam:03d}": int(np.sum(mask & (family == fam))) for fam in sorted(set(family[mask].tolist()))
                },
            }
            per_horizon[label] = block
        results[outcome_name] = per_horizon
    origin_values = sorted({int(origin) for origin in columns["origin"][matched]})
    return _round(
        {
            "series": "sb (state-based fatalities, UCDP best estimate)",
            "threshold": VIEWS_THRESHOLD,
            "rows_backtest": n,
            "rows_matched": int(matched.sum()),
            "runs_used": [run.name for run in runs if run.origin in set(origin_values)],
            "results": results,
        }
    )
