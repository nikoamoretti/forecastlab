from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from forecastlab.macro import SERIES, MacroDataError, fetch_latest_macro_snapshots
from forecastlab.question_selection import ScheduledRelease, choose_questions, parse_bls_calendar

NOW = datetime(2026, 9, 4, 20, tzinfo=UTC)


def bls_payload():
    def row(period, value):
        return {"year": period[:4], "period": "M" + period[-2:], "value": str(value), "footnotes": [{}]}
    return {"status": "REQUEST_SUCCEEDED", "Results": {"series": [
        {"seriesID": SERIES["unemployment"]["bls"], "data": [row("2026-08", 4.3)]},
        {"seriesID": SERIES["payrolls"]["bls"], "data": [row("2026-07", 150000), row("2026-08", 150022)]},
        {"seriesID": SERIES["cpi"]["bls"], "data": [row("2025-07", 100), row("2025-08", 101), row("2026-07", 103)]},
    ]}}


@pytest.fixture()
def snapshots(monkeypatch):
    monkeypatch.setattr("forecastlab.macro.utcnow", lambda: NOW)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=bls_payload()))) as http:
        return fetch_latest_macro_snapshots(client=http)


def releases():
    return [ScheduledRelease(family=family, observation_period=period, release_at=datetime.fromisoformat(at),
        source_url="https://www.bls.gov/schedule/2026/home.htm", source_hash="calendar-hash", checked_at=NOW)
        for family, period, at in [
            ("empsit", "2026-08", "2026-09-04T12:30:00+00:00"),
            ("empsit", "2026-09", "2026-10-02T12:30:00+00:00"),
            ("cpi", "2026-07", "2026-08-12T12:30:00+00:00"),
            ("cpi", "2026-08", "2026-09-11T12:30:00+00:00"),
        ]]


def calendar_html(rows):
    return "<p>All times on calendar are Eastern Time.</p><table>" + "".join(
        "<tr>" + "".join(f"<td><p>{cell}</p></td>" for cell in row) + "</tr>" for row in rows) + "</table>"


def test_calendar_uses_explicit_reference_month_and_daylight_saving_time():
    html = calendar_html([
        ("Wednesday, February 11, 2026", "08:30 AM", "Employment Situation for November 2025"),
        ("Friday, April 3, 2026", "08:30 AM", "Employment Situation for March 2026"),
        ("Friday, September 11, 2026", "08:30 AM", "Consumer Price Index for August 2026"),
        ("Friday, September 11, 2026", "10:00 AM", "Other survey for August 2026"),
    ])
    result = parse_bls_calendar(html, source_url="https://www.bls.gov/schedule/2026/home.htm", checked_at=NOW)
    assert len(result) == 3
    assert result[0].observation_period == "2025-11"  # Not January: explicitly delayed.
    assert result[0].release_at.hour == 13
    assert result[1].release_at.hour == 12
    assert result[2].event_id == "bls:cpi:2026-08"
    assert len(result[0].source_hash) == 64


@pytest.mark.parametrize("html,error", [
    ("<html>Access denied</html>", "timezone_evidence"),
    (calendar_html([]), "reference_periods_missing"),
    (calendar_html([("Friday, September 11, 2026", "TBD", "Consumer Price Index for August 2026")]), "release_invalid"),
    (calendar_html([("Friday, September 11, 2026", "08:30 AM", "Consumer Price Index for September 2026")]), "release_invalid"),
])
def test_calendar_cannot_invent_a_release(html, error):
    with pytest.raises(MacroDataError, match=error):
        parse_bls_calendar(html, source_url="https://www.bls.gov", checked_at=NOW)


def test_batch_uses_one_request_and_normalizes_all_three_indicators(monkeypatch):
    monkeypatch.setattr("forecastlab.macro.utcnow", lambda: NOW)
    requests = []
    def send(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=bls_payload())
    with httpx.Client(transport=httpx.MockTransport(send)) as http:
        result = fetch_latest_macro_snapshots(client=http)
    assert len(requests) == 1
    assert set(requests[0]["seriesid"]) == {meta["bls"] for meta in SERIES.values()}
    assert result["payrolls"].observations[-1].value == 22000
    assert result["payrolls"].observations[-1].units == "jobs"
    assert result["cpi"].observations[-1].value == 3.0
    assert result["cpi"].observations[-1].revision_basis == "latest_observed_revisions_not_first_release"
    bad = bls_payload()
    bad["Results"]["series"][0]["seriesID"] = "WRONG"
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=bad))) as http:
        with pytest.raises(MacroDataError, match="series_mismatch"):
            fetch_latest_macro_snapshots(client=http)


