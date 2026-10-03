"""Shared fixtures for the conflict-panel tests: fixture files and a synthetic panel.

The synthetic panel is generated, not observed data; it only exercises the code paths.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from forecastlab.conflict.months import parse_month
from forecastlab.conflict.panel import Panel
from forecastlab.conflict.sources import ReleaseSelection, UcdpRelease

FIXTURES = Path(__file__).parent / "fixtures" / "conflict"

FINAL = UcdpRelease(
    kind="ged_final",
    version="12.1",
    url="https://ucdp.uu.se/downloads/ged/ged121-csv.zip",
    first_month=parse_month("2010-01"),
    last_month=parse_month("2011-12"),
)
CANDIDATE_Q = UcdpRelease(
    kind="candidate_cumulative",
    version="12.01.12.02",
    url="https://ucdp.uu.se/downloads/candidateged/GEDEvent_v12_01_12_02.csv",
    first_month=parse_month("2012-01"),
    last_month=parse_month("2012-02"),
)
CANDIDATE_M = UcdpRelease(
    kind="candidate_monthly",
    version="12.0.3",
    url="https://ucdp.uu.se/downloads/candidateged/GEDEvent_v12_0_3.csv",
    first_month=parse_month("2012-03"),
    last_month=parse_month("2012-03"),
)
SELECTION = ReleaseSelection(releases=(FINAL, CANDIDATE_Q, CANDIDATE_M), last_month=parse_month("2012-03"), notes=())

SYNTHETIC_COUNTRIES = (
    (220, "France", "Europe", (0.995, 0.6, 0.3)),
    (365, "Russia (Soviet Union)", "Europe", (0.9, 0.85, 0.8)),
    (475, "Nigeria", "Africa", (0.7, 0.9, 0.85)),
    (625, "Sudan", "Africa", (0.8, 0.9, 0.9)),
    (626, "South Sudan", "Africa", (0.85, 0.85, 0.85)),
    (700, "Afghanistan", "Asia", (0.6, 0.9, 0.92)),
    (750, "India", "Asia", (0.5, 0.95, 0.6)),
    (840, "Philippines", "Asia", (0.7, 0.9, 0.7)),
)


def fixture_files(tmp_path: Path) -> dict[str, Path]:
    """Fixture releases as local files, with the final release zipped like UCDP's."""
    import zipfile

    archive = tmp_path / "ged121-csv.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.write(FIXTURES / "ged_final_mini.csv", arcname="GEDEvent_v12_1.csv")
    return {
        FINAL.url: archive,
        CANDIDATE_Q.url: FIXTURES / "candidate_12_01_12_02.csv",
        CANDIDATE_M.url: FIXTURES / "candidate_12_0_3.csv",
    }


def synthetic_panel(*, first: str = "2000-01", n_months: int = 156, seed: int = 7) -> Panel:
    """Eight countries with a three-state (calm / low / high intensity) Markov process."""
    rng = np.random.default_rng(seed)
    n_countries = len(SYNTHETIC_COUNTRIES)
    total = np.zeros((n_countries, n_months), dtype=np.int64)
    for c, (_cid, _name, _region, stay) in enumerate(SYNTHETIC_COUNTRIES):
        state = 0
        for m in range(n_months):
            if rng.random() > stay[state]:
                state = int(rng.choice([s for s in range(3) if s != state]))
            if state == 0:
                total[c, m] = int(rng.random() < 0.03) * int(rng.integers(1, 4))
            elif state == 1:
                total[c, m] = int(rng.poisson(8))
            else:
                total[c, m] = int(rng.poisson(160))
    share_sb = rng.uniform(0.3, 0.7, size=total.shape)
    sb = np.floor(total * share_sb).astype(np.int64)
    ns = np.floor((total - sb) * 0.5).astype(np.int64)
    os_ = total - sb - ns
    valid = np.ones_like(total, dtype=bool)
    valid[4, :30] = False  # a state that enters the system in month 30
    for name in (sb, ns, os_):
        name[~valid] = 0
    return Panel(
        country_ids=np.array([item[0] for item in SYNTHETIC_COUNTRIES], dtype=np.int64),
        names=tuple(item[1] for item in SYNTHETIC_COUNTRIES),
        iso3=("FRA", "RUS", "NGA", "SDN", "SSD", "AFG", "IND", "PHL"),
        regions=tuple(item[2] for item in SYNTHETIC_COUNTRIES),
        first_month=parse_month(first),
        counts={"sb": sb, "ns": ns, "os": os_, "total": sb + ns + os_},
        valid=valid,
        metadata={"dataset": "synthetic_test_panel", "built_at": "2026-01-01T00:00:00+00:00", "releases": []},
    )
