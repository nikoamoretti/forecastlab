#!/usr/bin/env python3
"""Country-month political-violence panel: fetch data, backtest, compare with ViEWS, forecast, score.

Usage (from the repository root, after ``uv sync --extra dev --frozen``):

    uv run --frozen --no-sync python scripts/conflict_panel.py fetch
    uv run --frozen --no-sync python scripts/conflict_panel.py backtest
    uv run --frozen --no-sync python scripts/conflict_panel.py views
    uv run --frozen --no-sync python scripts/conflict_panel.py forecast
    uv run --frozen --no-sync python scripts/conflict_panel.py score --fetch

Raw downloads and derived tables go to ``data/cache/conflict`` (gitignored). Small
summaries are written to ``artifacts/conflict_panel_v1``. See docs/CONFLICT_PANEL_V1.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))

from forecastlab.conflict.backtest import headline_table, run_backtest, summarize_backtest  # noqa: E402
from forecastlab.conflict.forecaster import PanelConfig  # noqa: E402
from forecastlab.conflict.ged import build_panel  # noqa: E402
from forecastlab.conflict.live import (  # noqa: E402
    code_identity,
    data_vintage,
    load_live_forecast,
    make_live_forecast,
    score_live_forecasts,
    write_live_artifacts,
)
from forecastlab.conflict.months import format_month, parse_month, year_of  # noqa: E402
from forecastlab.conflict.panel import Panel, load_panel, save_panel  # noqa: E402
from forecastlab.conflict.sources import (  # noqa: E402
    UCDP_DOWNLOADS_INDEX,
    RawStore,
    SourceError,
    fetch_text,
    make_client,
    parse_downloads_index,
    reference_candidate_release,
    select_releases,
    sha256_file,
)
from forecastlab.conflict.views import (  # noqa: E402
    VIEWS_API,
    compare_with_views,
    fetch_run_forecasts,
    fetch_views_outcomes,
    predictors_dataset,
    select_runs,
    vintage_effect,
)
from forecastlab.conflict.vintage import (  # noqa: E402
    VintageLibrary,
    historical_releases,
    probe_release_dates,
    run_vintage_backtest,
)

DEFAULT_CACHE = Path(os.environ.get("FORECASTLAB_CONFLICT_CACHE", ROOT / "data" / "cache" / "conflict"))
DEFAULT_ARTIFACTS = ROOT / "artifacts" / "conflict_panel_v1"
PANEL_FILE = "panel_country_month.csv.gz"
PANEL_MANIFEST = "panel_manifest.json"
UCDP_ATTRIBUTION = (
    "UCDP Georeferenced Event Dataset (GED) and UCDP Candidate Events Dataset, Uppsala Conflict Data "
    "Program (https://ucdp.uu.se/downloads/), CC BY 4.0. Cite Davies, Pettersson and Oberg (2026), "
    "Sundberg and Melander (2013) and Hegre, Croicu, Eck and Hogbladh (2020)."
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    os.replace(partial, path)


def _panel_paths(cache: Path) -> tuple[Path, Path]:
    return cache / "panel" / PANEL_FILE, cache / "panel" / PANEL_MANIFEST


def _load_cached_panel(cache: Path) -> Panel:
    csv_path, manifest_path = _panel_paths(cache)
    if not csv_path.exists() or not manifest_path.exists():
        raise SystemExit(f"no panel in {cache}; run `scripts/conflict_panel.py fetch` first")
    return load_panel(csv_path, manifest_path)


def fetch(args: argparse.Namespace) -> dict[str, Any]:
    cache: Path = args.cache_dir
    store = RawStore(cache / "raw")
    client = None if args.offline else make_client()
    try:
        if client is None:
            catalog = parse_downloads_index(" ".join(f'href="{url}"' for url in _cached_urls(store)))
        else:
            catalog = parse_downloads_index(fetch_text(UCDP_DOWNLOADS_INDEX, client=client))
        selection = select_releases(catalog)
        records = {}
        for release in selection.releases:
            records[release.url] = store.ensure(release.url, client=client, refresh=args.refresh, revalidate=True)
        reference = None
        notes = list(selection.notes)
        if not args.no_reference:
            release = reference_candidate_release(selection.final)
            try:
                records[release.url] = store.ensure(release.url, client=client, refresh=args.refresh)
                reference = (release, store.path_for(records[release.url]))
            except SourceError as exc:
                notes.append(f"vintage_reference_unavailable:{exc}")
    finally:
        if client is not None:
            client.close()
    files = {url: store.path_for(record) for url, record in records.items()}
    panel = build_panel(selection, files, records=records, candidate_status=args.candidate_status, reference=reference)
    panel.metadata["selection_notes"] = notes
    csv_path, manifest_path = _panel_paths(cache)
    save_panel(panel, csv_path, manifest_path)
    meta = panel.metadata
    manifest = {
        "attribution": UCDP_ATTRIBUTION,
        "sources": [records[release.url].as_dict() | release.describe() for release in selection.releases],
        "vintage_reference_file": records[reference[0].url].as_dict() | reference[0].describe() if reference else None,
        "panel": {
            "first_month": format_month(panel.first_month),
            "last_month": format_month(panel.last_month),
            "countries": panel.n_countries,
            "valid_country_months": int(panel.valid.sum()),
            "month_assignment": meta["month_assignment"],
            "fatality_estimate": meta["fatality_estimate"],
            "candidate_status_policy": meta["candidate_status_policy"],
            "fatalities_by_series": meta["fatalities_by_series"],
            "derived_file_sha256": sha256_file(csv_path),
            "built_at": meta["built_at"],
        },
        "final_release_counts": meta["final_release_counts"],
        "candidate_release_counts": meta["candidate_release_counts"],
        "candidate_events_used": meta["candidate_events_used"],
        "existence_window_adjustments": meta["existence_window_adjustments"],
        "selection_notes": notes,
        "vintage_check": meta.get("vintage_check"),
        "countries": [
            {"country_id": int(cid), "iso3": panel.iso3[i], "country": panel.names[i], "region": panel.regions[i]}
            for i, cid in enumerate(panel.country_ids)
        ],
    }
    _write_json(args.artifacts_dir / "data_manifest.json", manifest)
    return {
        "status": "ok",
        "releases": [release.version for release in selection.releases],
        "last_month": format_month(panel.last_month),
        "countries": panel.n_countries,
        "notes": notes,
        "panel": str(csv_path),
    }


def _cached_urls(store: RawStore) -> list[str]:
    if not store.manifest_path.exists():
        raise SystemExit(f"--offline needs cached files in {store.directory}")
    payload = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    return [item["url"] for item in payload.get("files", [])]


def backtest(args: argparse.Namespace) -> dict[str, Any]:
    panel = _load_cached_panel(args.cache_dir)
    config = PanelConfig(series=args.series)
    records = run_backtest(
        panel,
        config,
        first_origin=parse_month(args.start),
        last_origin=parse_month(args.end) if args.end else None,
        progress=lambda message: print(message, file=sys.stderr),
    )
    records.save_csv_gz(args.cache_dir / "backtest" / f"records_{args.series}.csv.gz")
    summary = summarize_backtest(records, panel, config, bootstrap=args.bootstrap)
    summary["headline"] = headline_table(summary)
    summary["data_vintage"] = data_vintage(panel)
    summary["code"] = code_identity(ROOT)
    name = "backtest_summary.json" if args.series == "total" else f"backtest_summary_{args.series}.json"
    _write_json(args.artifacts_dir / name, summary)
    return {"status": "ok", "records": len(records), "summary": str(args.artifacts_dir / name), "headline": summary["headline"]}


def views(args: argparse.Namespace) -> dict[str, Any]:
    panel = _load_cached_panel(args.cache_dir)
    store = RawStore(args.cache_dir / "views")
    client = None if args.offline else make_client()
    config = PanelConfig(series="sb")
    asof_records = None
    asof_skipped: list[dict[str, Any]] = []
    try:
        # The run list grows every month, so it is re-read whenever the API is reachable.
        index = store.ensure(VIEWS_API, client=client, filename="runs.json", refresh=client is not None)
        names = json.loads(store.path_for(index).read_text(encoding="utf-8")).get("runs", [])
        runs, skipped = select_runs(names)
        forecasts = {}
        used = []
        for run in runs:
            targets = [run.origin + h for h in config.horizons if run.origin + h <= panel.last_month]
            if not targets or run.origin < panel.first_month:
                continue
            forecasts[run.name] = fetch_run_forecasts(store, run, targets, client=client)
            used.append(run)
        outcomes = None
        dataset = predictors_dataset(names)
        if dataset and not args.no_views_outcomes:
            outcomes = fetch_views_outcomes(store, dataset, client=client)
        if not used:
            raise SystemExit("no ViEWS run has an observed target month in the panel")
        if not args.no_asof:
            origins = [run.origin for run in used]
            catalog = probe_release_dates(
                historical_releases(year_of(min(origins)) - 1, year_of(max(origins) + 1)),
                client=client,
                cache_path=args.cache_dir / "raw" / "release_dates.json",
            )
            library = VintageLibrary(RawStore(args.cache_dir / "raw"), catalog, client=client)
            asof_records, asof_skipped = run_vintage_backtest(
                panel, library, origins, config, progress=lambda message: print(message, file=sys.stderr)
            )
    finally:
        if client is not None:
            client.close()
    records = run_backtest(
        panel,
        config,
        first_origin=min(run.origin for run in used),
        origins=[run.origin for run in used],
        progress=lambda message: print(message, file=sys.stderr),
    )
    records.save_csv_gz(args.cache_dir / "backtest" / "records_sb_views_origins.csv.gz")
    comparison: dict[str, Any] = {
        "comparison": "panel baseline on the sb series versus published ViEWS fatalities runs",
        "views_api": VIEWS_API,
        "runs_used": [run.name for run in used],
        "runs_skipped_as_duplicates": skipped,
        "views_outcome_dataset": dataset if outcomes is not None else None,
        "asof_data": None,
        "revised_data": compare_with_views(records, forecasts, used, views_outcomes=outcomes, bootstrap=args.bootstrap),
    }
    if asof_records is not None:
        asof_records.save_csv_gz(args.cache_dir / "backtest" / "records_sb_views_origins_asof.csv.gz")
        comparison["asof_data"] = compare_with_views(asof_records, forecasts, used, views_outcomes=outcomes, bootstrap=args.bootstrap)
        comparison["asof_origins_skipped"] = asof_skipped
        comparison["asof_data_releases_by_origin"] = {
            fit["origin"]: {"cutoff": fit["asof_cutoff"], "releases": fit["data_releases"]}
            for fit in asof_records.fits
        }
        comparison["vintage_effect_on_panel_baseline"] = vintage_effect(records, asof_records)
    comparison.update(
        {
            "raw_responses": str(store.manifest_path.relative_to(args.cache_dir)),
            "data_vintage": data_vintage(panel),
            "code": code_identity(ROOT),
            "panel_config": config.as_dict(),
        }
    )
    _write_json(args.artifacts_dir / "views_comparison.json", comparison)
    primary = comparison["asof_data"] or comparison["revised_data"]
    return {
        "status": "ok",
        "runs": len(used),
        "primary": "asof_data" if comparison["asof_data"] else "revised_data",
        "rows_matched": primary["rows_matched"],
        "results": {name: block.get("all") for name, block in primary["results"].items()},
        "vintage_effect_on_panel_baseline": comparison.get("vintage_effect_on_panel_baseline", {}).get("all"),
    }


def forecast(args: argparse.Namespace) -> dict[str, Any]:
    panel = _load_cached_panel(args.cache_dir)
    origin = parse_month(args.origin) if args.origin else None
    payload = make_live_forecast(panel, PanelConfig(), origin=origin, root=ROOT)
    json_path, csv_path = write_live_artifacts(payload, args.artifacts_dir)
    return {
        "status": "ok",
        "origin": payload["origin"],
        "horizons": payload["horizons"],
        "artifact": str(json_path),
        "csv": str(csv_path),
        "top_next_month_p_ge25": payload["top_next_month_p_ge25"],
        "top_risers": payload["top_risers"],
        "focus_country": [
            {key: row[key] for key in ("target_month", "p_ge1", "p_ge25", "p_ge100", "fatalities_q50", "fatalities_q95")}
            for row in payload["focus_country"]
        ],
    }


def score(args: argparse.Namespace) -> dict[str, Any]:
    if args.fetch:
        fetch(args)
    path = args.forecast or _latest_forecast(args.artifacts_dir)
    payload = load_live_forecast(path)
    panel = _load_cached_panel(args.cache_dir)
    result = score_live_forecasts(payload, panel)
    result["forecast_artifact"] = path.name
    if result["scored_horizons"]:
        out = args.artifacts_dir / f"live_score_{payload['origin']}_data_{format_month(panel.last_month)}.json"
        _write_json(out, result)
        result["written"] = str(out)
    return result


def _latest_forecast(directory: Path) -> Path:
    candidates = sorted(directory.glob("live_forecast_*.json"))
    if not candidates:
        raise SystemExit(f"no live_forecast_*.json in {directory}; run `forecast` first")
    return candidates[-1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE, help="raw and derived data (gitignored)")
    parser.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS, help="small committed summaries")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_fetch_options(command: argparse.ArgumentParser) -> None:
        command.add_argument("--refresh", action="store_true", help="re-download files even if cached")
        command.add_argument("--offline", action="store_true", help="use cached files only")
        command.add_argument("--candidate-status", choices=("all", "clear"), default="all",
                             help="Candidate events to keep: all (default) or code_status Clear only")
        command.add_argument("--no-reference", action="store_true", help="skip the Candidate-vs-final vintage check")

    add_fetch_options(sub.add_parser("fetch", help="download UCDP files and build the country-month panel"))
    run = sub.add_parser("backtest", help="rolling-origin backtest and scoreboard")
    run.add_argument("--start", default="2015-01", help="first forecast origin (YYYY-MM)")
    run.add_argument("--end", default=None, help="last forecast origin (YYYY-MM)")
    run.add_argument("--series", choices=("total", "sb"), default="total")
    run.add_argument("--bootstrap", type=int, default=1000, help="country-bootstrap replicates for intervals")
    compare = sub.add_parser("views", help="compare the sb baseline with published ViEWS forecasts")
    compare.add_argument("--offline", action="store_true", help="use cached ViEWS responses only")
    compare.add_argument("--no-views-outcomes", action="store_true", help="skip scoring against ViEWS's own aggregation")
    compare.add_argument("--no-asof", action="store_true",
                         help="skip the as-of comparison (it downloads historical UCDP releases)")
    compare.add_argument("--bootstrap", type=int, default=1000)
    live = sub.add_parser("forecast", help="live forecasts from the latest panel")
    live.add_argument("--origin", default=None, help="data cutoff month (default: last month in the panel)")
    scorer = sub.add_parser("score", help="score a live forecast artifact once outcomes are available")
    scorer.add_argument("--forecast", type=Path, default=None, help="live_forecast_*.json (default: latest)")
    scorer.add_argument("--fetch", action="store_true", help="refresh UCDP data before scoring")
    add_fetch_options(scorer)
    return parser


COMMANDS = {"fetch": fetch, "backtest": backtest, "views": views, "forecast": forecast, "score": score}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = COMMANDS[args.command](args)
    print(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
