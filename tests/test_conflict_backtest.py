"""Conflict panel backtest, live forecasts and scoring, ViEWS comparison, as-of panels and CLI."""

from __future__ import annotations

import csv
import gzip
import json
import zipfile
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pytest
from tests.conflict_support import FIXTURES, SELECTION, fixture_files, synthetic_panel

from forecastlab.conflict.backtest import headline_table, run_backtest, summarize_backtest, top_countries
from forecastlab.conflict.forecaster import PanelConfig
from forecastlab.conflict.ged import build_panel
from forecastlab.conflict.live import (
    CSV_FIELDS,
    load_live_forecast,
    make_live_forecast,
    score_live_forecasts,
    write_live_artifacts,
)
from forecastlab.conflict.months import format_month, parse_month
from forecastlab.conflict.scoring import brier_score
from forecastlab.conflict.sources import RawStore, make_client
from forecastlab.conflict.views import (
    ViewsRun,
    compare_with_views,
    fetch_run_forecasts,
    forecast_url,
    parse_run_name,
    predictors_dataset,
    select_runs,
    vintage_effect,
)
from forecastlab.conflict.vintage import (
    VintageLibrary,
    asof_cutoff,
    available_releases,
    historical_releases,
    probe_release_dates,
    run_vintage_backtest,
)

CONFIG = PanelConfig(train_start="2002-01")
FIRST_ORIGIN = parse_month("2008-01")


@pytest.fixture(scope="module")
def panel():
    return synthetic_panel()


@pytest.fixture(scope="module")
def records(panel):
    return run_backtest(panel, CONFIG, first_origin=FIRST_ORIGIN)


def test_backtest_splits_score_only_observed_targets(panel, records) -> None:
    cols = records.columns
    assert len(records) > 0
    assert np.all(cols["origin"] >= FIRST_ORIGIN)
    assert np.all(cols["target_month"] == cols["origin"] + cols["horizon"])
    assert np.all(cols["target_month"] <= panel.last_month)
    for horizon in CONFIG.horizons:
        origins = np.unique(cols["origin"][cols["horizon"] == horizon])
        assert origins.min() == FIRST_ORIGIN
        assert origins.max() == panel.last_month - horizon
        assert origins.size == panel.last_month - horizon - FIRST_ORIGIN + 1
    # One row per country that exists at both the origin and the target month.
    one = (cols["horizon"] == 1) & (cols["origin"] == FIRST_ORIGIN)
    expected = panel.valid[:, FIRST_ORIGIN - panel.first_month] & panel.valid[:, FIRST_ORIGIN + 1 - panel.first_month]
    assert one.sum() == expected.sum()
    for fit in records.fits:
        assert fit["latest_training_outcome_month"] == fit["origin"]
        assert fit["n_train"] > 0
    for model in ("logit", "climatology", "persistence"):
        probs = np.stack([records.prob(model, t) for t in CONFIG.thresholds], axis=1)
        assert np.all((probs >= 0) & (probs <= 1))
        assert np.all(np.diff(probs, axis=1) <= 1e-15)
    for name in ("logit_crps_log1p", "climatology_crps_log1p", "persistence_crps_log1p", "logit_mean_log1p"):
        assert np.all(np.isfinite(cols[name])) and np.all(cols[name] >= -1e-12)
    # Outcomes are the observed totals in the target month.
    i = 0
    c = int(np.flatnonzero(panel.country_ids == cols["country_id"][i])[0])
    assert cols["y"][i] == panel.series("total")[c, int(cols["target_month"][i]) - panel.first_month]


def test_backtest_records_save_as_csv(records, tmp_path: Path) -> None:
    path = tmp_path / "records.csv.gz"
    records.save_csv_gz(path)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == len(records)
    assert rows[0]["origin"] == format_month(int(records.columns["origin"][0]))
    assert set(records.columns) == set(rows[0])


