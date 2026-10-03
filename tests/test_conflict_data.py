"""Conflict panel data layer: UCDP release discovery, downloads, parsing and aggregation."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import httpx
import numpy as np
import pytest
from tests.conflict_support import CANDIDATE_Q, FINAL, FIXTURES, SELECTION, fixture_files

from forecastlab.conflict.ged import (
    STANDARD_GED_COLUMNS,
    GedFormatError,
    build_panel,
    compare_vintages,
    iter_event_rows,
    read_candidate_release,
    read_final_release,
)
from forecastlab.conflict.months import format_month, from_views_month_id, parse_month, views_month_id
from forecastlab.conflict.panel import load_panel, save_panel
from forecastlab.conflict.sources import (
    RawStore,
    SourceError,
    UcdpRelease,
    candidate_cumulative_release,
    candidate_monthly_release,
    download_file,
    ged_final_release,
    make_client,
    parse_downloads_index,
    reference_candidate_release,
    select_releases,
)


def _cell(panel, country_id: int, month: str, series: str = "total") -> int:
    return int(panel.series(series)[panel.country_index(country_id), panel.month_index(parse_month(month))])


def test_month_arithmetic_round_trips() -> None:
    assert format_month(parse_month("2026-08")) == "2026-08"
    assert parse_month("2026-09") - parse_month("2026-08") == 1
    assert parse_month("2027-01") - parse_month("2026-12") == 1
    assert views_month_id(parse_month("1980-01")) == 1
    assert views_month_id(parse_month("2026-09")) == 561
    assert from_views_month_id(530) == parse_month("2024-02")
    with pytest.raises(ValueError):
        parse_month("2026-13")


def test_discovers_latest_final_and_contiguous_candidates() -> None:
    catalog = parse_downloads_index((FIXTURES / "ucdp_downloads_index.html").read_text())
    versions = {release.version for release in catalog}
    assert {"25.1", "26.1", "26.01.26.03", "26.01.26.06", "26.0.5", "26.0.6", "26.0.7", "26.0.8"} <= versions
    assert all(release.url.startswith("https://ucdp.uu.se/downloads/") for release in catalog)
    selection = select_releases(catalog)
    assert [release.version for release in selection.releases] == ["26.1", "26.01.26.06", "26.0.7", "26.0.8"]
    assert format_month(selection.final.last_month) == "2025-12"
    assert format_month(selection.last_month) == "2026-08"
    assert selection.notes == ()


def test_selection_stops_at_a_missing_candidate_month() -> None:
    catalog = [
        ged_final_release(26, 1),
        candidate_cumulative_release(26, 6),
        candidate_monthly_release(26, 8),
    ]
    selection = select_releases(catalog)
    assert format_month(selection.last_month) == "2026-06"
    assert selection.notes == ("missing_candidate_month_2026-07",)
    assert ged_final_release(26, 1).csv_member == "GEDEvent_v26_1.csv"
    reference = reference_candidate_release(ged_final_release(26, 1))
    assert reference.version == "25.01.25.12"
    assert reference.kind == "candidate_reference"


def test_selection_without_a_final_release_fails() -> None:
    with pytest.raises(SourceError):
        select_releases([candidate_monthly_release(26, 1)])


def _transport(payloads: dict[str, bytes], calls: list[str], *, declared: dict[str, str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url}")
        body = payloads.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        headers = {"last-modified": "Mon, 20 Jul 2026 17:46:11 GMT"}
        if request.method == "HEAD":
            headers["content-length"] = str(len(body))
        if declared and str(request.url) in declared:
            headers["content-length"] = declared[str(request.url)]
        return httpx.Response(200, content=b"" if request.method == "HEAD" else body, headers=headers)

    return httpx.MockTransport(handler)


def test_raw_store_records_hashes_and_reuses_cache(tmp_path: Path) -> None:
    url = "https://ucdp.uu.se/downloads/candidateged/GEDEvent_v26_0_8.csv"
    body = b"id,country\n1,France\n"
    calls: list[str] = []
    store = RawStore(tmp_path / "raw")
    with make_client(transport=_transport({url: body}, calls)) as client:
        record = store.ensure(url, client=client)
        assert record.sha256 == hashlib.sha256(body).hexdigest()
        assert record.bytes == len(body)
        assert record.last_modified == "Mon, 20 Jul 2026 17:46:11 GMT"
        assert store.ensure(url, client=client) == record
        assert calls == [f"GET {url}"]
        store.ensure(url, client=client, revalidate=True)
        assert calls[-1] == f"HEAD {url}"
        store.ensure(url, client=client, refresh=True)
        assert calls[-1] == f"GET {url}"
    manifest = json.loads((tmp_path / "raw" / "raw_manifest.json").read_text())
    assert manifest["files"][0]["sha256"] == record.sha256
    reopened = RawStore(tmp_path / "raw")
    assert reopened.ensure(url, client=None) == reopened.record(url)
    (tmp_path / "raw" / record.filename).write_bytes(b"tampered")
    with pytest.raises(SourceError, match="hash_mismatch"):
        reopened.ensure(url, client=None)


def test_downloads_are_limited_to_allowed_https_hosts(tmp_path: Path) -> None:
    calls: list[str] = []
    with make_client(transport=_transport({}, calls)) as client:
        for url in ("http://ucdp.uu.se/downloads/x.csv", "https://example.org/data.csv"):
            with pytest.raises(SourceError, match="url_not_allowed"):
                download_file(url, tmp_path / "x.csv", client=client)
        with pytest.raises(SourceError, match="http_404"):
            download_file("https://ucdp.uu.se/downloads/missing.csv", tmp_path / "missing.csv", client=client)
    assert calls == ["GET https://ucdp.uu.se/downloads/missing.csv"]
    assert not (tmp_path / "missing.csv").exists()
    with pytest.raises(SourceError, match="offline_and_not_cached"):
        RawStore(tmp_path / "raw").ensure("https://ucdp.uu.se/downloads/x.csv", client=None)


def test_truncated_downloads_are_rejected(tmp_path: Path) -> None:
    url = "https://ucdp.uu.se/downloads/ged/ged261-csv.zip"
    calls: list[str] = []
    with make_client(transport=_transport({url: b"abc"}, calls, declared={url: "10"})) as client:
        with pytest.raises(SourceError, match="truncated_download"):
            download_file(url, tmp_path / "ged.zip", client=client)
    assert not (tmp_path / "ged.zip").exists()


def test_builds_zero_filled_country_month_panel(tmp_path: Path) -> None:
    panel = build_panel(SELECTION, fixture_files(tmp_path))
    assert format_month(panel.first_month) == "2010-01"
    assert format_month(panel.last_month) == "2012-03"
    assert panel.n_months == 27
    assert list(panel.country_ids) == [2, 220, 625, 626]
    assert panel.iso3 == ("USA", "FRA", "SDN", "SSD")
    # Violence types are kept apart and summed into the total.
    assert _cell(panel, 625, "2010-01", "sb") == 10
    assert _cell(panel, 625, "2010-01", "ns") == 5
    assert _cell(panel, 625, "2010-01") == 15
    assert _cell(panel, 625, "2010-03", "os") == 30
    # Months without events are explicit zeros; an event with best = 0 adds nothing.
    assert _cell(panel, 625, "2010-02") == 0
    assert _cell(panel, 220, "2011-12") == 0
    assert _cell(panel, 220, "2010-03") == 2
    assert np.all(panel.valid[panel.country_index(220)])
    # The final release supplies its own months; candidate copies of earlier months are ignored.
    assert _cell(panel, 625, "2011-12") == 100
    # The later Candidate file replaces an event listed again with a revised estimate (7 -> 9).
    assert _cell(panel, 625, "2012-01") == 9
    assert _cell(panel, 625, "2012-03") == 12
    assert _cell(panel, 2, "2012-02") == 1
    # Events after the last covered month are excluded.
    assert int(panel.series("total")[panel.country_index(626)].sum()) == 40
    # South Sudan exists from July 2011 only.
    ssd = panel.country_index(626)
    assert not panel.valid[ssd, panel.month_index(parse_month("2011-06"))]
    assert panel.valid[ssd, panel.month_index(parse_month("2011-07"))]
    assert panel.metadata["candidate_events_used"] == 3
    counts = {item["version"]: item["counts"] for item in panel.metadata["candidate_release_counts"]}
    assert counts["12.01.12.02"]["before_candidate_period"] == 1
    assert counts["12.0.3"]["replaced_earlier_copy"] == 1
    assert counts["12.0.3"]["after_last_covered_month"] == 1
    assert panel.metadata["final_release_counts"] == {"rows": 8, "used": 7, "outside_coverage": 1}


def test_clear_only_policy_drops_flagged_candidate_events(tmp_path: Path) -> None:
    panel = build_panel(SELECTION, fixture_files(tmp_path), candidate_status="clear")
    assert _cell(panel, 625, "2012-01") == 7  # the flagged revision is excluded
    assert 2 not in panel.country_ids.tolist()  # its only event was flagged
    assert panel.metadata["candidate_status_policy"] == "clear"


def test_fixed_country_list_adds_zero_rows_and_counts_dropped_events(tmp_path: Path) -> None:
    countries = {220: ("France", "Europe"), 625: ("Sudan", "Africa"), 840: ("Philippines", "Asia")}
    panel = build_panel(SELECTION, fixture_files(tmp_path), countries=countries)
    assert list(panel.country_ids) == [220, 625, 840]
    assert int(panel.series("total")[panel.country_index(840)].sum()) == 0
    assert panel.metadata["fatalities_outside_country_list"] == {"2": 1, "626": 40}


def test_panel_round_trips_through_csv(tmp_path: Path) -> None:
    panel = build_panel(SELECTION, fixture_files(tmp_path))
    save_panel(panel, tmp_path / "panel.csv.gz", tmp_path / "panel.json")
    loaded = load_panel(tmp_path / "panel.csv.gz", tmp_path / "panel.json")
    assert list(loaded.country_ids) == list(panel.country_ids)
    assert loaded.names == panel.names and loaded.regions == panel.regions and loaded.iso3 == panel.iso3
    assert np.array_equal(loaded.valid, panel.valid)
    for name in ("sb", "ns", "os", "total"):
        assert np.array_equal(loaded.series(name), panel.series(name))
    manifest = json.loads((tmp_path / "panel.json").read_text())
    assert manifest["last_month"] == "2012-03"
    assert manifest["n_valid_country_months"] == int(panel.valid.sum())


def test_parser_rejects_missing_columns_and_bad_rows() -> None:
    with pytest.raises(GedFormatError, match="missing columns"):
        list(iter_event_rows(io.StringIO("id,country\n1,France\n"), source="bad.csv"))
    header = "id,country_id,country,region,type_of_violence,date_start,best\n"
    with pytest.raises(GedFormatError, match="unknown type_of_violence"):
        list(iter_event_rows(io.StringIO(header + "1,220,France,Europe,4,2020-01-01,1\n"), source="bad.csv"))
    with pytest.raises(GedFormatError, match="negative"):
        list(iter_event_rows(io.StringIO(header + "1,220,France,Europe,1,2020-01-01,-1\n"), source="bad.csv"))
    with pytest.raises(GedFormatError, match="bad.csv:2"):
        list(iter_event_rows(io.StringIO(header + "1,220,France,Europe,1,not-a-date,1\n"), source="bad.csv"))
    rows = list(iter_event_rows(io.StringIO(header + "7,220,France,Europe,1,2020-02-03,4\n"), source="ok.csv"))
    assert rows[0].code_status == "Clear" and rows[0].month == parse_month("2020-02")


def test_headerless_file_with_standard_layout_is_read(tmp_path: Path) -> None:
    values = dict.fromkeys(STANDARD_GED_COLUMNS, "")
    values.update(
        {"id": "506696", "code_status": "Clear", "type_of_violence": "3", "country": "Afghanistan", "country_id": "700",
         "region": "Asia", "date_start": "2024-01-02 00:00:00.000", "best": "2"}
    )
    line = ",".join(f'"{values[name]}"' for name in STANDARD_GED_COLUMNS)
    path = tmp_path / "GEDEvent_v24_0_1.csv"
    path.write_text(line + "\n" + line.replace("506696", "506697") + "\n")
    release = UcdpRelease("candidate_monthly", "24.0.1", "https://ucdp.uu.se/downloads/candidateged/GEDEvent_v24_0_1.csv",
                          parse_month("2024-01"), parse_month("2024-01"))
    rows = read_candidate_release(path, release)
    assert [row.event_id for row in rows] == [506696, 506697]
    assert rows[0].country_id == 700 and rows[0].best == 2


def test_vintage_check_counts_threshold_disagreements(tmp_path: Path) -> None:
    files = fixture_files(tmp_path)
    final = read_final_release(files[FINAL.url], FINAL)
    candidate = read_candidate_release(FIXTURES / "candidate_12_01_12_02.csv", CANDIDATE_Q)
    result = compare_vintages(final.sums, candidate, year=2011)
    assert result["final_fatalities_best"] == 140  # South Sudan 40 + Sudan 100 (+ France 0)
    summary = result["candidate_all_events"]
    assert summary["fatalities_best"] == 50
    assert summary["thresholds"]["ge_1"] == {"both": 1, "candidate_only": 0, "final_only": 1}
    assert summary["thresholds"]["ge_100"] == {"both": 0, "candidate_only": 0, "final_only": 1}
    assert result["candidate_clear_events_only"]["fatalities_best"] == 50
