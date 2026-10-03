"""Offline tests for FRED-sourced fast-resolving indicators (ICSA, DGS10). All HTTP is stubbed."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from forecastlab.http_client import SafeResponse
from forecastlab.macro import (
    SERIES,
    MacroDataError,
    MacroSnapshot,
    MacroSpec,
    bls_indicators,
    fetch_macro,
    normalize_observations,
    parse_fred_csv,
    series_id,
)
from forecastlab.root_event import digest

NOW = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)


def claims_spec(period: str = "2026-10-03", threshold: float = 197000, **kw) -> MacroSpec:
    return MacroSpec(indicator="jobless_claims", observation_period=period, threshold=threshold,
                     release_at=kw.pop("release_at", datetime(2026, 10, 8, 12, 30, tzinfo=UTC)), **kw)


def dgs10_spec(period: str = "2026-10-05", threshold: float = 5.24, **kw) -> MacroSpec:
    return MacroSpec(indicator="treasury_10y", observation_period=period, threshold=threshold,
                     release_at=kw.pop("release_at", datetime(2026, 10, 6, 21, tzinfo=UTC)), **kw)


# --- MacroSpec and series metadata -------------------------------------------------------------

def test_bls_indicators_keep_selection_at_three_items():
    assert bls_indicators() == ("unemployment", "payrolls", "cpi")
    assert {name for name, meta in SERIES.items() if meta["source"] == "fred"} == {"jobless_claims", "treasury_10y"}
    assert [series_id(name) for name in SERIES] == ["LNS14000000", "CES0000000001", "CUUR0000SA0", "ICSA", "DGS10"]
    assert {SERIES[name]["transform"] for name in bls_indicators()} == {"level", "change_thousands", "yoy_percent"}


def test_choose_questions_ignores_fred_indicators_and_event_keys_are_distinct():
    from forecastlab.question_selection import choose_questions, event_key

    suggestions, gaps = choose_questions([], {}, now=NOW)
    assert suggestions == [] and [gap.split(":")[0] for gap in gaps] == list(bls_indicators())
    assert event_key(claims_spec()) == ("jobless_claims", "2026-10-03")
    assert event_key(dgs10_spec()) == ("treasury_10y", "2026-10-05")
    bls = MacroSpec(indicator="payrolls", observation_period="2026-09", threshold=0,
                    release_at=datetime(2026, 10, 2, 12, 30, tzinfo=UTC))
    assert event_key(bls) == ("empsit", "2026-09")


@pytest.mark.parametrize("kwargs, error", [
    ({"indicator": "unemployment", "observation_period": "2026-09-01"}, "monthly_observation_period_must_be_year_month"),
    ({"indicator": "jobless_claims", "observation_period": "2026-10"}, "observation_period_must_be_iso_date"),
    ({"indicator": "jobless_claims", "observation_period": "2026-10-02"}, "weekly_observation_period_must_be_week_ending_saturday"),
    ({"indicator": "jobless_claims", "observation_period": "2026-10-03", "release_at": "2026-10-03T12:30:00Z"}, "observation_period_must_precede_release"),
    ({"indicator": "treasury_10y", "observation_period": "2026-10-03"}, "daily_observation_period_must_be_weekday"),
    ({"indicator": "treasury_10y", "observation_period": "2026-02-30"}, "observation_period_invalid_date"),
    ({"indicator": "treasury_10y", "observation_period": "2026-10-05", "release_at": "2026-10-05T23:00:00Z"}, "observation_period_must_precede_release"),
    # Friday's yield cannot be released on Saturday; the next business day is Monday.
    ({"indicator": "treasury_10y", "observation_period": "2026-10-02", "release_at": "2026-10-03T21:00:00Z"}, "observation_period_must_precede_release"),
])
def test_period_validation_by_cadence(kwargs, error):
    body = {"threshold": 1, "release_at": "2026-10-08T12:30:00Z", **kwargs}
    with pytest.raises(ValueError, match=error):
        MacroSpec.model_validate(body)


def test_valid_periods_by_cadence():
    assert claims_spec().observation_period == "2026-10-03"
    assert dgs10_spec("2026-10-02", release_at=datetime(2026, 10, 5, 21, tzinfo=UTC)).observation_period == "2026-10-02"
    assert MacroSpec(indicator="cpi", observation_period="2026-09", threshold=3,
                     release_at=datetime(2026, 10, 15, 12, 30, tzinfo=UTC)).observation_period == "2026-09"


def test_level_transform_passes_values_through_with_source_specific_ids():
    args = dict(available_at=NOW, vintage="v", source_url="https://fred.stlouisfed.org/series/ICSA", revision_basis="test")
    claims = normalize_observations("jobless_claims", {"2026-09-19": 198000.0, "2026-09-26": 197000.0}, **args)
    assert [(row.period, row.value, row.series_id, row.units) for row in claims] == [
        ("2026-09-19", 198000.0, "ICSA", "claims"), ("2026-09-26", 197000.0, "ICSA", "claims")]
    yields = normalize_observations("treasury_10y", {"2026-10-01": 5.24}, **args)
    assert (yields[0].value, yields[0].series_id, yields[0].units, yields[0].seasonal_adjustment) == (
        5.24, "DGS10", "percent", "not_seasonally_adjusted")
    assert normalize_observations("unemployment", {"2026-08": 4.3}, **args)[0].series_id == "LNS14000000"


# --- Live FRED adapter (stubbed) ---------------------------------------------------------------

def _csv(series: str, rows: list[tuple[str, str]]) -> bytes:
    return ("observation_date," + series + "\n" + "".join(f"{day},{value}\n" for day, value in rows)).encode()


def _weekly(last: date, count: int, start_value: int = 200000) -> list[tuple[str, str]]:
    return [((last - timedelta(days=7 * (count - 1 - i))).isoformat(), str(start_value + i)) for i in range(count)]


def _weekdays(last: date, count: int) -> list[tuple[str, str]]:
    days: list[date] = []
    day = last
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return [(day.isoformat(), f"{4 + i / 100:.2f}") for i, day in enumerate(reversed(days))]


def _client(content: bytes, seen: list | None = None, status: int = 200) -> httpx.Client:
    def handler(request):
        if seen is not None:
            seen.append(str(request.url))
        return httpx.Response(status, content=content, headers={"content-type": "application/csv"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fred_csv_parsing_skips_missing_values_caps_history_and_excludes_target(monkeypatch):
    monkeypatch.setattr("forecastlab.macro.utcnow", lambda: NOW)
    rows = _weekly(date(2026, 9, 26), 200)
    rows[-3] = (rows[-3][0], ".")
    rows.append(("2026-10-03", "999999"))  # never supplied: on or after the target
    seen: list[str] = []
    with _client(_csv("ICSA", rows), seen) as http:
        snapshot = fetch_macro(claims_spec(), client=http)
    assert seen[0].startswith("https://fred.stlouisfed.org/graph/fredgraph.csv?id=ICSA&cosd=")
    assert len(snapshot.observations) == 104
    assert snapshot.observations[-1].period == "2026-09-26"
    assert rows[-4][0] not in {row.period for row in snapshot.observations}
    assert all(row.period < "2026-10-03" for row in snapshot.observations)
    assert snapshot.source_lineage == "agency:dol"
    assert snapshot.observations[-1].source_url == "https://fred.stlouisfed.org/series/ICSA"
    assert snapshot.raw_payload["csv"].startswith("observation_date,ICSA") and snapshot.raw_hash == digest(snapshot.raw_payload)

    daily = _weekdays(date(2026, 10, 1), 200)
    daily.insert(-5, ("2026-09-07", ""))  # FRED's newer blank holiday format
    with _client(_csv("DGS10", daily)) as http:
        yields = fetch_macro(dgs10_spec(), client=http)
    assert len(yields.observations) == 130 and yields.observations[-1].period == "2026-10-01"
    assert yields.source_lineage == "agency:frb"


@pytest.mark.parametrize("spec, rows, now", [
    (claims_spec(), _weekly(date(2026, 9, 26), 30), datetime(2026, 10, 11, tzinfo=UTC)),   # 15 days > 14
    (dgs10_spec("2026-10-12", release_at=datetime(2026, 10, 13, 21, tzinfo=UTC)), _weekdays(date(2026, 10, 1), 30),
     datetime(2026, 10, 9, tzinfo=UTC)),                                                      # 8 days > 7
])
def test_fred_staleness_limits(monkeypatch, spec, rows, now):
    monkeypatch.setattr("forecastlab.macro.utcnow", lambda: now)
    with _client(_csv(SERIES[spec.indicator]["fred"], rows)) as http, pytest.raises(MacroDataError, match="current_conditions_stale"):
        fetch_macro(spec, client=http)


def test_fred_staleness_boundary_is_inclusive(monkeypatch):
    monkeypatch.setattr("forecastlab.macro.utcnow", lambda: datetime(2026, 10, 10, tzinfo=UTC))
    with _client(_csv("ICSA", _weekly(date(2026, 9, 26), 30))) as http:
        assert fetch_macro(claims_spec(), client=http).observations[-1].period == "2026-09-26"


@pytest.mark.parametrize("content, error", [
    (b"observation_date,WRONG\n2026-09-26,1\n", "fred_csv_malformed"),
    (b"<html>blocked</html>", "fred_csv_malformed"),
    (b"observation_date,ICSA\n2026-09-26,abc\n", "fred_csv_malformed"),
    (b"observation_date,ICSA\n09/26/2026,1\n", "fred_csv_malformed"),
    (b"observation_date,ICSA\n2026-09-26,.\n", "macro_observations_missing"),
])
def test_malformed_or_empty_fred_csv_fails_closed(monkeypatch, content, error):
    monkeypatch.setattr("forecastlab.macro.utcnow", lambda: NOW)
    with _client(content) as http, pytest.raises(MacroDataError, match=error):
        fetch_macro(claims_spec(), client=http)


def test_fred_http_errors_and_backtests_fail_closed(monkeypatch):
    monkeypatch.setattr("forecastlab.macro.utcnow", lambda: NOW)
    with _client(b"", status=503) as http, pytest.raises(MacroDataError, match="macro_request_failed:HTTPStatusError"):
        fetch_macro(claims_spec(), client=http)
    with pytest.raises(MacroDataError, match="historical_fred_series_not_supported"):
        fetch_macro(claims_spec(), as_of=datetime(2026, 10, 1, tzinfo=UTC), fred_api_key="test")


def test_parse_fred_csv_keeps_missing_values_raw():
    assert parse_fred_csv("observation_date,DGS10\n2026-09-04,4.78\n2026-09-07,\n2026-09-08,.\n", "DGS10") == [
        ("2026-09-04", "4.78"), ("2026-09-07", ""), ("2026-09-08", ".")]


# --- Contract templates ------------------------------------------------------------------------

def _bls_template_matrix() -> str:
    out = {}
    for indicator, release in [("unemployment", datetime(2026, 10, 2, 12, 30, tzinfo=UTC)),
                               ("payrolls", datetime(2026, 10, 2, 12, 30, tzinfo=UTC)),
                               ("cpi", datetime(2026, 10, 15, 12, 30, tzinfo=UTC))]:
        for comparison in ("gt", "ge", "lt", "le"):
            for policy in ("first_release", "as_of_resolution"):
                spec = MacroSpec(indicator=indicator, observation_period="2026-09", comparison=comparison,
                                 threshold=4.3 if indicator != "payrolls" else 22000, release_at=release, revision_policy=policy)
                out[f"{indicator}:{comparison}:{policy}"] = spec.template("q", "c").model_dump(mode="json", exclude={"created_at"})
    return json.dumps(out, sort_keys=True)


def test_bls_templates_are_byte_for_byte_unchanged():
    # Digest of the same matrix produced by the pre-change template (commit 18caea5),
    # so already-frozen BLS contracts still validate.
    assert hashlib.sha256(_bls_template_matrix().encode()).hexdigest() == \
        "f882c5ceb13e89c5404b0d0b5aade29bbace809c5f2c8e527808a027eba502b0"
    contract = MacroSpec(indicator="unemployment", observation_period="2026-09", threshold=4.3,
                         release_at=datetime(2026, 10, 2, 12, 30, tzinfo=UTC)).template("q", "c")
    assert contract.authoritative_source == "https://www.bls.gov/news.release/empsit.nr0.htm"
    assert contract.resolution_method.startswith("Use BLS series LNS14000000 (seasonally_adjusted) and the first published value.")


def test_fred_templates_name_series_sources_and_initial_vintage():
    claims = claims_spec().template("q", "c")
    assert claims.normalized_question == (
        "Will the first published value of U.S. initial jobless claims (seasonally adjusted, FRED series ICSA) "
        "for the week ending 2026-10-03 be greater than 197,000 claims in the release expected on 2026-10-08?")
    assert claims.authoritative_source == "https://fred.stlouisfed.org/series/ICSA"
    assert claims.fallback_sources == ["https://alfred.stlouisfed.org/series?seid=ICSA", "https://www.dol.gov/ui/data.pdf"]
    assert "earliest ALFRED vintage of ICSA" in claims.resolution_method
    assert "advance figure is revised the following week" in claims.resolution_method
    assert claims.units == "claims" and claims.resolution_date == datetime(2026, 10, 8, 12, 30, tzinfo=UTC)
    assert claims.created_by == "macro_template_fred_v1" and claims.approval_errors() == []
    yields = dgs10_spec().template("q", "c")
    assert "U.S. 10-year Treasury constant-maturity yield" in yields.normalized_question
    assert "FRED series DGS10" in yields.normalized_question and "for 2026-10-05 be greater than 5.24 percent" in yields.normalized_question
    assert yields.authoritative_source == "https://fred.stlouisfed.org/series/DGS10"
    assert yields.fallback_sources[-1] == "https://www.federalreserve.gov/releases/h15/"
    assert "earliest ALFRED vintage of DGS10" in yields.resolution_method
    assert "holiday" in yields.cancellation_conditions


# --- Evidence packet ---------------------------------------------------------------------------

def _snapshot(indicator: str, rows: dict[str, float], *, retrieved: datetime = NOW) -> MacroSnapshot:
    meta = SERIES[indicator]
    observations = normalize_observations(indicator, rows, available_at=retrieved, vintage=retrieved.isoformat(),
        source_url=f"https://fred.stlouisfed.org/series/{meta['fred']}",
        revision_basis="latest_observed_revisions_not_first_release")
    payload = {"csv": "x"}
    return MacroSnapshot(indicator=indicator, retrieved_at=retrieved, observations=observations,
                         raw_hash=digest(payload), raw_payload=payload, source_lineage=meta["lineage"])


def test_fred_macro_packet_uses_series_metadata_and_fits_the_root_budget():
    from forecastlab.budget import estimate_prompt_tokens
    from forecastlab.macro_evidence import validate_snapshot
    from forecastlab_api.root_executor import _macro_packet

    claims = _snapshot("jobless_claims", {day: float(value) for day, value in _weekly(date(2026, 9, 26), 104, 1_234_000)})
    validate_snapshot(claims, claims_spec(), NOW)
    packet = _macro_packet(claims.model_dump(mode="json"))
    assert packet[0]["title"] == "FRED ICSA observations (U.S. initial jobless claims)"
    assert packet[0]["source_lineage"] == "agency:dol" and packet[0]["url"] == "https://fred.stlouisfed.org/series/ICSA"
    assert packet[0]["required_sections"] == ["current_conditions", "reference_class"]
    yields = _snapshot("treasury_10y", {day: float(value) for day, value in _weekdays(date(2026, 10, 1), 130)})
    validate_snapshot(yields, dgs10_spec(), NOW)
    ypacket = _macro_packet(yields.model_dump(mode="json"))
    assert ypacket[0]["title"].startswith("FRED DGS10") and ypacket[0]["source_lineage"] == "agency:frb"
    # Capped history stays a small fraction of the 24,000-token root estimate reserve.
    assert estimate_prompt_tokens(json.dumps(packet)) < 3000
    assert estimate_prompt_tokens(json.dumps(ypacket)) < 3000
    # ICSA observations cannot stand in for DGS10.
    with pytest.raises(MacroDataError, match="units_or_adjustment_mismatch"):
        validate_snapshot(claims.model_copy(update={"indicator": "treasury_10y"}), dgs10_spec(), NOW)


def test_bls_macro_packet_output_is_unchanged():
    from forecastlab_api.root_executor import _macro_packet

    observations = normalize_observations("unemployment", {"2026-07": 4.2, "2026-08": 4.3}, available_at=NOW, vintage="v",
        source_url="https://data.bls.gov/timeseries/LNS14000000", revision_basis="latest_observed_revisions_not_first_release")
    payload = {"x": 1}
    snapshot = MacroSnapshot(indicator="unemployment", retrieved_at=NOW, observations=observations,
                             raw_hash=digest(payload), raw_payload=payload).model_dump(mode="json")
    assert _macro_packet(snapshot) == [{
        "schema_version": "evidence_assessment_v1", "claim_id": "macro:" + digest(payload),
        "classification": "background", "relevant": True, "usable": True, "required_sections": ["current_conditions"],
        "reason": "Validated official series; deterministic units and transformations; retained raw response",
        "claim": 'unemployment: [{"period": "2026-07", "value": 4.2}, {"period": "2026-08", "value": 4.3}]; '
                 "units=percent; adjustment=seasonally_adjusted",
        "quote": "", "url": "https://data.bls.gov/timeseries/LNS14000000", "title": "BLS unemployment observations",
        "primary_source": True, "source_lineage": "agency:bls", "source_available_at": NOW.isoformat().replace("+00:00", "Z"),
        "extraction_method": "structured_macro_adapter_v1", "revision_basis": "latest_observed_revisions_not_first_release",
    }]


@pytest.mark.parametrize("spec, quote", [
    (claims_spec(), "Initial claims for the week ending September 26, 2026 were 218,000."),
    (dgs10_spec(), "The 10-year Treasury yield closed at 4.12% on 2026-10-01."),
])
def test_validate_macro_packet_accepts_fred_measurements(spec, quote):
    from forecastlab.macro_evidence import validate_macro_packet

    row = {"quote": quote, "classification": "supporting", "usable": True,
           "required_sections": ["current_conditions"], "source_available_at": "2026-10-01T00:00:00+00:00"}
    unrelated = {**row, "quote": "Retail sales rose 0.4 percent in August 2026."}
    accepted, rejected = validate_macro_packet([row, unrelated], spec, cutoff=NOW.isoformat())
    assert accepted["usable"] and accepted["required_sections"] == ["current_conditions"]
    assert not rejected["usable"]


# --- ALFRED initial-release adjudication -------------------------------------------------------

class FakeAlfred:
    """ALFRED semantics observed live: as-of vintages, clamped future dates, dated value header."""

    def __init__(self, series: str, vintages: dict[date, dict[str, str]], today: date):
        self.series, self.vintages, self.today, self.seen = series, vintages, today, []
        self.content_type, self.body = "application/csv", None

    def __call__(self, url: str) -> SafeResponse:
        self.seen.append(url)
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}
        assert urlsplit(url)._replace(query="").geturl() == "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
        assert query["id"] == self.series
        vintage = min(date.fromisoformat(query["vintage_date"]), self.today)
        values: dict[str, str] = {}
        for effective in sorted(day for day in self.vintages if day <= vintage):
            values.update(self.vintages[effective])
        rows = [(day, value) for day, value in sorted(values.items()) if query["cosd"] <= day <= query["coed"]]
        body = self.body or _csv(f"{self.series}_{vintage.strftime('%Y%m%d')}", rows)
        return SafeResponse(url=url, final_url=url, status_code=200, content=body, content_type=self.content_type,
                            bytes_read=len(body))


def _install(monkeypatch, fake: FakeAlfred, now: datetime, safe_get=None):
    from forecastlab_api import official_fred_outcomes, official_macro_outcomes

    clock = [now]
    monkeypatch.setattr(official_macro_outcomes, "utcnow", lambda: clock[0])
    monkeypatch.setattr(official_fred_outcomes, "utcnow", lambda: clock[0])
    monkeypatch.setattr(official_fred_outcomes, "fetch_vintage_csv", fake)
    monkeypatch.setattr(official_macro_outcomes, "safe_get", safe_get or (lambda *a, **k: pytest.fail("BLS fetch for a FRED entry")))
    return clock


def _fred_entry(session, *, entry_id: str, spec: MacroSpec, cohort_id: str = "cohort"):
    from forecastlab_api.models import ProspectiveEntry, Question

    question_id = "question-" + entry_id
    contract = spec.template(question_id, "contract-" + entry_id).model_copy(update={"status": "approved"})
    session.add(Question(id=question_id, original_text=contract.original_question, normalized_text=contract.normalized_question,
                         requested_mode="live", requested_profile_id="root_event_ensemble_v1"))
    session.flush()
    entry = ProspectiveEntry(id=entry_id, cohort_id=cohort_id, question_id=question_id, contract_json=contract.model_dump_json(),
        macro_json=spec.model_dump_json(), release_event=f"{spec.indicator}-{spec.observation_period}",
        cutoff=as_cutoff(spec))
    session.add(entry)
    return entry, contract


def as_cutoff(spec: MacroSpec) -> datetime:
    return spec.release_at - timedelta(days=2)


CLAIMS_VINTAGES = {
    date(2026, 10, 1): {"2026-09-26": "197000"},
    date(2026, 10, 8): {"2026-09-26": "198000", "2026-10-03": "205000"},   # advance figure
    date(2026, 10, 15): {"2026-10-03": "190000", "2026-10-10": "201000"},  # revision: never used
}


@pytest.mark.parametrize("threshold, expected", [(197000, 1), (205000, 0)])
def test_alfred_initial_release_adjudicates_yes_and_no_without_revisions(client, monkeypatch, threshold, expected):
    from tests.test_official_macro_outcomes import _cohort, _freeze_cohort

    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ProspectiveOutcome
    from forecastlab_api.official_fred_outcomes import PARSER_VERSION, POLICY_VERSION, SYSTEM_ATTRIBUTION
    from forecastlab_api.official_macro_outcomes import process_prospective_entry
    from forecastlab_api.prospective import cohort_report

    fake = FakeAlfred("ICSA", CLAIMS_VINTAGES, today=date(2026, 10, 20))
    _install(monkeypatch, fake, datetime(2026, 10, 20, 15, tzinfo=UTC))
    with SessionLocal() as session:
        cohort = _cohort(session)
        spec = claims_spec(threshold=threshold)
        row = _fred_entry(session, entry_id="claims", spec=spec)
        _freeze_cohort(cohort, [row])
        session.commit()
        amendment = process_prospective_entry(session, "claims")
        assert (amendment.status, amendment.outcome) == ("ready", expected)
        assert (amendment.policy_version, amendment.parser_version) == (POLICY_VERSION, PARSER_VERSION)
        measurement = json.loads(amendment.measurement_json)
        assert measurement["value_decimal"] == "205000" and measurement["first_vintage_date"] == "2026-10-08"
        assert measurement["prior_vintage_date"] == "2026-10-07"
        assert amendment.source_url == ("https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=ICSA&cosd=2026-09-19"
                                        "&coed=2026-10-03&vintage_date=2026-10-08")
        assert json.loads(amendment.source_identity_json)["vintage_date"] == "2026-10-08"
        assert amendment.outcome_known_at == spec.release_at and amendment.retrieved_at == datetime(2026, 10, 20, 15, tzinfo=UTC)
        artifact = json.loads(amendment.artifact_json)
        assert artifact["sha256"] == amendment.source_sha256 and artifact["content_type"] == "text/csv"
        from forecastlab_api.artifact_store import get_bytes
        assert get_bytes(artifact).startswith(b"observation_date,ICSA_20261008\n")
        outcome = session.scalars(select(ProspectiveOutcome)).one()
        assert outcome.outcome == expected and outcome.confirmed_by == SYSTEM_ATTRIBUTION
        assert json.loads(outcome.evidence)["policy_version"] == POLICY_VERSION
        assert cohort_report(session, cohort.id)["questions"][0]["official_outcome_amendment"]["policy_version"] == POLICY_VERSION
        # Idempotent: no further ALFRED requests or rows.
        requests = len(fake.seen)
        assert process_prospective_entry(session, "claims").id == amendment.id and len(fake.seen) == requests


def test_alfred_not_yet_published_is_retryable_then_ready(client, monkeypatch):
    from tests.test_official_macro_outcomes import _cohort, _freeze_cohort

    from forecastlab_api.autopilot_models import OfficialMacroOutcomeAmendment
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.official_macro_outcomes import process_prospective_entry

    # ALFRED's own date still trails: the vintage for Oct 8 does not exist yet.
    fake = FakeAlfred("ICSA", CLAIMS_VINTAGES, today=date(2026, 10, 7))
    clock = _install(monkeypatch, fake, datetime(2026, 10, 8, 12, 45, tzinfo=UTC))
    with SessionLocal() as session:
        cohort = _cohort(session)
        _freeze_cohort(cohort, [_fred_entry(session, entry_id="claims", spec=claims_spec())])
        session.commit()
        first = process_prospective_entry(session, "claims")
        assert (first.status, first.exception_code) == ("exception", "fred_initial_release_not_yet_published")
        assert process_prospective_entry(session, "claims").id == first.id  # 30-minute cooldown
        fake.today = date(2026, 10, 8)
        clock[0] = datetime(2026, 10, 8, 13, 20, tzinfo=UTC)
        ready = process_prospective_entry(session, "claims")
        assert (ready.status, ready.outcome) == ("ready", 1)
        assert len(session.scalars(select(OfficialMacroOutcomeAmendment)).all()) == 2


def test_alfred_holiday_blank_is_terminal_without_outcome(client, monkeypatch):
    from tests.test_official_macro_outcomes import _cohort, _freeze_cohort

    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ProspectiveOutcome
    from forecastlab_api.official_macro_outcomes import process_prospective_entry

    vintages = {date(2026, 9, 5): {"2026-09-04": "4.78"}, date(2026, 9, 9): {"2026-09-07": "", "2026-09-08": "4.80"}}
    fake = FakeAlfred("DGS10", vintages, today=date(2026, 9, 12))
    _install(monkeypatch, fake, datetime(2026, 9, 12, tzinfo=UTC))
    with SessionLocal() as session:
        cohort = _cohort(session)
        spec = dgs10_spec("2026-09-07", threshold=4.78, release_at=datetime(2026, 9, 8, 21, tzinfo=UTC))
        _freeze_cohort(cohort, [_fred_entry(session, entry_id="holiday", spec=spec)])
        session.commit()
        amendment = process_prospective_entry(session, "holiday")
        assert (amendment.status, amendment.exception_code) == ("exception", "fred_initial_release_value_missing")
        assert process_prospective_entry(session, "holiday").id == amendment.id
        assert session.scalars(select(ProspectiveOutcome)).all() == []


@pytest.mark.parametrize("body, content_type, code", [
    (b"<html>blocked</html>", "application/csv", "fred_vintage_csv_malformed"),
    (b"observation_date,DGS10_20261006\n2026-10-05,high\n", "application/csv", "fred_vintage_csv_malformed"),
    (b"observation_date,DGS10_20261006\n2026-10-05,5.30\n", "text/html", "official_fred_vintage_unavailable"),
])
def test_alfred_malformed_or_unexpected_csv_records_exception(client, monkeypatch, body, content_type, code):
    from tests.test_official_macro_outcomes import _cohort, _freeze_cohort

    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ProspectiveOutcome
    from forecastlab_api.official_macro_outcomes import process_prospective_entry

    fake = FakeAlfred("DGS10", {}, today=date(2026, 10, 6))
    fake.body, fake.content_type = body, content_type
    _install(monkeypatch, fake, datetime(2026, 10, 6, 22, tzinfo=UTC))
    with SessionLocal() as session:
        cohort = _cohort(session)
        _freeze_cohort(cohort, [_fred_entry(session, entry_id="bad", spec=dgs10_spec())])
        session.commit()
        amendment = process_prospective_entry(session, "bad")
        assert (amendment.status, amendment.exception_code) == ("exception", code)
        assert session.scalars(select(ProspectiveOutcome)).all() == []


def test_alfred_value_absent_after_search_window_is_terminal(client, monkeypatch):
    from tests.test_official_macro_outcomes import _cohort, _freeze_cohort

    from forecastlab_api.db import SessionLocal
    from forecastlab_api.official_fred_outcomes import MAX_SEARCH_DAYS
    from forecastlab_api.official_macro_outcomes import process_prospective_entry

    fake = FakeAlfred("DGS10", {date(2026, 9, 1): {"2026-08-31": "4.70"}}, today=date(2026, 10, 1))
    _install(monkeypatch, fake, datetime(2026, 10, 1, tzinfo=UTC))
    with SessionLocal() as session:
        cohort = _cohort(session)
        spec = dgs10_spec("2026-09-01", threshold=4.7, release_at=datetime(2026, 9, 2, 21, tzinfo=UTC))
        _freeze_cohort(cohort, [_fred_entry(session, entry_id="absent", spec=spec)])
        session.commit()
        amendment = process_prospective_entry(session, "absent")
        assert (amendment.status, amendment.exception_code) == (
            "exception", "fred_initial_release_not_found_within_search_window")
        assert fake.seen[0].endswith(f"vintage_date={(date(2026, 9, 1) + timedelta(days=MAX_SEARCH_DAYS)).isoformat()}")
        assert process_prospective_entry(session, "absent").id == amendment.id


def test_dgs10_first_vintage_is_the_next_business_day(client, monkeypatch):
    from tests.test_official_macro_outcomes import _cohort, _freeze_cohort

    from forecastlab_api.db import SessionLocal
    from forecastlab_api.official_macro_outcomes import process_prospective_entry

    vintages = {date(2026, 10, 3): {"2026-10-02": "5.20"}, date(2026, 10, 6): {"2026-10-05": "5.31"},
                date(2026, 10, 9): {"2026-10-05": "5.29"}}
    fake = FakeAlfred("DGS10", vintages, today=date(2026, 10, 7))
    _install(monkeypatch, fake, datetime(2026, 10, 7, 1, tzinfo=UTC))
    with SessionLocal() as session:
        cohort = _cohort(session)
        _freeze_cohort(cohort, [_fred_entry(session, entry_id="dgs10", spec=dgs10_spec())])
        session.commit()
        amendment = process_prospective_entry(session, "dgs10")
        measurement = json.loads(amendment.measurement_json)
        assert (amendment.status, amendment.outcome, measurement["value_decimal"]) == ("ready", 1, "5.31")
        assert (measurement["first_vintage_date"], measurement["prior_vintage_date"]) == ("2026-10-06", "2026-10-05")
        assert amendment.outcome_known_at == datetime(2026, 10, 6, 21, tzinfo=UTC)


def test_dispatch_leaves_bls_adjudication_untouched(client, monkeypatch):
    from tests.test_official_macro_outcomes import _cohort, _entry, _freeze_cohort, cpi_html

    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ProspectiveOutcome
    from forecastlab_api.official_macro_outcomes import (
        POLICY_VERSION,
        SYSTEM_ATTRIBUTION,
        process_due_prospective_entries,
    )

    bls_urls: list[str] = []
    def safe_get(url, **_):
        bls_urls.append(url)
        content = cpi_html()
        return SafeResponse(url=url, final_url=url, status_code=200, content=content, content_type="text/html", bytes_read=len(content))
    fake = FakeAlfred("ICSA", CLAIMS_VINTAGES, today=date(2026, 10, 20))
    _install(monkeypatch, fake, datetime(2026, 10, 20, 15, tzinfo=UTC), safe_get=safe_get)
    with SessionLocal() as session:
        cohort = _cohort(session)
        bls = _entry(session, entry_id="bls", threshold=3.2)
        fred = _fred_entry(session, entry_id="fred", spec=claims_spec())
        _freeze_cohort(cohort, [bls, fred])
        session.commit()
        amendments = {row.prospective_entry_id: row for row in process_due_prospective_entries(session, cohort_id="cohort")}
        assert amendments["bls"].policy_version == POLICY_VERSION and amendments["bls"].outcome == 1
        assert amendments["bls"].parser_version == "macro_first_release_v1"
        assert amendments["fred"].policy_version == "official_fred_initial_release_v1"
        assert bls_urls == ["https://www.bls.gov/news.release/archives/cpi_09112026.htm"]
        assert all("ICSA" in url for url in fake.seen)
        outcomes = {row.entry_id: row for row in session.scalars(select(ProspectiveOutcome))}
        assert outcomes["bls"].confirmed_by == SYSTEM_ATTRIBUTION
        assert json.loads(outcomes["bls"].evidence)["policy_version"] == POLICY_VERSION


# --- Fast question generator -------------------------------------------------------------------

def _fast_snapshots(retrieved: datetime = NOW) -> dict[str, MacroSnapshot]:
    return {"jobless_claims": _snapshot("jobless_claims", {"2026-09-19": 198000.0, "2026-09-26": 197000.0}, retrieved=retrieved),
            "treasury_10y": _snapshot("treasury_10y", {"2026-09-30": 5.29, "2026-10-01": 5.24}, retrieved=retrieved)}


def test_fast_questions_follow_the_fixed_rule():
    from forecastlab.fast_questions import propose_fast_questions
    from forecastlab_api.prospective import CohortQuestionIn

    now = datetime(2026, 10, 2, 23, 29, 41, tzinfo=UTC)  # Friday evening, US daylight time
    questions = propose_fast_questions(now, _fast_snapshots())
    parsed = [CohortQuestionIn.model_validate(item) for item in questions]
    assert [(q.macro.indicator, q.macro.observation_period, q.macro.threshold, q.macro.release_at.isoformat(), q.release_event)
            for q in parsed] == [
        ("jobless_claims", "2026-10-03", 197000.0, "2026-10-08T12:30:00+00:00", "claims-2026-10-08"),
        ("jobless_claims", "2026-10-10", 197000.0, "2026-10-15T12:30:00+00:00", "claims-2026-10-15"),
        ("treasury_10y", "2026-10-05", 5.24, "2026-10-06T21:00:00+00:00", "dgs10-2026-10-05"),
        ("treasury_10y", "2026-10-06", 5.24, "2026-10-07T21:00:00+00:00", "dgs10-2026-10-06"),
        ("treasury_10y", "2026-10-07", 5.24, "2026-10-08T21:00:00+00:00", "dgs10-2026-10-07"),
        ("treasury_10y", "2026-10-08", 5.24, "2026-10-09T21:00:00+00:00", "dgs10-2026-10-08"),
        ("treasury_10y", "2026-10-09", 5.24, "2026-10-12T21:00:00+00:00", "dgs10-2026-10-09"),
    ]
    assert {q.cutoff for q in parsed} == {datetime(2026, 10, 3, 5, 29, tzinfo=UTC)}
    assert all(now < q.cutoff < q.macro.release_at and q.macro.comparison == "gt" for q in parsed)
    assert len(questions) <= 10


def test_fast_questions_use_eastern_standard_time_after_dst_ends():
    from forecastlab.fast_questions import claims_release_at, propose_fast_questions

    assert claims_release_at(date(2026, 10, 24)) == datetime(2026, 10, 29, 12, 30, tzinfo=UTC)  # EDT
    assert claims_release_at(date(2026, 10, 31)) == datetime(2026, 11, 5, 13, 30, tzinfo=UTC)   # EST
    assert claims_release_at(date(2027, 3, 13)) == datetime(2027, 3, 18, 12, 30, tzinfo=UTC)    # EDT again
    with pytest.raises(ValueError):
        claims_release_at(date(2026, 10, 30))
    now = datetime(2026, 10, 29, 14, tzinfo=UTC)  # Thursday after the 8:30 release
    snapshots = {"jobless_claims": _snapshot("jobless_claims", {"2026-10-24": 210000.0}, retrieved=now)}
    claims = propose_fast_questions(now, snapshots)
    assert [(q["macro"]["observation_period"], q["macro"]["release_at"], q["release_event"]) for q in claims] == [
        ("2026-10-31", "2026-11-05T13:30:00Z", "claims-2026-11-05"),
        ("2026-11-07", "2026-11-12T13:30:00Z", "claims-2026-11-12")]
    assert all(q["macro"]["threshold"] == 210000.0 for q in claims)


def test_fast_questions_drop_questions_without_a_valid_cutoff():
    from forecastlab.fast_questions import _cutoff, propose_fast_questions

    # Thursday 12:10 UTC: the week ending Oct 3 is still unreleased (12:30) but
    # the safety margin leaves no cutoff, so it is dropped and still counted.
    now = datetime(2026, 10, 8, 12, 10, tzinfo=UTC)
    questions = propose_fast_questions(now, _fast_snapshots(now))
    claims = [q for q in questions if q["macro"]["indicator"] == "jobless_claims"]
    assert [q["macro"]["observation_period"] for q in claims] == ["2026-10-10"]
    assert [q["macro"]["observation_period"] for q in questions if q["macro"]["indicator"] == "treasury_10y"] == [
        "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"]
    open_ = datetime(2026, 10, 5, 13, 30, tzinfo=UTC)
    release = datetime(2026, 10, 6, 21, tzinfo=UTC)
    assert _cutoff(datetime(2026, 10, 5, 10, tzinfo=UTC), release, market_open=open_) == datetime(2026, 10, 5, 13, tzinfo=UTC)
    assert _cutoff(datetime(2026, 10, 5, 4, tzinfo=UTC), release, market_open=open_) == datetime(2026, 10, 5, 10, tzinfo=UTC)
    assert _cutoff(datetime(2026, 10, 5, 13, 5, tzinfo=UTC), release, market_open=open_) is None
    # Missing or post-target observations yield no question rather than a guessed threshold.
    assert propose_fast_questions(now, {}) == []


def test_fast_questions_create_a_cohort_through_the_api(client):
    from forecastlab.fast_questions import propose_fast_questions
    from forecastlab.timeutil import utcnow

    now = utcnow()
    snapshots = {
        "jobless_claims": _snapshot("jobless_claims", {(now.date() - timedelta(days=12)).isoformat(): 200000.0}, retrieved=now),
        "treasury_10y": _snapshot("treasury_10y", {(now.date() - timedelta(days=1)).isoformat(): 4.5}, retrieved=now),
    }
    questions = propose_fast_questions(now, snapshots)
    # 2 + 5, unless now is inside the 30-minute margin before a claims release.
    assert len(questions) in {6, 7}
    response = client.post("/api/prospective/cohorts", json={"name": "Fast FRED pilot", "budget_usd": 1, "questions": questions})
    assert response.status_code == 201, response.text
    contracts = [q["contract"] for q in response.json()["questions"]]
    assert {c["authoritative_source"] for c in contracts} == {
        "https://fred.stlouisfed.org/series/ICSA", "https://fred.stlouisfed.org/series/DGS10"}
