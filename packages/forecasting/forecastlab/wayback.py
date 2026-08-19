from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from forecastlab.http_client import safe_get
from forecastlab.timeutil import as_utc, parse_datetime

CDX_URL = "https://web.archive.org/cdx/search/cdx"


@dataclass(frozen=True)
class WaybackSnapshot:
    url: str
    timestamp: datetime
    snapshot_url: str
    status: str | None = None
    queried_at: datetime | None = None
    discovery: str = "found"


def snapshot_eligible(snapshot_at: datetime, as_of: datetime) -> bool:
    return as_utc(snapshot_at) <= as_utc(as_of)


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
                discovery="mock",
            )
        )
    return out


def _as_of_cdx(as_of: datetime) -> str:
    return as_of.strftime("%Y%m%d%H%M%S")


def discover_snapshots(
    url: str,
    *,
    as_of: datetime | None = None,
    timeout: float = 20.0,
) -> list[WaybackSnapshot]:
    params = {
        "url": url,
        "output": "json",
        "fl": "timestamp,original,statuscode",
        "filter": "statuscode:200",
        "limit": "1" if as_of else "50",
        "sort": "reverse",
    }
    if as_of:
        params["to"] = _as_of_cdx(as_of)
    else:
        params["collapse"] = "timestamp:8"
    query = "&".join(f"{key}={value}" for key, value in params.items())
    response = safe_get(f"{CDX_URL}?{query}", timeout=timeout, expect_json=True)
    from forecastlab.timeutil import utcnow

    rows = json.loads(response.content.decode("utf-8") or "[]")
    snapshots: list[WaybackSnapshot] = []
    queried_at = utcnow()
    if not isinstance(rows, list) or len(rows) < 2:
        return snapshots
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
                queried_at=queried_at,
                discovery="cdx",
            )
        )
    return snapshots
