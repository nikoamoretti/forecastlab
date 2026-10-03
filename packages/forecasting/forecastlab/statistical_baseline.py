"""Deterministic statistical baseline for macro questions (``statistical_baseline_v1``).

Rule ``statistical_baseline_rule_v1``. No model, search, or paid call is involved.

1. Point forecast.
   * Persistent series (unemployment level, CPI year-over-year, initial claims,
     10-year yield): a random walk, i.e. the last available value.
   * Payroll monthly change: the mean of the last three monthly changes (the
     origin month and the two before it; all three must be published).
2. Errors. The rule is applied at every origin inside a trailing window
   (120 months, 156 weeks or 520 weekdays, ending at the last observation) and
   compared with the value ``h`` periods later, where ``h`` is the distance from
   the last available observation to the target period in months, weeks, or
   weekdays. Origins overlap; pairs with a missing value are skipped, never
   imputed.
3. Predictive sample. ``point + error`` for each historical error, rounded
   half-up to the series' publication precision (0.1 for unemployment and CPI
   year-over-year, 0.01 for the 10-year yield, 1 claim, 1,000 jobs).
4. Probability. ``(k + 0.5) / (n + 1)``, where ``k`` counts sample values that
   satisfy the question's comparison with the threshold (exact decimals), then
   clipped to the bounds of the existing aggregators, ``[0.02, 0.98]``.

The result also reports the empirical quantiles and the most likely published
value (the mode of the rounded sample). Arithmetic is decimal, so a value equal
to the threshold never counts as ``gt`` because of binary floating-point noise.
"""
from __future__ import annotations

import calendar
import math
import operator
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from forecastlab.aggregation import MAX_AGG_P, MIN_AGG_P, clip_aggregated_probability
from forecastlab.macro import SERIES, MacroObservation, MacroSnapshot, MacroSpec

METHOD = "statistical_baseline_v1"
RULE_VERSION = "statistical_baseline_rule_v1"
# Trailing window, in the series' own periods, ending at the last observation.
WINDOW_PERIODS = {"monthly": 120, "weekly": 156, "daily": 520}
PERIOD_UNITS = {"monthly": "month", "weekly": "week", "daily": "weekday"}
PUBLICATION_PRECISION = {
    "unemployment": Decimal("0.1"),
    "cpi": Decimal("0.1"),
    "treasury_10y": Decimal("0.01"),
    "jobless_claims": Decimal("1"),
    # BLS publishes payroll levels and changes in thousands; the adapter's
    # changes in jobs are therefore whole thousands.
    "payrolls": Decimal("1000"),
}
TRAILING_MEAN_PERIODS = 3
MIN_ERRORS = 24
QUANTILE_LEVELS = (5, 10, 25, 50, 75, 90, 95)
PROBABILITY_RULE = "(k + 0.5) / (n + 1), clipped to [0.02, 0.98]"
_COMPARE: dict[str, Callable[[Any, Any], bool]] = {
    "gt": operator.gt, "ge": operator.ge, "lt": operator.lt, "le": operator.le}
_OPERATOR_TEXT = {"gt": "above", "ge": "at or above", "lt": "below", "le": "at or below"}
_EPOCH_SATURDAY = date(1970, 1, 3)
_EPOCH_MONDAY = date(1970, 1, 5)


class BaselineUnavailable(ValueError):
    """A stable gap reason: the baseline withholds instead of guessing."""


def point_rule(indicator: str) -> str:
    if SERIES[indicator]["transform"] == "change_thousands":
        return "trailing_3_month_mean_of_monthly_changes"
    return "random_walk_last_value"


def fred_history_options(indicator: str) -> dict[str, int]:
    """Keyword arguments for ``fetch_macro`` so FRED history covers the baseline window.

    The root evidence packet keeps its own capped history; only the baseline
    passes these.
    """
    meta = SERIES[indicator]
    if meta["source"] != "fred":
        return {}
    window = WINDOW_PERIODS[meta["cadence"]]
    # The 21-day margin covers FRED's staleness allowance (7 or 14 days).
    days = 7 * window if meta["cadence"] == "weekly" else math.ceil(window * 7 / 5)
    return {"fred_history_limit": window, "fred_lookback_days": days + 21}


