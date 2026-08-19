from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx

from forecastlab.timeutil import parse_datetime

CDX_URL = "https://web.archive.org/cdx/search/cdx"


@dataclass(frozen=True)
class WaybackSnapshot:
    url: str
    timestamp: datetime
    snapshot_url: str
    status: str | None = None


def snapshot_eligible(snapshot_at: datetime, as_of: datetime) -> bool:
    return snapshot_at <= as_of


def nearest_eligible_snapshot(
    snapshots: list[WaybackSnapshot],
    as_of: datetime,
) -> WaybackSnapshot | None:
    eligible = [item for item in snapshots if snapshot_eligible(item.timestamp, as_of)]
    if not eligible:
        return None
    return max(eligible, key=lambda item: item.timestamp)


def mock_snapshots(url: str) -> list[WaybackSnapshot]:
    stamps = ["20230601000000", "20240601000000", "20250601000000"]
    out: list[WaybackSnapshot] = []
    for stamp in stamps:
        ts = parse_datetime(stamp)
        assert ts is not None
        out.append(
            WaybackSnapshot(
                url=url,
                timestamp=ts,
                snapshot_url=f"https://web.archive.org/web/{stamp}/{url}",
                status="200",
            )
        )
    return out


def discover_snapshots(url: str, *, timeout: float = 20.0) -> list[WaybackSnapshot]:
    params = {
        "url": url,
        "output": "json",
        "fl": "timestamp,original,statuscode",
        "filter": "statuscode:200",
        "limit": 50,
        "collapse": "timestamp:8",
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.get(CDX_URL, params=params)
        response.raise_for_status()
        rows = response.json()
    snapshots: list[WaybackSnapshot] = []
    for row in rows[1:]:
        stamp, original, status = row[0], row[1], row[2]
        ts = parse_datetime(stamp)
        if ts is None:
            continue
        snapshots.append(
            WaybackSnapshot(
                url=original,
                timestamp=ts,
                snapshot_url=f"https://web.archive.org/web/{stamp}/{original}",
                status=str(status),
            )
        )
    return snapshots
