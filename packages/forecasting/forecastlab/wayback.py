from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode, urlparse, urlunparse

from forecastlab.errors import EvidenceIntegrityError
from forecastlab.http_client import SafeResponse, safe_get
from forecastlab.timeutil import as_utc, parse_datetime

CDX_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_HOSTS = {"web.archive.org", "archive.org"}
WAYBACK_RE = re.compile(
    r"^https?://(?:web\.)?archive\.org/web/(?P<stamp>\d{8,14})(?P<mod>[a-z]{0,4}_)?/(?P<original>.+)$",
    re.I,
)


@dataclass(frozen=True)
class WaybackSnapshot:
    url: str
    timestamp: datetime
    snapshot_url: str
    status: str | None = None
    queried_at: datetime | None = None
    discovery: str = "found"


@dataclass(frozen=True)
class WaybackCapture:
    final_url: str
    timestamp: datetime
    archived_original_url: str
    modifier: str | None
    replay: bool


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


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = parsed.query
    return urlunparse((parsed.scheme.lower() or "http", host, path, "", query, ""))


def parse_wayback_url(url: str) -> WaybackCapture | None:
    match = WAYBACK_RE.match(url.strip())
    if match is None:
        return None
    stamp = match.group("stamp")
    original = match.group("original")
    ts = parse_datetime(stamp)
    if ts is None:
        return None
    return WaybackCapture(
        final_url=url,
        timestamp=ts,
        archived_original_url=original,
        modifier=(match.group("mod") or None),
        replay=True,
    )


def discover_snapshots(
    url: str,
    *,
    as_of: datetime | None = None,
    timeout: float = 20.0,
) -> list[WaybackSnapshot]:
    parsed = urlparse(url)
    cdx_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))
    params = {
        "url": cdx_url,
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
    query = urlencode(params)
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


def verify_final_capture(
    response: SafeResponse,
    *,
    requested_url: str,
    as_of: datetime,
) -> tuple[WaybackCapture, str]:
    host = (urlparse(response.final_url).hostname or "").lower()
    if host not in WAYBACK_HOSTS and not host.endswith(".archive.org"):
        raise EvidenceIntegrityError("final_snapshot_not_wayback")
    capture = parse_wayback_url(response.final_url)
    if capture is None:
        raise EvidenceIntegrityError("final_snapshot_metadata_unparseable")
    if not snapshot_eligible(capture.timestamp, as_of):
        raise EvidenceIntegrityError("final_snapshot_after_as_of")
    if canonicalize_url(capture.archived_original_url) != canonicalize_url(requested_url):
        raise EvidenceIntegrityError("final_snapshot_original_url_mismatch")
    return capture, "verified"
