"""Deterministic initial-release adjudication for FRED-sourced fast indicators.

Weekly initial jobless claims (ICSA) and the daily 10-year Treasury yield
(DGS10) resolve on their *initial release*: the value for the target
observation in the earliest ALFRED vintage that contains it. Later revisions
(ICSA's advance figure is revised the following week) are never substituted.

ALFRED serves keyless vintage CSVs at
``https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=<ID>&cosd=<start>&coed=<end>&vintage_date=<YYYY-MM-DD>``.
The response is the series "as of" that date, with the value column named
``<ID>_<YYYYMMDD>`` for the requested vintage date. ALFRED silently clamps a
future vintage date to its current date (the header then names a different
date), and it returns the whole history when the requested range is empty, so
both the header and the exact observation row are checked.

The earliest vintage is established as the first date ``V`` whose vintage
contains the observation while the vintage of ``V - 1 day`` does not. Both CSVs
are retained. This module reuses the append-only amendment and outcome
machinery of :mod:`forecastlab_api.official_macro_outcomes`; BLS adjudication
is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from forecastlab.http_client import MAX_BYTES, SafeResponse
from forecastlab.macro import SERIES, MacroDataError, parse_fred_csv
from forecastlab.official_releases import PUBLIC_DATA_USER_AGENT
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.artifact_store import put_bytes
from forecastlab_api.autopilot_models import OfficialMacroOutcomeAmendment
from forecastlab_api.official_macro_outcomes import (
    FrozenMacroTarget,
    OfficialMacroOutcomeError,
    _append_amendment,
    _begin_immediate,
    _comparison,
    _exception_code,
    _validate_frozen_contract,
)

POLICY_VERSION = "official_fred_initial_release_v1"
PARSER_VERSION = "alfred_initial_vintage_csv_v1"
SYSTEM_ATTRIBUTION = f"system:{POLICY_VERSION}"
ALFRED_GRAPH_CSV = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
NOT_READY = "fred_initial_release_not_yet_published"
UNAVAILABLE = "official_fred_vintage_unavailable"
# A target still absent this long after its observation date is not "late",
# it is missing; stop retrying and leave the question for owner cancellation.
MAX_SEARCH_DAYS = 21
RANGE_DAYS = 14
EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class VintageCheck:
    vintage_date: date
    url: str
    response: SafeResponse
    value: str | None  # None: observation row absent from this vintage.


def alfred_vintage_url(series: str, observation: date, vintage: date) -> str:
    # A non-empty range ending on the target avoids ALFRED's full-history fallback.
    start = observation - timedelta(days=RANGE_DAYS)
    return (f"{ALFRED_GRAPH_CSV}?id={series}&cosd={start.isoformat()}&coed={observation.isoformat()}"
            f"&vintage_date={vintage.isoformat()}")


def fetch_vintage_csv(url: str) -> SafeResponse:
    """Keyless GET with no redirects and a byte ceiling. Tests replace this."""
    with httpx.Client(timeout=8, follow_redirects=False, headers={"User-Agent": PUBLIC_DATA_USER_AGENT}) as client:
        with client.stream("GET", url) as response:
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_BYTES:
                    raise OfficialMacroOutcomeError("official_fred_vintage_too_large")
                chunks.append(chunk)
            return SafeResponse(url=url, final_url=str(response.url), status_code=response.status_code,
                                content=b"".join(chunks), content_type=response.headers.get("content-type", ""),
                                bytes_read=total)


def _check_vintage(series: str, observation: date, vintage: date) -> VintageCheck | None:
    """Return the vintage's view of the observation, or None if ALFRED clamped the date."""
    url = alfred_vintage_url(series, observation, vintage)
    try:
        response = fetch_vintage_csv(url)
    except OfficialMacroOutcomeError:
        raise
    except Exception:
        raise OfficialMacroOutcomeError(UNAVAILABLE) from None
    kind = response.content_type.lower().split(";", 1)[0].strip()
    if response.status_code != 200 or response.final_url != url or kind not in {"application/csv", "text/csv", "text/plain"}:
        raise OfficialMacroOutcomeError(UNAVAILABLE)
    try:
        text = response.content.decode("utf-8")
        header = text.lstrip("\ufeff").split("\n", 1)[0].split(",")
    except UnicodeDecodeError:
        raise OfficialMacroOutcomeError("fred_vintage_csv_malformed") from None
    expected = f"{series}_{vintage.strftime('%Y%m%d')}"
    if len(header) == 2 and header[1].strip() != expected and header[1].strip().startswith(series + "_"):
        # Requested vintage is after ALFRED's current date: not published yet.
        return None
    try:
        rows = parse_fred_csv(text, expected)
    except MacroDataError:
        raise OfficialMacroOutcomeError("fred_vintage_csv_malformed") from None
    values = [value for day, value in rows if day == observation.isoformat()]
    if len(values) > 1:
        raise OfficialMacroOutcomeError("fred_vintage_csv_malformed")
    return VintageCheck(vintage_date=vintage, url=url, response=response, value=values[0] if values else None)


