"""Real-time ("as-of") panels built only from UCDP files published by a given date.

A forecast for origin ``t`` (data through month ``t``) can at the earliest be made after
UCDP publishes the Candidate file for month ``t``, around the 20th of month ``t + 1``.
``asof_cutoff`` therefore uses the last day of month ``t + 1``: every GED or Candidate file
whose HTTP ``Last-Modified`` date falls on or before it counts as available. Rebuilding the
panel from those files reproduces, approximately, the data vintage a forecaster such as
ViEWS had for that origin. Outcomes are always scored on the latest data.
"""

from __future__ import annotations

import json
import os
from calendar import monthrange
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from forecastlab.conflict.backtest import BacktestRecords, _forecast_columns
from forecastlab.conflict.forecaster import PanelConfig, PanelForecaster
from forecastlab.conflict.ged import (
    CandidateStatusPolicy,
    EventRow,
    FinalAggregate,
    assemble_panel,
    read_candidate_release,
    read_final_release,
)
from forecastlab.conflict.months import format_month
from forecastlab.conflict.panel import Panel
from forecastlab.conflict.sources import (
    RawStore,
    ReleaseSelection,
    UcdpRelease,
    candidate_cumulative_release,
    candidate_monthly_release,
    check_allowed_url,
    ged_final_release,
    select_releases,
)


@dataclass(frozen=True)
class DatedRelease:
    release: UcdpRelease
    published: date


def asof_cutoff(origin: int) -> date:
    """Last day of the month after ``origin``."""
    year, index = divmod(origin + 1, 12)
    month = index + 1
    return date(year, month, monthrange(year, month)[1])


def historical_releases(first_year: int, last_year: int) -> list[UcdpRelease]:
    """Every GED final (``yy.1``) and Candidate file name UCDP uses for these years."""
    releases: list[UcdpRelease] = []
    for year in range(first_year, last_year + 1):
        yy = year - 2000
        releases.append(ged_final_release(yy, 1))
        releases.extend(candidate_cumulative_release(yy, month) for month in (3, 6, 9, 12))
        releases.extend(candidate_monthly_release(yy, month) for month in range(1, 13))
    return releases


def probe_release_dates(
    releases: Iterable[UcdpRelease], *, client: httpx.Client | None, cache_path: Path
) -> list[DatedRelease]:
    """Publication dates from HTTP Last-Modified headers (cached; missing files are skipped)."""
    cache: dict[str, str | None] = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    dated: list[DatedRelease] = []
    changed = False
    for release in releases:
        if release.url not in cache:
            if client is None:
                continue
            check_allowed_url(release.url)
            response = client.head(release.url)
            modified = response.headers.get("last-modified") if response.status_code == 200 else None
            cache[release.url] = parsedate_to_datetime(modified).date().isoformat() if modified else None
            changed = True
        published = cache.get(release.url)
        if published:
            dated.append(DatedRelease(release=release, published=date.fromisoformat(published)))
    if changed:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        partial = cache_path.with_name(cache_path.name + ".part")
        partial.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(partial, cache_path)
    return dated


def available_releases(catalog: Iterable[DatedRelease], cutoff: date) -> list[UcdpRelease]:
    return [item.release for item in catalog if item.published <= cutoff]


class VintageLibrary:
    """Builds as-of panels, downloading and parsing each UCDP file at most once."""

    def __init__(
        self,
        store: RawStore,
        catalog: Sequence[DatedRelease],
        *,
        client: httpx.Client | None,
        candidate_status: CandidateStatusPolicy = "all",
    ):
        self.store = store
        self.catalog = list(catalog)
        self.client = client
        self.candidate_status = candidate_status
        self._finals: dict[str, FinalAggregate] = {}
        self._candidates: dict[str, list[EventRow]] = {}

    def selection_at(self, cutoff: date) -> ReleaseSelection:
        return select_releases(available_releases(self.catalog, cutoff))

    def _path(self, release: UcdpRelease) -> Path:
        return self.store.path_for(self.store.ensure(release.url, client=self.client))

    def _final(self, release: UcdpRelease) -> FinalAggregate:
        if release.url not in self._finals:
            self._finals[release.url] = read_final_release(self._path(release), release)
        return self._finals[release.url]

    def _candidate(self, release: UcdpRelease) -> list[EventRow]:
        if release.url not in self._candidates:
            self._candidates[release.url] = read_candidate_release(self._path(release), release)
        return self._candidates[release.url]

    def panel_at(self, origin: int, *, countries: Mapping[int, tuple[str, str]] | None = None) -> Panel | None:
        """Panel from files published by ``asof_cutoff(origin)``; None if it does not reach ``origin``."""
        selection = self.selection_at(asof_cutoff(origin))
        if selection.last_month < origin:
            return None
        panel = assemble_panel(
            selection,
            self._final(selection.final),
            {release.url: self._candidate(release) for release in selection.candidates},
            records={release.url: record for release in selection.releases if (record := self.store.record(release.url))},
            candidate_status=self.candidate_status,
            countries=countries,
        )
        panel.metadata["asof_cutoff"] = asof_cutoff(origin).isoformat()
        return panel if panel.last_month == origin else panel.truncated(origin)


def run_vintage_backtest(
    latest: Panel,
    library: VintageLibrary,
    origins: Iterable[int],
    config: PanelConfig | None = None,
    *,
    progress: Callable[[str], None] | None = None,
) -> tuple[BacktestRecords, list[dict[str, Any]]]:
    """Forecast each origin from its as-of panel and score on the latest panel's outcomes."""
    config = config or PanelConfig()
    countries = {int(cid): (latest.names[i], latest.regions[i]) for i, cid in enumerate(latest.country_ids)}
    outcome_series = latest.series(config.series)
    chunks: list[dict[str, np.ndarray]] = []
    fits: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for origin in sorted(set(origins)):
        if origin + min(config.horizons) > latest.last_month:
            continue
        asof = library.panel_at(origin, countries=countries)
        if asof is None:
            skipped.append({"origin": format_month(origin), "reason": "as-of data do not reach the origin"})
            continue
        if not np.array_equal(asof.country_ids, latest.country_ids):
            raise AssertionError("as-of panel country list differs from the latest panel")
        forecaster = PanelForecaster(asof, config)
        index = forecaster.origin_index(origin)
        for horizon in config.horizons:
            target = origin + horizon
            if target > latest.last_month:
                continue
            forecast = forecaster.forecast(index, horizon)
            target_index = latest.month_index(target)
            observed = latest.valid[forecast.rows, target_index]
            outcome = outcome_series[forecast.rows, target_index].astype(np.float64)
            columns = _forecast_columns(forecast, outcome, forecaster)
            chunks.append({key: values[observed] for key, values in columns.items()})
            fits.append(
                {
                    "origin": format_month(origin),
                    "horizon": horizon,
                    "asof_cutoff": asof.metadata.get("asof_cutoff"),
                    "data_releases": [release["version"] for release in asof.metadata.get("releases", [])],
                    "n_train": forecast.model.n_train,
                }
            )
        if progress is not None:
            progress(f"as-of {format_month(origin)}: {', '.join(fits[-1]['data_releases']) if fits else ''}")
    if not chunks:
        raise ValueError("no as-of origin could be forecast")
    columns = {key: np.concatenate([chunk[key] for chunk in chunks]) for key in chunks[0]}
    return BacktestRecords(thresholds=config.thresholds, columns=columns, fits=fits), skipped
