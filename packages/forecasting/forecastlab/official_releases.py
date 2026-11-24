"""Official BLS documents republished by DOL, with current Fed schedule checks."""
from __future__ import annotations

import calendar
import hashlib
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from forecastlab.macro import MacroDataError, MacroSpec
from forecastlab.macro_evidence import MONTHS, parse_release_document, release_pdf_text
from forecastlab.question_selection import ScheduledRelease
from forecastlab.timeutil import as_utc

DOL_INDEX = "https://www.dol.gov/newsroom/economicdata"
PUBLIC_DATA_USER_AGENT = "ForecastLab/1.0 (+https://forecastlab-web.vercel.app; contact: https://github.com/nikoamoretti)"
DOL_PDF_PATTERN = r"https://www\.dol\.gov/newsroom/economicdata/(empsit|cpi)_(\d{8})\.pdf"
LABELS = {"empsit": "Employment Situation", "cpi": "Consumer Price Index"}


def official_correction_evidence_url(url: str) -> bool:
    """True for existing www.bls.gov HTTPS URLs and dated DOL economic-data PDFs."""
    if not isinstance(url, str) or not url or len(url) > 1000 or any(ch.isspace() for ch in url):
        return False
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username is not None or parts.password is not None or parts.port is not None:
        return False
    host = (parts.hostname or "").casefold()
    if host == "www.bls.gov":
        return parts.path.startswith("/")
    if host == "www.dol.gov":
        if parts.query or parts.fragment:
            return False
        return re.fullmatch(DOL_PDF_PATTERN, f"https://www.dol.gov{parts.path}") is not None
    return False


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(urljoin(DOL_INDEX, href))


def dol_release_links(html: str) -> dict[str, str]:
    parser = _Links()
    parser.feed(html)
    found: dict[str, set[str]] = {family: set() for family in LABELS}
    for url in parser.links:
        match = re.fullmatch(DOL_PDF_PATTERN, url)
        if match:
            found[match[1]].add(url)
    if any(len(urls) > 1 for urls in found.values()):
        raise MacroDataError("dol_release_index_ambiguous")
    return {family: next(iter(urls)) for family, urls in found.items() if urls}


def release_announcements(content: bytes, *, source_url: str, checked_at: datetime) -> tuple[ScheduledRelease, ScheduledRelease]:
    match = re.fullmatch(DOL_PDF_PATTERN, source_url)
    if not match:
        raise MacroDataError("official_dated_release_required")
    family, suffix = match.groups()
    text = release_pdf_text(content)
    heading = re.search(r"(?:THE EMPLOYMENT SITUATION|CONSUMER PRICE INDEX)\s*[-–—]\s*(" + MONTHS + r")\s+(\d{4})", text, re.I)
    if not heading:
        raise MacroDataError("release_observation_period_mismatch")
    period = f"{heading[2]}-{list(calendar.month_name).index(heading[1].title()):02d}"
    # Both pilot releases are at 08:30 ET. The dated document must independently
    # confirm this exact timestamp; a filename alone is never publication proof.
    published = datetime.strptime(suffix, "%m%d%Y").replace(hour=8, minute=30, tzinfo=ZoneInfo("America/New_York"))
    spec = MacroSpec(indicator="cpi" if family == "cpi" else "unemployment", observation_period=period,
                     threshold=0, release_at=published)
    parse_release_document(content, spec, source_url=source_url, retrieved_at=checked_at)
    if not timedelta(0) <= as_utc(checked_at) - as_utc(published) <= timedelta(days=75):
        raise MacroDataError("release_announcement_stale")
    pattern = (r"(?:The )?" + LABELS[family] + r"(?: news release)? for (" + MONTHS + r") (\d{4}) "
        r"is scheduled to be (?:published|released) on (\w+), (" + MONTHS + r") (\d{1,2}), (\d{4}), "
        r"at (\d{1,2}):(\d{2}) (a\.m\.|p\.m\.) \(ET\)")
    findings = list(re.finditer(pattern, text, re.I))
    if len(findings) != 1:
        raise MacroDataError("next_release_announcement_missing_or_ambiguous")
    upcoming = findings[0]
    month, year, weekday, release_month, day, release_year, hour, minute, meridiem = upcoming.groups()
    if not 1 <= int(hour) <= 12:
        raise MacroDataError("next_release_announcement_invalid")
    target_period = f"{year}-{list(calendar.month_name).index(month.title()):02d}"
    at = datetime(int(release_year), list(calendar.month_name).index(release_month.title()), int(day),
        int(hour) % 12 + (12 if meridiem.lower() == "p.m." else 0), int(minute), tzinfo=ZoneInfo("America/New_York"))
    if at.strftime("%A").lower() != weekday.lower() or target_period <= period or at <= published:
        raise MacroDataError("next_release_announcement_invalid")
    MacroSpec(indicator=spec.indicator, observation_period=target_period, threshold=0, release_at=at)
    common = {"family": family, "source_url": source_url, "source_hash": hashlib.sha256(content).hexdigest(),
              "checked_at": checked_at}
    return (ScheduledRelease(**common, observation_period=period, release_at=as_utc(published),
                schedule_basis="dol_dated_release_v1", quote=text[:heading.end()]),
            ScheduledRelease(**common, observation_period=target_period, release_at=as_utc(at),
                schedule_basis="dol_fed_schedule_v1", quote=upcoming[0]))


