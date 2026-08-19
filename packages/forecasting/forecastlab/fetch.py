from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import trafilatura
from pypdf import PdfReader

from forecastlab.hashing import content_hash
from forecastlab.schemas import FetchedDocument
from forecastlab.ssrf import UnsafeURLError, validate_url
from forecastlab.timeutil import parse_datetime, utcnow

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "sources"
MAX_BYTES = 2_000_000
FETCH_TIMEOUT = 20.0

FIXTURE_PAGES: dict[str, str] = {
    "https://fixtures.forecastlab.local/bls-employment-situation": "bls-employment-situation.html",
    "https://fixtures.forecastlab.local/fred-unrate": "fred-unrate.html",
    "https://fixtures.forecastlab.local/cbo-outlook": "cbo-outlook.html",
    "https://fixtures.forecastlab.local/nber-cycles": "nber-cycles.html",
    "https://fixtures.forecastlab.local/jolts": "jolts.html",
}


@dataclass
class FetchLimits:
    max_bytes: int = MAX_BYTES
    timeout: float = FETCH_TIMEOUT


def _extract_html(raw: str, url: str) -> tuple[str, str | None]:
    extracted = trafilatura.extract(raw, url=url, include_comments=False, include_tables=True)
    title = trafilatura.extract_metadata(raw)
    page_title = title.title if title else None
    return (extracted or re.sub(r"<[^>]+>", " ", raw)), page_title


def _extract_pdf(data: bytes) -> str:
    reader = PdfReader(__import__("io").BytesIO(data))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(pages)


def fetch_document(
    url: str,
    *,
    as_of: datetime | None = None,
    allow_local_fixtures: bool = True,
    snapshot_url: str | None = None,
    snapshot_at: datetime | None = None,
    limits: FetchLimits | None = None,
) -> FetchedDocument:
    limits = limits or FetchLimits()
    now = utcnow()
    if url in FIXTURE_PAGES and allow_local_fixtures:
        path = FIXTURES_DIR / FIXTURE_PAGES[url]
        raw = path.read_text(encoding="utf-8")
        text, title = _extract_html(raw, url)
        published = _published_from_html(raw)
        eligible = True
        reason = None
        if as_of and published and published > as_of:
            eligible = False
            reason = "published_after_as_of"
        return FetchedDocument(
            url=url,
            title=title or path.stem.replace("-", " ").title(),
            publisher="ForecastLab fixtures",
            published_at=published,
            retrieved_at=now,
            text=text[:20_000],
            content_hash=content_hash(text),
            snapshot_url=snapshot_url or url,
            snapshot_at=snapshot_at or published,
            status_code=200,
            rejected=not eligible,
            rejection_reason=reason,
            as_of_eligible=eligible,
        )

    try:
        validate_url(url, allow_local_fixtures=allow_local_fixtures)
    except UnsafeURLError as exc:
        return FetchedDocument(
            url=url,
            title="",
            publisher=None,
            published_at=None,
            retrieved_at=now,
            text="",
            content_hash=content_hash(""),
            status_code=0,
            rejected=True,
            rejection_reason=f"unsafe_url:{exc}",
            as_of_eligible=False,
        )

    target = snapshot_url or url
    try:
        with httpx.Client(timeout=limits.timeout, follow_redirects=True, max_redirects=3) as client:
            response = client.get(target, headers={"User-Agent": "ForecastLab/0.1 research-fetch"})
            data = response.content[: limits.max_bytes]
            status = response.status_code
            content_type = response.headers.get("content-type", "")
    except httpx.HTTPError as exc:
        return FetchedDocument(
            url=url,
            title="",
            retrieved_at=now,
            text="",
            content_hash=content_hash(""),
            status_code=0,
            rejected=True,
            rejection_reason=f"fetch_error:{exc.__class__.__name__}",
            as_of_eligible=False,
        )

    if status >= 400:
        return FetchedDocument(
            url=url,
            title="",
            retrieved_at=now,
            text="",
            content_hash=content_hash(""),
            status_code=status,
            rejected=True,
            rejection_reason=f"http_{status}",
            as_of_eligible=False,
        )

    if "pdf" in content_type or target.lower().endswith(".pdf"):
        text = _extract_pdf(data)
        title = urlparse(url).path.rsplit("/", 1)[-1]
        published = None
    else:
        raw = data.decode("utf-8", errors="replace")
        text, title = _extract_html(raw, url)
        published = _published_from_html(raw)

    eligible = True
    reason = None
    if as_of and published and published > as_of:
        eligible = False
        reason = "published_after_as_of"
    if as_of and snapshot_at and snapshot_at > as_of:
        eligible = False
        reason = "snapshot_after_as_of"

    return FetchedDocument(
        url=url,
        title=title or url,
        publisher=urlparse(url).hostname,
        published_at=published,
        retrieved_at=now,
        text=text[:20_000],
        content_hash=content_hash(text),
        snapshot_url=snapshot_url,
        snapshot_at=snapshot_at,
        status_code=status,
        rejected=not eligible,
        rejection_reason=reason,
        as_of_eligible=eligible,
    )


def _published_from_html(raw: str) -> datetime | None:
    patterns = [
        r'<meta[^>]+property="article:published_time"[^>]+content="([^"]+)"',
        r'<meta[^>]+name="date"[^>]+content="([^"]+)"',
        r'data-published="([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, re.I)
        if match:
            parsed = parse_datetime(match.group(1))
            if parsed:
                return parsed
    return None