def test_summary_reports_overall_top_n_and_france(panel, records) -> None:
    summary = summarize_backtest(records, panel, CONFIG, top_n=3, bootstrap=50)
    assert set(summary["slices"]) == {"overall", "top3", "france"}
    top = top_countries(panel, "total", int(records.columns["target_month"].min()), panel.last_month, 3)
    assert [item["country_id"] for item in summary["top3_countries"]] == top
    block = summary["slices"]["overall"]["1"]["thresholds"]["25"]
    assert block["n"] == int((records.columns["horizon"] == 1).sum())
    assert set(block["models"]) == {"logit", "climatology", "persistence"}
    outcome = (records.columns["y"][records.columns["horizon"] == 1] >= 25).astype(float)
    expected_brier = brier_score(records.prob("logit", 25)[records.columns["horizon"] == 1], outcome)
    assert block["models"]["logit"]["brier"] == pytest.approx(expected_brier, rel=1e-5)
    assert block["brier_skill_vs_climatology"]["logit"] == pytest.approx(
        1 - expected_brier / block["models"]["climatology"]["brier"], rel=1e-4
    )
    assert len(block["logit_brier_skill_95ci_country_bootstrap"]) == 2
    assert "logit_brier_skill_95ci_country_bootstrap" not in summary["slices"]["france"]["1"]["thresholds"]["1"]
    count = summary["slices"]["overall"]["12"]["count"]
    assert {"mae_log1p_median", "mse_log1p_mean", "crps_log1p", "coverage_90"} <= set(count["logit"])
    assert len(summary["calibration_overall"]["1"]["100"]["logit_table"]) == 10
    assert summary["monotonicity"]["rows_with_raw_violation"] >= 0
    rows = headline_table(summary)
    assert {(row["slice"], row["horizon"], row["threshold"]) for row in rows} >= {("overall", 1, 1), ("france", 12, 100)}


def test_live_forecast_artifact_shape(panel, tmp_path: Path) -> None:
    payload = make_live_forecast(panel, CONFIG, top_n=5)
    n_countries = int(panel.valid[:, -1].sum())
    assert payload["origin"] == format_month(panel.last_month)
    assert payload["horizons"] == {str(h): format_month(panel.last_month + h) for h in CONFIG.horizons}
    assert len(payload["forecasts"]) == n_countries * len(CONFIG.horizons)
    assert [row["target_month"] for row in payload["focus_country"]] == list(payload["horizons"].values())
    assert all(row["country_id"] == 220 for row in payload["focus_country"])
    assert len(payload["top_risers"]) == 5 and len(payload["top_next_month_p_ge25"]) == 5
    changes = [row["change_p_ge25"] for row in payload["top_risers"]]
    assert changes == sorted(changes, reverse=True)
    levels = [row["p_ge25"] for row in payload["top_next_month_p_ge25"]]
    assert levels == sorted(levels, reverse=True)
    for row in payload["forecasts"]:
        assert row["p_ge1"] >= row["p_ge25"] >= row["p_ge100"]
        assert row["fatalities_q05"] <= row["fatalities_q50"] <= row["fatalities_q95"]
        assert len(row["bin_location_log1p"]) == 3
    assert {"git_commit", "working_tree_dirty", "conflict_package_sha256", "model_version"} <= set(payload["code"])
    assert payload["data_vintage"]["last_month"] == format_month(panel.last_month)
    json_path, csv_path = write_live_artifacts(payload, tmp_path)
    assert json_path.name == f"live_forecast_{payload['origin']}.json"
    loaded = load_live_forecast(json_path)
    assert loaded["forecasts"] == payload["forecasts"]
    with csv_path.open() as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == CSV_FIELDS
        assert len(list(reader)) == len(payload["forecasts"])
    (tmp_path / "other.json").write_text('{"kind": "something_else"}')
    with pytest.raises(ValueError):
        load_live_forecast(tmp_path / "other.json")


