"""Parse UCDP GED and Candidate event files and aggregate them to the country-month panel.

Aggregation rules:

* each event's ``best`` estimate is assigned to the calendar month of ``date_start``;
* the final GED release supplies every month it covers; Candidate files supply only the
  later months, and an event appearing in several Candidate files (UCDP repeats events
  whose dates span two monthly exports) is counted once, from the latest file;
* every panel country gets an explicit zero for months with no recorded event.
"""

from __future__ import annotations

import csv
import io
import itertools
import math
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TextIO

import numpy as np

from forecastlab.conflict.countries import existence_window, iso3_for
from forecastlab.conflict.months import format_month, month_of_timestamp, month_ordinal, year_of
from forecastlab.conflict.panel import VIOLENCE_SERIES, Panel
from forecastlab.conflict.sources import FileRecord, ReleaseSelection, UcdpRelease

REQUIRED_COLUMNS = ("id", "country_id", "country", "region", "type_of_violence", "date_start", "best")
# Column order of GED and Candidate CSV files. A few Candidate files were published
# without a header row; they are read with this layout only if the row width matches.
STANDARD_GED_COLUMNS = (
    "id", "relid", "year", "active_year", "code_status", "type_of_violence", "conflict_dset_id",
    "conflict_new_id", "conflict_name", "dyad_dset_id", "dyad_new_id", "dyad_name", "side_a_dset_id",
    "side_a_new_id", "side_a", "side_b_dset_id", "side_b_new_id", "side_b", "number_of_sources",
    "source_article", "source_office", "source_date", "source_headline", "source_original", "where_prec",
    "where_coordinates", "where_description", "adm_1", "adm_2", "latitude", "longitude", "geom_wkt",
    "priogrid_gid", "country", "country_id", "region", "event_clarity", "date_prec", "date_start", "date_end",
    "deaths_a", "deaths_b", "deaths_civilians", "deaths_unknown", "best", "high", "low", "gwnoa", "gwnob",
)
VIOLENCE_CODES = {1: "sb", 2: "ns", 3: "os"}
CandidateStatusPolicy = Literal["all", "clear"]
VINTAGE_THRESHOLDS = (1, 25, 100)


class GedFormatError(ValueError):
    """An event file is missing columns or contains an unparseable row."""


@dataclass(frozen=True, slots=True)
class EventRow:
    event_id: int
    country_id: int
    country: str
    region: str
    violence: int
    month: int
    best: int
    code_status: str


def _is_headerless_standard_row(row: list[str]) -> bool:
    return len(row) == len(STANDARD_GED_COLUMNS) and row[0].strip().isdigit()


def iter_event_rows(handle: TextIO, *, source: str) -> Iterator[EventRow]:
    reader = csv.reader(handle)
    try:
        first = next(reader)
    except StopIteration as exc:
        raise GedFormatError(f"{source}: empty file") from exc
    header = [name.strip() for name in first]
    pending: list[list[str]] = []
    if any(name not in header for name in REQUIRED_COLUMNS) and _is_headerless_standard_row(first):
        header = list(STANDARD_GED_COLUMNS)
        pending.append(first)
    missing = [name for name in REQUIRED_COLUMNS if name not in header]
    if missing:
        raise GedFormatError(f"{source}: missing columns {missing}")
    index = {name: header.index(name) for name in REQUIRED_COLUMNS}
    # Final GED releases contain only events UCDP has cleared; older ones lack the column.
    status_index = header.index("code_status") if "code_status" in header else None
    first_line = 1 if pending else 2
    for line_number, row in enumerate(itertools.chain(pending, reader), start=first_line):
        if not row:
            continue
        try:
            violence = int(row[index["type_of_violence"]])
            best = int(float(row[index["best"]]))
            event = EventRow(
                event_id=int(float(row[index["id"]])),
                country_id=int(float(row[index["country_id"]])),
                country=row[index["country"]].strip(),
                region=row[index["region"]].strip(),
                violence=violence,
                month=month_of_timestamp(row[index["date_start"]]),
                best=best,
                code_status=row[status_index].strip() if status_index is not None else "Clear",
            )
        except (ValueError, IndexError) as exc:
            raise GedFormatError(f"{source}:{line_number}: {exc}") from exc
        if violence not in VIOLENCE_CODES:
            raise GedFormatError(f"{source}:{line_number}: unknown type_of_violence {violence}")
        if best < 0:
            raise GedFormatError(f"{source}:{line_number}: negative best estimate")
        yield event


