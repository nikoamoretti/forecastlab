"""Conservative macro evidence and first-release parsing, versioned independently."""
from __future__ import annotations

import calendar
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from forecastlab.macro import SERIES, MacroDataError, MacroSnapshot, MacroSpec, series_id
from forecastlab.root_event import digest
from forecastlab.timeutil import as_utc, parse_datetime

ALIASES = {
    "unemployment": r"unemployment|jobless|LNS14000000|UNRATE",
    "payrolls": r"nonfarm|non-farm|payroll|CES0000000001|PAYEMS",
    "cpi": r"consumer price|CPI|inflation|CUUR0000SA0",
    "jobless_claims": r"initial claims|jobless claims|unemployment insurance claims|ICSA",
    "treasury_10y": r"10-year|ten-year|treasury yield|DGS10",
}
MONTHS = "|".join(calendar.month_name[1:])
FIRST_RELEASE_PARSER_VERSION = "macro_first_release_v1"


def archived_first_release_url(spec: MacroSpec) -> str:
    """The immutable dated BLS identity required for automated adjudication."""
    family = "cpi" if spec.indicator == "cpi" else "empsit"
    suffix = as_utc(spec.release_at).astimezone(ZoneInfo("America/New_York")).strftime("%m%d%Y")
    return f"https://www.bls.gov/news.release/archives/{family}_{suffix}.htm"


def validate_snapshot(snapshot: MacroSnapshot, spec: MacroSpec, cutoff: datetime) -> None:
    meta = SERIES[spec.indicator]
    if snapshot.indicator != spec.indicator or digest(snapshot.raw_payload) != snapshot.raw_hash:
        raise MacroDataError("macro_snapshot_identity_or_hash_mismatch")
    if as_utc(snapshot.retrieved_at) > as_utc(cutoff) or not snapshot.observations:
        raise MacroDataError("macro_snapshot_after_cutoff_or_empty")
    for observation in snapshot.observations:
        if (observation.series_id != series_id(spec.indicator) or observation.units != meta["units"] or
                observation.seasonal_adjustment != meta["adjustment"]):
            raise MacroDataError("macro_measurement_units_or_adjustment_mismatch")
        if observation.period >= spec.observation_period or as_utc(observation.available_at) > as_utc(cutoff):
            raise MacroDataError("macro_observation_period_or_cutoff_mismatch")


def validate_macro_packet(packet: list[dict], spec: MacroSpec, *, cutoff: str | None) -> list[dict]:
    result = []
    for item in packet:
        row = dict(item, schema_version="evidence_assessment_v2")
        text = row.get("quote", "")
        identity = bool(re.search(ALIASES[spec.indicator], text, re.I))
        period = bool(re.search(r"\b(?:19|20)\d{2}(?:-\d{2})?\b|\b(?:" + MONTHS + r")\b", text, re.I))
        measurement = identity and period and bool(re.search(r"\d[\d,.]*\s*(?:percent|%|jobs|thousand|million)|[+-]?\d{1,3}(?:,\d{3})+", text, re.I))
        resolution = bool(re.search(r"release|schedule|revision|methodolog|seasonal|survey|definition", text, re.I))
        allowed = [s for s in row.get("required_sections", []) if (s == "resolution" and resolution) or
                   (s in {"reference_class", "current_conditions"} and measurement)]
        row["required_sections"] = allowed
        if row.get("classification") in {"supporting", "opposing"} and not measurement:
            row["usable"] = False
            row["reason"] = "Claim lacks a relevant measurement and observation period for this event"
        available = parse_datetime(row.get("source_available_at"))
        if cutoff and (available is None or as_utc(available) > as_utc(datetime.fromisoformat(cutoff))):
            row["usable"] = False
            row["reason"] = "Source availability is not established before the frozen forecast cutoff"
        result.append(row)
    return result


class _ReleaseText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.active = True

    def handle_endtag(self, tag):
        if tag == "pre":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def parse_first_release(html: str, spec: MacroSpec, *, source_url: str, retrieved_at: datetime) -> dict:
    """Only the dated headline release can resolve a first-release contract.

    Tables in later releases contain revisions. No current API value, date-only
    timestamp, or model interpretation is accepted as an outcome measurement.
    """
    if spec.revision_policy != "first_release":
        raise MacroDataError("first_release_contract_required")
    family = "cpi" if spec.indicator == "cpi" else "empsit"
    date_suffix = as_utc(spec.release_at).astimezone(ZoneInfo("America/New_York")).strftime("%m%d%Y")
    allowed = {f"https://www.bls.gov/news.release/{family}.nr0.htm",
               f"https://www.bls.gov/news.release/archives/{family}_{date_suffix}.htm"}
    if source_url not in allowed:
        raise MacroDataError("official_dated_release_required")
    parser = _ReleaseText()
    parser.feed(html)
    text = " ".join(" ".join(parser.parts).split())
    return _parse_release_text(text, spec, source_url=source_url, retrieved_at=retrieved_at)


def pdf_text(content: bytes) -> str:
    """Whitespace-normalized text of a small, unencrypted PDF; no publisher identity check."""
    if not content.startswith(b"%PDF-") or len(content) > 2_000_000:
        raise MacroDataError("official_release_pdf_invalid")
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 40:
            raise MacroDataError("official_release_pdf_page_limit")
        return " ".join(" ".join(page.extract_text() or "" for page in reader.pages).split())
    except MacroDataError:
        raise
    except Exception:
        raise MacroDataError("official_release_pdf_extraction_failed") from None


