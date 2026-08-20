from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import trafilatura
from pypdf import PdfReader

from forecastlab.errors import EvidenceIntegrityError
from forecastlab.hashing import content_hash
from forecastlab.http_client import SafeResponse, safe_get
from forecastlab.schemas import FetchedDocument
from forecastlab.ssrf import UnsafeURLError, validate_url
from forecastlab.timeutil import as_utc, parse_datetime, utcnow
from forecastlab.wayback import parse_wayback_url, verify_final_capture

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "sources"
MAX_BYTES = 2_000_000
FETCH_TIMEOUT = 20.0

FIXTURE_PAGES: dict[str, str] = {
    "https://fixtures.forecastlab.local/bls-employment-situation": "bls-employment-situation.html",
    "https://fixtures.forecastlab.local/fred-unrate": "fred-unrate.html",
    "https://fixtures.forecastlab.local/cbo-outlook": "cbo-outlook.html",
    "https://fixtures.forecastlab.local/nber-cycles": "nber-cycles.html",
    "https://fixtures.forecastlab.local/jolts": "jolts.html",
    "https://fixtures.forecastlab.local/undated": "undated.html",
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


def _rejected(
    url: str,
    reason: str,
    *,
    now: datetime,
    published: datetime | None = None,
    snapshot_url: str | None = None,
    snapshot_at: datetime | None = None,
    requested_snapshot_url: str | None = None,
    requested_snapshot_at: datetime | None = None,
    final_snapshot_url: str | None = None,
    final_snapshot_at: datetime | None = None,
    archived_original_url: str | None = None,
    snapshot_verification_status: str | None = None,
    status_code: int = 0,
) -> FetchedDocument:
    return FetchedDocument(
        url=url,
        title="",
        publisher=None,
        published_at=published,
        retrieved_at=now,
        text="",
        content_hash=content_hash(""),
        snapshot_url=snapshot_url,
        snapshot_at=snapshot_at,
        requested_snapshot_url=requested_snapshot_url,
        requested_snapshot_at=requested_snapshot_at,
        final_snapshot_url=final_snapshot_url,
        final_snapshot_at=final_snapshot_at,
        archived_original_url=archived_original_url,
        snapshot_verification_status=snapshot_verification_status or reason,
        status_code=status_code,
        rejected=True,
        rejection_reason=reason,
        as_of_eligible=False,
        published_at_unknown=published is None,
    )


def fetch_document(
    url: str,
    *,
    as_of: datetime | None = None,
    allow_local_fixtures: bool = True,
    snapshot_url: str | None = None,
    snapshot_at: datetime | None = None,
    limits: FetchLimits | None = None,
    mode: str = "live",
) -> FetchedDocument:
    limits = limits or FetchLimits()
    now = utcnow()
    if as_of is not None:
        as_of = as_utc(as_of)
    if snapshot_at is not None:
        snapshot_at = as_utc(snapshot_at)
    historical = mode == "backtest" or as_of is not None
    if mode == "backtest" and as_of is None:
        return _rejected(url, "unverifiable_as_of", now=now)

    if url in FIXTURE_PAGES and allow_local_fixtures:
        path = FIXTURES_DIR / FIXTURE_PAGES[url]
        raw = path.read_text(encoding="utf-8")
        text, title = _extract_html(raw, url)
        published = _published_from_html(raw)
        if historical and as_of:
            if published is None:
                return _rejected(url, "unverifiable_as_of", now=now)
            if published > as_of:
                return _rejected(url, "published_after_as_of", now=now, published=published)
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
            requested_snapshot_url=snapshot_url,
            requested_snapshot_at=snapshot_at,
            final_snapshot_url=snapshot_url or url,
            final_snapshot_at=snapshot_at or published,
            archived_original_url=url,
            snapshot_verification_status="fixture",
            status_code=200,
            rejected=False,
            rejection_reason=None,
            as_of_eligible=True,
            published_at_unknown=published is None,
        )

    if historical and not snapshot_url:
        return _rejected(
            url,
            "no_eligible_historical_snapshot",
            now=now,
            snapshot_at=snapshot_at,
            requested_snapshot_url=snapshot_url,
            requested_snapshot_at=snapshot_at,
        )
    if historical and snapshot_at and as_of and snapshot_at > as_of:
        return _rejected(
            url,
            "snapshot_after_as_of",
            now=now,
            snapshot_url=snapshot_url,
            snapshot_at=snapshot_at,
            requested_snapshot_url=snapshot_url,
            requested_snapshot_at=snapshot_at,
        )

    try:
        validate_url(snapshot_url or url, allow_local_fixtures=allow_local_fixtures)
    except UnsafeURLError as exc:
        return _rejected(url, f"unsafe_url:{exc}", now=now)

    target = snapshot_url or url
    try:
        response: SafeResponse = safe_get(
            target,
            allow_local_fixtures=allow_local_fixtures,
            timeout=limits.timeout,
            max_bytes=limits.max_bytes,
        )
    except UnsafeURLError as exc:
        return _rejected(
            url,
            f"unsafe_url:{exc}",
            now=now,
            snapshot_url=snapshot_url,
            snapshot_at=snapshot_at,
            requested_snapshot_url=snapshot_url,
            requested_snapshot_at=snapshot_at,
        )
    except Exception as exc:
        return _rejected(
            url,
            f"historical_fetch_failed:{exc.__class__.__name__}" if historical else f"fetch_error:{exc.__class__.__name__}",
            now=now,
            snapshot_url=snapshot_url,
            snapshot_at=snapshot_at,
            requested_snapshot_url=snapshot_url,
            requested_snapshot_at=snapshot_at,
        )

    if response.truncated:
        return _rejected(url, "response_too_large", now=now, status_code=response.status_code)
    if response.status_code >= 400:
        return _rejected(url, f"http_{response.status_code}", now=now, status_code=response.status_code)

    final_url = response.final_url
    final_at = snapshot_at
    archived_original = url
    verification_status = "live" if not historical else "unverified"
    if historical and as_of is not None:
        try:
            capture, verification_status = verify_final_capture(response, requested_url=url, as_of=as_of)
        except EvidenceIntegrityError as exc:
            reason = str(exc)
            parsed = parse_wayback_url(response.final_url)
            return _rejected(
                url,
                reason,
                now=now,
                snapshot_url=snapshot_url,
                snapshot_at=snapshot_at,
                requested_snapshot_url=snapshot_url,
                requested_snapshot_at=snapshot_at,
                final_snapshot_url=response.final_url,
                final_snapshot_at=parsed.timestamp if parsed else None,
                archived_original_url=parsed.archived_original_url if parsed else None,
                snapshot_verification_status=reason,
                status_code=response.status_code,
            )
        final_url = capture.final_url
        final_at = capture.timestamp
        archived_original = capture.archived_original_url

    data = response.content
    content_type = response.content_type
    if "pdf" in content_type or target.lower().endswith(".pdf"):
        text = _extract_pdf(data)
        title = urlparse(url).path.rsplit("/", 1)[-1]
        published = None
    else:
        raw = data.decode("utf-8", errors="replace")
        text, title = _extract_html(raw, url)
        published = _published_from_html(raw)

    if historical and as_of and published and published > as_of:
        return _rejected(
            url,
            "published_after_as_of",
            now=now,
            published=published,
            snapshot_url=snapshot_url,
            snapshot_at=snapshot_at,
            status_code=response.status_code,
        )

    return FetchedDocument(
        url=url,
        title=title or url,
        publisher=urlparse(url).hostname,
        published_at=published,
        retrieved_at=now,
        text=text[:20_000],
        content_hash=content_hash(text),
            snapshot_url=final_url or snapshot_url,
            snapshot_at=final_at or snapshot_at,
            requested_snapshot_url=snapshot_url,
            requested_snapshot_at=snapshot_at,
            final_snapshot_url=final_url,
            final_snapshot_at=final_at,
            archived_original_url=archived_original,
            snapshot_verification_status=verification_status,
            status_code=response.status_code,
            rejected=False,
            rejection_reason=None,
            as_of_eligible=True,
            published_at_unknown=published is None,
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