def iter_release_rows(path: Path, release: UcdpRelease) -> Iterator[EventRow]:
    """Rows of a GED zip archive or Candidate CSV file."""
    source = release.filename
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            member = release.csv_member if release.csv_member in names else None
            if member is None:
                if len(names) != 1:
                    raise GedFormatError(f"{source}: expected one CSV member, found {names}")
                member = names[0]
            with archive.open(member) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
                yield from iter_event_rows(text, source=source)
    else:
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
            yield from iter_event_rows(handle, source=source)


@dataclass
class FinalAggregate:
    """Country-month sums of best estimates by violence type from a final GED release."""

    sums: dict[tuple[int, int, int], int]
    names: dict[int, str]
    regions: dict[int, str]
    first_event_month: dict[int, int]
    last_event_month: dict[int, int]
    counts: Counter[str] = field(default_factory=Counter)


class _Accumulator:
    def __init__(self, base: FinalAggregate | None = None):
        self.sums: defaultdict[tuple[int, int, int], int] = defaultdict(int, base.sums if base else {})
        self.names = dict(base.names) if base else {}
        self.regions = dict(base.regions) if base else {}
        self.first_event_month = dict(base.first_event_month) if base else {}
        self.last_event_month = dict(base.last_event_month) if base else {}

    def add(self, event: EventRow) -> None:
        self.sums[(event.country_id, event.month, event.violence)] += event.best
        self.names.setdefault(event.country_id, event.country)
        self.regions.setdefault(event.country_id, event.region)
        first = self.first_event_month.get(event.country_id)
        if first is None or event.month < first:
            self.first_event_month[event.country_id] = event.month
        last = self.last_event_month.get(event.country_id)
        if last is None or event.month > last:
            self.last_event_month[event.country_id] = event.month


def read_final_release(path: Path, release: UcdpRelease) -> FinalAggregate:
    """Aggregate a final GED release over the months it covers."""
    accumulator = _Accumulator()
    counts: Counter[str] = Counter()
    for event in iter_release_rows(path, release):
        counts["rows"] += 1
        if release.first_month <= event.month <= release.last_month:
            accumulator.add(event)
            counts["used"] += 1
        else:
            counts["outside_coverage"] += 1
    return FinalAggregate(
        sums=dict(accumulator.sums),
        names=accumulator.names,
        regions=accumulator.regions,
        first_event_month=accumulator.first_event_month,
        last_event_month=accumulator.last_event_month,
        counts=counts,
    )


def read_candidate_release(path: Path, release: UcdpRelease) -> list[EventRow]:
    return list(iter_release_rows(path, release))


def _total_by_country_month(sums: Mapping[tuple[int, int, int], int], year: int) -> dict[tuple[int, int], int]:
    totals: dict[tuple[int, int], int] = defaultdict(int)
    for (country_id, month, _violence), value in sums.items():
        if year_of(month) == year:
            totals[(country_id, month)] += value
    return totals


