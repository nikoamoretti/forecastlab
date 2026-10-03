"""Small, typed BLS/FRED/ALFRED boundary. No present-day data in historical calls."""
from __future__ import annotations

import calendar
import csv
import io
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from forecastlab.root_event import digest
from forecastlab.schemas import ForecastContract
from forecastlab.timeutil import as_utc, utcnow

# ``source`` selects the live adapter and outcome adjudicator, ``cadence`` the
# observation-period format, and ``transform`` the deterministic normalization.
# BLS entries keep their original keys and values; the FRED ``fred`` id of a BLS
# series is only used for historical ALFRED vintages.
SERIES = {
    "unemployment": {"bls": "LNS14000000", "fred": "UNRATE", "units": "percent", "adjustment": "seasonally_adjusted", "label": "U.S. unemployment rate",
                     "source": "bls", "cadence": "monthly", "transform": "level", "lineage": "agency:bls"},
    "payrolls": {"bls": "CES0000000001", "fred": "PAYEMS", "units": "jobs", "adjustment": "seasonally_adjusted", "label": "U.S. nonfarm payroll monthly change",
                 "source": "bls", "cadence": "monthly", "transform": "change_thousands", "lineage": "agency:bls"},
    "cpi": {"bls": "CUUR0000SA0", "fred": "CPIAUCNS", "units": "percent_year_over_year", "adjustment": "not_seasonally_adjusted", "label": "U.S. CPI year-over-year inflation",
            "source": "bls", "cadence": "monthly", "transform": "yoy_percent", "lineage": "agency:bls"},
    "jobless_claims": {"fred": "ICSA", "units": "claims", "adjustment": "seasonally_adjusted", "label": "U.S. initial jobless claims",
                       "source": "fred", "cadence": "weekly", "transform": "level", "lineage": "agency:dol",
                       "publisher": "U.S. Department of Labor", "fallback_url": "https://www.dol.gov/ui/data.pdf"},
    "treasury_10y": {"fred": "DGS10", "units": "percent", "adjustment": "not_seasonally_adjusted", "label": "U.S. 10-year Treasury constant-maturity yield",
                     "source": "fred", "cadence": "daily", "transform": "level", "lineage": "agency:frb",
                     "publisher": "Board of Governors of the Federal Reserve System", "fallback_url": "https://www.federalreserve.gov/releases/h15/"},
}
FRED_GRAPH_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
# Weekly/daily history is capped so the root evidence packet stays well inside
# the 24,000-token context reserved for each root estimate.
FRED_HISTORY_LIMITS = {"weekly": 104, "daily": 130}
FRED_STALENESS_DAYS = {"weekly": 14, "daily": 7}
_FRED_LOOKBACK_DAYS = {"weekly": 7 * 110, "daily": 200}
_FRED_VALUE = re.compile(r"^-?\d+(?:\.\d+)?$")


def bls_indicators() -> tuple[str, ...]:
    """The three monthly BLS indicators used by question selection and Autopilot."""
    return tuple(name for name, meta in SERIES.items() if meta["source"] == "bls")


def series_id(indicator: str) -> str:
    """The identifier of the series in the indicator's own live source."""
    meta = SERIES[indicator]
    return meta["bls"] if meta["source"] == "bls" else meta["fred"]


def next_weekday(day: date) -> date:
    following = day + timedelta(days=1)
    while following.weekday() >= 5:
        following += timedelta(days=1)
    return following


class MacroSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    indicator: Literal["unemployment", "payrolls", "cpi", "jobless_claims", "treasury_10y"]
    # YYYY-MM for monthly series; YYYY-MM-DD for weekly (week-ending Saturday)
    # and daily (weekday) series. The cadence check is in the model validator.
    observation_period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?$")
    threshold: float
    comparison: Literal["gt", "ge", "lt", "le"] = "gt"
    release_at: datetime
    revision_policy: Literal["first_release", "as_of_resolution"] = "first_release"

    @field_validator("release_at")
    @classmethod
    def aware_release(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("release_timezone_required")
        return as_utc(value)

    @model_validator(mode="after")
    def period_precedes_release(self):
        cadence = SERIES[self.indicator]["cadence"]
        if cadence == "monthly":
            if len(self.observation_period) != 7:
                raise ValueError("monthly_observation_period_must_be_year_month")
            year, month = map(int, self.observation_period.split("-"))
            if date(year, month, calendar.monthrange(year, month)[1]) >= self.release_at.date():
                raise ValueError("observation_period_must_precede_release")
            return self
        if len(self.observation_period) != 10:
            raise ValueError("observation_period_must_be_iso_date")
        try:
            observed = date.fromisoformat(self.observation_period)
        except ValueError:
            raise ValueError("observation_period_invalid_date") from None
        if cadence == "weekly" and observed.weekday() != 5:
            raise ValueError("weekly_observation_period_must_be_week_ending_saturday")
        if cadence == "daily" and observed.weekday() >= 5:
            raise ValueError("daily_observation_period_must_be_weekday")
        if observed >= self.release_at.date():
            raise ValueError("observation_period_must_precede_release")
        if cadence == "daily" and self.release_at.date() < next_weekday(observed):
            # A daily yield is posted after its trading day, usually the next business day.
            raise ValueError("observation_period_must_precede_release")
        return self

    def template(self, question_id: str, contract_id: str) -> ForecastContract:
        series = SERIES[self.indicator]
        if series["source"] == "fred":
            return self._fred_template(question_id, contract_id)
        op = {"gt": "greater than", "ge": "at least", "lt": "less than", "le": "at most"}[self.comparison]
        rule = "first published value" if self.revision_policy == "first_release" else "latest value available at the resolution time"
        release_name = "cpi" if self.indicator == "cpi" else "empsit"
        question = f"Will the {series['label']} for {self.observation_period} be {op} {self.threshold:g} {series['units'].replace('_', ' ')} in the {self.release_at.date()} release?"
        return ForecastContract(
            id=contract_id, question_id=question_id, created_at=utcnow(), created_by="macro_template_v1",
            original_question=question, normalized_question=question,
            yes_condition=f"The {rule} of {series['label']} for {self.observation_period} is {op} {self.threshold:g} {series['units']}.",
            no_condition="The specified published value does not satisfy the yes condition.",
            resolution_date=self.release_at, authoritative_source=f"https://www.bls.gov/news.release/{release_name}.nr0.htm",
            fallback_sources=[f"https://www.bls.gov/bls/news-release/{release_name}.htm"],
            resolution_method=f"Use BLS series {series['bls']} ({series['adjustment']}) and the {rule}. Payroll changes use the published change in jobs; CPI uses the published 12-month percent change. Retain the dated release table as outcome evidence. Do not substitute later revisions for a first release.",
            cancellation_conditions="Cancel if the specified release is withdrawn or the measurement cannot be established from an official dated record.",
            domain="us_macro", geography="US", units=series["units"],
            initial_reference_class=f"Historical monthly observations of {series['label']}",
            suggested_drivers=["Recent monthly observations", "Trend and revision uncertainty"],
        )

    def _fred_template(self, question_id: str, contract_id: str) -> ForecastContract:
        series = SERIES[self.indicator]
        fred_id = series["fred"]
        weekly = series["cadence"] == "weekly"
        op = {"gt": "greater than", "ge": "at least", "lt": "less than", "le": "at most"}[self.comparison]
        rule = "first published value" if self.revision_policy == "first_release" else "latest value available at the resolution time"
        units = series["units"].replace("_", " ")
        period = f"the week ending {self.observation_period}" if weekly else self.observation_period
        name = f"{series['label']} ({series['adjustment'].replace('_', ' ')}, FRED series {fred_id})"
        threshold = f"{int(self.threshold):,}" if self.threshold.is_integer() else f"{self.threshold:g}"
        question = (f"Will the {rule} of {name} for {period} be {op} {threshold} {units} "
                    f"in the release expected on {self.release_at.date()}?")
        revision_note = ("The DOL advance figure is revised the following week; that revision is not used. "
                         if weekly else "")
        return ForecastContract(
            id=contract_id, question_id=question_id, created_at=utcnow(), created_by="macro_template_fred_v1",
            original_question=question, normalized_question=question,
            yes_condition=f"The {rule} of {name} for {period} is {op} {threshold} {units}.",
            no_condition="The specified published value does not satisfy the yes condition.",
            resolution_date=self.release_at, authoritative_source=f"https://fred.stlouisfed.org/series/{fred_id}",
            fallback_sources=[f"https://alfred.stlouisfed.org/series?seid={fred_id}", series["fallback_url"]],
            resolution_method=(f"Use FRED series {fred_id} ({series['adjustment']}, {series['cadence']}, units {series['units']}), "
                f"originally published by the {series['publisher']}, and the {rule}. The initial-release value is the "
                f"value for {self.observation_period} in the earliest ALFRED vintage of {fred_id} that contains that "
                f"observation. Retain the dated ALFRED vintage CSV and its vintage date as outcome evidence. "
                f"{revision_note}Do not substitute later revisions for a first release."),
            cancellation_conditions=("Cancel if no value is published for the specified observation period (for example "
                "a market or federal holiday), the release is withdrawn, or the measurement cannot be established from "
                "an official dated record."),
            domain="us_macro", geography="US", units=series["units"],
            initial_reference_class=f"Historical {series['cadence']} observations of {series['label']}",
            suggested_drivers=[f"Recent {series['cadence']} observations",
                               "Revision uncertainty" if weekly else "Scheduled data releases and monetary-policy communications"],
        )


class MacroObservation(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    series_id: str
    period: str
    value: float
    units: str
    seasonal_adjustment: str
    source_url: str
    available_at: datetime
    vintage: str
    revision_basis: str
    footnotes: list[str] = Field(default_factory=list)


class MacroSnapshot(BaseModel):
    indicator: str
    retrieved_at: datetime
    observations: list[MacroObservation]
    raw_hash: str
    raw_payload: dict
    source_lineage: str = "agency:bls"


class MacroDataError(ValueError):
    pass


def _previous_period(period: str, months: int) -> str:
    year, month = map(int, period.split("-"))
    index = year * 12 + month - 1 - months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def normalize_observations(indicator: str, values: dict[str, float], *, available_at: datetime,
                           vintage: str, source_url: str, revision_basis: str,
                           footnotes: dict[str, list[str]] | None = None) -> list[MacroObservation]:
    meta = SERIES[indicator]
    transform = meta["transform"]
    output = []
    for period, value in sorted(values.items()):
        if transform == "change_thousands":
            prior = values.get(_previous_period(period, 1))
            if prior is None:
                continue
            value = round((value - prior) * 1000)
        elif transform == "yoy_percent":
            prior = values.get(_previous_period(period, 12))
            if prior is None or prior <= 0:
                continue
            value = round((value / prior - 1) * 100, 1)
        elif transform != "level":
            raise MacroDataError("macro_transform_unknown")
        output.append(MacroObservation(
            series_id=series_id(indicator), period=period, value=value, units=meta["units"],
            seasonal_adjustment=meta["adjustment"], source_url=source_url,
            available_at=available_at, vintage=vintage, revision_basis=revision_basis,
            footnotes=(footnotes or {}).get(period, []),
        ))
    return output


def fetch_latest_macro_snapshots(*, client: httpx.Client | None = None, history_years: int = 3) -> dict[str, MacroSnapshot]:
    """One keyless BLS request for question selection, covering all three series.

    The short history is only a threshold anchor. Research fetches its own full
    history after approval; these observations never substitute for vintage data.
    """
    owned = client is None
    http = client or httpx.Client(timeout=12, follow_redirects=False)
    now = utcnow()
    try:
        response = http.post("https://api.bls.gov/publicAPI/v1/timeseries/data/", json={
            "seriesid": [SERIES[name]["bls"] for name in bls_indicators()],
            "startyear": str(now.year - min(10, max(3, history_years)) + 1), "endyear": str(now.year)})
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "REQUEST_SUCCEEDED":
            raise MacroDataError("bls_request_not_succeeded")
        series = {row["seriesID"]: row["data"] for row in payload["Results"]["series"]}
        if set(series) != {SERIES[name]["bls"] for name in bls_indicators()}:
            raise MacroDataError("macro_series_mismatch")
        retrieved = utcnow()
        snapshots = {}
        for indicator in bls_indicators():
            meta = SERIES[indicator]
            values = {}
            notes = {}
            for row in series[meta["bls"]]:
                period = row["period"]
                if not period.startswith("M") or not 1 <= int(period[1:]) <= 12:
                    continue
                if str(row["value"]).strip() in {"-", ".", ""}:
                    continue
                key = f"{row['year']}-{int(period[1:]):02d}"
                values[key] = float(row["value"])
                notes[key] = [n["text"] for n in row.get("footnotes", []) if n.get("text")]
            observations = normalize_observations(indicator, values, available_at=retrieved,
                vintage=retrieved.isoformat(), source_url=f"https://data.bls.gov/timeseries/{meta['bls']}",
                revision_basis="latest_observed_revisions_not_first_release", footnotes=notes)
            snapshots[indicator] = MacroSnapshot(indicator=indicator, retrieved_at=retrieved, observations=observations,
                raw_payload=payload, raw_hash=digest(payload))
        return snapshots
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, MacroDataError):
            raise
        raise MacroDataError(f"macro_request_failed:{type(exc).__name__}") from None
    finally:
        if owned:
            http.close()


def fetch_macro(spec: MacroSpec, *, as_of: datetime | None = None, fred_api_key: str | None = None,
                client: httpx.Client | None = None, timeout: float = 15,
                fred_history_limit: int | None = None, fred_lookback_days: int | None = None) -> MacroSnapshot:
    """Historical mode deliberately excludes the cutoff date absent intraday proof.

    ``fred_history_limit`` and ``fred_lookback_days`` only widen the weekly/daily
    FRED history for callers that need it (the statistical baseline). The
    defaults keep the capped history that bounds the root evidence packet.
    """
    owned = client is None
    http = client or httpx.Client(timeout=min(15, timeout), follow_redirects=False)
    now = utcnow()
    meta = SERIES[spec.indicator]
    try:
        if meta["source"] == "fred":
            if as_of is not None:
                # Weekly/daily ALFRED backtests need intraday publication evidence
                # that this adapter does not have. Fail closed instead of guessing.
                raise MacroDataError("historical_fred_series_not_supported")
            return fetch_fred_snapshot(spec.indicator, before=date.fromisoformat(spec.observation_period),
                                       client=http, now=now, history_limit=fred_history_limit,
                                       lookback_days=fred_lookback_days)
        if as_of is not None:
            if not fred_api_key:
                raise MacroDataError("historical_macro_vintage_key_required")
            cutoff = as_utc(as_of)
            if cutoff.timetz().replace(tzinfo=None) != time.min:
                raise MacroDataError("historical_intraday_publication_time_evidence_required")
            vintage_date = cutoff.date() - timedelta(days=1)
            vintage = vintage_date.isoformat()
            available = datetime.combine(vintage_date, time.max, tzinfo=UTC)
            response = http.get("https://api.stlouisfed.org/fred/series/observations", params={
                "api_key": fred_api_key, "file_type": "json", "series_id": meta["fred"],
                "realtime_start": vintage, "realtime_end": vintage,
                "observation_start": f"{cutoff.year - 10}-01-01", "observation_end": vintage,
            })
            response.raise_for_status()
            payload = response.json()
            if any(row.get("realtime_start", vintage) > vintage for row in payload["observations"]):
                raise MacroDataError("macro_vintage_after_cutoff")
            values = {row["date"][:7]: float(row["value"]) for row in payload["observations"] if row["value"] != "."}
            source = f"https://alfred.stlouisfed.org/series?seid={meta['fred']}&vintage_date={vintage}"
            basis = "alfred_previous_day_vintage"
            notes: dict[str, list[str]] = {}
        else:
            response = http.post("https://api.bls.gov/publicAPI/v1/timeseries/data/", json={
                "seriesid": [meta["bls"]], "startyear": str(now.year - 9), "endyear": str(now.year)})
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "REQUEST_SUCCEEDED":
                raise MacroDataError("bls_request_not_succeeded")
            rows = payload["Results"]["series"][0]
            if rows["seriesID"] != meta["bls"]:
                raise MacroDataError("macro_series_mismatch")
            values = {}
            notes = {}
            for row in rows["data"]:
                if row["period"].startswith("M") and 1 <= int(row["period"][1:]) <= 12:
                    period = f"{row['year']}-{int(row['period'][1:]):02d}"
                    # BLS represents unavailable observations (including the
                    # October 2025 shutdown gap) with a hyphen. Retain the raw
                    # row, never impute it or bridge it when computing changes.
                    if str(row["value"]).strip() in {"-", ".", ""}:
                        continue
                    values[period] = float(row["value"])
                    notes[period] = [note["text"] for note in row.get("footnotes", []) if note.get("text")]
            available = utcnow()
            vintage = available.isoformat()
            source = f"https://data.bls.gov/timeseries/{meta['bls']}"
            basis = "latest_observed_revisions_not_first_release"
        observations = normalize_observations(spec.indicator, values, available_at=available, vintage=vintage,
                                             source_url=source, revision_basis=basis, footnotes=notes)
        if not observations:
            raise MacroDataError("macro_observations_missing")
        # Never supply the target or a later period to the forecast.
        observations = [row for row in observations if row.period < spec.observation_period]
        if not observations:
            raise MacroDataError("macro_pre_target_observations_missing")
        latest = date.fromisoformat(observations[-1].period + "-01")
        latest_end = latest.replace(day=calendar.monthrange(latest.year, latest.month)[1])
        if ((as_utc(as_of).date() if as_of else now.date()) - latest_end).days > 75:
            raise MacroDataError("macro_current_conditions_stale")
        return MacroSnapshot(indicator=spec.indicator, retrieved_at=utcnow(), observations=observations,
                             raw_hash=digest(payload), raw_payload=payload)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, MacroDataError):
            raise
        # httpx exception text can contain FRED credentials in the URL.
        raise MacroDataError(f"macro_request_failed:{type(exc).__name__}") from None
    finally:
        if owned:
            http.close()


