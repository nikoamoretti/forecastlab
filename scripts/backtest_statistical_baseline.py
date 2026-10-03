#!/usr/bin/env python3
"""Point-in-time historical backtest of the statistical baseline (``statistical_baseline_backtest_v1``).

    uv run python scripts/backtest_statistical_baseline.py            # download what is not cached, write artifacts
    uv run python scripts/backtest_statistical_baseline.py --offline  # use cached responses only

It backtests the deployed rule (``forecastlab.statistical_baseline.baseline_forecast``), not the
language-model methods, and writes ``artifacts/statistical_baseline_backtest_v1/results.json`` and
``summary.md``. Raw responses are cached under the gitignored ``data/local/``; their SHA-256 values are
recorded in ``results.json``.

Question rule (fixed before any result was computed; not tuned). For every target month from 2016-01 to
the latest first release, three questions ask whether the first-release value is greater than the last
published value minus one step, the last published value, and the last published value plus one step.
Steps: 0.1 point (unemployment rate, CPI year-over-year) and 50,000 jobs (payroll change).

Point in time. The information set is the ALFRED vintage dated the day before the target's first release,
cut to what the live BLS v1 adapter returns (January of the vintage year minus nine onward) and
transformed by the adapter's own code. The outcome is the target in the first ALFRED vintage that
contains it: UNRATE as published, the PAYEMS level minus the same vintage's prior month (jobs), and the
CPIAUCNS 12-month change rounded half-up to 0.1. These are ALFRED reconstructions of the BLS headline
figures, not parsed BLS news releases. First-release vintages come from ALFRED's published vintage-date
list for the series and are verified against the data (present in the release vintage, absent the day
before).

Approximations (labeled, optional). Weekly initial claims (ICSA) and the daily 10-year yield (DGS10) use
the current FRED vintage for both history and outcomes, one period ahead, with steps of 10,000 claims and
0.05 point. ICSA is revised after its advance release and has annual seasonal-factor revisions, so these
results are not point in time.

Politeness: one request at a time, honoring robots.txt crawl delays (ALFRED 2 s, FRED 1 s), retrying
transient failures with exponential backoff. ALFRED returns at most 12 vintage lines per graph request.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "packages" / "forecasting", ROOT / "apps" / "api"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from forecastlab.aggregation import MAX_AGG_P, MIN_AGG_P, clip_aggregated_probability  # noqa: E402
from forecastlab.evaluation import brier_score, log_loss  # noqa: E402
from forecastlab.gitinfo import current_git_commit  # noqa: E402
from forecastlab.macro import SERIES, MacroObservation, MacroSpec, normalize_observations, parse_fred_csv  # noqa: E402
from forecastlab.official_releases import PUBLIC_DATA_USER_AGENT  # noqa: E402
from forecastlab.statistical_baseline import (  # noqa: E402
    METHOD,
    PROBABILITY_RULE,
    PUBLICATION_PRECISION,
    QUANTILE_LEVELS,
    RULE_VERSION,
    WINDOW_PERIODS,
    BaselineUnavailable,
    baseline_forecast,
)

BACKTEST_VERSION = "statistical_baseline_backtest_v1"
START_MONTH = "2016-01"
START_DAY = date(2016, 1, 1)
CACHE_DIR = ROOT / "data" / "local" / BACKTEST_VERSION
OUT_DIR = ROOT / "artifacts" / BACKTEST_VERSION
ALFRED_CSV = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
ALFRED_VINTAGE_PAGE = "https://alfred.stlouisfed.org/series/downloaddata?seid={series}"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
LINES_PER_REQUEST = 12  # ALFRED silently drops graph lines beyond the twelfth.
CRAWL_DELAY_SECONDS = {"alfred.stlouisfed.org": 2.0, "fred.stlouisfed.org": 1.0}  # robots.txt Crawl-delay
MAX_ATTEMPTS = 6
FIRST_RELEASE_SEARCH_MONTHS = 6
BLS_V1_YEARS = 10  # The live adapter asks BLS v1 for startyear = now.year - 9 through now.year.
MONTHLY = ("unemployment", "payrolls", "cpi")
APPROXIMATE = ("jobless_claims", "treasury_10y")
STEPS = {"unemployment": Decimal("0.1"), "cpi": Decimal("0.1"), "payrolls": Decimal("50000"),
         "jobless_claims": Decimal("10000"), "treasury_10y": Decimal("0.05")}
POSITIONS = ("below", "at", "above")  # threshold = last - step, last, last + step
APPROX_HISTORY_ROWS = {"weekly": 160, "daily": 560}  # rows handed to the rule; it applies its own window
VINTAGE_SELECT = re.compile(r'<select[^>]*id="form_selected_vintage_dates"[^>]*>(.*?)</select>', re.S)
VINTAGE_OPTION = re.compile(r'<option value="(\d{4}-\d{2}-\d{2})"')
NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
QUESTION_COLUMNS = ["series", "target", "position", "threshold", "probability", "raw_probability", "k", "n", "h",
                    "persistence_probability", "outcome"]
TARGET_COLUMNS = ["series", "target", "first_release_vintage", "information_vintage", "last_period", "last_value",
                  "h", "n", "window_start", "window_observations", "point_forecast", "mode",
                  *[f"q{level}" for level in QUANTILE_LEVELS], "first_release_value"]


SOURCE_FILES = ("scripts/backtest_statistical_baseline.py", "packages/forecasting/forecastlab/statistical_baseline.py",
                "packages/forecasting/forecastlab/macro.py")


class BacktestError(RuntimeError):
    pass


def source_identity() -> dict[str, Any]:
    """The base commit plus the SHA-256 of the code that produced the artifact (it may be uncommitted)."""
    try:
        status = subprocess.run(["git", "status", "--porcelain", "--", *SOURCE_FILES], cwd=ROOT, check=False,
                                capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        status = "unknown"
    return {"base_commit": current_git_commit(), "source_files_uncommitted": bool(status),
            "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}}


# --- Periods -----------------------------------------------------------------------------------------

def month_index(period: str) -> int:
    year, month = map(int, period[:7].split("-"))
    return year * 12 + month - 1


def month_period(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def shift_month(period: str, months: int) -> str:
    return month_period(month_index(period) + months)


def month_start(period: str) -> date:
    return date.fromisoformat(period[:7] + "-01")


def month_end(period: str) -> date:
    return month_start(shift_month(period, 1)) - timedelta(days=1)


# --- Polite, cached HTTP -------------------------------------------------------------------------------

class Fetcher:
    """One request at a time with per-host crawl delays, retries, and a content-addressed local cache."""

    def __init__(self, cache_dir: Path, *, offline: bool, sleep: Callable[[float], None] = time.sleep) -> None:
        self.cache_dir = cache_dir
        self.offline = offline
        self.sleep = sleep
        self.requests: list[dict[str, Any]] = []
        self.downloads = 0
        self._last: dict[str, float] = {}
        self._client = httpx.Client(timeout=60, follow_redirects=False,
                                    headers={"User-Agent": PUBLIC_DATA_USER_AGENT})

    def close(self) -> None:
        self._client.close()

    def get(self, url: str, *, kind: str) -> bytes:
        key = hashlib.sha256(url.encode()).hexdigest()[:32]
        body_path, meta_path = self.cache_dir / f"{key}.body", self.cache_dir / f"{key}.json"
        if body_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            body = body_path.read_bytes()
            if meta.get("url") != url or hashlib.sha256(body).hexdigest() != meta.get("sha256"):
                raise BacktestError(f"cache_entry_corrupt:{body_path.name}")
            cached = True
        else:
            if self.offline:
                raise BacktestError(f"offline_cache_miss:{url}")
            body, content_type = self._download(url)
            expected = ("text/html",) if kind == "html" else ("application/csv", "text/csv", "text/plain")
            if not content_type.lower().startswith(expected):
                raise BacktestError(f"unexpected_content_type:{content_type}:{url}")
            meta = {"url": url, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                    "content_type": content_type, "retrieved_at": datetime.now(UTC).isoformat()}
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            body_path.write_bytes(body)
            meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
            cached = False
        cache_file = body_path.relative_to(ROOT) if body_path.is_relative_to(ROOT) else body_path
        self.requests.append({"url": url, "sha256": meta["sha256"], "bytes": meta["bytes"],
                              "retrieved_at": meta["retrieved_at"], "content_type": meta["content_type"],
                              "cache_file": str(cache_file), "served_from_cache": cached})
        return body

    def _download(self, url: str) -> tuple[bytes, str]:
        host = urlsplit(url).hostname or ""
        delay = CRAWL_DELAY_SECONDS.get(host, 2.0)
        failure = "unknown"
        for attempt in range(MAX_ATTEMPTS):
            wait = delay - (time.monotonic() - self._last.get(host, float("-inf")))
            if wait > 0:
                self.sleep(wait)
            self._last[host] = time.monotonic()
            self.downloads += 1
            try:
                response = self._client.get(url)
            except httpx.TransportError as exc:
                failure = type(exc).__name__
            else:
                if response.status_code == 200 and response.content:
                    return response.content, response.headers.get("content-type", "")
                if response.status_code not in {200, 429, 500, 502, 503, 504}:
                    raise BacktestError(f"http_{response.status_code}:{url}")
                failure = f"http_{response.status_code}"
            self.sleep(min(60.0, 2.0 ** (attempt + 1)))
        raise BacktestError(f"download_failed:{failure}:{url}")


# --- ALFRED ----------------------------------------------------------------------------------------------

def vintage_dates(fetcher: Fetcher, series: str) -> list[date]:
    """ALFRED's published list of vintage dates for a series (the download form's date list)."""
    html = fetcher.get(ALFRED_VINTAGE_PAGE.format(series=series), kind="html").decode("utf-8", "replace")
    select = VINTAGE_SELECT.search(html)
    if select is None:
        raise BacktestError(f"alfred_vintage_list_missing:{series}")
    dates = [date.fromisoformat(value) for value in VINTAGE_OPTION.findall(select.group(1))]
    if not dates or any(later <= earlier for earlier, later in zip(dates, dates[1:], strict=False)):
        raise BacktestError(f"alfred_vintage_list_malformed:{series}")
    return dates


def vintage_url(series: str, batch: Sequence[tuple[date, date, date]]) -> str:
    def joined(values: Sequence[date]) -> str:
        return ",".join(value.isoformat() for value in values)
    return (f"{ALFRED_CSV}?id={','.join([series] * len(batch))}&cosd={joined([line[1] for line in batch])}"
            f"&coed={joined([line[2] for line in batch])}&vintage_date={joined([line[0] for line in batch])}")


def fetch_vintages(fetcher: Fetcher, series: str, lines: Sequence[tuple[date, date, date]]
                   ) -> tuple[dict[date, dict[str, str]], dict[date, str]]:
    """Fetch ``(vintage_date, start, end)`` lines, 12 per request.

    Returns the non-blank raw values per vintage (keyed by observation date) and each vintage's URL. Every
    range spans at least two observations: ALFRED answers an empty range with the full history. A
    clamped (future) vintage date changes the column header, which fails here.
    """
    if len({line[0] for line in lines}) != len(lines):
        raise BacktestError("duplicate_vintage_lines")
    values: dict[date, dict[str, str]] = {}
    urls: dict[date, str] = {}
    ordered = sorted(lines)
    for offset in range(0, len(ordered), LINES_PER_REQUEST):
        batch = ordered[offset:offset + LINES_PER_REQUEST]
        url = vintage_url(series, batch)
        text = fetcher.get(url, kind="csv").decode("utf-8")
        rows = [row for row in csv.reader(io.StringIO(text.lstrip("﻿"))) if row]
        header = [cell.strip() for cell in rows[0]] if rows else []
        expected = {f"{series}_{line[0].strftime('%Y%m%d')}": line[0] for line in batch}
        if not header or header[0] not in {"observation_date", "DATE"} or sorted(header[1:]) != sorted(expected):
            raise BacktestError(f"alfred_header_mismatch:{url}")
        columns = [expected[name] for name in header[1:]]
        for vintage in columns:
            values[vintage] = {}
            urls[vintage] = url
        for row in rows[1:]:
            if len(row) != len(header):
                raise BacktestError(f"alfred_csv_malformed:{url}")
            day = row[0].strip()
            date.fromisoformat(day)
            for vintage, cell in zip(columns, row[1:], strict=True):
                cell = cell.strip()
                if cell in {"", "."}:
                    continue
                if not NUMBER.match(cell):
                    raise BacktestError(f"alfred_value_malformed:{url}")
                values[vintage][day] = cell
    return values, urls


def monthly_values(raw: dict[str, str]) -> dict[str, str]:
    return {day[:7]: value for day, value in raw.items()}


# --- Questions and scoring --------------------------------------------------------------------------------

def thresholds(indicator: str, last: Decimal) -> list[tuple[str, Decimal]]:
    step = STEPS[indicator]
    return [("below", last - step), ("at", last), ("above", last + step)]


def persistence(last: Decimal, threshold: Decimal) -> float:
    """1 if the last value already satisfies ``> threshold``, else 0, clipped to the method bounds."""
    return clip_aggregated_probability(1.0 if last > threshold else 0.0)


def ask(indicator: str, target: str, observations: Sequence[MacroObservation], release_at: datetime,
        last: Decimal, outcome_value: Decimal) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None]:
    """Three questions for one target; returns question rows, the value forecast, or a withholding reason."""
    rows: list[dict[str, Any]] = []
    forecast: dict[str, Any] | None = None
    for position, threshold in thresholds(indicator, last):
        spec = MacroSpec(indicator=indicator, observation_period=target, threshold=float(threshold),
                         comparison="gt", release_at=release_at)
        try:
            result = baseline_forecast(spec, observations)
        except BaselineUnavailable as exc:
            return [], None, str(exc)
        forecast = forecast or result
        rows.append({"series": indicator, "target": target, "position": position, "threshold": float(threshold),
                     "probability": result["probability"], "raw_probability": result["raw_probability"],
                     "k": result["k"], "n": result["n"], "h": result["horizon"]["h"],
                     "persistence_probability": persistence(last, threshold),
                     "outcome": int(outcome_value > threshold)})
    return rows, forecast, None


def target_row(indicator: str, target: str, forecast: dict[str, Any], outcome: Decimal, *, first: str | None,
               info: str | None) -> dict[str, Any]:
    return {"series": indicator, "target": target, "first_release_vintage": first, "information_vintage": info,
            "last_period": forecast["last_observation"]["period"], "last_value": forecast["last_observation"]["value"],
            "h": forecast["horizon"]["h"], "n": forecast["n"], "window_start": forecast["window"]["start"],
            "window_observations": forecast["window"]["observations"], "point_forecast": forecast["point_forecast"],
            "mode": forecast["mode"], **{f"q{level}": forecast["quantiles"][str(level)] for level in QUANTILE_LEVELS},
            "first_release_value": int(outcome) if PUBLICATION_PRECISION[indicator] >= 1 else float(outcome)}


def reliability(pairs: Sequence[tuple[float, int]]) -> tuple[list[dict[str, Any]], float | None]:
    """Ten equal-width probability bins and the count-weighted mean |forecast - observed| (ECE)."""
    bins: list[list[tuple[float, int]]] = [[] for _ in range(10)]
    for probability, outcome in pairs:
        bins[min(9, int(Decimal(repr(probability)) * 10))].append((probability, outcome))
    table = []
    ece = 0.0
    for index, members in enumerate(bins):
        mean_forecast = sum(p for p, _ in members) / len(members) if members else None
        observed = sum(y for _, y in members) / len(members) if members else None
        if members and mean_forecast is not None and observed is not None:
            ece += len(members) / len(pairs) * abs(mean_forecast - observed)
        table.append({"bin": f"{index / 10:.1f}-{(index + 1) / 10:.1f}", "count": len(members),
                      "mean_forecast": mean_forecast, "observed_frequency": observed})
    return table, (ece if pairs else None)


def score(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"questions": 0}

    def mean(values: Sequence[float]) -> float:
        return sum(values) / len(values)
    briers = {name: mean([brier_score(row[key], row["outcome"]) for row in rows])
              for name, key in (("baseline", "probability"), ("persistence", "persistence_probability"))}
    briers["uninformed_50"] = mean([brier_score(0.5, row["outcome"]) for row in rows])
    losses = {name: mean([log_loss(row[key], row["outcome"]) for row in rows])
              for name, key in (("baseline", "probability"), ("persistence", "persistence_probability"))}
    losses["uninformed_50"] = mean([log_loss(0.5, row["outcome"]) for row in rows])
    table, ece = reliability([(row["probability"], row["outcome"]) for row in rows])
    return {"questions": n, "outcome_rate": mean([row["outcome"] for row in rows]),
            "mean_forecast": mean([row["probability"] for row in rows]),
            "brier": {key: briers[key] for key in ("baseline", "uninformed_50", "persistence")},
            "log_loss": {key: losses[key] for key in ("baseline", "uninformed_50", "persistence")},
            "brier_skill_vs_uninformed": 1 - briers["baseline"] / briers["uninformed_50"],
            "brier_skill_vs_persistence": 1 - briers["baseline"] / briers["persistence"],
            "expected_calibration_error": ece, "reliability": table}


def value_scores(targets: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not targets:
        return {"targets": 0}

    def share(test: Callable[[dict[str, Any]], bool]) -> float:
        return sum(1 for row in targets if test(row)) / len(targets)
    return {"targets": len(targets),
            "interval_80_coverage": share(lambda row: row["q10"] <= row["first_release_value"] <= row["q90"]),
            "interval_90_coverage": share(lambda row: row["q5"] <= row["first_release_value"] <= row["q95"]),
            "mode_hit_rate": share(lambda row: row["mode"] == row["first_release_value"]),
            "median_hit_rate": share(lambda row: row["q50"] == row["first_release_value"])}


def by_position(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    output = {}
    for position in POSITIONS:
        subset = [row for row in rows if row["position"] == position]
        summary = score(subset)
        output[position] = {key: summary[key] for key in ("questions", "outcome_rate", "mean_forecast", "brier")
                            if key in summary}
    return output


# --- Point-in-time monthly series ----------------------------------------------------------------------------

def monthly_backtest(fetcher: Fetcher, indicator: str, today: date) -> dict[str, Any]:
    meta = SERIES[indicator]
    series = meta["fred"]
    vintages = [v for v in vintage_dates(fetcher, series) if month_end(START_MONTH) < v <= today]
    # Pass 1: every vintage after the first target month, wide enough for T-12 (CPI) and delayed releases.
    release_lines = [(v, month_start(shift_month(v.isoformat(), -15)), month_start(v.isoformat())) for v in vintages]
    releases, release_urls = fetch_vintages(fetcher, series, release_lines)
    found: list[tuple[str, date]] = []
    skipped: list[dict[str, str]] = []
    target = START_MONTH
    while month_end(target) < today:
        horizon_end = month_end(shift_month(target, FIRST_RELEASE_SEARCH_MONTHS))
        candidates = [v for v in vintages if month_end(target) < v <= horizon_end]
        first = next((v for v in candidates if target in monthly_values(releases[v])), None)
        if first is not None:
            found.append((target, first))
        else:
            skipped.append({"target": target, "reason": "never_published_within_six_months"
                            if horizon_end < today else "not_yet_published"})
        target = shift_month(target, 1)
    # Pass 2: the information set, the vintage dated the day before each first release.
    latest_for_day: dict[date, str] = {}
    for period, first in found:
        day = first - timedelta(days=1)
        latest_for_day[day] = max(latest_for_day.get(day, period), period)
    info_lines = [(day, date(day.year - (BLS_V1_YEARS - 1), 1, 1), month_start(period))
                  for day, period in sorted(latest_for_day.items())]
    information, information_urls = fetch_vintages(fetcher, series, info_lines)
    questions: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    rounding_mismatches = 0
    for period, first in found:
        day = first - timedelta(days=1)
        known = monthly_values(information[day])
        if period in known or any(key > period for key in known):
            skipped.append({"target": period, "reason": "information_vintage_contains_target"})
            continue
        released = monthly_values(releases[first])
        level = Decimal(released[period])
        if meta["transform"] == "level":
            outcome = level
        elif meta["transform"] == "change_thousands":
            prior = released.get(shift_month(period, -1))
            if prior is None:
                skipped.append({"target": period, "reason": "prior_month_missing_in_release_vintage"})
                continue
            outcome = (level - Decimal(prior)) * 1000
        else:
            base = released.get(shift_month(period, -12))
            if base is None:
                skipped.append({"target": period, "reason": "year_ago_index_missing_in_release_vintage"})
                continue
            outcome = ((level / Decimal(base) - 1) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        available = datetime.combine(day, clock_time.max, tzinfo=UTC)
        observations = normalize_observations(indicator, {key: float(value) for key, value in known.items()},
            available_at=available, vintage=day.isoformat(), source_url=information_urls[day],
            revision_basis="alfred_vintage_day_before_first_release")
        if not observations:
            skipped.append({"target": period, "reason": "information_set_empty"})
            continue
        if indicator == "cpi":
            for row in observations:
                exact = ((Decimal(known[row.period]) / Decimal(known[shift_month(row.period, -12)]) - 1) * 100)
                if exact.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) != Decimal(repr(row.value)):
                    rounding_mismatches += 1
        last = Decimal(repr(observations[-1].value))
        release_at = datetime.combine(first, clock_time(12, 30), tzinfo=UTC)
        rows, forecast, withheld = ask(indicator, period, observations, release_at, last, outcome)
        if forecast is None:
            skipped.append({"target": period, "reason": f"baseline_withheld:{withheld}"})
            continue
        questions.extend(rows)
        targets.append(target_row(indicator, period, forecast, outcome, first=first.isoformat(),
                                  info=day.isoformat()))
    result = {
        "kind": "point_in_time_alfred", "fred_series": series, "transform": meta["transform"],
        "step": str(STEPS[indicator]), "first_target": found[0][0] if found else None,
        "last_target": targets[-1]["target"] if targets else None, "targets_scored": len(targets),
        "skipped_targets": skipped, "release_vintage_urls": len(set(release_urls.values())),
        "information_vintage_urls": len(set(information_urls.values())),
        "horizons": {str(h): sum(1 for row in targets if row["h"] == h) for h in sorted({row["h"] for row in targets})},
        "metrics": score(questions), "by_threshold_position": by_position(questions),
        "value_forecast": value_scores(targets),
    }
    if indicator == "cpi":
        result["cpi_adapter_rounding_mismatches"] = rounding_mismatches
    return {"summary": result, "questions": questions, "targets": targets}


# --- Current-vintage approximations (ICSA, DGS10) -------------------------------------------------------------

def approximate_backtest(fetcher: Fetcher, indicator: str) -> dict[str, Any]:
    meta = SERIES[indicator]
    series = meta["fred"]
    cadence = meta["cadence"]
    lookback = timedelta(weeks=WINDOW_PERIODS["weekly"] + 8) if cadence == "weekly" else timedelta(days=830)
    url = f"{FRED_CSV}?id={series}&cosd={(START_DAY - lookback).isoformat()}"
    text = fetcher.get(url, kind="csv").decode("utf-8")
    raw = [(day, value) for day, value in parse_fred_csv(text, series) if value not in {"", "."}]
    retrieved = datetime.fromisoformat(fetcher.requests[-1]["retrieved_at"])
    observations = normalize_observations(indicator, {day: float(value) for day, value in raw},
        available_at=retrieved, vintage=retrieved.isoformat(), source_url=url,
        revision_basis="fred_current_vintage_revised")
    values = dict(raw)
    rows_per_target = APPROX_HISTORY_ROWS[cadence]
    questions: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for index, row in enumerate(observations):
        if date.fromisoformat(row.period) < START_DAY or index == 0:
            continue
        history = observations[max(0, index - rows_per_target):index]
        last = Decimal(values[history[-1].period])
        release_at = datetime.combine(date.fromisoformat(row.period) + timedelta(days=6), clock_time(21),
                                      tzinfo=UTC)
        rows, forecast, withheld = ask(indicator, row.period, history, release_at, last, Decimal(values[row.period]))
        if forecast is None:
            skipped.append({"target": row.period, "reason": f"baseline_withheld:{withheld}"})
            continue
        questions.extend(rows)
        targets.append(target_row(indicator, row.period, forecast, Decimal(values[row.period]), first=None,
                                  info=None))
    summary = {
        "kind": "approximation_current_fred_vintage", "fred_series": series, "transform": meta["transform"],
        "step": str(STEPS[indicator]), "first_target": targets[0]["target"] if targets else None,
        "last_target": targets[-1]["target"] if targets else None, "targets_scored": len(targets),
        "skipped_targets": skipped,
        "horizons": {str(h): sum(1 for row in targets if row["h"] == h) for h in sorted({row["h"] for row in targets})},
        "metrics": score(questions), "by_threshold_position": by_position(questions),
        "value_forecast": value_scores(targets),
        "caveat": ("Current FRED vintage for both history and outcomes, one period ahead. Not point in time: ICSA "
                   "advance figures are revised the following week and its seasonal factors are revised every year; "
                   "DGS10 revisions are rare but were not verified."),
    }
    return {"summary": summary, "questions": questions, "targets": targets}


# --- Output -------------------------------------------------------------------------------------------------

def compact(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> dict[str, Any]:
    return {"columns": list(columns), "rows": [[row[column] for column in columns] for row in rows]}


INNERMOST_ARRAY = re.compile(r"\[\n\s+([^\[\]{}]*?)\n\s*\]")


def dump_json(value: Any) -> str:
    """Indented JSON with every innermost array (one question or target row) on a single line."""
    text = json.dumps(value, indent=1, sort_keys=True)
    # JSON strings never contain raw newlines, so ",\n" only separates array elements here.
    text = INNERMOST_ARRAY.sub(lambda match: "[" + ", ".join(
        part.strip() for part in match.group(1).split(",\n")) + "]", text)
    if json.loads(text) != value:
        raise BacktestError("compact_json_round_trip_failed")
    return text + "\n"


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


LABELS = {"unemployment": "Unemployment rate (UNRATE)", "payrolls": "Payroll change (PAYEMS)",
          "cpi": "CPI year-over-year (CPIAUCNS)", "jobless_claims": "Initial claims (ICSA), approximation",
          "treasury_10y": "10-year yield (DGS10), approximation"}


def summary_markdown(results: dict[str, Any]) -> str:
    code = results["code"]
    origin = (f"uncommitted code on top of commit `{code['base_commit']}`" if code["source_files_uncommitted"]
              else f"commit `{code['base_commit']}`")
    lines = [
        "# Statistical baseline backtest v1",
        "",
        f"Backtest of the fixed rule `{RULE_VERSION}` (method `{METHOD}`), generated {results['generated_at'][:10]} "
        f"with data through {results['data_as_of']}, from {origin} (source SHA-256 in `results.json`). This is a "
        "historical backtest of a deterministic rule, not a prospective result, and not evidence about the "
        "language-model methods. Raw ALFRED/FRED responses are cached under `data/local/` and their SHA-256 values "
        "are listed in `results.json`.",
        "",
        "## Questions",
        "",
        "For every target period, three questions: is the value greater than the last published value minus one "
        "step, the last published value, and the last published value plus one step? Steps: 0.1 point "
        "(unemployment, CPI), 50,000 jobs (payrolls), 10,000 claims (ICSA), 0.05 point (DGS10). The rule and steps "
        "were fixed before any result was computed.",
        "",
        "The monthly series are point in time: the information set is the ALFRED vintage dated the day before the "
        "target's first release, limited to the ten calendar years the live BLS v1 adapter returns; the outcome is "
        "the target in its first ALFRED vintage (unemployment as published, payroll level minus the same vintage's "
        "prior month, CPI 12-month change rounded half-up to 0.1). ICSA and DGS10 use the current FRED vintage and "
        "are approximations.",
        "",
        "## Results",
        "",
        "| Series | Targets | Questions | Brier: baseline | Brier: 50% | Brier: persistence | Log loss: baseline "
        "| Log loss: 50% | Log loss: persistence | ECE | 80% interval coverage |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    rows = [(LABELS[name], results["series"][name]) for name in (*MONTHLY, *APPROXIMATE) if name in results["series"]]
    pooled = results["overall"]["point_in_time_monthly"]
    rows.insert(len(MONTHLY), ("**Monthly series pooled (point in time)**", pooled))
    for label, item in rows:
        metrics = item["metrics"]
        if not metrics.get("questions"):
            continue
        lines.append(
            f"| {label} | {item.get('targets_scored', item.get('value_forecast', {}).get('targets', 0))} | "
            f"{metrics['questions']} | {_fmt(metrics['brier']['baseline'])} | "
            f"{_fmt(metrics['brier']['uninformed_50'])} | {_fmt(metrics['brier']['persistence'])} | "
            f"{_fmt(metrics['log_loss']['baseline'])} | {_fmt(metrics['log_loss']['uninformed_50'])} | "
            f"{_fmt(metrics['log_loss']['persistence'])} | {_fmt(metrics['expected_calibration_error'])} | "
            f"{_fmt(item['value_forecast'].get('interval_80_coverage'), 2)} |")
    lines += ["", "Persistence forecasts 98% when the last value already satisfies the question and 2% otherwise; "
              "the uninformed forecast is 50%. ECE is the count-weighted mean absolute gap between forecast and "
              "observed frequency over ten probability bins. Interval coverage is the share of first-release values "
              "inside the baseline's 10th-90th percentile range.", ""]
    lines += ["## Brier score by threshold position", "",
              "| Series | Position | Questions | Outcome rate | Mean forecast | Brier: baseline | Brier: 50% "
              "| Brier: persistence |", "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name in (*MONTHLY, *APPROXIMATE):
        item = results["series"].get(name)
        if not item:
            continue
        for position, summary in item["by_threshold_position"].items():
            if not summary.get("questions"):
                continue
            lines.append(f"| {LABELS[name]} | {position} | {summary['questions']} | {_fmt(summary['outcome_rate'], 2)} | "
                         f"{_fmt(summary['mean_forecast'], 2)} | {_fmt(summary['brier']['baseline'])} | "
                         f"{_fmt(summary['brier']['uninformed_50'])} | {_fmt(summary['brier']['persistence'])} |")
    lines += ["", "## Reliability (baseline probability, ten bins)", ""]
    for label, item in [("Monthly series pooled (point in time)", pooled),
                        *[(LABELS[name], results["series"][name]) for name in (*MONTHLY, *APPROXIMATE)
                          if name in results["series"]]]:
        table = item["metrics"].get("reliability") or []
        lines += [f"### {label}", "", "| Forecast bin | Questions | Mean forecast | Observed frequency |",
                  "| --- | ---: | ---: | ---: |"]
        lines += [f"| {row['bin']} | {row['count']} | {_fmt(row['mean_forecast'], 2)} | "
                  f"{_fmt(row['observed_frequency'], 2)} |" for row in table if row["count"]]
        lines.append("")
    lines += ["## Value forecasts", "", "| Series | Targets | 80% interval coverage | 90% interval coverage "
              "| Mode equals outcome | Median equals outcome |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name in (*MONTHLY, *APPROXIMATE):
        item = results["series"].get(name)
        if not item:
            continue
        value = item["value_forecast"]
        lines.append(f"| {LABELS[name]} | {value['targets']} | {_fmt(value['interval_80_coverage'], 2)} | "
                     f"{_fmt(value['interval_90_coverage'], 2)} | {_fmt(value['mode_hit_rate'], 2)} | "
                     f"{_fmt(value['median_hit_rate'], 2)} |")
    lines += ["", "## Caveats", ""] + [f"- {item}" for item in results["caveats"]] + [""]
    return "\n".join(lines)


CAVEATS = [
    "Historical backtest of a fixed, deterministic rule; these are not prospective results.",
    "The three questions per target are nested (thresholds on one value) and targets in adjacent periods share "
    "most of their history, so questions are strongly correlated. The effective sample is far smaller than the "
    "question count; no significance test is reported.",
    "Thresholds are anchored on the last published value, which favors persistence for the 'below' and 'above' "
    "questions and makes 'at' questions roughly coin flips for it.",
    "The trailing window (120 months, 156 weeks, 520 weekdays) includes the 2020 pandemic shock until the shock "
    "leaves the window, which widens the predictive distribution for those years.",
    "Monthly outcomes are ALFRED reconstructions of BLS headline figures (UNRATE as published, PAYEMS level minus "
    "the same vintage's prior month, CPIAUCNS 12-month change rounded half-up), not parsed BLS news releases.",
    "The live baseline uses the BLS v1 API (latest revised data); this backtest substitutes ALFRED vintages for the "
    "same series, limited to the same ten calendar years.",
    "October 2025 unemployment and CPI were never published (federal shutdown); those targets are skipped and the "
    "next target is forecast two months ahead.",
    "ICSA and DGS10 results use the current FRED vintage for history and outcomes and are approximations, not point "
    "in time.",
]


def run(*, offline: bool, today: date, out_dir: Path, cache_dir: Path) -> dict[str, Any]:
    fetcher = Fetcher(cache_dir, offline=offline)
    try:
        series_results = {name: monthly_backtest(fetcher, name, today) for name in MONTHLY}
        series_results.update({name: approximate_backtest(fetcher, name) for name in APPROXIMATE})
    finally:
        fetcher.close()
    monthly_questions = [row for name in MONTHLY for row in series_results[name]["questions"]]
    monthly_targets = [row for name in MONTHLY for row in series_results[name]["targets"]]
    approximate_questions = [row for name in APPROXIMATE for row in series_results[name]["questions"]]
    results: dict[str, Any] = {
        "backtest_version": BACKTEST_VERSION, "method": METHOD, "rule_version": RULE_VERSION,
        "generated_at": datetime.now(UTC).isoformat(), "code": source_identity(),
        "data_as_of": today.isoformat(),
        "question_rule": {"targets_from": START_MONTH, "comparison": "gt", "positions": list(POSITIONS),
                          "thresholds": "last published value minus one step, the value itself, plus one step",
                          "steps": {name: str(step) for name, step in STEPS.items()}},
        "method_rule": {"window_periods": WINDOW_PERIODS, "probability_rule": PROBABILITY_RULE,
                        "probability_bounds": [MIN_AGG_P, MAX_AGG_P]},
        "comparators": {"uninformed_50": "0.5 for every question",
                        "persistence": "0.98 if the last published value already satisfies the question, else 0.02"},
        "information_set": ("ALFRED vintage dated the day before the target's first release, from January of the "
                            "vintage year minus nine (the live BLS v1 adapter's ten calendar years), transformed by "
                            "forecastlab.macro.normalize_observations"),
        "outcome_rule": ("First ALFRED vintage containing the target: UNRATE as published; PAYEMS level minus the "
                         "same vintage's prior month, in jobs; CPIAUCNS 12-month change rounded half-up to 0.1"),
        "series": {name: item["summary"] for name, item in series_results.items()},
        "overall": {"point_in_time_monthly": {"metrics": score(monthly_questions),
                                              "value_forecast": value_scores(monthly_targets),
                                              "by_threshold_position": by_position(monthly_questions)},
                    "approximations_current_vintage": {"metrics": score(approximate_questions)}},
        "questions_point_in_time": compact(monthly_questions, QUESTION_COLUMNS),
        "targets_point_in_time": compact(monthly_targets, TARGET_COLUMNS),
        "approximation_rows": ("Per-question rows for the ICSA and DGS10 approximations are not stored; rerun the "
                               "script against the cached FRED responses listed below to regenerate them."),
        "provenance": {"cache_dir": str(cache_dir.relative_to(ROOT)) if cache_dir.is_relative_to(ROOT)
                       else str(cache_dir), "user_agent": PUBLIC_DATA_USER_AGENT,
                       "crawl_delay_seconds": CRAWL_DELAY_SECONDS, "lines_per_alfred_request": LINES_PER_REQUEST,
                       "requests": sorted({row["url"]: {k: v for k, v in row.items() if k != "served_from_cache"}
                                           for row in fetcher.requests}.values(), key=lambda row: row["url"])},
        "caveats": CAVEATS,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(dump_json(results))
    (out_dir / "summary.md").write_text(summary_markdown(results))
    results["_downloads"] = fetcher.downloads
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--offline", action="store_true", help="use cached responses only")
    parser.add_argument("--today", type=date.fromisoformat, default=datetime.now(UTC).date(),
                        help="last vintage date to use (default: today, UTC)")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--cache", type=Path, default=CACHE_DIR)
    args = parser.parse_args(argv)
    results = run(offline=args.offline, today=args.today, out_dir=args.out, cache_dir=args.cache)
    print(f"{BACKTEST_VERSION}: {results['_downloads']} downloads; wrote {args.out}")
    for name, item in results["series"].items():
        metrics = item["metrics"]
        if metrics.get("questions"):
            print(f"  {name}: {metrics['questions']} questions, Brier {metrics['brier']['baseline']:.3f} "
                  f"(50% {metrics['brier']['uninformed_50']:.3f}, persistence {metrics['brier']['persistence']:.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