def period_index(cadence: str, period: str) -> int:
    """Integer position on the series' own grid (months, weeks, or weekdays)."""
    if cadence == "monthly":
        year, month = map(int, period.split("-"))
        return year * 12 + month - 1
    day = date.fromisoformat(period)
    if cadence == "weekly":
        if day.weekday() != 5:
            raise BaselineUnavailable("statistical_baseline_weekly_period_not_saturday")
        return (day - _EPOCH_SATURDAY).days // 7
    if cadence == "daily":
        if day.weekday() >= 5:
            raise BaselineUnavailable("statistical_baseline_daily_period_not_weekday")
        weeks, weekday = divmod((day - _EPOCH_MONDAY).days, 7)
        return weeks * 5 + weekday
    raise BaselineUnavailable("statistical_baseline_cadence_unknown")


def index_period(cadence: str, index: int) -> str:
    if cadence == "monthly":
        return f"{index // 12:04d}-{index % 12 + 1:02d}"
    if cadence == "weekly":
        return date.fromordinal(_EPOCH_SATURDAY.toordinal() + 7 * index).isoformat()
    weeks, weekday = divmod(index, 5)
    return date.fromordinal(_EPOCH_MONDAY.toordinal() + 7 * weeks + weekday).isoformat()


def horizon(cadence: str, last_period: str, target_period: str) -> int:
    """Periods from the last available observation to the target (months, weeks, weekdays)."""
    h = period_index(cadence, target_period) - period_index(cadence, last_period)
    if h < 1:
        raise BaselineUnavailable("statistical_baseline_target_not_after_history")
    return h