def test_live_forecasts_are_scored_once_outcomes_arrive(panel) -> None:
    origin = panel.last_month - 4
    payload = json.loads(json.dumps(make_live_forecast(panel.truncated(origin), CONFIG)))
    pending_only = score_live_forecasts(payload, panel.truncated(origin))
    assert pending_only["scored_horizons"] == {}
    assert len(pending_only["pending_target_months"]) == 4
    result = score_live_forecasts(payload, panel)
    assert set(result["scored_horizons"]) == {"1", "3"}
    assert result["pending_target_months"] == [format_month(origin + 6), format_month(origin + 12)]
    rows = [row for row in payload["forecasts"] if row["horizon"] == 1]
    target_index = origin + 1 - panel.first_month
    outcome = np.array([panel.series("total")[int(np.flatnonzero(panel.country_ids == row["country_id"])[0]), target_index] for row in rows])
    expected = brier_score(np.array([row["p_ge1"] for row in rows]), (outcome >= 1).astype(float))
    scored = result["scored_horizons"]["1"]
    assert scored["n"] == len(rows)
    assert scored["thresholds"]["1"]["brier"]["logit"] == pytest.approx(expected, rel=1e-5)
    assert scored["count_log1p"]["logit"]["crps"] >= 0


def test_views_run_names_and_selection() -> None:
    run = parse_run_name("fatalities002_2024_01_t01")
    assert run == ViewsRun("fatalities002_2024_01_t01", 2, parse_month("2024-01"), 1)
    assert (run.ln_field, run.dich_field) == ("main_mean_ln", "main_dich")
    assert parse_run_name("fatalities001_2022_06_t01").ln_field == "sc_cm_sb_main"
    for name in ("fatalities001_2022_00_t01", "escwa_2021_02_01", "predictors_fatalities003_0000_00"):
        assert parse_run_name(name) is None
    names = ["fatalities002_2023_09_t01", "fatalities002_2023_09_t02", "fatalities002_2025_10_t01",
             "fatalities003_2025_10_t01", "fatalities001_2023_00_t01", "r_2021_01_01"]
    selected, skipped = select_runs(name for name in names)
    assert [item.name for item in selected] == ["fatalities002_2023_09_t02", "fatalities003_2025_10_t01"]
    assert skipped == ["fatalities002_2023_09_t01", "fatalities002_2025_10_t01"]
    assert predictors_dataset(["predictors_fatalities002_2025_12", "predictors_fatalities003_0000_00"]) == "predictors_fatalities003_0000_00"
    assert forecast_url(run, "main_mean_ln", [parse_month("2024-04"), parse_month("2024-02")]).endswith(
        "fatalities002_2024_01_t01/cm/sb/main_mean_ln?pagesize=10000&month=530&month=532"
    )


def test_views_forecasts_are_fetched_cached_and_keyed_by_ged_country(tmp_path: Path) -> None:
    body = (FIXTURES / "views_run_cm_sb.json").read_bytes()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=body)

    run = parse_run_name("fatalities002_2024_01_t01")
    store = RawStore(tmp_path / "views")
    targets = [parse_month("2024-02"), parse_month("2024-04")]
    with make_client(transport=httpx.MockTransport(handler)) as client:
        values = fetch_run_forecasts(store, run, targets, client=client, pause=0)
    assert len(calls) == 2
    assert values[(220, parse_month("2024-02"))] == {"ln": 0.1361, "p25": 0.0034}
    assert values[(345, parse_month("2024-04"))] == {"ln": 0.01, "p25": 0.001}  # ViEWS 340 -> GED 345
    assert values[(369, parse_month("2024-04"))] == {"ln": 8.5322}
    assert fetch_run_forecasts(store, run, targets, client=None) == values  # served from the cache
    assert len(calls) == 2


