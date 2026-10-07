"""Plain-language wording for macro questions and their published values.

The track record shows these instead of the contract text. The contract (first
release, FRED series, vintages) stays the authority for resolution.
"""

from __future__ import annotations

from datetime import date

from forecastlab.macro import SERIES, MacroSpec

OPERATOR_WORDS = {"gt": "above", "ge": "at or above", "lt": "below", "le": "at or below"}
TOPICS = {"unemployment": "Jobs", "payrolls": "Jobs", "jobless_claims": "Jobs", "cpi": "Inflation",
          "treasury_10y": "Interest rates"}


def _month(period: str) -> str:
    return date.fromisoformat(f"{period}-01").strftime("%B %Y")


def _day(period: str) -> str:
    day = date.fromisoformat(period)
    return f"{day.strftime('%b')} {day.day}, {day.year}"


def format_value(indicator: str, value: float | str) -> str:
    """A published value in everyday units, e.g. '4.2%', '+29,000 jobs', '231,000 claims'."""
    number = float(value)
    if indicator in {"unemployment", "cpi"}:
        return f"{number:.1f}%"
    if indicator == "treasury_10y":
        return f"{number:.2f}%"
    if indicator == "payrolls":
        return f"{'+' if number > 0 else ''}{number:,.0f} jobs"
    return f"{number:,.0f} claims"


def question_title(spec: MacroSpec) -> str:
    """One short question a reader understands without knowing the data series."""
    op = OPERATOR_WORDS[spec.comparison]
    threshold = format_value(spec.indicator, spec.threshold)
    period = spec.observation_period
    if spec.indicator == "unemployment":
        return f"Will the U.S. unemployment rate for {_month(period)} come in {op} {threshold}?"
    if spec.indicator == "cpi":
        return f"Will U.S. inflation (CPI, year over year) for {_month(period)} come in {op} {threshold}?"
    if spec.indicator == "payrolls":
        more = {"gt": "more than", "ge": "at least", "lt": "fewer than", "le": "at most"}[spec.comparison]
        return f"Will the U.S. economy add {more} {threshold.removeprefix('+')} in {_month(period)}?"
    if spec.indicator == "jobless_claims":
        return f"Will weekly U.S. jobless claims for the week ending {_day(period)} come in {op} {threshold}?"
    return f"Will the 10-year U.S. Treasury yield on {_day(period)} be {op} {threshold}?"


def question_text(spec: MacroSpec) -> str:
    """The precise wording: first published value of the named FRED series."""
    meta = SERIES[spec.indicator]
    period = f"the week ending {spec.observation_period}" if meta["cadence"] == "weekly" else spec.observation_period
    threshold = f"{int(spec.threshold):,}" if spec.threshold >= 1000 else f"{spec.threshold:g}"
    return (f"Will the first published {meta['label']} (FRED {meta['fred']}) for {period} be "
            f"{OPERATOR_WORDS[spec.comparison]} {threshold} {meta['units'].replace('_', ' ')}?")