def parse_fred_csv(text: str, value_column: str) -> list[tuple[str, str]]:
    """Strictly parse a two-column FRED/ALFRED CSV into raw (date, value) strings.

    Missing values (``.`` or empty, e.g. a market holiday) are kept as raw
    strings so callers can tell an absent row from a published blank.
    """
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    rows = [row for row in rows if row]
    if not rows or len(rows[0]) != 2 or rows[0][0].strip() not in {"observation_date", "DATE"} or rows[0][1].strip() != value_column:
        raise MacroDataError("fred_csv_malformed")
    output = []
    for row in rows[1:]:
        if len(row) != 2:
            raise MacroDataError("fred_csv_malformed")
        day, value = row[0].strip(), row[1].strip()
        try:
            if len(day) != 10:
                raise ValueError(day)
            date.fromisoformat(day)
        except ValueError:
            raise MacroDataError("fred_csv_malformed") from None
        if value not in {"", "."} and not _FRED_VALUE.match(value):
            raise MacroDataError("fred_csv_malformed")
        output.append((day, value))
    return output


def fetch_fred_snapshot(indicator: str, *, before: date | None = None, client: httpx.Client | None = None,
                        now: datetime | None = None, history_limit: int | None = None,
                        lookback_days: int | None = None) -> MacroSnapshot:
    """Latest keyless FRED observations strictly before ``before``.

    This is current (revised) data for live forecasting, never an initial-release
    outcome. Stale or empty history fails closed. ``history_limit`` and
    ``lookback_days`` default to the capped root-evidence history.
    """
    meta = SERIES[indicator]
    if meta["source"] != "fred":
        raise MacroDataError("fred_series_required")
    if (history_limit is not None and history_limit < 1) or (lookback_days is not None and lookback_days < 1):
        raise MacroDataError("fred_history_window_invalid")
    owned = client is None
    http = client or httpx.Client(timeout=12, follow_redirects=False)
    now = as_utc(now or utcnow())
    cadence = meta["cadence"]
    fred_id = meta["fred"]
    limit = FRED_HISTORY_LIMITS[cadence] if history_limit is None else history_limit
    lookback = _FRED_LOOKBACK_DAYS[cadence] if lookback_days is None else lookback_days
    start = min(before or now.date(), now.date()) - timedelta(days=lookback)
    try:
        url = f"{FRED_GRAPH_CSV}?id={fred_id}&cosd={start.isoformat()}"
        response = http.get(url)
        response.raise_for_status()
        text = response.content.decode("utf-8")
        values: dict[str, float] = {}
        for day, raw in parse_fred_csv(text, fred_id):
            if raw in {"", "."}:
                continue
            values[day] = float(raw)
        retrieved = utcnow()
        observations = normalize_observations(indicator, values, available_at=retrieved, vintage=retrieved.isoformat(),
            source_url=f"https://fred.stlouisfed.org/series/{fred_id}",
            revision_basis="latest_observed_revisions_not_first_release")
        if not observations:
            raise MacroDataError("macro_observations_missing")
        if before is not None:
            # Never supply the target or a later period to the forecast.
            observations = [row for row in observations if row.period < before.isoformat()]
        if not observations:
            raise MacroDataError("macro_pre_target_observations_missing")
        if (now.date() - date.fromisoformat(observations[-1].period)).days > FRED_STALENESS_DAYS[cadence]:
            raise MacroDataError("macro_current_conditions_stale")
        observations = observations[-limit:]
        payload = {"source_url": url, "content_type": response.headers.get("content-type", ""), "csv": text}
        return MacroSnapshot(indicator=indicator, retrieved_at=retrieved, observations=observations,
                             raw_hash=digest(payload), raw_payload=payload, source_lineage=meta["lineage"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, MacroDataError):
            raise
        raise MacroDataError(f"macro_request_failed:{type(exc).__name__}") from None
    finally:
        if owned:
            http.close()
