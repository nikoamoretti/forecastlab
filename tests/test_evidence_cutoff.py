from __future__ import annotations

from datetime import UTC, datetime, timedelta

from forecastlab.engine import run_forecast_engine
from forecastlab.fetch import fetch_document
from forecastlab.http_client import SafeResponse
from forecastlab.providers.mock import SAMPLE_QUESTION, MockModelProvider, MockSearchProvider
from forecastlab.schemas import SearchHit
from forecastlab.wayback import mock_snapshots, nearest_eligible_snapshot


def test_no_eligible_snapshot_rejects_without_current_page() -> None:
    as_of = datetime(2024, 1, 1, tzinfo=UTC)
    doc = fetch_document(
        "https://example.org/current-undated",
        as_of=as_of,
        allow_local_fixtures=False,
        mode="backtest",
    )
    assert doc.rejected is True
    assert doc.rejection_reason == "no_eligible_historical_snapshot"
    assert doc.text == ""


def test_current_undated_page_rejected_in_backtest() -> None:
    as_of = datetime(2024, 1, 1, tzinfo=UTC)
    doc = fetch_document(
        "https://fixtures.forecastlab.local/undated",
        as_of=as_of,
        allow_local_fixtures=True,
        mode="backtest",
    )
    assert doc.rejected is True
    assert doc.rejection_reason == "unverifiable_as_of"


def test_live_undated_page_is_labeled() -> None:
    doc = fetch_document(
        "https://fixtures.forecastlab.local/undated",
        allow_local_fixtures=True,
        mode="live",
    )
    assert doc.rejected is False
    assert doc.published_at is None
    assert doc.published_at_unknown is True


def test_snapshot_after_cutoff_rejected() -> None:
    as_of = datetime(2024, 1, 1, tzinfo=UTC)
    snaps = mock_snapshots("https://example.org/report")
    late = [item for item in snaps if item.timestamp > as_of]
    assert late
    assert nearest_eligible_snapshot(late, as_of) is None
    doc = fetch_document(
        "https://example.org/report",
        as_of=as_of,
        snapshot_url=late[0].snapshot_url,
        snapshot_at=late[0].timestamp,
        mode="backtest",
    )
    assert doc.rejection_reason == "snapshot_after_as_of"


def test_newest_eligible_prior_snapshot_is_used() -> None:
    as_of = datetime(2024, 12, 1, tzinfo=UTC)
    nearest = nearest_eligible_snapshot(mock_snapshots("https://example.org/report"), as_of)
    assert nearest is not None
    assert nearest.timestamp <= as_of
    later = nearest.timestamp + timedelta(seconds=1)
    assert later > nearest.timestamp


def test_rejected_documents_never_enter_forecast_packet() -> None:
    result = run_forecast_engine(
        question=SAMPLE_QUESTION,
        contract=None,
        profile_id="three_track_ensemble",
        mode="backtest",
        as_of=datetime(2024, 1, 1, tzinfo=UTC),
        model=MockModelProvider(),
        search=MockSearchProvider(),
        allow_local_fixtures=True,
    )
    accepted = [item for track in result.tracks for item in track.evidence]
    rejected = [item for track in result.tracks for item in track.rejected]
    assert rejected
    assert all(item.get("rejected") for item in rejected)
    assert all(not item.get("rejected") for item in accepted)
    accepted_ids = {item["id"] for item in accepted}
    for track in result.tracks:
        if track.forecast:
            for driver in track.forecast.key_drivers:
                assert all(eid in accepted_ids or driver.inference for eid in driver.evidence_ids)


def test_search_snippets_are_not_evidence(monkeypatch) -> None:
    secret = "SEARCH_SNIPPET_AFTER_CUTOFF"

    class SnippetSearch:
        name = "mock"

        def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
            return [
                SearchHit(
                    title="Current page",
                    url="https://fixtures.forecastlab.local/undated",
                    snippet=secret,
                    score=1.0,
                )
            ]

    result = run_forecast_engine(
        question=SAMPLE_QUESTION,
        contract=None,
        profile_id="three_track_ensemble",
        mode="backtest",
        as_of=datetime(2024, 1, 1, tzinfo=UTC),
        model=MockModelProvider(),
        search=SnippetSearch(),
        allow_local_fixtures=True,
    )
    packet_text = " ".join(item.get("excerpt") or "" for track in result.tracks for item in track.evidence)
    assert secret not in packet_text
    assert all(item.get("rejected") for track in result.tracks for item in track.rejected)


def test_cdx_query_includes_cutoff(monkeypatch) -> None:
    from forecastlab import wayback

    captured: dict[str, str] = {}

    def fake_get(url: str, **kwargs):
        captured["url"] = url
        return SafeResponse(
            url=url,
            final_url=url,
            status_code=200,
            content=b'[["timestamp","original","statuscode"],["20240101120000","https://example.org","200"]]',
            content_type="application/json",
        )

    monkeypatch.setattr(wayback, "safe_get", fake_get)
    snaps = wayback.discover_snapshots("https://example.org", as_of=datetime(2024, 6, 1, tzinfo=UTC))
    assert "to=20240601000000" in captured["url"]
    assert "filter=statuscode%3A200" in captured["url"] or "filter=statuscode:200" in captured["url"]
    assert snaps[0].timestamp.year == 2024