def test_selects_nearest_events_without_model_probabilities(snapshots):
    items, gaps = choose_questions(releases(), snapshots, now=NOW)
    assert not gaps
    assert len(items) == 3
    assert items[0].macro.indicator == "cpi"
    assert {item.macro.indicator: item.macro.threshold for item in items} == {"unemployment": 4.3, "payrolls": 22000, "cpi": 3.0}
    assert all(item.macro.release_at > NOW for item in items)
    assert all(item.macro.revision_policy == "first_release" for item in items)
    assert all("probability" not in item.model_dump() for item in items)
    assert [i.id for i in items] == [i.id for i in choose_questions(list(reversed(releases())), snapshots, now=NOW)[0]]
    assert len({item.release_event for item in items}) == 2


def test_duplicates_are_links_and_same_release_is_visible(snapshots):
    cpi, payroll, unemployment = choose_questions(releases(), snapshots, now=NOW)[0]
    tracked = [("existing-cpi", "draft-cpi", cpi.macro),
               ("existing-other-threshold", None, unemployment.macro.model_copy(update={"threshold": 5}))]
    items, _ = choose_questions(releases(), snapshots, now=NOW, tracked=tracked)
    assert items[-1].existing_question_id == "existing-cpi"
    assert items[-1].existing_draft_run_id == "draft-cpi"
    assert items[0].existing_question_id is None
    assert "existing-other-threshold" in items[0].related_question_ids  # Payroll/unemployment share one event.
    assert items[-1].id == cpi.id
    # The completed pilot is related evidence of tracking, not an ordinary draft
    # to resume or an instruction to modify a frozen assignment.
    items, _ = choose_questions(releases(), snapshots, now=NOW, cohort_questions=[("pilot", cpi.macro)])
    assert items[0].existing_question_id is None
    assert items[0].related_question_ids == ["pilot"]


@pytest.mark.parametrize("change", ["units", "series", "adjustment", "future", "stale", "lagging"])
def test_unverified_or_stale_observations_never_become_questions(snapshots, change):
    snapshot = snapshots["unemployment"]
    row = snapshot.observations[0]
    if change == "stale":
        snapshot.retrieved_at = NOW - timedelta(days=2)
    else:
        updates = {"units": {"units": "thousands"}, "series": {"series_id": "WRONG"},
                   "adjustment": {"seasonal_adjustment": "not_seasonally_adjusted"},
                   "future": {"available_at": NOW + timedelta(seconds=1)}, "lagging": {"period": "2026-07"}}
        snapshot.observations = [row.model_copy(update=updates[change])]
    items, gaps = choose_questions(releases(), snapshots, now=NOW)
    assert "unemployment" not in [item.macro.indicator for item in items]
    assert any(gap.startswith("unemployment:") for gap in gaps)


def test_missing_cpi_prior_year_index_and_near_release_are_excluded(snapshots):
    snapshots["cpi"].raw_payload["Results"]["series"][2]["data"][1]["value"] = "-"
    items, gaps = choose_questions(releases(), snapshots, now=NOW)
    assert "cpi" not in [item.macro.indicator for item in items]
    assert any("prior-year" in gap for gap in gaps)
    near = [r.model_copy(update={"release_at": NOW + timedelta(minutes=5)}) for r in releases() if r.release_at > NOW]
    assert choose_questions(near, snapshots, now=NOW)[0] == []


def test_cache_persists_and_coalesces_concurrent_fetches(tmp_path, monkeypatch, snapshots):
    from forecastlab_api import question_suggestions as service
    monkeypatch.setattr(service.settings, "data_dir", tmp_path)
    monkeypatch.setattr(service, "utcnow", lambda: NOW)
    calls = []
    def fetch(now):
        calls.append(now)
        return service.SelectionSources(checked_at=now, releases=releases(), snapshots=snapshots)
    monkeypatch.setattr(service, "_fetch_sources", fetch)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: service.selection_sources(), range(6)))
    assert len(calls) == 1
    assert len(results[0].snapshots) == 3
    assert service.selection_sources() == results[0]
    assert len(calls) == 1
    monkeypatch.setattr(service, "utcnow", lambda: NOW + timedelta(hours=13))
    service.selection_sources()
    assert len(calls) == 2