def compare_vintages(
    final_sums: Mapping[tuple[int, int, int], int],
    candidate_events: list[EventRow],
    *,
    year: int,
) -> dict[str, Any]:
    """How a full-year Candidate release differs from the final GED for the same year.

    Counts country-months (over countries with an event in either source) where each
    threshold is met by both sources, only by the Candidate data, or only by the final data.
    """
    final_totals = _total_by_country_month(final_sums, year)
    months = [month_ordinal(year, m) for m in range(1, 13)]
    countries = sorted(
        {key[0] for key in final_totals}
        | {event.country_id for event in candidate_events if year_of(event.month) == year}
    )

    def summarise(events: list[EventRow]) -> dict[str, Any]:
        candidate_totals: dict[tuple[int, int], int] = defaultdict(int)
        for event in events:
            if year_of(event.month) == year:
                candidate_totals[(event.country_id, event.month)] += event.best
        thresholds: dict[str, dict[str, int]] = {}
        for threshold in VINTAGE_THRESHOLDS:
            both = candidate_only = final_only = 0
            for country in countries:
                for month in months:
                    in_candidate = candidate_totals.get((country, month), 0) >= threshold
                    in_final = final_totals.get((country, month), 0) >= threshold
                    both += in_candidate and in_final
                    candidate_only += in_candidate and not in_final
                    final_only += in_final and not in_candidate
            thresholds[f"ge_{threshold}"] = {"both": both, "candidate_only": candidate_only, "final_only": final_only}
        cells = [(country, month) for country in countries for month in months]
        abs_diff = [
            abs(math.log1p(candidate_totals.get(cell, 0)) - math.log1p(final_totals.get(cell, 0))) for cell in cells
        ]
        return {
            "events": sum(1 for event in events if year_of(event.month) == year),
            "fatalities_best": int(sum(candidate_totals.values())),
            "country_months_compared": len(cells),
            "thresholds": thresholds,
            "mean_abs_log1p_difference": round(sum(abs_diff) / len(abs_diff), 4) if abs_diff else None,
        }

    return {
        "year": year,
        "final_fatalities_best": int(sum(final_totals.values())),
        "candidate_all_events": summarise(candidate_events),
        "candidate_clear_events_only": summarise([event for event in candidate_events if event.code_status == "Clear"]),
    }


