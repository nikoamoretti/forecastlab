"""Pre-specified fast-resolving question proposals (FRED ICSA and DGS10).

This is a fixed, transparent rule, not an optimized or probability-seeking
selection. Given ``now`` and the latest observed values it proposes:

* Initial jobless claims (ICSA): the next 2 week-ending Saturdays whose advance
  release (the following Thursday, 8:30 America/New_York) is still in the
  future. Threshold = latest observed ICSA value, comparison ``gt``.
* 10-year Treasury yield (DGS10): the next 5 weekdays strictly after ``now``'s
  UTC date that are not U.S. bond-market full-close holidays (SIFMA's
  recommended full closes follow the federal holidays plus Good Friday), released
  the next weekday at 21:00 UTC. Threshold = latest observed DGS10 value,
  comparison ``gt``. Version 1 did not skip holidays, so its holiday entries have
  no published value and resolve as cancelled.

Forecast cutoff per question = the earliest of ``now + 6h``, (for DGS10) the
observation date's 13:30 UTC market open minus a safety margin, and the release
minus the margin. A question without ``now < cutoff < release`` is dropped.
The output dicts are ``CohortQuestionIn``-compatible (2 + 5 = 7 <= 10).
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from forecastlab.macro import MacroSnapshot, MacroSpec, next_weekday
from forecastlab.timeutil import as_utc

FAST_SELECTION_VERSION = "fast_fred_question_selection_v2"
CLAIMS_QUESTIONS = 2
DGS10_QUESTIONS = 5
COHORT_MAX_QUESTIONS = 10
CUTOFF_HORIZON = timedelta(hours=6)
SAFETY_MARGIN = timedelta(minutes=30)
MARKET_OPEN_UTC = time(13, 30)
DGS10_RELEASE_UTC = time(21, 0)
CLAIMS_RELEASE_LOCAL = time(8, 30)
NEW_YORK = ZoneInfo("America/New_York")


def claims_release_at(week_ending: date) -> datetime:
    """DOL advance release: the Thursday after the week-ending Saturday, 8:30 New York time."""
    if week_ending.weekday() != 5:
        raise ValueError("week_ending_must_be_saturday")
    thursday = week_ending + timedelta(days=5)
    return datetime.combine(thursday, CLAIMS_RELEASE_LOCAL, tzinfo=NEW_YORK).astimezone(UTC)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    return day - timedelta(days=1) if day.weekday() == 5 else day + timedelta(days=1) if day.weekday() == 6 else day


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def bond_market_holidays(year: int) -> frozenset[date]:
    """U.S. bond-market full-close days following SIFMA's standard recommendation.

    Federal holidays (observed) plus Good Friday. SIFMA occasionally recommends
    an early close instead of a full close on Good Friday; such a day simply
    becomes a cancelled question.
    """
    return frozenset({
        _observed(date(year, 1, 1)), _nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3),
        _easter(year) - timedelta(days=2), _last_weekday(year, 5, 0), _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)), _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 10, 0, 2),
        _observed(date(year, 11, 11)), _nth_weekday(year, 11, 3, 4), _observed(date(year, 12, 25)),
    })


def dgs10_release_at(observation: date) -> datetime:
    return datetime.combine(next_weekday(observation), DGS10_RELEASE_UTC, tzinfo=UTC)


def _cutoff(now: datetime, release_at: datetime, *, market_open: datetime | None = None) -> datetime | None:
    candidates = [now + CUTOFF_HORIZON, release_at - SAFETY_MARGIN]
    if market_open is not None:
        candidates.append(market_open - SAFETY_MARGIN)
    cutoff = min(candidates).replace(second=0, microsecond=0)
    return cutoff if now < cutoff < release_at else None


def _latest(snapshots: Mapping[str, MacroSnapshot], indicator: str, before: str) -> float | None:
    snapshot = snapshots.get(indicator)
    if snapshot is None or snapshot.indicator != indicator:
        return None
    prior = [row for row in snapshot.observations if row.period < before]
    return max(prior, key=lambda row: row.period).value if prior else None


def _question(indicator: str, period: date, threshold: float, release_at: datetime, cutoff: datetime,
              release_event: str) -> dict:
    spec = MacroSpec(indicator=indicator, observation_period=period.isoformat(), threshold=threshold,
                     comparison="gt", release_at=release_at)
    return {"macro": spec.model_dump(mode="json"), "cutoff": cutoff.isoformat(), "release_event": release_event}


def propose_fast_questions(now: datetime, snapshots: Mapping[str, MacroSnapshot]) -> list[dict]:
    """Return CohortQuestionIn-compatible dicts for the fixed fast-question rule.

    ``snapshots`` maps ``jobless_claims`` / ``treasury_10y`` to observations
    retrieved at or before ``now``; an indicator without an observation before
    its target period yields no question.
    """
    now = as_utc(now)
    questions: list[dict] = []
    # The most recent Saturday on or before today, then forward week by week.
    saturday = now.date() - timedelta(days=(now.date().weekday() - 5) % 7)
    claims = 0
    while claims < CLAIMS_QUESTIONS:
        release_at = claims_release_at(saturday)
        if release_at > now:
            threshold = _latest(snapshots, "jobless_claims", saturday.isoformat())
            cutoff = _cutoff(now, release_at)
            if threshold is not None and cutoff is not None:
                questions.append(_question("jobless_claims", saturday, threshold, release_at, cutoff,
                                           f"claims-{release_at.astimezone(NEW_YORK).date().isoformat()}"))
            claims += 1
        saturday += timedelta(days=7)
    day = now.date()
    for _ in range(DGS10_QUESTIONS):
        day = next_weekday(day)
        while day in bond_market_holidays(day.year):
            day = next_weekday(day)
        release_at = dgs10_release_at(day)
        threshold = _latest(snapshots, "treasury_10y", day.isoformat())
        cutoff = _cutoff(now, release_at, market_open=datetime.combine(day, MARKET_OPEN_UTC, tzinfo=UTC))
        if threshold is not None and cutoff is not None:
            questions.append(_question("treasury_10y", day, threshold, release_at, cutoff, f"dgs10-{day.isoformat()}"))
    return questions[:COHORT_MAX_QUESTIONS]
