"""Official publication documents behind the FRED fast series (ICSA and DGS10).

BLS cohort entries receive a verified release schedule as ``resolution``
evidence. FRED's own pages do not state a schedule for a single observation,
but the originating agencies publish documents that define the measurement
and its publication practice:

* ``jobless_claims`` (ICSA): the U.S. Department of Labor weekly claims news
  release, ``https://www.dol.gov/ui/data.pdf``. Its embargo line gives the
  publication time (8:30 a.m. Eastern, Thursday) and its first sentence names
  the week-ending Saturday whose *advance* seasonally adjusted figure it
  publishes. The next sentence states that the previous week was revised.
* ``treasury_10y`` (DGS10): the Federal Reserve Board H.15 Selected Interest
  Rates release, ``https://www.federalreserve.gov/releases/h15/``. It states
  that it is posted daily Monday through Friday and not on holidays, gives its
  release date, and lists the trading days it covers.

Each parser returns one contiguous quotation taken verbatim from the
whitespace-normalized document text. The documents never state the target
observation's release date. ``verify_release_target`` therefore checks only
that the retained document follows the publication pattern the contract
assumes (advance claims five days after the week ends at 8:30 New York time;
a daily yield on the next weekday), that it is recent, and that the target
lies after the latest published period. Anything else is a named gap.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import Literal

from pydantic import BaseModel, ConfigDict

from forecastlab.fast_questions import NEW_YORK, claims_release_at
from forecastlab.macro import SERIES, MacroDataError, MacroSpec, next_weekday
from forecastlab.macro_evidence import MONTHS, pdf_text
from forecastlab.timeutil import as_utc

FredIndicator = Literal["jobless_claims", "treasury_10y"]
DOCUMENT_URLS: dict[str, str] = {name: SERIES[name]["fallback_url"] for name in ("jobless_claims", "treasury_10y")}
CONTENT_TYPES = {"jobless_claims": "application/pdf", "treasury_10y": "text/html"}
EXTRACTION_METHODS = {"jobless_claims": "dol_weekly_claims_pdf_v1", "treasury_10y": "frb_h15_html_v1"}
TITLES = {"jobless_claims": "U.S. Department of Labor Unemployment Insurance Weekly Claims news release",
          "treasury_10y": "Federal Reserve Board H.15 Selected Interest Rates release"}
# A retained document older than this (New York calendar days) is stale. Claims
# publish weekly; H.15 skips weekends and holidays.
MAX_DOCUMENT_AGE_DAYS = {"jobless_claims": 8, "treasury_10y": 5}
CLAIMS_RELEASE_LAG_DAYS = 5
UNRECOGNIZED = "official_release_document_unrecognized"

_MONTH_NUMBER = {name.lower(): index for index, name in enumerate(calendar.month_name) if name}
_ABBREVIATION = {name.lower(): index for index, name in enumerate(calendar.month_abbr) if name}

_CLAIMS = re.compile(
    r"TRANSMISSION OF MATERIALS IN THIS RELEASE IS EMBARGOED UNTIL (\d{1,2}):(\d{2}) ([AP])\.M\. \(Eastern\) "
    r"(\w+),? (" + MONTHS + r") (\d{1,2}),? (\d{4}) UNEMPLOYMENT INSURANCE WEEKLY CLAIMS SEASONALLY ADJUSTED DATA "
    r"In the week ending (" + MONTHS + r") (\d{1,2}), the advance figure for seasonally adjusted initial claims "
    r"was \d{1,3}(?:,\d{3})*,? [^.]*\.( The previous week[’']s level was [^.]*\.)?", re.I)
_H15 = re.compile(
    r"The release is posted daily Monday through Friday at (\d{1,2}:\d{2} ?(?:[ap]m|[ap]\.m\.))\.? The release is not posted "
    r"on holidays or in the event that the Board is closed\. Release date: (" + MONTHS + r") (\d{1,2}), (\d{4}) "
    r"Selected Interest Rates Yields in percent per annum Instruments((?: \d{4} [A-Z][a-z]{2} \d{1,2})+)")
_H15_COLUMN = re.compile(r"(\d{4}) ([A-Z][a-z]{2}) (\d{1,2})")


class ReleaseDocument(BaseModel):
    """What a retained official document verifiably says about its own publication."""
    model_config = ConfigDict(extra="forbid")
    indicator: FredIndicator
    source_url: str
    release_date: date
    # Claims: the embargo time; H.15 states a posting time without a time zone.
    published_at: datetime | None = None
    posting_time: str | None = None
    latest_period: str
    quote: str
    reports_revision: bool = False


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._hidden:
            self._hidden -= 1

    def handle_data(self, data):
        if not self._hidden:
            self.parts.append(data)


def html_text(content: bytes) -> str:
    """Whitespace-normalized visible text of an HTML document (entities decoded)."""
    if len(content) > 2_000_000:
        raise MacroDataError("official_release_document_too_large")
    try:
        markup = content.decode("utf-8")
    except UnicodeDecodeError:
        raise MacroDataError(UNRECOGNIZED) from None
    parser = _VisibleText()
    parser.feed(markup)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def document_text(indicator: str, content: bytes) -> str:
    if indicator == "jobless_claims":
        return pdf_text(content)
    if indicator == "treasury_10y":
        return html_text(content)
    raise MacroDataError("fred_series_required")


def _date(month: str, day: str, year: int | str) -> date:
    try:
        return date(int(year), _MONTH_NUMBER[month.lower()], int(day))
    except (KeyError, ValueError):
        raise MacroDataError(UNRECOGNIZED) from None


def parse_dol_weekly_claims(content: bytes) -> ReleaseDocument:
    text = pdf_text(content)
    findings = list(_CLAIMS.finditer(text))
    if len(findings) != 1:
        raise MacroDataError(UNRECOGNIZED)
    found = findings[0]
    hour, minute, meridiem, weekday, month, day, year, week_month, week_day, revision = found.groups()
    released = _date(month, day, year)
    if (int(hour), int(minute), meridiem.upper()) != (8, 30, "A") or released.strftime("%A").lower() != weekday.lower():
        raise MacroDataError(UNRECOGNIZED)
    # The week-ending sentence has no year: it is the Saturday shortly before the release.
    candidates = [_date(week_month, week_day, released.year - offset) for offset in (0, 1)]
    week = next((day for day in candidates if timedelta(days=1) <= released - day <= timedelta(days=7)), None)
    if week is None or week.weekday() != 5:
        raise MacroDataError(UNRECOGNIZED)
    published = datetime(released.year, released.month, released.day, 8, 30, tzinfo=NEW_YORK)
    return ReleaseDocument(indicator="jobless_claims", source_url=DOCUMENT_URLS["jobless_claims"],
                           release_date=released, published_at=as_utc(published), latest_period=week.isoformat(),
                           quote=found.group(0), reports_revision=revision is not None)


def parse_h15_release(content: bytes) -> ReleaseDocument:
    text = html_text(content)
    findings = list(_H15.finditer(text))
    if len(findings) != 1:
        raise MacroDataError(UNRECOGNIZED)
    found = findings[0]
    posting_time, month, day, year, header = found.groups()
    released = _date(month, day, year)
    try:
        columns = [date(int(y), _ABBREVIATION[m.lower()], int(d)) for y, m, d in _H15_COLUMN.findall(header)]
    except (KeyError, ValueError):
        raise MacroDataError(UNRECOGNIZED) from None
    if (released.weekday() >= 5 or not columns or columns != sorted(set(columns)) or columns[-1] >= released
            or not re.search(r"Treasury constant maturities Nominal\b.{0,400}?\b10-year \d", text[found.end():found.end() + 4000])):
        raise MacroDataError(UNRECOGNIZED)
    return ReleaseDocument(indicator="treasury_10y", source_url=DOCUMENT_URLS["treasury_10y"], release_date=released,
                           posting_time=posting_time, latest_period=columns[-1].isoformat(), quote=found.group(0))


def parse_release_document(indicator: str, content: bytes) -> ReleaseDocument:
    if indicator == "jobless_claims":
        return parse_dol_weekly_claims(content)
    if indicator == "treasury_10y":
        return parse_h15_release(content)
    raise MacroDataError("fred_series_required")


def verify_release_target(document: ReleaseDocument, spec: MacroSpec, *, checked_at: datetime) -> dict:
    """Check that the contract's assumed publication follows the retained document's own pattern.

    Returns the audit record of what was checked. Raises a ``MacroDataError``
    whose message is the gap code when the document is stale, breaks the
    pattern (for example a holiday-shifted release), or cannot apply to the target.
    """
    if spec.indicator != document.indicator:
        raise MacroDataError("official_release_document_series_mismatch")
    checked = as_utc(checked_at)
    age = (checked.astimezone(NEW_YORK).date() - document.release_date).days
    if age < 0 or (document.published_at is not None and as_utc(document.published_at) > checked):
        raise MacroDataError("official_release_document_date_invalid")
    if age > MAX_DOCUMENT_AGE_DAYS[document.indicator]:
        raise MacroDataError("official_release_document_stale")
    latest = date.fromisoformat(document.latest_period)
    target = date.fromisoformat(spec.observation_period)
    if target <= latest:
        raise MacroDataError("official_release_document_not_before_target")
    if document.indicator == "jobless_claims":
        if (document.published_at is None
                or as_utc(document.published_at) != claims_release_at(latest)
                or (document.release_date - latest).days != CLAIMS_RELEASE_LAG_DAYS):
            raise MacroDataError("official_release_pattern_unverified")
        expected = claims_release_at(target)
        if as_utc(spec.release_at) != expected:
            raise MacroDataError("official_release_time_not_verified")
        rule = ("Advance seasonally adjusted initial claims for a week ending Saturday are published at 8:30 a.m. "
                "Eastern the following Thursday; the retained release follows this pattern.")
    else:
        if next_weekday(latest) != document.release_date:
            raise MacroDataError("official_release_pattern_unverified")
        expected_day = next_weekday(target)
        if as_utc(spec.release_at).astimezone(NEW_YORK).date() != expected_day:
            raise MacroDataError("official_release_time_not_verified")
        rule = ("H.15 is posted on weekdays and covers trading days through the previous business day; the "
                "retained release follows this pattern. Only the release date is checked, not the posting time.")
    return {"basis": "publication_pattern_of_retained_official_document", "rule": rule,
            "retained_release_date": document.release_date.isoformat(),
            "retained_published_at": document.published_at.isoformat() if document.published_at else None,
            "retained_latest_period": document.latest_period, "target_observation_period": spec.observation_period,
            "target_release_at": as_utc(spec.release_at).isoformat(),
            "target_release_stated_in_document": False}


def release_claim(document: ReleaseDocument) -> str:
    """The factual claim, limited to what the quotation states."""
    released = f"{document.release_date:%A}, {document.release_date:%B} {document.release_date.day}, {document.release_date.year}"
    latest = date.fromisoformat(document.latest_period)
    latest_text = f"{latest:%B} {latest.day}, {latest.year}"
    if document.indicator == "jobless_claims":
        revision = ", and revises the previous week's level" if document.reports_revision else ""
        return (f"The U.S. Department of Labor Unemployment Insurance Weekly Claims news release, embargoed until "
                f"8:30 a.m. Eastern on {released}, reports the advance figure for seasonally adjusted initial claims "
                f"for the week ending {latest:%A}, {latest_text}{revision}.")
    return (f"The Federal Reserve Board H.15 Selected Interest Rates release is posted daily Monday through Friday "
            f"at {document.posting_time}, and not on holidays or when the Board is closed; the release dated "
            f"{released} reports selected interest rates for dates through {latest_text}.")
