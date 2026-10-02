"""Pre-specified fast-resolving question proposals (FRED ICSA and DGS10).

This is a fixed, transparent rule, not an optimized or probability-seeking
selection. Given ``now`` and the latest observed values it proposes:

* Initial jobless claims (ICSA): the next 2 week-ending Saturdays whose advance
  release (the following Thursday, 8:30 America/New_York) is still in the
  future. Threshold = latest observed ICSA value, comparison ``gt``.
* 10-year Treasury yield (DGS10): the next 5 weekdays strictly after ``now``'s
  UTC date, released the next weekday at 21:00 UTC. Threshold = latest observed
  DGS10 value, comparison ``gt``. Holidays are not skipped: a holiday has no
  published value, and its entry should be cancelled (the adjudicator records a
  non-retryable ``fred_initial_release_value_missing`` exception).

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

FAST_SELECTION_VERSION = "fast_fred_question_selection_v1"
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
        release_at = dgs10_release_at(day)
        threshold = _latest(snapshots, "treasury_10y", day.isoformat())
        cutoff = _cutoff(now, release_at, market_open=datetime.combine(day, MARKET_OPEN_UTC, tzinfo=UTC))
        if threshold is not None and cutoff is not None:
            questions.append(_question("treasury_10y", day, threshold, release_at, cutoff, f"dgs10-{day.isoformat()}"))
    return questions[:COHORT_MAX_QUESTIONS]
