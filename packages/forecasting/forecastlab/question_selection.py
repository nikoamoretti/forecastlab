"""Deterministic, source-backed question selection. No forecasting/model calls."""
from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from forecastlab.macro import (
    SERIES,
    MacroDataError,
    MacroObservation,
    MacroSnapshot,
    MacroSpec,
    bls_indicators,
    published_level_periods,
)
from forecastlab.root_event import digest
from forecastlab.timeutil import as_utc

SELECTION_VERSION = "macro_question_selection_v2"
HORIZON_DAYS = 90


class ScheduledRelease(BaseModel):
    family: Literal["empsit", "cpi"]
    observation_period: str
    release_at: datetime
    source_url: str
    source_hash: str
    checked_at: datetime
    schedule_basis: str = "bls_calendar_v1"
    quote: str = ""
    verification_sources: list[dict] = Field(default_factory=list)

    @property
    def event_id(self) -> str:
        # A schedule change does not create an independent release event.
        return f"bls:{self.family}:{self.observation_period}"


class QuestionSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    selection_version: str = SELECTION_VERSION
    question: str
    macro: MacroSpec
    reason: str
    baseline: MacroObservation
    observation_source_hash: str
    schedule: ScheduledRelease
    release_event: str
    related_question_ids: list[str]
    existing_question_id: str | None = None
    existing_draft_run_id: str | None = None


