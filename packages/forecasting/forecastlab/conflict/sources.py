"""Public, keyless data sources for the conflict panel and a hash-recording download cache.

UCDP bulk files come from https://ucdp.uu.se/downloads/ (CC BY 4.0). The UCDP REST API
requires a token and is not used. ViEWS forecasts come from the public ViEWS API. Only
HTTPS requests to these two hosts are allowed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx

from forecastlab.conflict.months import format_month, month_ordinal, year_of

UCDP_DOWNLOADS_INDEX = "https://ucdp.uu.se/downloads/"
UCDP_GED_URL = "https://ucdp.uu.se/downloads/ged/ged{major:02d}{minor}-csv.zip"
UCDP_CANDIDATE_BASE = "https://ucdp.uu.se/downloads/candidateged/"
ALLOWED_HOSTS = frozenset({"ucdp.uu.se", "api.viewsforecasting.org"})
USER_AGENT = "ForecastLab-conflict-panel/1 (research use of public bulk files)"
DEFAULT_TIMEOUT = 180.0

ReleaseKind = Literal["ged_final", "candidate_cumulative", "candidate_monthly", "candidate_reference"]

_GED_FINAL_RE = re.compile(r"/ged/ged(\d{2})(\d)-csv\.zip$")
_CANDIDATE_MONTHLY_RE = re.compile(r"/candidateged/GEDEvent_v(\d{2})_0_(\d{1,2})\.csv$")
_CANDIDATE_CUMULATIVE_RE = re.compile(r"/candidateged/GEDEvent_v(\d{2})_01_(\d{2})_(\d{2})\.csv$")
_HREF_RE = re.compile(r"href=\"([^\"]+)\"", re.IGNORECASE)


class SourceError(RuntimeError):
    """A source could not be resolved, downloaded or verified."""


@dataclass(frozen=True)
class UcdpRelease:
    """One UCDP GED or Candidate file and the event months it is used for."""

    kind: ReleaseKind
    version: str
    url: str
    first_month: int
    last_month: int

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]

    @property
    def csv_member(self) -> str | None:
        """CSV file name inside the GED zip archive."""
        if self.kind != "ged_final":
            return None
        return f"GEDEvent_v{self.version.replace('.', '_')}.csv"

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "version": self.version,
            "url": self.url,
            "first_month": format_month(self.first_month),
            "last_month": format_month(self.last_month),
        }


def ged_final_release(major: int, minor: int) -> UcdpRelease:
    """GED ``major.minor`` (e.g. 26.1) covers events from January 1989 to December of 20(major-1)."""
    last_year = 2000 + major - 1
    return UcdpRelease(
        kind="ged_final",
        version=f"{major}.{minor}",
        url=UCDP_GED_URL.format(major=major, minor=minor),
        first_month=month_ordinal(1989, 1),
        last_month=month_ordinal(last_year, 12),
    )


def candidate_monthly_release(yy: int, month: int) -> UcdpRelease:
    ordinal = month_ordinal(2000 + yy, month)
    return UcdpRelease(
        kind="candidate_monthly",
        version=f"{yy}.0.{month}",
        url=f"{UCDP_CANDIDATE_BASE}GEDEvent_v{yy}_0_{month}.csv",
        first_month=ordinal,
        last_month=ordinal,
    )


def candidate_cumulative_release(yy: int, last_month: int, *, kind: ReleaseKind = "candidate_cumulative") -> UcdpRelease:
    """Quarterly cumulative Candidate file covering January..``last_month`` of 20yy."""
    return UcdpRelease(
        kind=kind,
        version=f"{yy:02d}.01.{yy:02d}.{last_month:02d}",
        url=f"{UCDP_CANDIDATE_BASE}GEDEvent_v{yy:02d}_01_{yy:02d}_{last_month:02d}.csv",
        first_month=month_ordinal(2000 + yy, 1),
        last_month=month_ordinal(2000 + yy, last_month),
    )


def reference_candidate_release(final: UcdpRelease) -> UcdpRelease:
    """Full-year Candidate file for the last year of ``final``, used only for the vintage check."""
    yy = year_of(final.last_month) % 100
    return candidate_cumulative_release(yy, 12, kind="candidate_reference")


def parse_downloads_index(html: str, *, base_url: str = UCDP_DOWNLOADS_INDEX) -> list[UcdpRelease]:
    """Recognise GED final and Candidate CSV links on the UCDP downloads page."""
    releases: dict[str, UcdpRelease] = {}
    for href in _HREF_RE.findall(html):
        url = urljoin(base_url, href.strip())
        path = urlparse(url).path
        release: UcdpRelease | None = None
        if match := _GED_FINAL_RE.search(path):
            release = ged_final_release(int(match.group(1)), int(match.group(2)))
        elif match := _CANDIDATE_CUMULATIVE_RE.search(path):
            yy, end_yy, end_month = (int(part) for part in match.groups())
            if end_yy == yy and 1 <= end_month <= 12:
                release = candidate_cumulative_release(yy, end_month)
        elif match := _CANDIDATE_MONTHLY_RE.search(path):
            month = int(match.group(2))
            if 1 <= month <= 12:
                release = candidate_monthly_release(int(match.group(1)), month)
        if release is not None:
            releases[release.url] = release
    return sorted(releases.values(), key=lambda item: (item.kind, item.first_month, item.last_month))


@dataclass(frozen=True)
class ReleaseSelection:
    releases: tuple[UcdpRelease, ...]
    last_month: int
    notes: tuple[str, ...]

    @property
    def final(self) -> UcdpRelease:
        return self.releases[0]

    @property
    def candidates(self) -> tuple[UcdpRelease, ...]:
        return self.releases[1:]


def select_releases(catalog: Iterable[UcdpRelease]) -> ReleaseSelection:
    """Latest final GED plus the Candidate files that extend it month by month without gaps.

    For each year after the final release, the latest cumulative (quarterly) Candidate file
    is used, followed by monthly files for the months after it. Coverage stops at the first
    missing month so that an absent file is never read as a month without events.
    """
    items = list(catalog)
    finals = [item for item in items if item.kind == "ged_final"]
    if not finals:
        raise SourceError("no_ged_final_release_found")
    final = max(finals, key=lambda item: tuple(int(part) for part in item.version.split(".")))
    selected: list[UcdpRelease] = [final]
    notes: list[str] = []
    covered = final.last_month
    candidates = [item for item in items if item.kind in ("candidate_cumulative", "candidate_monthly")]
    years = sorted({year_of(item.first_month) for item in candidates if item.last_month > covered})
    for year in years:
        if covered != month_ordinal(year, 1) - 1:
            notes.append(f"coverage_gap_before_{year}")
            break
        cumulative = [item for item in candidates if item.kind == "candidate_cumulative" and year_of(item.first_month) == year]
        if cumulative:
            best = max(cumulative, key=lambda item: item.last_month)
            selected.append(best)
            covered = best.last_month
        monthly = sorted(
            (
                item
                for item in candidates
                if item.kind == "candidate_monthly" and year_of(item.first_month) == year and item.last_month > covered
            ),
            key=lambda item: item.last_month,
        )
        gap = False
        for item in monthly:
            if item.first_month != covered + 1:
                notes.append(f"missing_candidate_month_{format_month(covered + 1)}")
                gap = True
                break
            selected.append(item)
            covered = item.last_month
        if gap or covered != month_ordinal(year, 12):
            break
    return ReleaseSelection(releases=tuple(selected), last_month=covered, notes=tuple(notes))


def check_allowed_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise SourceError(f"url_not_allowed:{url}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class FileRecord:
    """A downloaded raw file: where it came from, when, and its SHA-256."""

    url: str
    filename: str
    sha256: str
    bytes: int
    retrieved_at: str
    last_modified: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_client(*, timeout: float = DEFAULT_TIMEOUT, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    kwargs: dict[str, Any] = {"timeout": timeout, "follow_redirects": True, "headers": {"User-Agent": USER_AGENT}}
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.Client(**kwargs)


def fetch_text(url: str, *, client: httpx.Client) -> str:
    check_allowed_url(url)
    response = client.get(url)
    check_allowed_url(str(response.url))
    if response.status_code != 200:
        raise SourceError(f"http_{response.status_code}:{url}")
    return response.text


def download_file(url: str, destination: Path, *, client: httpx.Client) -> FileRecord:
    """Stream ``url`` to ``destination`` atomically, hashing the bytes as they arrive."""
    check_allowed_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    digest = hashlib.sha256()
    size = 0
    with client.stream("GET", url) as response:
        check_allowed_url(str(response.url))
        if response.status_code != 200:
            raise SourceError(f"http_{response.status_code}:{url}")
        declared = response.headers.get("content-length")
        last_modified = response.headers.get("last-modified")
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    if declared is not None and declared.isdigit() and int(declared) != size:
        partial.unlink(missing_ok=True)
        raise SourceError(f"truncated_download:{url}:{size}/{declared}")
    if size == 0:
        partial.unlink(missing_ok=True)
        raise SourceError(f"empty_download:{url}")
    os.replace(partial, destination)
    return FileRecord(
        url=url,
        filename=destination.name,
        sha256=digest.hexdigest(),
        bytes=size,
        retrieved_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        last_modified=last_modified,
    )


class RawStore:
    """Download cache keyed by URL. Raw bytes are kept as received; the manifest records hashes."""

    MANIFEST = "raw_manifest.json"

    def __init__(self, directory: Path):
        self.directory = directory
        self.manifest_path = directory / self.MANIFEST
        self._records: dict[str, FileRecord] = {}
        if self.manifest_path.exists():
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            for item in payload.get("files", []):
                record = FileRecord(**item)
                self._records[record.url] = record

    def record(self, url: str) -> FileRecord | None:
        return self._records.get(url)

    def path_for(self, record: FileRecord) -> Path:
        return self.directory / record.filename

    def cached(self, url: str, *, verify: bool = True) -> FileRecord | None:
        record = self._records.get(url)
        if record is None:
            return None
        path = self.path_for(record)
        if not path.exists():
            return None
        if verify and sha256_file(path) != record.sha256:
            raise SourceError(f"cached_file_hash_mismatch:{record.filename}")
        return record

    def ensure(
        self,
        url: str,
        *,
        client: httpx.Client | None,
        filename: str | None = None,
        refresh: bool = False,
        revalidate: bool = False,
    ) -> FileRecord:
        """Return the cached file for ``url``, downloading it when absent or when ``refresh``.

        With ``revalidate`` and a client, a HEAD request re-downloads a cached file whose
        published size or Last-Modified header changed.
        """
        if not refresh:
            existing = self.cached(url)
            if existing is not None:
                if not revalidate or client is None or not self._changed_upstream(existing, client):
                    return existing
        if client is None:
            raise SourceError(f"offline_and_not_cached:{url}")
        name = filename or url.rsplit("/", 1)[-1]
        record = download_file(url, self.directory / name, client=client)
        self._records[url] = record
        self._save()
        return record

    @staticmethod
    def _changed_upstream(record: FileRecord, client: httpx.Client) -> bool:
        check_allowed_url(record.url)
        try:
            response = client.head(record.url)
        except httpx.HTTPError:
            return False
        check_allowed_url(str(response.url))
        if response.status_code != 200:
            return False
        length = response.headers.get("content-length")
        modified = response.headers.get("last-modified")
        if length is not None and length.isdigit() and int(length) != record.bytes:
            return True
        return modified is not None and record.last_modified is not None and modified != record.last_modified

    def _save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {"files": [record.as_dict() for record in sorted(self._records.values(), key=lambda r: r.url)]}
        partial = self.manifest_path.with_name(self.manifest_path.name + ".part")
        partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(partial, self.manifest_path)
