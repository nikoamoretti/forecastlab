"""Score exported prospective cohort artifacts against first-release outcomes.

Prospective batches can run in an ephemeral local instance whose database is
discarded. Their exported artifacts (``cohort_report.json`` and
``frozen_manifest.json``) are scored here, from any later checkout, once the
official values are published.

* FRED fast indicators (ICSA, DGS10) resolve exactly as the in-app adjudicator
  does: :func:`official_fred_outcomes.find_initial_release`.
* Monthly BLS indicators resolve from the ALFRED vintage dated on the scheduled
  release day (New York date). The observation must be present in that vintage
  and absent from the previous day's vintage, so the value is the first print.
  The headline is reconstructed deterministically from that vintage:
  unemployment as published (``UNRATE``), payrolls as the level minus the
  revised prior month in thousands (``PAYEMS``), CPI as the 12-month percent
  change of ``CPIAUCNS`` rounded half-up to one decimal. These are ALFRED
  reconstructions of the BLS figures, not parsed BLS news-release documents.

Unpublished questions stay ``pending``; nothing is guessed.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from forecastlab.macro import SERIES, MacroDataError, MacroSpec, parse_fred_csv
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api import official_fred_outcomes
from forecastlab_api.official_fred_outcomes import ALFRED_GRAPH_CSV, NOT_READY
from forecastlab_api.official_macro_outcomes import OfficialMacroOutcomeError

SCORING_VERSION = "prospective_artifact_scoring_v1"
# Optional post-freeze statistical-baseline forecasts (scripts/baseline_supplement.py).
SUPPLEMENT_FILE = "baseline_supplement.json"
SUPPLEMENT_SUFFIX = " (post-freeze supplement)"
NEW_YORK = ZoneInfo("America/New_York")
EPSILON = 1e-9
Fetch = Callable[[str], str]


def fetch_text(url: str) -> str:
    response = official_fred_outcomes.fetch_vintage_csv(url)
    if response.status_code != 200:
        raise MacroDataError("alfred_vintage_unavailable")
    return response.content.decode("utf-8")


def _month_start(period: str) -> date:
    return date.fromisoformat(period + "-01")


def _shift_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _vintage(series: str, start: date, end: date, vintage: date, fetch: Fetch) -> tuple[dict[str, str], str] | None:
    url = f"{ALFRED_GRAPH_CSV}?id={series}&cosd={start.isoformat()}&coed={end.isoformat()}&vintage_date={vintage.isoformat()}"
    text = fetch(url)
    header = text.lstrip("﻿").split("\n", 1)[0].split(",")
    expected = f"{series}_{vintage.strftime('%Y%m%d')}"
    if len(header) == 2 and header[1].strip() != expected:
        return None  # ALFRED clamps future vintages: not published yet.
    return dict(parse_fred_csv(text, expected)), url


def monthly_first_release(spec: MacroSpec, *, today: date, fetch: Fetch = fetch_text) -> dict[str, Any]:
    """Reconstruct a BLS headline from the release-day ALFRED vintage."""
    meta = SERIES[spec.indicator]
    release_day = as_utc(spec.release_at).astimezone(NEW_YORK).date()
    if release_day > today:
        return {"status": "pending", "reason": "release_in_future"}
    observation = _month_start(spec.observation_period)
    # Never request a single-day range: ALFRED answers an empty one with the full history.
    lookback = {"yoy_percent": -12}.get(meta["transform"], -1)
    start = _shift_months(observation, lookback)
    current = _vintage(meta["fred"], start, observation, release_day, fetch)
    if current is None:
        return {"status": "pending", "reason": "release_vintage_not_available"}
    rows, url = current
    if rows.get(observation.isoformat(), "") in {"", "."}:
        return {"status": "pending", "reason": "observation_absent_from_release_vintage", "vintage_url": url}
    prior = _vintage(meta["fred"], start, observation, release_day - timedelta(days=1), fetch)
    if prior is None:
        return {"status": "exception", "reason": "prior_vintage_unavailable"}
    prior_rows, prior_url = prior
    if prior_rows.get(observation.isoformat(), "") not in {"", "."}:
        return {"status": "exception", "reason": "observation_published_before_scheduled_release",
                "vintage_url": url, "prior_vintage_url": prior_url}
    level = Decimal(rows[observation.isoformat()])
    if meta["transform"] == "level":
        value = level
    elif meta["transform"] == "change_thousands":
        previous = rows.get(start.isoformat(), "")
        if previous in {"", "."}:
            return {"status": "exception", "reason": "prior_month_missing", "vintage_url": url}
        value = (level - Decimal(previous)) * 1000
    else:
        base = rows.get(start.isoformat(), "")
        if base in {"", "."}:
            return {"status": "exception", "reason": "year_ago_index_missing", "vintage_url": url}
        value = ((level / Decimal(base) - 1) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return {"status": "resolved", "value": str(value), "basis": f"alfred_release_day_vintage:{meta['transform']}",
            "vintage_date": release_day.isoformat(), "vintage_url": url, "prior_vintage_url": prior_url}


def fred_first_release(spec: MacroSpec, *, today: date) -> dict[str, Any]:
    """Weekly/daily FRED indicators: identical rule to the in-app adjudicator."""
    meta = SERIES[spec.indicator]
    try:
        first, prior = official_fred_outcomes.find_initial_release(
            meta["fred"], date.fromisoformat(spec.observation_period), today=today)
    except OfficialMacroOutcomeError as exc:
        status = "pending" if str(exc) == NOT_READY else "exception"
        return {"status": status, "reason": str(exc)}
    if first.value in {None, "", "."}:
        return {"status": "cancelled", "reason": "fred_initial_release_value_missing", "vintage_url": first.url}
    return {"status": "resolved", "value": first.value, "basis": "alfred_initial_vintage",
            "vintage_date": first.vintage_date.isoformat(), "vintage_url": first.url, "prior_vintage_url": prior.url}


def outcome_for(spec: MacroSpec, value: str) -> int:
    measured, threshold = Decimal(value), Decimal(str(spec.threshold))
    return int({"gt": measured > threshold, "ge": measured >= threshold,
                "lt": measured < threshold, "le": measured <= threshold}[spec.comparison])


def resolve(spec: MacroSpec, *, today: date, fetch: Fetch = fetch_text) -> dict[str, Any]:
    try:
        result = (fred_first_release(spec, today=today) if SERIES[spec.indicator]["source"] == "fred"
                  else monthly_first_release(spec, today=today, fetch=fetch))
    except (MacroDataError, OfficialMacroOutcomeError) as exc:
        result = {"status": "pending", "reason": f"source_error:{exc}"}
    if result["status"] == "resolved":
        result["outcome"] = outcome_for(spec, result["value"])
    return result


def brier(probability: float, outcome: int) -> float:
    return (probability - outcome) ** 2


def log_loss(probability: float, outcome: int) -> float:
    clipped = min(max(probability, EPSILON), 1 - EPSILON)
    return -math.log(clipped if outcome else 1 - clipped)


def score_artifact(directory: Path, *, today: date | None = None, fetch: Fetch = fetch_text) -> dict[str, Any]:
    """Resolve every entry of one exported cohort and score each method's forecasts."""
    today = today or utcnow().date()
    report = json.loads((directory / "cohort_report.json").read_text())
    manifest = json.loads((directory / "frozen_manifest.json").read_text())["manifest"]
    specs = {entry["entry_id"]: MacroSpec.model_validate(entry["macro"]) for entry in manifest["entries"]}
    outcomes = {entry_id: resolve(spec, today=today, fetch=fetch) for entry_id, spec in specs.items()}
    cells = []
    for question in report["questions"]:
        result = outcomes[question["id"]]
        for cell in question["cells"]:
            probability, outcome = cell["probability"], result.get("outcome")
            row = {"entry_id": question["id"], "release_event": question["release_event"], "method": cell["method"],
                   "status": cell["status"], "probability": probability, "outcome": outcome}
            if probability is not None and outcome is not None:
                row |= {"brier": brier(probability, outcome), "log_loss": log_loss(probability, outcome)}
            cells.append(row)
    supplement_path = directory / SUPPLEMENT_FILE
    supplement_methods: list[str] = []
    if supplement_path.exists():
        # Added after the cohort froze; never part of the frozen manifest.
        supplement = json.loads(supplement_path.read_text())
        method = f"{supplement['method']}{SUPPLEMENT_SUFFIX}"
        supplement_methods.append(method)
        for cell in supplement["cells"]:
            result = outcomes[cell["entry_id"]]
            probability, outcome = cell["probability"], result.get("outcome")
            row = {"entry_id": cell["entry_id"], "release_event": cell["release_event"], "method": method,
                   "status": cell["status"], "probability": probability, "outcome": outcome}
            if probability is not None and outcome is not None:
                row |= {"brier": brier(probability, outcome), "log_loss": log_loss(probability, outcome)}
            cells.append(row)
    methods = {}
    for method in [*manifest["methods"], *supplement_methods]:
        own = [row for row in cells if row["method"] == method]
        scored = [row for row in own if "brier" in row]
        methods[method] = {
            "assigned": len(own), "forecasted": sum(row["probability"] is not None for row in own),
            "resolved_forecasts": len(scored),
            "mean_brier": sum(row["brier"] for row in scored) / len(scored) if scored else None,
            "mean_log_loss": sum(row["log_loss"] for row in scored) / len(scored) if scored else None,
            "uninformed_50pct_brier": 0.25 if scored else None,
        }
    return {"scoring_version": SCORING_VERSION, "scored_on": today.isoformat(), "cohort_id": report["id"],
            "manifest_hash": report["manifest_hash"],
            "resolved_entries": sum(result["status"] == "resolved" for result in outcomes.values()),
            "total_entries": len(outcomes), "outcomes": outcomes, "methods": methods, "cells": cells,
            "interpretation": "Small, correlated sample; operational evidence only, not an accuracy or calibration claim."}


def write_scores(directory: Path, *, today: date | None = None, fetch: Fetch = fetch_text) -> dict[str, Any]:
    scores = score_artifact(directory, today=today, fetch=fetch)
    (directory / "scores.json").write_text(json.dumps(scores, indent=2, sort_keys=True) + "\n")
    return scores