def round_published(indicator: str, value: float | int | Decimal) -> Decimal:
    """Round half-up (ties away from zero) to the series' publication precision.

    Floats enter through their shortest decimal representation, so 0.35 is the
    decimal 0.35 (rounded to 0.4) rather than its binary approximation.
    """
    step = PUBLICATION_PRECISION[indicator]
    number = value if isinstance(value, Decimal) else Decimal(repr(float(value)))
    if not number.is_finite():
        raise BaselineUnavailable("statistical_baseline_value_not_finite")
    # Adding zero turns a rounded negative zero (-0.04 -> -0.0) into 0.0.
    return (number / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step + 0


def _decimal(value: float | int | Decimal) -> Decimal:
    number = value if isinstance(value, Decimal) else Decimal(repr(float(value)))
    if not number.is_finite():
        raise BaselineUnavailable("statistical_baseline_value_not_finite")
    return number


def _point(indicator: str, values: dict[int, Decimal], origin: int) -> Decimal | None:
    if SERIES[indicator]["transform"] == "change_thousands":
        inputs = [values.get(origin - lag) for lag in range(TRAILING_MEAN_PERIODS)]
        if any(item is None for item in inputs):
            return None
        return sum((item for item in inputs if item is not None), Decimal(0)) / TRAILING_MEAN_PERIODS
    return values.get(origin)


def _number(indicator: str, value: Decimal) -> float | int:
    """JSON number at publication precision (integers for claims and jobs)."""
    if PUBLICATION_PRECISION[indicator] >= 1:
        return int(value.to_integral_value(rounding=ROUND_HALF_UP))
    return float(value)


def format_value(indicator: str, value: float | int | Decimal) -> str:
    number = _decimal(value)
    if indicator in {"unemployment", "cpi"}:
        return f"{number.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"
    if indicator == "treasury_10y":
        return f"{number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"
    whole = int(number.to_integral_value(rounding=ROUND_HALF_UP))
    if indicator == "jobless_claims":
        return f"{whole:,} claims"
    return f"{whole:+,} jobs"


def period_label(cadence: str, period: str) -> str:
    if cadence == "monthly":
        year, month = map(int, period.split("-"))
        return f"{calendar.month_name[month]} {year}"
    if cadence == "weekly":
        return f"the week ending {period}"
    return period


def _target_phrase(cadence: str, period: str, indicator: str) -> str:
    noun = "change" if indicator == "payrolls" else "figure"
    if cadence == "weekly":
        return f"the {noun} for {period_label(cadence, period)}"
    return f"the {period_label(cadence, period)} {noun}"


def _span(count: int, unit: str) -> str:
    return f"{count} {unit}" if count == 1 else f"{count} {unit}s"


def _percent(probability: float) -> str:
    return f"{probability * 100:.0f}%"


def _quantile(ordered: list[Decimal], level: int) -> Decimal:
    """Inverse empirical CDF (type 1): the smallest sample value with F(x) >= level/100."""
    rank = -(-level * len(ordered) // 100)
    return ordered[max(1, rank) - 1]


def baseline_forecast(spec: MacroSpec, observations: Iterable[MacroObservation]) -> dict[str, Any]:
    """Apply ``statistical_baseline_rule_v1`` to observations that precede the target.

    Raises :class:`BaselineUnavailable` (a gap reason) instead of guessing.
    """
    meta = SERIES[spec.indicator]
    cadence = meta["cadence"]
    target_index = period_index(cadence, spec.observation_period)
    values: dict[int, Decimal] = {}
    for row in observations:
        index = period_index(cadence, row.period)
        if index >= target_index:
            # Never let the target or a later period into the forecast.
            raise BaselineUnavailable("statistical_baseline_observation_not_before_target")
        if index in values:
            raise BaselineUnavailable("statistical_baseline_duplicate_observation_period")
        values[index] = _decimal(row.value)
    if not values:
        raise BaselineUnavailable("statistical_baseline_history_missing")
    last = max(values)
    window = WINDOW_PERIODS[cadence]
    values = {index: value for index, value in values.items() if index > last - window}
    first = min(values)
    h = target_index - last
    point = _point(spec.indicator, values, last)
    if point is None:
        raise BaselineUnavailable("statistical_baseline_point_inputs_missing")
    errors: list[tuple[int, Decimal]] = []
    for origin in sorted(values):
        if origin + h > last:
            break
        outcome = values.get(origin + h)
        forecast = _point(spec.indicator, values, origin)
        if outcome is None or forecast is None:
            continue
        errors.append((origin, outcome - forecast))
    n = len(errors)
    if n < MIN_ERRORS:
        raise BaselineUnavailable("statistical_baseline_history_insufficient")
    sample = sorted(round_published(spec.indicator, point + error) for _, error in errors)
    threshold = Decimal(str(spec.threshold))
    compare = _COMPARE[spec.comparison]
    k = sum(1 for value in sample if compare(value, threshold))
    raw_probability = (k + 0.5) / (n + 1)
    probability = clip_aggregated_probability(raw_probability)
    counts = Counter(sample)
    point_published = round_published(spec.indicator, point)
    mode = min(counts, key=lambda value: (-counts[value], abs(value - point_published), value))
    quantiles = {str(level): _quantile(sample, level) for level in QUANTILE_LEVELS}
    unit = PERIOD_UNITS[cadence]
    last_period = index_period(cadence, last)
    span = last - first + 1
    result: dict[str, Any] = {
        "method": METHOD,
        "rule_version": RULE_VERSION,
        "indicator": spec.indicator,
        "series_id": meta["bls"] if meta["source"] == "bls" else meta["fred"],
        "label": meta["label"],
        "units": meta["units"],
        "target": {"observation_period": spec.observation_period, "threshold": spec.threshold,
                   "comparison": spec.comparison, "release_at": spec.release_at.isoformat(),
                   "revision_policy": spec.revision_policy},
        "point_rule": point_rule(spec.indicator),
        "point_forecast": float(point),
        "point_forecast_published": _number(spec.indicator, point_published),
        "horizon": {"h": h, "unit": unit, "from_period": last_period, "to_period": spec.observation_period},
        "window": {"max_periods": window, "unit": unit, "span_periods": span, "start": index_period(cadence, first),
                   "end": last_period, "observations": len(values)},
        "n": n,
        "k": k,
        "last_observation": {"period": last_period, "value": _number(spec.indicator, values[last])},
        "publication_precision": str(PUBLICATION_PRECISION[spec.indicator]),
        "rounding": "decimal_round_half_up",
        "probability_rule": PROBABILITY_RULE,
        "raw_probability": raw_probability,
        "probability": probability,
        "probability_bounds": [MIN_AGG_P, MAX_AGG_P],
        "distribution": [{"value": _number(spec.indicator, value), "count": count, "share": count / n}
                         for value, count in sorted(counts.items())],
        "quantiles": {level: _number(spec.indicator, value) for level, value in quantiles.items()},
        "mode": _number(spec.indicator, mode),
        "mean": float(sum(sample, Decimal(0)) / n),
        "interval_80": [_number(spec.indicator, quantiles["10"]), _number(spec.indicator, quantiles["90"])],
        "interval_90": [_number(spec.indicator, quantiles["5"]), _number(spec.indicator, quantiles["95"])],
        "error_range": [float(min(error for _, error in errors)), float(max(error for _, error in errors))],
        "model_calls": 0,
        "search_calls": 0,
        "cost_usd": 0.0,
    }
    result["rationale"] = _rationale(spec, result, span=span, start=index_period(cadence, first))
    return result


def _rationale(spec: MacroSpec, result: dict[str, Any], *, span: int, start: str) -> str:
    cadence = SERIES[spec.indicator]["cadence"]
    unit = PERIOD_UNITS[cadence]
    h = result["horizon"]["h"]
    n, k = result["n"], result["k"]
    last = result["last_observation"]
    window = f"Over the last {_span(span, unit)} of official data ({start} to {last['period']})"
    target = _target_phrase(cadence, spec.observation_period, spec.indicator)
    op = _OPERATOR_TEXT[spec.comparison]
    threshold = format_value(spec.indicator, spec.threshold)
    if result["point_rule"] == "random_walk_last_value":
        opening = (f"{window}, the {result['label']} series has {n} changes over {_span(h, unit)}. Adding each of those "
                   f"changes to the latest value, {format_value(spec.indicator, last['value'])} for "
                   f"{period_label(cadence, last['period'])}, puts {target} {op} {threshold} in {k} of {n} cases.")
    else:
        months = ", ".join(index_period(cadence, period_index(cadence, last["period"]) - lag)
                           for lag in reversed(range(TRAILING_MEAN_PERIODS)))
        opening = (f"{window}, the average of three monthly payroll changes can be compared with the change "
                   f"{_span(h, unit)} later {n} times. Adding each of those {n} differences to the current "
                   f"three-month average, {format_value(spec.indicator, result['point_forecast'])} ({months}), "
                   f"puts {target} {op} {threshold} in {k} of {n} cases.")
    if result["probability"] == result["raw_probability"]:
        chance = (f"The (k + 0.5) / (n + 1) rule gives a probability of {_percent(result['probability'])} that "
                  f"{target} is {op} {threshold}.")
    else:
        chance = (f"The (k + 0.5) / (n + 1) rule gives {result['raw_probability'] * 100:.1f}%, which the method's "
                  f"bounds of 2% and 98% turn into a probability of {_percent(result['probability'])} that {target} "
                  f"is {op} {threshold}.")
    low, high = result["interval_80"]
    value = (f"The most likely published value is {format_value(spec.indicator, result['mode'])}, and the 80% "
             f"range is {format_value(spec.indicator, low)} to {format_value(spec.indicator, high)}.")
    return " ".join((opening, chance, value))


def forecast_from_snapshot(spec: MacroSpec, snapshot: MacroSnapshot) -> dict[str, Any]:
    """Baseline result plus the provenance of the official snapshot it used."""
    if snapshot.indicator != spec.indicator or not snapshot.observations:
        raise BaselineUnavailable("statistical_baseline_snapshot_identity_mismatch")
    result = baseline_forecast(spec, snapshot.observations)
    cadence = SERIES[spec.indicator]["cadence"]
    latest = max(snapshot.observations, key=lambda row: period_index(cadence, row.period))
    result["last_observation"].update({
        "series_id": latest.series_id, "units": latest.units, "seasonal_adjustment": latest.seasonal_adjustment,
        "available_at": latest.available_at.isoformat(), "vintage": latest.vintage,
        "revision_basis": latest.revision_basis, "source_url": latest.source_url})
    result["snapshot"] = {
        "raw_hash": snapshot.raw_hash,
        "source_url": latest.source_url,
        "request_url": snapshot.raw_payload.get("source_url"),
        "retrieved_at": snapshot.retrieved_at.isoformat(),
        "source_lineage": snapshot.source_lineage,
        "revision_basis": latest.revision_basis,
        "observations": len(snapshot.observations),
    }
    return result


def aggregation_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Compact, run-level summary stored with the ForecastVersion."""
    keys = ("method", "rule_version", "probability", "raw_probability", "probability_rule", "probability_bounds",
            "n", "k", "horizon", "window", "point_rule", "point_forecast", "mode", "quantiles", "interval_80",
            "interval_90", "last_observation", "snapshot", "rationale")
    return {key: result[key] for key in keys if key in result}