def test_views_comparison_scores_both_forecasts_on_the_same_rows(panel) -> None:
    config = PanelConfig(series="sb", train_start="2002-01", horizons=(1, 3))
    origins = [parse_month("2010-01"), parse_month("2010-02"), parse_month("2011-06")]
    sb_records = run_backtest(panel, config, first_origin=origins[0], origins=origins)
    runs = [ViewsRun(f"fatalities002_{format_month(o).replace('-', '_')}_t01", 2, o, 1) for o in origins]
    rng = np.random.default_rng(2)
    views = {}
    cols = sb_records.columns
    for i in range(len(sb_records)):
        name = runs[origins.index(int(cols["origin"][i]))].name
        cell = (int(cols["country_id"][i]), int(cols["target_month"][i]))
        views.setdefault(name, {})[cell] = {
            "ln": float(cols["logit_mean_log1p"][i] + rng.normal(0.8, 0.3)),
            "p25": float(np.clip(cols["p_logit_ge25"][i] + rng.normal(0, 0.2), 0, 1)),
        }
    del views[runs[0].name][next(iter(views[runs[0].name]))]
    result = compare_with_views(sb_records, views, runs, bootstrap=50)
    assert result["rows_matched"] == len(sb_records) - 1
    overall = result["results"]["ucdp_panel"]["all"]
    assert overall["n"] == len(sb_records) - 1
    assert overall["mse_log1p"]["panel_baseline"] < overall["mse_log1p"]["views"]
    assert overall["mse_log1p"]["relative_improvement_vs_views"] > 0
    assert set(result["results"]["ucdp_panel"]) == {"all", "1", "3"}
    effect = vintage_effect(sb_records, sb_records)
    assert effect["all"]["revised_data"] == effect["all"]["asof_data"]


def _vintage_world(tmp_path: Path):
    """Two final releases and Candidate files with publication dates, served by a mock UCDP."""
    header = "id,country_id,country,region,type_of_violence,date_start,best,code_status\n"
    files = {
        "ged/ged111-csv.zip": ("zip", (FIXTURES / "ged_final_mini.csv").read_text(), "Wed, 01 Jun 2011 10:00:00 GMT"),
        "ged/ged121-csv.zip": ("zip", (FIXTURES / "ged_final_mini.csv").read_text(), "Sun, 10 Jun 2012 10:00:00 GMT"),
        "candidateged/GEDEvent_v11_01_11_12.csv": (
            "csv",
            header + "51,625,Sudan,Africa,1,2011-12-20,60,Check deaths\n52,626,South Sudan,Africa,1,2011-09-01,5,Clear\n",
            "Fri, 20 Jan 2012 10:00:00 GMT",
        ),
        "candidateged/GEDEvent_v12_0_1.csv": (
            "csv", header + "61,625,Sudan,Africa,1,2012-01-10,30,Clear\n", "Mon, 20 Feb 2012 10:00:00 GMT"
        ),
    }
    bodies: dict[str, bytes] = {}
    for path, (kind, text, _modified) in files.items():
        url = f"https://ucdp.uu.se/downloads/{path}"
        if kind == "zip":
            archive = tmp_path / path.rsplit("/", 1)[-1]
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("events.csv", text)
            bodies[url] = archive.read_bytes()
        else:
            bodies[url] = text.encode()
    modified = {f"https://ucdp.uu.se/downloads/{path}": item[2] for path, item in files.items()}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url not in bodies:
            return httpx.Response(404)
        headers = {"last-modified": modified[url], "content-length": str(len(bodies[url]))}
        return httpx.Response(200, content=b"" if request.method == "HEAD" else bodies[url], headers=headers)

    return httpx.MockTransport(handler)


def test_asof_panels_use_only_files_published_by_the_cutoff(tmp_path: Path) -> None:
    assert asof_cutoff(parse_month("2024-01")) == date(2024, 2, 29)
    assert asof_cutoff(parse_month("2025-12")) == date(2026, 1, 31)
    with make_client(transport=_vintage_world(tmp_path)) as client:
        catalog = probe_release_dates(historical_releases(2011, 2012), client=client, cache_path=tmp_path / "dates.json")
        assert sorted(item.release.version for item in catalog) == ["11.01.11.12", "11.1", "12.0.1", "12.1"]
        assert [r.version for r in available_releases(catalog, date(2012, 1, 31))] == ["11.1", "11.01.11.12"]
        library = VintageLibrary(RawStore(tmp_path / "raw"), catalog, client=client)
        december = library.panel_at(parse_month("2011-12"))
        assert december is not None and format_month(december.last_month) == "2011-12"
        sudan = december.country_index(625)
        # 2011 comes from the Candidate file (60), not from the later final release (100).
        assert december.series("total")[sudan, december.month_index(parse_month("2011-12"))] == 60
        assert december.series("total")[december.country_index(626), december.month_index(parse_month("2011-08"))] == 0
        assert [item["version"] for item in december.metadata["releases"]] == ["11.1", "11.01.11.12"]
        january = library.panel_at(parse_month("2012-01"))
        assert january is not None and january.series("total")[january.country_index(625), -1] == 30
        assert library.panel_at(parse_month("2012-02")) is None  # no file for February yet
    offline = probe_release_dates(historical_releases(2011, 2012), client=None, cache_path=tmp_path / "dates.json")
    assert sorted(item.release.version for item in offline) == sorted(item.release.version for item in catalog)


