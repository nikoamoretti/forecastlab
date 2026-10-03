"""Offline tests: keyless BLS quota fallback to the FRED mirror, and the live monthly cache. HTTP is stubbed."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

import forecastlab.macro as macro
from forecastlab.macro import (
    BLS_REVISION_BASIS,
    FRED_GRAPH_CSV,
    FRED_MIRROR_REVISION_BASIS,
    SERIES,
    LiveMonthlyCache,
    MacroDataError,
    MacroSpec,
    bls_indicators,
    fetch_latest_macro_snapshots,
    fetch_macro,
    published_level_periods,
)
from forecastlab.macro_evidence import validate_snapshot
from forecastlab.root_event import digest

NOW = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)  # 06:00 New York, before the 08:30 release time
QUOTA = {"status": "REQUEST_NOT_PROCESSED", "responseTime": 12, "Results": {},
         "message": ["Request could not be serviced, as the daily threshold for total number of requests "
                     "allocated to the user has been reached."]}
SPECS = {
    "unemployment": MacroSpec(indicator="unemployment", observation_period="2026-10", threshold=4.3,
                              release_at=datetime(2026, 11, 6, 13, 30, tzinfo=UTC)),
    "payrolls": MacroSpec(indicator="payrolls", observation_period="2026-10", threshold=50000,
                          release_at=datetime(2026, 11, 6, 13, 30, tzinfo=UTC)),
    "cpi": MacroSpec(indicator="cpi", observation_period="2026-09", threshold=3.0,
                     release_at=datetime(2026, 10, 14, 12, 30, tzinfo=UTC)),
}


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    clock = {"now": NOW}
    monkeypatch.setattr(macro, "utcnow", lambda: clock["now"])
    return clock


def _levels(indicator: str, *, start: tuple[int, int] = (2017, 1), end: tuple[int, int] = (2026, 9)) -> dict[str, str]:
    output = {}
    index = 0
    year, month = start
    while (year, month) <= end:
        if indicator == "unemployment":
            value = f"{4.0 + (index % 7) / 10:.1f}"
        elif indicator == "payrolls":
            value = str(150000 + 120 * index)
        else:
            value = f"{240 * 1.0025 ** index:.3f}"
        output[f"{year}-{month:02d}"] = value
        index += 1
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return output


def _bls(indicator: str, levels: dict[str, str]) -> dict:
    return {"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"seriesID": SERIES[indicator]["bls"], "data": [
        {"year": period[:4], "period": "M" + period[5:], "value": value, "footnotes": [{}]}
        for period, value in sorted(levels.items(), reverse=True)]}]}}


def _csv(indicator: str, levels: dict[str, str]) -> bytes:
    fred_id = SERIES[indicator]["fred"]
    return ("observation_date," + fred_id + "\n" +
            "".join(f"{period}-01,{value}\n" for period, value in sorted(levels.items()))).encode()


def _client(calls: list[str], *, bls, fred=None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "api.bls.gov":
            return bls(request) if callable(bls) else bls
        if request.url.host == "fred.stlouisfed.org" and fred is not None:
            return fred(request) if callable(fred) else fred
        raise AssertionError(f"unexpected request {request.url}")
    return httpx.Client(transport=httpx.MockTransport(handler))


def _fred_ok(levels_by_indicator: dict[str, dict[str, str]]):
    by_id = {SERIES[name]["fred"]: name for name in levels_by_indicator}

    def respond(request: httpx.Request) -> httpx.Response:
        indicator = by_id[request.url.params["id"]]
        return httpx.Response(200, content=_csv(indicator, levels_by_indicator[indicator]),
                              headers={"content-type": "text/csv"})
    return respond


def _identity(snapshot) -> list[tuple]:
    return [(row.period, row.value, row.series_id, row.units, row.seasonal_adjustment) for row in snapshot.observations]


@pytest.mark.parametrize("indicator", bls_indicators())
def test_quota_falls_back_to_fred_mirror_with_same_window_and_normalization(indicator):
    spec, levels = SPECS[indicator], _levels(indicator)
    calls: list[str] = []
    with _client(calls, bls=httpx.Response(200, json=_bls(indicator, levels))) as http:
        direct = fetch_macro(spec, client=http)
    assert len(calls) == 1 and direct.observations[-1].revision_basis == BLS_REVISION_BASIS

    calls.clear()
    with _client(calls, bls=httpx.Response(200, json=QUOTA), fred=_fred_ok({indicator: levels})) as http:
        mirror = fetch_macro(spec, client=http)
    fred_id = SERIES[indicator]["fred"]
    # One refused BLS request, then one keyless FRED CSV over the same ten calendar years.
    assert calls[0].startswith(macro.BLS_API_V1)
    assert calls[1:] == [f"{FRED_GRAPH_CSV}?id={fred_id}&cosd=2017-01-01"]
    # Same deterministic normalization: payroll changes in jobs, CPI 12-month percent rounded to 0.1.
    assert _identity(mirror) == _identity(direct)
    assert all(row.series_id == SERIES[indicator]["bls"] for row in mirror.observations)
    assert {row.source_url for row in mirror.observations} == {f"https://fred.stlouisfed.org/series/{fred_id}"}
    assert {row.revision_basis for row in mirror.observations} == {FRED_MIRROR_REVISION_BASIS}
    assert mirror.source_lineage == "agency:bls"
    assert mirror.raw_payload["bls_fallback_reason"] == "bls_status:REQUEST_NOT_PROCESSED"
    assert mirror.raw_payload["bls_response"] == QUOTA
    assert mirror.raw_payload["mirror_of"] == {"agency": "bls", "bls_series_id": SERIES[indicator]["bls"],
                                               "fred_series_id": fred_id}
    assert mirror.raw_hash == digest(mirror.raw_payload)
    assert all(row.period < spec.observation_period for row in mirror.observations)
    assert mirror.retrieved_at == NOW and all(row.available_at == NOW for row in mirror.observations)
    if indicator == "payrolls":
        assert mirror.observations[-1].value == 120000 and mirror.observations[-1].units == "jobs"
    if indicator == "cpi":
        assert mirror.observations[-1].value == round(mirror.observations[-1].value, 1)


def test_http_429_from_bls_also_falls_back():
    calls: list[str] = []
    levels = _levels("unemployment")
    with _client(calls, bls=httpx.Response(429), fred=_fred_ok({"unemployment": levels})) as http:
        snapshot = fetch_macro(SPECS["unemployment"], client=http)
    assert snapshot.raw_payload["bls_fallback_reason"] == "bls_http_429"
    assert snapshot.raw_payload["bls_response"] is None
    assert snapshot.observations[-1].revision_basis == FRED_MIRROR_REVISION_BASIS


def test_other_bls_failures_do_not_fall_back():
    calls: list[str] = []
    with _client(calls, bls=httpx.Response(500)) as http, pytest.raises(MacroDataError, match="^macro_request_failed:HTTPStatusError$"):
        fetch_macro(SPECS["unemployment"], client=http)
    assert len(calls) == 1


@pytest.mark.parametrize(("fred", "reason"), [
    (httpx.Response(503), "HTTPStatusError"),
    (httpx.Response(200, content=b"observation_date,WRONG\n2026-09-01,4.3\n"), "fred_csv_malformed"),
    (httpx.Response(200, content=b"observation_date,UNRATE\n2026-09-15,4.3\n"), "fred_csv_malformed"),
])
def test_mirror_failure_keeps_the_provider_failure_prefix(fred, reason):
    calls: list[str] = []
    with _client(calls, bls=httpx.Response(200, json=QUOTA), fred=fred) as http, pytest.raises(MacroDataError) as error:
        fetch_macro(SPECS["unemployment"], client=http)
    # Executors map this prefix to a provider failure, not to missing evidence.
    assert str(error.value) == f"bls_request_not_succeeded:fred_mirror_failed:{reason}"


def test_mirror_blank_month_is_skipped_and_never_bridged():
    levels = _levels("payrolls")
    levels["2025-10"] = "."  # FRED publishes the October 2025 shutdown gap as a blank.
    calls: list[str] = []
    with _client(calls, bls=httpx.Response(200, json=QUOTA), fred=_fred_ok({"payrolls": levels})) as http:
        snapshot = fetch_macro(SPECS["payrolls"], client=http)
    periods = {row.period for row in snapshot.observations}
    assert "2025-10" not in periods and "2025-11" not in periods and "2025-12" in periods


def test_mirror_keeps_target_and_staleness_checks():
    calls: list[str] = []
    stale = _levels("unemployment", end=(2026, 6))
    with _client(calls, bls=httpx.Response(200, json=QUOTA), fred=_fred_ok({"unemployment": stale})) as http, \
            pytest.raises(MacroDataError, match="macro_current_conditions_stale"):
        fetch_macro(SPECS["unemployment"], client=http)
    early = MacroSpec(indicator="unemployment", observation_period="2026-08", threshold=4.3,
                      release_at=datetime(2026, 9, 4, 12, 30, tzinfo=UTC))
    with _client(calls, bls=httpx.Response(200, json=QUOTA), fred=_fred_ok({"unemployment": _levels("unemployment")})) as http:
        snapshot = fetch_macro(early, client=http)
    assert snapshot.observations[-1].period == "2026-07"


def test_selection_snapshots_fall_back_and_cpi_prior_year_check_reads_the_mirror():
    from forecastlab.question_selection import ScheduledRelease, choose_questions

    levels = {name: _levels(name, start=(2024, 1), end=(2026, 8)) for name in bls_indicators()}
    calls: list[str] = []
    with _client(calls, bls=httpx.Response(200, json=QUOTA), fred=_fred_ok(levels)) as http:
        snapshots = fetch_latest_macro_snapshots(client=http)
    assert len(calls) == 4 and all("cosd=2024-01-01" in url for url in calls[1:])
    for name in bls_indicators():
        latest = snapshots[name].observations[-1]
        assert (latest.series_id, latest.revision_basis) == (SERIES[name]["bls"], FRED_MIRROR_REVISION_BASIS)
        assert snapshots[name].source_lineage == "agency:bls"
    assert "2025-09" in published_level_periods(snapshots["cpi"])

    release = ScheduledRelease(family="cpi", observation_period="2026-09", release_at=datetime(2026, 10, 14, 12, 30, tzinfo=UTC),
                               source_url="https://www.bls.gov/schedule/2026/home.htm", source_hash="a" * 64, checked_at=NOW)
    suggestions, gaps = choose_questions([release], snapshots, now=NOW)
    assert [item.macro.indicator for item in suggestions] == ["cpi"]
    assert not [gap for gap in gaps if gap.startswith("cpi")]

    missing = dict(levels, cpi={k: v for k, v in levels["cpi"].items() if k != "2025-09"})
    with _client([], bls=httpx.Response(200, json=QUOTA), fred=_fred_ok(missing)) as http:
        snapshots = fetch_latest_macro_snapshots(client=http)
    suggestions, gaps = choose_questions([release], snapshots, now=NOW)
    assert suggestions == [] and any("prior-year index is missing" in gap for gap in gaps)


# --- Live monthly cache ------------------------------------------------------------------------

def test_cache_reuses_one_response_per_indicator_and_keeps_point_in_time_checks(frozen_clock):
    cache = LiveMonthlyCache()
    calls: list[str] = []
    payloads = {name: _bls(name, _levels(name)) for name in bls_indicators()}

    def bls(request):
        series = request.read().decode()
        name = next(n for n in bls_indicators() if SERIES[n]["bls"] in series)
        return httpx.Response(200, json=payloads[name])

    with _client(calls, bls=bls) as http:
        first = fetch_macro(SPECS["unemployment"], client=http, cache=cache)
        frozen_clock["now"] = NOW + timedelta(minutes=30)
        earlier_target = MacroSpec(indicator="unemployment", observation_period="2026-08", threshold=4.3,
                                   release_at=datetime(2026, 9, 4, 12, 30, tzinfo=UTC))
        second = fetch_macro(earlier_target, client=http, cache=cache)
        fetch_macro(SPECS["payrolls"], client=http, cache=cache)
    assert len(calls) == 2  # one per indicator
    # The reused response keeps its original retrieval time and is re-filtered per target.
    assert second.retrieved_at == first.retrieved_at == NOW
    assert second.observations[-1].period == "2026-07" and first.observations[-1].period == "2026-09"
    validate_snapshot(second, earlier_target, NOW + timedelta(minutes=30))
    with pytest.raises(MacroDataError, match="after_cutoff"):
        validate_snapshot(second, earlier_target, NOW - timedelta(minutes=1))


def test_cache_stores_mirror_responses_but_never_failures(frozen_clock):
    cache = LiveMonthlyCache()
    calls: list[str] = []
    state = {"fred": httpx.Response(503)}
    levels = _levels("unemployment")
    with _client(calls, bls=httpx.Response(200, json=QUOTA), fred=lambda request: state["fred"]) as http:
        with pytest.raises(MacroDataError, match="fred_mirror_failed"):
            fetch_macro(SPECS["unemployment"], client=http, cache=cache)
        state["fred"] = httpx.Response(200, content=_csv("unemployment", levels))
        first = fetch_macro(SPECS["unemployment"], client=http, cache=cache)
        again = fetch_macro(SPECS["unemployment"], client=http, cache=cache)
    assert len(calls) == 4  # failed pair, successful pair, then a cache hit
    assert again.raw_hash == first.raw_hash and again.observations[-1].revision_basis == FRED_MIRROR_REVISION_BASIS


@pytest.mark.parametrize(("retrieved", "now", "reusable"), [
    (datetime(2026, 10, 3, 13, 0, tzinfo=UTC), datetime(2026, 10, 3, 13, 59, tzinfo=UTC), True),
    # 08:00 then 08:31 New York (EDT): a BLS release time lies in between.
    (datetime(2026, 10, 3, 12, 0, tzinfo=UTC), datetime(2026, 10, 3, 12, 31, tzinfo=UTC), False),
    # Standard time: 08:30 New York is 13:30 UTC.
    (datetime(2026, 12, 4, 13, 0, tzinfo=UTC), datetime(2026, 12, 4, 13, 31, tzinfo=UTC), False),
    (datetime(2026, 12, 4, 12, 31, tzinfo=UTC), datetime(2026, 12, 4, 13, 29, tzinfo=UTC), True),
    # Older than one hour.
    (datetime(2026, 10, 3, 14, 0, tzinfo=UTC), datetime(2026, 10, 3, 15, 1, tzinfo=UTC), False),
    # 23:50 and 00:10 New York are different days.
    (datetime(2026, 10, 3, 3, 50, tzinfo=UTC), datetime(2026, 10, 3, 4, 10, tzinfo=UTC), False),
    # A retrieval in the future of "now" is never reused.
    (datetime(2026, 10, 3, 14, 0, tzinfo=UTC), datetime(2026, 10, 3, 13, 59, tzinfo=UTC), False),
])
def test_cache_reuse_window(retrieved, now, reusable):
    assert LiveMonthlyCache.reusable(retrieved, now) is reusable


def test_cache_refetches_after_the_release_time(frozen_clock):
    cache = LiveMonthlyCache()
    calls: list[str] = []
    frozen_clock["now"] = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)  # 08:00 New York
    with _client(calls, bls=httpx.Response(200, json=_bls("unemployment", _levels("unemployment")))) as http:
        fetch_macro(SPECS["unemployment"], client=http, cache=cache)
        frozen_clock["now"] = datetime(2026, 10, 3, 12, 31, tzinfo=UTC)
        later = fetch_macro(SPECS["unemployment"], client=http, cache=cache)
    assert len(calls) == 2 and later.retrieved_at == frozen_clock["now"]


def test_historical_requests_never_use_the_cache():
    cache = LiveMonthlyCache()
    cache.put(("unemployment", NOW.year - 9), macro.MonthlyFetch(values={"2026-09": 99.0}, notes={}, payload={},
              source_url="x", revision_basis="x", retrieved_at=NOW))
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"observations": [{"date": "2026-08-01", "value": "4.2"}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        snapshot = fetch_macro(SPECS["unemployment"], as_of=datetime(2026, 9, 30, tzinfo=UTC), fred_api_key="test",
                               client=http, cache=cache)
    assert len(seen) == 1 and snapshot.observations[-1].value == 4.2


def test_root_packet_names_the_mirror_and_keeps_bls_lineage():
    from forecastlab_api.root_executor import _macro_packet

    calls: list[str] = []
    with _client(calls, bls=httpx.Response(200, json=QUOTA),
                 fred=_fred_ok({"unemployment": _levels("unemployment")})) as http:
        snapshot = fetch_macro(SPECS["unemployment"], client=http)
    (item,) = _macro_packet(snapshot.model_dump(mode="json"))
    assert item["title"] == "BLS unemployment observations (FRED mirror UNRATE)"
    assert item["source_lineage"] == "agency:bls" and item["primary_source"]
    assert item["url"] == "https://fred.stlouisfed.org/series/UNRATE"
    assert item["revision_basis"] == FRED_MIRROR_REVISION_BASIS
    assert set(item["required_sections"]) == {"current_conditions", "reference_class"}