def fed_calendar_url(release: ScheduledRelease) -> str:
    local = as_utc(release.release_at).astimezone(ZoneInfo("America/New_York"))
    return f"https://www.newyorkfed.org/research/calendars/i-{calendar.month_abbr[local.month].lower()}{local.year % 100:02d}.html"


class _Cells(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.stack: list[dict] = []
        self.cells: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            if self.stack:
                self.stack[-1]["nested"] = True
            self.stack.append({"text": [], "links": [], "nested": False})
        elif tag == "a" and self.stack:
            self.stack[-1]["links"].append(dict(attrs).get("href", ""))

    def handle_data(self, data):
        self.text.append(data)
        if self.stack:
            self.stack[-1]["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.stack:
            cell = self.stack.pop()
            if not cell["nested"]:
                self.cells.append((" ".join(" ".join(cell["text"]).split()), cell["links"]))


def verify_fed_schedule(html: str, release: ScheduledRelease, *, source_url: str) -> ScheduledRelease:
    if source_url != fed_calendar_url(release):
        raise MacroDataError("fed_calendar_url_mismatch")
    parser = _Cells()
    parser.feed(html)
    text = " ".join(" ".join(parser.text).split())
    local = as_utc(release.release_at).astimezone(ZoneInfo("America/New_York"))
    month_heading = f"PREVIOUS MONTH {calendar.month_name[local.month]} {local.year} NEXT MONTH"
    if month_heading not in text or "all Eastern Time" not in text:
        raise MacroDataError("fed_calendar_month_or_timezone_missing")
    times = []
    for cell, links in parser.cells:
        if f"https://www.bls.gov/news.release/{release.family}.toc.htm" not in links:
            continue
        day = re.match(r"^(\d{1,2})\s", cell)
        time = re.search(re.escape(LABELS[release.family]) + r"\s*\((\d{2}):(\d{2})\)", cell)
        if day and time:
            times.append(local.replace(day=int(day[1]), hour=int(time[1]), minute=int(time[2])))
    if len(times) != 1 or times[0] != local:
        raise MacroDataError("official_release_schedule_conflict_or_missing")
    return release.model_copy(update={"verification_sources": [{"url": source_url,
        "source_hash": hashlib.sha256(html.encode()).hexdigest(), "checked_at": release.checked_at.isoformat(),
        "role": "current_release_time_confirmation", "source_lineage": "agency:bls"}]})