def test_vintage_backtest_forecasts_from_asof_data_and_scores_latest(tmp_path: Path) -> None:
    latest = build_panel(SELECTION, fixture_files(tmp_path))
    config = PanelConfig(train_start="2010-06", horizons=(1, 2))
    with make_client(transport=_vintage_world(tmp_path)) as client:
        catalog = probe_release_dates(historical_releases(2011, 2012), client=client, cache_path=tmp_path / "dates.json")
        library = VintageLibrary(RawStore(tmp_path / "raw"), catalog, client=client)
        origins = [parse_month("2011-12"), parse_month("2012-01"), parse_month("2012-02")]
        records, skipped = run_vintage_backtest(latest, library, origins, config)
    assert skipped == [{"origin": "2012-02", "reason": "as-of data do not reach the origin"}]
    cols = records.columns
    assert sorted({format_month(int(o)) for o in cols["origin"]}) == ["2011-12", "2012-01"]
    assert np.all(cols["target_month"] <= latest.last_month)
    sudan = cols["country_id"] == 625
    january = sudan & (cols["target_month"] == parse_month("2012-01"))
    assert cols["y"][january].tolist() == [9.0]  # outcome from the latest data
    december = sudan & (cols["origin"] == parse_month("2011-12"))
    np.testing.assert_allclose(cols["y_origin"][december], 60.0)  # origin value from the as-of data
    assert all(fit["asof_cutoff"] for fit in records.fits)


def test_cli_end_to_end_offline(tmp_path: Path) -> None:
    from scripts import conflict_panel

    cache = tmp_path / "cache"
    artifacts = tmp_path / "artifacts"
    sources = {
        "https://ucdp.uu.se/downloads/ged/ged121-csv.zip": fixture_files(tmp_path)[SELECTION.final.url].read_bytes(),
        "https://ucdp.uu.se/downloads/candidateged/GEDEvent_v12_01_12_02.csv": (FIXTURES / "candidate_12_01_12_02.csv").read_bytes(),
        "https://ucdp.uu.se/downloads/candidateged/GEDEvent_v12_0_3.csv": (FIXTURES / "candidate_12_0_3.csv").read_bytes(),
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=sources[str(request.url)]))
    store = RawStore(cache / "raw")
    with make_client(transport=transport) as client:
        for url in sources:
            store.ensure(url, client=client)
    base = ["--cache-dir", str(cache), "--artifacts-dir", str(artifacts)]
    assert conflict_panel.main([*base, "fetch", "--offline", "--no-reference"]) == 0
    manifest = json.loads((artifacts / "data_manifest.json").read_text())
    assert [item["version"] for item in manifest["sources"]] == ["12.1", "12.01.12.02", "12.0.3"]
    assert manifest["panel"]["last_month"] == "2012-03"
    assert all(len(item["sha256"]) == 64 for item in manifest["sources"])
    assert conflict_panel.main([*base, "backtest", "--start", "2011-06", "--bootstrap", "0"]) == 0
    summary = json.loads((artifacts / "backtest_summary.json").read_text())
    assert summary["origins"][0] == "2011-06" and summary["headline"]
    assert conflict_panel.main([*base, "forecast"]) == 0
    forecast_path = artifacts / "live_forecast_2012-03.json"
    assert load_live_forecast(forecast_path)["horizons"]["1"] == "2012-04"
    assert conflict_panel.main([*base, "score", "--forecast", str(forecast_path)]) == 0
    assert not list(artifacts.glob("live_score_*.json"))  # nothing observed yet