class _CalendarRows(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] = []
        self.cell: list[str] | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag == "td":
            self.cell = []

    def handle_data(self, data):
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            self.row.append(" ".join(" ".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row:
            self.rows.append(self.row)
            self.row = []


def parse_bls_calendar(html: str, *, source_url: str, checked_at: datetime) -> list[ScheduledRelease]:
    """Read explicit reference periods, dates and Eastern times from the annual calendar.

    Do not infer a reference month by subtracting one from a release date: delayed
    releases make that wrong. Unknown layouts/times fail closed.
    """
    parser = _CalendarRows()
    parser.feed(html)
    if "All times on calendar are Eastern Time" not in " ".join(" ".join(parser.text).split()):
        raise MacroDataError("calendar_timezone_evidence_missing")
    releases: dict[tuple[str, str], ScheduledRelease] = {}
    for row in parser.rows:
        if len(row) != 3:
            continue
        match = re.fullmatch(r"(Employment Situation|Consumer Price Index) for ([A-Za-z]+ \d{4})", row[2])
        if not match:
            continue
        try:
            period = datetime.strptime(match[2], "%B %Y").strftime("%Y-%m")
            local = datetime.strptime(f"{row[0]} {row[1]}", "%A, %B %d, %Y %I:%M %p")
            release_at = local.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)
            family = "empsit" if match[1] == "Employment Situation" else "cpi"
            # Reuse contract validation for impossible reference/release pairs.
            MacroSpec(indicator="unemployment" if family == "empsit" else "cpi",
                      observation_period=period, threshold=0, release_at=release_at)
            release = ScheduledRelease(family=family, observation_period=period, release_at=release_at,
                source_url=source_url, source_hash=digest(html), checked_at=checked_at)
            key = (family, period)
            if key in releases and releases[key].release_at != release_at:
                raise MacroDataError("calendar_conflicting_release_dates")
            releases[key] = release
        except ValueError as exc:
            raise MacroDataError("calendar_release_invalid") from exc
    if not releases:
        raise MacroDataError("calendar_reference_periods_missing")
    return sorted(releases.values(), key=lambda row: (row.release_at, row.family))


def event_key(spec: MacroSpec) -> tuple[str, str]:
    if spec.indicator not in bls_indicators():
        # Weekly/daily FRED observations are their own release events.
        return (spec.indicator, spec.observation_period)
    return ("cpi" if spec.indicator == "cpi" else "empsit", spec.observation_period)


def same_question(left: MacroSpec, right: MacroSpec) -> bool:
    # Identity survives rescheduling. Existing approved contracts are not edited.
    return left.model_dump(exclude={"release_at"}) == right.model_dump(exclude={"release_at"})


def choose_questions(releases: list[ScheduledRelease], snapshots: dict[str, MacroSnapshot], *, now: datetime,
                     tracked: list[tuple[str, str | None, MacroSpec]] | None = None,
                     cohort_questions: list[tuple[str, MacroSpec]] | None = None) -> tuple[list[QuestionSuggestion], list[str]]:
    """One upcoming event per indicator, ordered by deadline; threshold = latest observation.

    This is a transparent selection rule, not an estimated probability or an
    assertion that the chosen questions maximize information or accuracy.
    """
    now = as_utc(now)
    tracked = tracked or []
    suggestions = []
    gaps = []
    for indicator in bls_indicators():
        meta = SERIES[indicator]
        family = "cpi" if indicator == "cpi" else "empsit"
        upcoming = sorted((r for r in releases if r.family == family and
                           now + timedelta(minutes=10) < as_utc(r.release_at) <= now + timedelta(days=HORIZON_DAYS)),
                          key=lambda r: r.release_at)
        if not upcoming:
            gaps.append(f"{indicator}: No verified release in the next {HORIZON_DAYS} days with time to forecast.")
            continue
        release = upcoming[0]
        snapshot = snapshots.get(indicator)
        if snapshot is None or not snapshot.observations:
            gaps.append(f"{indicator}: Recent BLS observations are unavailable.")
            continue
        if not timedelta(0) <= now - as_utc(snapshot.retrieved_at) <= timedelta(hours=24):
            gaps.append(f"{indicator}: Observations need a fresh source check.")
            continue
        latest = max(snapshot.observations, key=lambda row: row.period)
        if (snapshot.indicator != indicator or latest.series_id != meta["bls"] or latest.units != meta["units"] or
                latest.seasonal_adjustment != meta["adjustment"] or as_utc(latest.available_at) > now):
            gaps.append(f"{indicator}: Observation identity, units, or availability could not be verified.")
            continue
        latest_date = date.fromisoformat(latest.period + "-01")
        month_end = latest_date.replace(day=calendar.monthrange(latest_date.year, latest_date.month)[1])
        published_periods = [r.observation_period for r in releases
                             if r.family == family and as_utc(r.release_at) <= as_utc(snapshot.retrieved_at)]
        if (latest.period >= release.observation_period or (now.date() - month_end).days > 75 or
                (published_periods and latest.period < max(published_periods))):
            gaps.append(f"{indicator}: The latest scheduled observation is not yet available in the data.")
            continue
        if indicator == "cpi":
            # An absent prior-year index makes this specific YoY event unsuitable.
            prior_year = f"{int(release.observation_period[:4]) - 1}{release.observation_period[4:]}"
            # The retained payload is either the BLS response or its FRED mirror CSV.
            if prior_year not in published_level_periods(snapshot):
                gaps.append("cpi: The target month's prior-year index is missing; a year-over-year question is not selectable.")
                continue
        spec = MacroSpec(indicator=indicator, observation_period=release.observation_period,
                         threshold=latest.value, release_at=release.release_at)
        related = sorted({question_id for question_id, existing in
                          [(qid, existing) for qid, _, existing in tracked] + (cohort_questions or [])
                          if event_key(existing) == event_key(spec)})
        exact = next(((question_id, draft_id) for question_id, draft_id, existing in tracked if same_question(spec, existing)), None)
        readable_units = meta["units"].replace("_", " ")
        suggestions.append(QuestionSuggestion(
            id=digest({"version": SELECTION_VERSION, "macro": spec.model_dump(mode="json")}),
            question=spec.template("selection", "selection").normalized_question, macro=spec,
            reason=f"Next scheduled {meta['label']} release. The threshold is the latest observed value: "
                   f"{latest.value:g} {readable_units} for {latest.period}. This asks whether the next reading will be higher.",
            baseline=latest, observation_source_hash=snapshot.raw_hash,
            schedule=release, release_event=release.event_id, related_question_ids=related,
            existing_question_id=exact[0] if exact else None, existing_draft_run_id=exact[1] if exact else None,
        ))
    return sorted(suggestions, key=lambda s: (s.existing_question_id is not None, s.macro.release_at, s.macro.indicator)), gaps