def test_cache_invalidates_at_release_and_cools_down_source_failures():
    from forecastlab_api.question_suggestions import SelectionSources, _fresh
    release = releases()[0]
    checked = release.release_at - timedelta(minutes=5)
    source = SelectionSources(checked_at=checked, releases=[release])
    assert _fresh(source, checked + timedelta(minutes=1))
    assert not _fresh(source, release.release_at)
    failed = SelectionSources(checked_at=NOW, gaps=["Unavailable"])
    assert _fresh(failed, NOW + timedelta(minutes=9))
    assert not _fresh(failed, NOW + timedelta(minutes=10))
    assert not _fresh(failed, NOW - timedelta(seconds=1))


def test_suggestions_create_no_runs_and_selected_draft_is_free_and_idempotent(client, monkeypatch, snapshots):
    from sqlalchemy import func, select

    from forecastlab.execution import resolve_execution_context
    from forecastlab_api import question_suggestions as service
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ForecastRun, Job, PersonalForecast
    monkeypatch.setattr(service, "utcnow", lambda: NOW)
    monkeypatch.setattr("forecastlab_api.personal_forecasts.resolve_for_question", lambda question: resolve_execution_context(
        requested_mode="live", profile_id="root_event_ensemble_v1", settings={
            "model_provider": "openai", "model_name": "gpt-5-mini", "model_api_key": "test-not-real",
            "search_provider": "tavily", "search_api_key": "test-not-real", "max_cost_usd": 5}))
    monkeypatch.setattr(service, "selection_sources", lambda: service.SelectionSources(
        checked_at=NOW, releases=releases(), snapshots=snapshots))
    monkeypatch.setattr("forecastlab_api.personal_forecasts.build_model_provider", lambda **_: pytest.fail("Selection cannot call a model"))
    before = client.get("/api/question-suggestions")
    assert before.status_code == 200
    first = before.json()["items"][0]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ForecastRun)) == 0
    selected = client.post(f"/api/question-suggestions/{first['id']}/draft")
    assert selected.status_code == 201, selected.text
    draft = selected.json()
    assert draft["status"] == "awaiting_review"
    assert draft["cost_usd"] == 0
    assert draft["result"]["question_selection"] == first
    assert draft["contract"]["normalized_question"] == first["question"]
    assert client.post(f"/api/question-suggestions/{first['id']}/draft").json()["run_id"] == draft["run_id"]
    assert client.get(f"/api/forecast-drafts/{draft['run_id']}").json()["result"]["question_selection"] == first
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ForecastRun)) == 1
        assert db.scalar(select(func.count()).select_from(PersonalForecast)) == 1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.job_type == "forecast_run")) == 0
    after = client.get("/api/question-suggestions").json()["items"]
    assert after[-1]["existing_question_id"] == draft["question_id"]
    assert client.post("/api/question-suggestions/" + "f" * 64 + "/draft").status_code == 409


def test_unavailable_sources_and_expired_suggestions_never_create_work(client, monkeypatch):
    from forecastlab_api import question_suggestions as service
    monkeypatch.setattr(service, "selection_sources", lambda: service.SelectionSources(checked_at=NOW, gaps=["Calendar unavailable"]))
    result = client.get("/api/question-suggestions").json()
    assert not result["items"]
    assert "Calendar unavailable" in result["gaps"]
    assert client.post("/api/question-suggestions/" + "a" * 64 + "/draft").status_code == 409
    assert client.post("/api/question-suggestions/invalid/draft").status_code == 404


def test_official_monthly_lists_replace_unavailable_annual_calendar(monkeypatch, tmp_path):
    from forecastlab_api import question_suggestions as service
    from forecastlab_api.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    original_client = httpx.Client
    calls = []
    def send(request):
        calls.append(str(request.url))
        if request.method == 'POST':
            return httpx.Response(200, json=bls_payload())
        if request.url.path.endswith('home.htm'):
            return httpx.Response(503)
        return httpx.Response(200, text=calendar_html([
            ('Friday, September 11, 2026', '08:30 AM', 'Consumer Price Index for August 2026')]))
    monkeypatch.setattr(service.httpx, 'Client', lambda **kwargs: original_client(transport=httpx.MockTransport(send), **kwargs))
    result = service._fetch_sources(NOW)
    assert result.releases and result.snapshots and not result.gaps
    assert result.diagnostics[0]['error'] == 'HTTP 503'
    assert any('_sched_list.htm' in url for url in calls)
    assert len([url for url in calls if 'api.bls.gov' in url]) == 1
    assert result.documents