def find_initial_release(series: str, observation: date, *, today: date) -> tuple[VintageCheck, VintageCheck]:
    """Return (first vintage containing the observation, the preceding vintage without it).

    Raises the retryable ``NOT_READY`` code when no vintage yet contains it.
    """
    latest = min(today, observation + timedelta(days=MAX_SEARCH_DAYS))
    if latest <= observation:
        raise OfficialMacroOutcomeError(NOT_READY)
    newest = _check_vintage(series, observation, latest)
    if newest is None and latest - timedelta(days=1) > observation:
        # ALFRED's current date can trail UTC by a few hours.
        latest -= timedelta(days=1)
        newest = _check_vintage(series, observation, latest)
    if newest is None or newest.value is None:
        if newest is not None and latest >= observation + timedelta(days=MAX_SEARCH_DAYS):
            raise OfficialMacroOutcomeError("fred_initial_release_not_found_within_search_window")
        raise OfficialMacroOutcomeError(NOT_READY)
    first = newest
    day = latest - timedelta(days=1)
    while day >= observation:
        check = _check_vintage(series, observation, day)
        if check is None:
            raise OfficialMacroOutcomeError("fred_vintage_date_inconsistent")
        if check.value is None:
            return first, check
        first = check
        day -= timedelta(days=1)
    # A vintage dated on or before the observation date cannot contain it.
    raise OfficialMacroOutcomeError("fred_vintage_predates_observation")


def _source_identity(target: FrozenMacroTarget) -> dict[str, str]:
    meta = SERIES[target.macro.indicator]
    return {
        "publisher": "Federal Reserve Bank of St. Louis (ALFRED)",
        "original_publisher": meta["publisher"],
        "indicator": target.macro.indicator,
        "series_id": meta["fred"],
        "observation_period": target.macro.observation_period,
        "release_at": as_utc(target.macro.release_at).isoformat(),
        "revision_policy": target.macro.revision_policy,
        "source_kind": "alfred_initial_release_vintage_csv",
        "source_url": f"https://alfred.stlouisfed.org/series?seid={meta['fred']}",
    }


def _outcome_known_at(target: FrozenMacroTarget, vintage: date) -> datetime:
    """ALFRED vintages are dated, not timed.

    When the first vintage is the scheduled release date, the scheduled release
    time is used; otherwise the start of the vintage date in New York.
    """
    release = as_utc(target.macro.release_at)
    if release.astimezone(EASTERN).date() == vintage:
        return release
    return datetime.combine(vintage, time.min, tzinfo=EASTERN).astimezone(UTC)


def retain_official_fred_amendment(session: Session, target: FrozenMacroTarget) -> OfficialMacroOutcomeAmendment:
    """Fetch ALFRED vintages and store one append-only amendment (ready or exception)."""
    _begin_immediate(session)
    identity = _source_identity(target)
    versions = {"policy_version": POLICY_VERSION, "parser_version": PARSER_VERSION}
    try:
        meta = SERIES[target.macro.indicator]
        if meta["source"] != "fred":
            raise OfficialMacroOutcomeError("fred_series_required")
        _validate_frozen_contract(target)
        observation = date.fromisoformat(target.macro.observation_period)
        first, prior = find_initial_release(meta["fred"], observation, today=utcnow().date())
        retrieved = utcnow()
        if first.value in {"", "."}:
            # Published as missing (e.g. a market holiday): no measurement exists.
            raise OfficialMacroOutcomeError("fred_initial_release_value_missing")
        assert first.value is not None
        outcome = _comparison(target.macro, first.value)
        artifact = put_bytes(first.response.content, content_type="text/csv", prefix="official-fred-outcomes")
        prior_artifact = put_bytes(prior.response.content, content_type="text/csv", prefix="official-fred-outcomes")
        known_at = _outcome_known_at(target, first.vintage_date)
        source = {**identity, "source_url": first.url, "vintage_date": first.vintage_date.isoformat()}
        measurement = {
            "schema_version": PARSER_VERSION, "parser_version": PARSER_VERSION, "indicator": target.macro.indicator,
            "series_id": meta["fred"], "period": target.macro.observation_period, "value": float(first.value),
            "value_decimal": str(Decimal(first.value)), "units": meta["units"], "seasonal_adjustment": meta["adjustment"],
            "revision_basis": "alfred_initial_release_vintage", "first_vintage_date": first.vintage_date.isoformat(),
            "first_vintage_url": first.url, "prior_vintage_date": prior.vintage_date.isoformat(),
            "prior_vintage_url": prior.url, "prior_vintage_artifact": prior_artifact,
            "publication_time": known_at.isoformat(), "retrieved_at": as_utc(retrieved).isoformat(),
            "source_url": first.url, "outcome": outcome,
        }
        return _append_amendment(session, target, status="ready", source_url=first.url, source_identity=source,
            artifact=artifact, source_sha256=artifact["sha256"], measurement=measurement, outcome=outcome,
            outcome_known_at=known_at, retrieved_at=retrieved, **versions)
    except Exception as exc:
        return _append_amendment(session, target, status="exception", source_url=identity["source_url"],
            source_identity=identity, exception_code=_exception_code(exc), **versions)
