"""Country-month panel of UCDP best-estimate fatalities."""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from forecastlab.conflict.months import format_month, parse_month

# sb = state-based, ns = non-state, os = one-sided violence; total = their sum.
VIOLENCE_SERIES = ("sb", "ns", "os", "total")
PANEL_CSV_COLUMNS = ("country_id", "iso3", "country", "region", "month", "sb", "ns", "os", "total")
FRANCE_COUNTRY_ID = 220


@dataclass
class Panel:
    """Fatalities for every panel country and month, with zeros where no event was recorded.

    ``valid[c, m]`` is False for months in which a state did not exist (see
    ``countries.existence_window``); those cells are not observations.
    """

    country_ids: np.ndarray
    names: tuple[str, ...]
    iso3: tuple[str | None, ...]
    regions: tuple[str, ...]
    first_month: int
    counts: dict[str, np.ndarray]
    valid: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        n_countries = len(self.country_ids)
        if not (len(self.names) == len(self.iso3) == len(self.regions) == n_countries):
            raise ValueError("country metadata length mismatch")
        if set(self.counts) != set(VIOLENCE_SERIES):
            raise ValueError("panel needs sb, ns, os and total series")
        shape = self.valid.shape
        if shape[0] != n_countries:
            raise ValueError("valid mask shape mismatch")
        for name, values in self.counts.items():
            if values.shape != shape:
                raise ValueError(f"series {name} shape mismatch")
            if np.any(values < 0):
                raise ValueError(f"series {name} has negative fatalities")
        if np.any(self.counts["total"] != self.counts["sb"] + self.counts["ns"] + self.counts["os"]):
            raise ValueError("total must equal sb + ns + os")

    @property
    def n_countries(self) -> int:
        return len(self.country_ids)

    @property
    def n_months(self) -> int:
        return int(self.valid.shape[1])

    @property
    def last_month(self) -> int:
        return self.first_month + self.n_months - 1

    @property
    def months(self) -> np.ndarray:
        return np.arange(self.first_month, self.first_month + self.n_months)

    def month_index(self, month: int) -> int:
        index = int(month) - self.first_month
        if not 0 <= index < self.n_months:
            raise IndexError(f"month {format_month(month)} outside panel")
        return index

    def country_index(self, country_id: int) -> int:
        matches = np.flatnonzero(self.country_ids == int(country_id))
        if matches.size == 0:
            raise KeyError(f"country {country_id} not in panel")
        return int(matches[0])

    def series(self, name: str = "total") -> np.ndarray:
        if name not in self.counts:
            raise KeyError(f"unknown series {name}")
        return self.counts[name]

    def truncated(self, last_month: int) -> Panel:
        """Copy of the panel without months after ``last_month``."""
        keep = self.month_index(last_month) + 1
        return Panel(
            country_ids=self.country_ids.copy(),
            names=self.names,
            iso3=self.iso3,
            regions=self.regions,
            first_month=self.first_month,
            counts={name: values[:, :keep].copy() for name, values in self.counts.items()},
            valid=self.valid[:, :keep].copy(),
            metadata={**self.metadata, "truncated_to": format_month(last_month)},
        )


def save_panel(panel: Panel, csv_path: Path, manifest_path: Path) -> None:
    """Write valid country-months as gzip CSV (zeros included) plus a JSON manifest."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(PANEL_CSV_COLUMNS)
    for c in range(panel.n_countries):
        for m in np.flatnonzero(panel.valid[c]):
            writer.writerow(
                [
                    int(panel.country_ids[c]),
                    panel.iso3[c] or "",
                    panel.names[c],
                    panel.regions[c],
                    format_month(panel.first_month + int(m)),
                    *(int(panel.counts[name][c, m]) for name in ("sb", "ns", "os", "total")),
                ]
            )
    partial = csv_path.with_name(csv_path.name + ".part")
    with gzip.open(partial, "wt", encoding="utf-8", compresslevel=9, newline="") as handle:
        handle.write(buffer.getvalue())
    os.replace(partial, csv_path)
    manifest = {
        **panel.metadata,
        "first_month": format_month(panel.first_month),
        "last_month": format_month(panel.last_month),
        "n_countries": panel.n_countries,
        "n_valid_country_months": int(panel.valid.sum()),
        "panel_file": csv_path.name,
    }
    partial_manifest = manifest_path.with_name(manifest_path.name + ".part")
    partial_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(partial_manifest, manifest_path)


def load_panel(csv_path: Path, manifest_path: Path) -> Panel:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_month = parse_month(manifest["first_month"])
    last_month = parse_month(manifest["last_month"])
    rows: list[list[str]] = []
    with gzip.open(csv_path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if tuple(header) != PANEL_CSV_COLUMNS:
            raise ValueError(f"unexpected panel columns: {header}")
        rows = list(reader)
    meta: dict[int, tuple[str, str, str]] = {}
    for row in rows:
        meta.setdefault(int(row[0]), (row[1], row[2], row[3]))
    country_ids = np.array(sorted(meta), dtype=np.int64)
    position = {int(cid): i for i, cid in enumerate(country_ids)}
    n_months = last_month - first_month + 1
    counts = {name: np.zeros((len(country_ids), n_months), dtype=np.int64) for name in VIOLENCE_SERIES}
    valid = np.zeros((len(country_ids), n_months), dtype=bool)
    for row in rows:
        c = position[int(row[0])]
        m = parse_month(row[4]) - first_month
        valid[c, m] = True
        for offset, name in enumerate(("sb", "ns", "os", "total")):
            counts[name][c, m] = int(row[5 + offset])
    return Panel(
        country_ids=country_ids,
        names=tuple(meta[int(cid)][1] for cid in country_ids),
        iso3=tuple(meta[int(cid)][0] or None for cid in country_ids),
        regions=tuple(meta[int(cid)][2] for cid in country_ids),
        first_month=first_month,
        counts=counts,
        valid=valid,
        metadata={key: value for key, value in manifest.items() if key not in {"first_month", "last_month"}},
    )
