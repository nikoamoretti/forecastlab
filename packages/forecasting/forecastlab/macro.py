"""Small, typed BLS/ALFRED boundary. No present-day data in historical calls."""
from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from forecastlab.root_event import digest
from forecastlab.schemas import ForecastContract
from forecastlab.timeutil import as_utc, utcnow

SERIES = {
    "unemployment": {"bls": "LNS14000000", "fred": "UNRATE", "units": "percent", "adjustment": "seasonally_adjusted", "label": "U.S. unemployment rate"},
    "payrolls": {"bls": "CES0000000001", "fred": "PAYEMS", "units": "jobs", "adjustment": "seasonally_adjusted", "label": "U.S. nonfarm payroll monthly change"},
    "cpi": {"bls": "CUUR0000SA0", "fred": "CPIAUCNS", "units": "percent_year_over_year", "adjustment": "not_seasonally_adjusted", "label": "U.S. CPI year-over-year inflation"},
}


class MacroSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    indicator: Literal["unemployment", "payrolls", "cpi"]
    observation_period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
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
        year, month = map(int, self.observation_period.split("-"))
        if date(year, month, calendar.monthrange(year, month)[1]) >= self.release_at.date():
            raise ValueError("observation_period_must_precede_release")
        return self

    def template(self, question_id: str, contract_id: str) -> ForecastContract:
        series = SERIES[self.indicator]
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
    output = []
    for period, value in sorted(values.items()):
        if indicator != "unemployment":
            prior = values.get(_previous_period(period, 1 if indicator == "payrolls" else 12))
            if prior is None or (indicator == "cpi" and prior <= 0):
                continue
            value = round((value - prior) * 1000) if indicator == "payrolls" else round((value / prior - 1) * 100, 1)
        output.append(MacroObservation(
            series_id=meta["bls"], period=period, value=value, units=meta["units"],
            seasonal_adjustment=meta["adjustment"], source_url=source_url,
            available_at=available_at, vintage=vintage, revision_basis=revision_basis,
            footnotes=(footnotes or {}).get(period, []),
        ))
    return output


def fetch_latest_macro_snapshots(*, client: httpx.Client | None = None) -> dict[str, MacroSnapshot]:
    """One keyless BLS request for question selection, covering all three series.

    The short history is only a threshold anchor. Research fetches its own full
    history after approval; these observations never substitute for vintage data.
    """
    owned = client is None
    http = client or httpx.Client(timeout=12, follow_redirects=False)
    now = utcnow()
    try:
        response = http.post("https://api.bls.gov/publicAPI/v1/timeseries/data/", json={
            "seriesid": [meta["bls"] for meta in SERIES.values()],
            "startyear": str(now.year - 2), "endyear": str(now.year)})
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "REQUEST_SUCCEEDED":
            raise MacroDataError("bls_request_not_succeeded")
        series = {row["seriesID"]: row["data"] for row in payload["Results"]["series"]}
        if set(series) != {meta["bls"] for meta in SERIES.values()}:
            raise MacroDataError("macro_series_mismatch")
        retrieved = utcnow()
        snapshots = {}
        for indicator, meta in SERIES.items():
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
                client: httpx.Client | None = None, timeout: float = 15) -> MacroSnapshot:
    """Historical mode deliberately excludes the cutoff date absent intraday proof."""
    owned = client is None
    http = client or httpx.Client(timeout=min(15, timeout), follow_redirects=False)
    now = utcnow()
    meta = SERIES[spec.indicator]
    try:
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