def release_pdf_text(content: bytes) -> str:
    """Extract retained original bytes; never accept an HTML error as a PDF."""
    text = pdf_text(content)
    if "Bureau of Labor Statistics" not in text[:2000] or "embargoed until" not in text[:500]:
        raise MacroDataError("official_release_pdf_identity_missing")
    return text


def parse_release_document(content: bytes, spec: MacroSpec, *, source_url: str, retrieved_at: datetime) -> dict:
    """DOL republishes BLS's dated original PDF; it is the same source lineage."""
    family = "cpi" if spec.indicator == "cpi" else "empsit"
    suffix = as_utc(spec.release_at).astimezone(ZoneInfo("America/New_York")).strftime("%m%d%Y")
    if source_url == f"https://www.dol.gov/newsroom/economicdata/{family}_{suffix}.pdf":
        if spec.revision_policy != "first_release":
            raise MacroDataError("first_release_contract_required")
        result = _parse_release_text(release_pdf_text(content), spec, source_url=source_url, retrieved_at=retrieved_at)
        return {**result, "schema_version": "macro_first_release_v2", "source_lineage": "agency:bls",
            "publisher": "U.S. Department of Labor", "extraction_method": "original_pdf_text_v1",
            "original_source_url": f"https://www.bls.gov/news.release/archives/{family}_{suffix}.htm"}
    if content.startswith(b"%PDF-"):
        raise MacroDataError("official_dated_release_required")
    return parse_first_release(content.decode("utf-8"), spec, source_url=source_url, retrieved_at=retrieved_at)


def _parse_release_text(text: str, spec: MacroSpec, *, source_url: str, retrieved_at: datetime) -> dict:
    family = "cpi" if spec.indicator == "cpi" else "empsit"
    if not text or re.search(r"corrected|correction to|reissued", text[:2000], re.I):
        raise MacroDataError("first_release_unavailable_or_corrected")
    timestamp = re.search(r"(\d{1,2}):(\d{2})\s*(a\.m\.|p\.m\.)\s*\(ET\)\s*\w+,?\s*(" + MONTHS + r")\s+(\d{1,2}),?\s+(\d{4})", text, re.I)
    if not timestamp:
        raise MacroDataError("release_publication_time_unverified")
    hour, minute, meridiem, month, day, year = timestamp.groups()
    published = datetime(int(year), list(calendar.month_name).index(month.title()), int(day),
        int(hour) % 12 + (12 if meridiem.lower().startswith("p") else 0), int(minute), tzinfo=ZoneInfo("America/New_York"))
    if as_utc(published) != as_utc(spec.release_at) or as_utc(retrieved_at) < as_utc(published):
        raise MacroDataError("wrong_release_time_or_future_information")
    target_year, target_month = map(int, spec.observation_period.split("-"))
    title = "CONSUMER PRICE INDEX" if family == "cpi" else "THE EMPLOYMENT SITUATION"
    heading = re.search(re.escape(title) + r"\s*[-–—]\s*" + calendar.month_name[target_month] + r"\s+" + str(target_year), text, re.I)
    if not heading:
        raise MacroDataError("release_observation_period_mismatch")
    headline = text[heading.end():heading.end() + 1800]
    if spec.indicator == "unemployment":
        matches = list(re.finditer(r"(?:the )?unemployment rate\b(?!\s+(?:for|among|of)\b)[^.;]{0,100}?\b(?:at|to)\s+(\d+(?:\.\d+)?)\s+percent", headline, re.I))
        measurements = [Decimal(match.group(1)) for match in matches]
        if any(not 0 <= measurement <= 100 for measurement in measurements):
            raise MacroDataError("headline_measurement_unverified")
    elif spec.indicator == "payrolls":
        matches = list(re.finditer(r"total nonfarm payroll employment\b([^.;]{0,130}?)([+-]?\d{1,3}(?:,\d{3})+|[+-]?\d+)\)?\s*(?:in\s+" + calendar.month_name[target_month] + r"|\))", headline, re.I))
        measurements = [Decimal(match.group(2).replace(",", "")) for match in matches]
        measurements = [-abs(value) if re.search(r"fell|declin|decreas|lost", match.group(1), re.I) else value
                        for match, value in zip(matches, measurements, strict=True)]
    else:
        matches = list(re.finditer(r"over the last 12 months,? the all items index\s+(increased|rose|decreased|fell)\s+(\d+(?:\.\d+)?)\s+percent\s+before seasonal adjustment", headline, re.I))
        measurements = [Decimal(match.group(2)) * (-1 if match.group(1).lower() in {"decreased", "fell"} else 1)
                        for match in matches]
    if not matches:
        raise MacroDataError("headline_measurement_unverified")
    if len(set(measurements)) != 1:
        raise MacroDataError("headline_measurement_conflicting")
    finding = matches[0]
    value = float(measurements[0])
    try:
        measured = Decimal(str(value))
        threshold = Decimal(str(spec.threshold))
    except (InvalidOperation, ValueError) as exc:
        raise MacroDataError("macro_threshold_not_decimal") from exc
    outcome = {"gt": measured > threshold, "ge": measured >= threshold,
               "lt": measured < threshold, "le": measured <= threshold}[spec.comparison]
    return {"schema_version": FIRST_RELEASE_PARSER_VERSION, "indicator": spec.indicator,
        "period": spec.observation_period, "value": value, "units": SERIES[spec.indicator]["units"],
        "seasonal_adjustment": SERIES[spec.indicator]["adjustment"], "publication_time": as_utc(published).isoformat(),
        "retrieved_at": as_utc(retrieved_at).isoformat(), "revision_basis": "official_first_release",
        "source_url": source_url, "quote": finding.group(0), "outcome": int(outcome)}