def assemble_panel(
    selection: ReleaseSelection,
    final: FinalAggregate,
    candidates: Mapping[str, list[EventRow]],
    *,
    records: Mapping[str, FileRecord] | None = None,
    candidate_status: CandidateStatusPolicy = "all",
    reference: tuple[UcdpRelease, list[EventRow]] | None = None,
    countries: Mapping[int, tuple[str, str]] | None = None,
) -> Panel:
    """Combine a parsed final release and Candidate files into a zero-filled panel.

    ``candidates`` maps each selected Candidate URL to its rows. ``countries`` (country_id ->
    (name, region)) fixes the panel's country list; events elsewhere are then dropped and
    counted, and listed countries without events get zeros.
    """
    final_release = selection.final
    accumulator = _Accumulator(final)
    candidate_events: dict[int, EventRow] = {}
    candidate_source: dict[int, str] = {}
    release_stats: list[dict[str, Any]] = []
    for release in selection.candidates:
        stats: Counter[str] = Counter()
        for event in candidates[release.url]:
            stats["rows"] += 1
            stats[f"status:{event.code_status}"] += 1
            if event.month <= final_release.last_month:
                stats["before_candidate_period"] += 1
                continue
            if event.month > selection.last_month:
                stats["after_last_covered_month"] += 1
                continue
            if candidate_status == "clear" and event.code_status != "Clear":
                stats["excluded_status"] += 1
                continue
            if event.month < release.first_month:
                stats["earlier_month_events"] += 1
            if event.event_id in candidate_events:
                stats["replaced_earlier_copy"] += 1
            candidate_events[event.event_id] = event
            candidate_source[event.event_id] = release.version
        release_stats.append({**release.describe(), "counts": dict(sorted(stats.items()))})
    used_by_release = Counter(candidate_source.values())
    for item in release_stats:
        item["counts"]["events_used_after_dedup"] = used_by_release.get(item["version"], 0)
    for event in candidate_events.values():
        accumulator.add(event)

    first_month = final_release.first_month
    last_month = selection.last_month
    n_months = last_month - first_month + 1
    if countries is None:
        country_ids = np.array(sorted(accumulator.names), dtype=np.int64)
        names = {cid: accumulator.names[cid] for cid in accumulator.names}
        regions = {cid: accumulator.regions[cid] for cid in accumulator.regions}
    else:
        country_ids = np.array(sorted(countries), dtype=np.int64)
        names = {cid: countries[cid][0] for cid in countries}
        regions = {cid: countries[cid][1] for cid in countries}
    position = {int(cid): i for i, cid in enumerate(country_ids)}
    counts = {name: np.zeros((len(country_ids), n_months), dtype=np.int64) for name in VIOLENCE_SERIES}
    outside: Counter[int] = Counter()
    for (country_id, month, violence), value in accumulator.sums.items():
        if country_id not in position:
            outside[country_id] += value
            continue
        counts[VIOLENCE_CODES[violence]][position[country_id], month - first_month] += value
    counts["total"] = counts["sb"] + counts["ns"] + counts["os"]

    valid = np.zeros((len(country_ids), n_months), dtype=bool)
    window_adjustments: list[dict[str, Any]] = []
    for i, cid in enumerate(country_ids):
        start, end = existence_window(int(cid), first_month, last_month)
        first_event = accumulator.first_event_month.get(int(cid))
        last_event = accumulator.last_event_month.get(int(cid))
        if first_event is not None and last_event is not None and (first_event < start or last_event > end):
            window_adjustments.append(
                {"country_id": int(cid), "window": [format_month(start), format_month(end)],
                 "events": [format_month(first_event), format_month(last_event)]}
            )
            start, end = min(start, first_event), max(end, last_event)
        valid[i, start - first_month : end - first_month + 1] = True

    def describe(release: UcdpRelease) -> dict[str, Any]:
        digest = {"sha256": records[release.url].sha256} if records and release.url in records else {}
        return {**release.describe(), **digest}

    metadata: dict[str, Any] = {
        "dataset": "ucdp_country_month_panel",
        "built_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "month_assignment": "date_start",
        "fatality_estimate": "best",
        "candidate_status_policy": candidate_status,
        "releases": [describe(release) for release in selection.releases],
        "selection_notes": list(selection.notes),
        "final_release_counts": dict(final.counts),
        "candidate_release_counts": release_stats,
        "candidate_events_used": len(candidate_events),
        "existence_window_adjustments": window_adjustments,
        "fatalities_outside_country_list": {str(cid): value for cid, value in sorted(outside.items())},
        "fatalities_by_series": {name: int(values.sum()) for name, values in counts.items()},
    }
    if reference is not None:
        reference_release, reference_events = reference
        metadata["vintage_check"] = {
            "reference_release": describe(reference_release),
            "final_release": final_release.version,
            **compare_vintages(final.sums, reference_events, year=year_of(reference_release.last_month)),
        }
    return Panel(
        country_ids=country_ids,
        names=tuple(names[int(cid)] for cid in country_ids),
        iso3=tuple(iso3_for(int(cid)) for cid in country_ids),
        regions=tuple(regions[int(cid)] for cid in country_ids),
        first_month=first_month,
        counts=counts,
        valid=valid,
        metadata=metadata,
    )


def build_panel(
    selection: ReleaseSelection,
    files: Mapping[str, Path],
    *,
    records: Mapping[str, FileRecord] | None = None,
    candidate_status: CandidateStatusPolicy = "all",
    reference: tuple[UcdpRelease, Path] | None = None,
    countries: Mapping[int, tuple[str, str]] | None = None,
) -> Panel:
    """Read the selected releases from ``files`` (URL -> local path) and assemble the panel.

    ``reference`` optionally supplies a full-year Candidate release for a year the final
    GED covers, for the vintage check.
    """
    final = read_final_release(files[selection.final.url], selection.final)
    candidates = {release.url: read_candidate_release(files[release.url], release) for release in selection.candidates}
    reference_rows = None
    if reference is not None:
        reference_rows = (reference[0], read_candidate_release(reference[1], reference[0]))
    return assemble_panel(
        selection,
        final,
        candidates,
        records=records,
        candidate_status=candidate_status,
        reference=reference_rows,
        countries=countries,
    )
