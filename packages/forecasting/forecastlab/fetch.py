from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

import trafilatura
from pypdf import PdfReader

from forecastlab.errors import EvidenceIntegrityError
from forecastlab.hashing import content_hash, sha256_bytes
from forecastlab.http_client import SafeResponse, safe_get
from forecastlab.paths import project_root
from forecastlab.schemas import FetchedDocument
from forecastlab.ssrf import UnsafeURLError, validate_url
from forecastlab.timeutil import as_utc, parse_datetime, utcnow
from forecastlab.wayback import parse_wayback_url, verify_final_capture

FIXTURES_DIR = project_root() / "fixtures" / "sources"
MAX_BYTES = 2_000_000
FETCH_TIMEOUT = 20.0

_ACCESS_WALL_TITLE_MARKERS = {
    "access denied",
    "attention required",
    "just a moment",
    "request access",
    "verify you are human",
}
_ACCESS_WALL_BODY_MARKERS = (
    "captcha",
    "complete the security check",
    "enable javascript and cookies to continue",
    "press and hold to confirm you are a human",
    "verify you are human",
)

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


@dataclass(frozen=True)
class DocumentDateMetadata:
    published_at: datetime | None = None
    publication_date_source: str | None = None
    publication_date_verified: bool = False
    modified_at: datetime | None = None
    modified_date_source: str | None = None


def _access_wall_reason(text: str, title: str | None) -> str | None:
    normalized_title = " ".join((title or "").casefold().split())
    normalized_body = " ".join(text[:6000].casefold().split())
    if normalized_title in _ACCESS_WALL_TITLE_MARKERS:
        return "access_wall_or_challenge"
    if any(marker in normalized_body for marker in _ACCESS_WALL_BODY_MARKERS):
        return "access_wall_or_challenge"
    if (
        len(normalized_body) < 2500
        and "request access" in normalized_body
        and (
            "access to this page" in normalized_body
            or "automated access" in normalized_body
            or "temporarily unavailable" in normalized_body
        )
    ):
        return "access_wall_or_challenge"
    return None


class _DateMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: list[dict[str, str]] = []
        self.times: list[dict[str, str]] = []
        self.json_ld: list[str] = []
        self._json_ld_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        lowered = tag.casefold()
        if lowered == "meta":
            self.meta.append(values)
        elif lowered == "time" and values.get("datetime"):
            self.times.append(values)
        elif lowered == "script" and "ld+json" in values.get("type", "").casefold():
            self._json_ld_parts = []

    def handle_data(self, data: str) -> None:
        if self._json_ld_parts is not None:
            self._json_ld_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._json_ld_parts is not None:
            self.json_ld.append("".join(self._json_ld_parts))
            self._json_ld_parts = None


def _extract_html(raw: str, url: str) -> tuple[str, str | None]:
    extracted = trafilatura.extract(raw, url=url, include_comments=False, include_tables=True)
    title = trafilatura.extract_metadata(raw)
    page_title = title.title if title else None
    return (extracted or re.sub(r"<[^>]+>", " ", raw)), page_title


def _parse_pdf_date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return as_utc(value)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.startswith("D:"):
        normalized = normalized[2:]
    match = re.match(r"^(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", normalized)
    if match is None:
        return parse_datetime(normalized)
    parts = match.groups(default="01")
    try:
        return as_utc(
            datetime(
                int(parts[0]),
                int(parts[1]),
                int(parts[2]),
                int(parts[3] if match.group(4) else "00"),
                int(parts[4] if match.group(5) else "00"),
                int(parts[5] if match.group(6) else "00"),
            )
        )
    except ValueError:
        return None


def _extract_pdf(data: bytes) -> tuple[str, DocumentDateMetadata]:
    reader = PdfReader(BytesIO(data))
    pages = [(page.extract_text() or "") for page in reader.pages]
    metadata: Any = reader.metadata or {}
    created = _parse_pdf_date(metadata.get("/CreationDate"))
    modified = _parse_pdf_date(metadata.get("/ModDate"))
    return (
        "\n".join(pages),
        DocumentDateMetadata(
            published_at=created,
            publication_date_source="pdf_creation_metadata" if created else None,
            publication_date_verified=False,
            modified_at=modified,
            modified_date_source="pdf_modified_metadata" if modified else None,
        ),
    )


def _json_ld_values(value: Any, key: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        candidate = value.get(key)
        if isinstance(candidate, str):
            found.append(candidate)
        for nested in value.values():
            found.extend(_json_ld_values(nested, key))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_json_ld_values(nested, key))
    return found


def _first_parsed(values: list[str]) -> datetime | None:
    for value in values:
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed
    return None


def _date_metadata_from_html(raw: str) -> DocumentDateMetadata:
    parser = _DateMetadataParser()
    try:
        parser.feed(raw)
    except Exception:
        parser = _DateMetadataParser()

    published: datetime | None = None
    publication_source: str | None = None
    modified: datetime | None = None
    modified_source: str | None = None

    published_keys = {
        "article:published_time",
        "date",
        "pubdate",
        "publishdate",
        "published",
        "datepublished",
        "dc.date",
        "dcterms.date",
    }
    modified_keys = {
        "article:modified_time",
        "datemodified",
        "modified",
        "last-modified",
    }
    for item in parser.meta:
        key = (
            item.get("property")
            or item.get("name")
            or item.get("itemprop")
            or ""
        ).casefold()
        content = item.get("content") or ""
        parsed = parse_datetime(content)
        if parsed is None:
            continue
        if published is None and key in published_keys:
            published = parsed
            publication_source = f"html_meta:{key}"
        if modified is None and key in modified_keys:
            modified = parsed
            modified_source = f"html_meta:{key}"

    for payload in parser.json_ld:
        try:
            decoded = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        if published is None:
            published = _first_parsed(_json_ld_values(decoded, "datePublished"))
            if published is not None:
                publication_source = "json_ld:datePublished"
        if modified is None:
            modified = _first_parsed(_json_ld_values(decoded, "dateModified"))
            if modified is not None:
                modified_source = "json_ld:dateModified"

    if published is None:
        prioritized_times = sorted(
            parser.times,
            key=lambda item: 0
            if "publish" in " ".join(
                (item.get("itemprop", ""), item.get("class", ""), item.get("rel", ""))
            ).casefold()
            else 1,
        )
        published = _first_parsed([item["datetime"] for item in prioritized_times])
        if published is not None:
            publication_source = "html_time:datetime"

    if published is None:
        data_match = re.search(r"data-published=[\"']([^\"']+)[\"']", raw, re.I)
        if data_match:
            published = parse_datetime(data_match.group(1))
            if published is not None:
                publication_source = "html_attribute:data-published"

    if published is None:
        try:
            metadata = trafilatura.extract_metadata(raw)
        except Exception:
            metadata = None
        metadata_date = parse_datetime(getattr(metadata, "date", None)) if metadata else None
        if metadata_date is not None:
            published = metadata_date
            publication_source = "trafilatura_metadata:date"

    return DocumentDateMetadata(
        published_at=published,
        publication_date_source=publication_source,
        publication_date_verified=published is not None,
        modified_at=modified,
        modified_date_source=modified_source,
    )


def _with_search_hint(
    metadata: DocumentDateMetadata,
    hint: datetime | None,
    hint_source: str | None = None,
) -> tuple[DocumentDateMetadata, datetime | None, str | None]:
    parsed_hint = as_utc(hint) if hint is not None else None
    source = hint_source or "search_provider_hint"
    if metadata.published_at is not None or parsed_hint is None:
        return metadata, parsed_hint, source if parsed_hint is not None else None
    return (
        DocumentDateMetadata(
            published_at=parsed_hint,
            publication_date_source=source,
            publication_date_verified=False,
            modified_at=metadata.modified_at,
            modified_date_source=metadata.modified_date_source,
        ),
        parsed_hint,
        source,
    )


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
    available_at = final_snapshot_at or snapshot_at or published or now
    temporal_basis = (
        "snapshot_date"
        if final_snapshot_at is not None or snapshot_at is not None
        else "publication_date"
        if published is not None
        else "retrieval_date"
    )
    return FetchedDocument(
        url=url,
        final_url=final_snapshot_url,
        title="",
        publisher=None,
        published_at=published,
        retrieved_at=now,
        source_available_at=available_at,
        temporal_basis=temporal_basis,
        publication_date_source="document_metadata" if published else None,
        publication_date_verified=published is not None,
        text="",
        content_hash=content_hash(""),
        raw_content_hash=sha256_bytes(b""),
        extracted_text_hash=content_hash(""),
        content_type=None,
        byte_length=0,
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
    publication_date_hint: datetime | None = None,
    publication_date_hint_source: str | None = None,
    retain_bytes=None,
) -> FetchedDocument:
    limits = limits or FetchLimits()
    now = utcnow()
    if as_of is not None:
        as_of = as_utc(as_of)
    if snapshot_at is not None:
        snapshot_at = as_utc(snapshot_at)
    if publication_date_hint is not None:
        publication_date_hint = as_utc(publication_date_hint)
    historical = mode == "backtest" or as_of is not None
    if mode == "backtest" and as_of is None:
        return _rejected(url, "unverifiable_as_of", now=now)

    if url in FIXTURE_PAGES and allow_local_fixtures:
        path = FIXTURES_DIR / FIXTURE_PAGES[url]
        raw = path.read_text(encoding="utf-8")
        raw_bytes = raw.encode("utf-8")
        if retain_bytes:
            retain_bytes(raw_bytes, "text/html")
        text, title = _extract_html(raw, url)
        metadata, retained_hint, default_hint_source = _with_search_hint(
            _date_metadata_from_html(raw),
            publication_date_hint,
            publication_date_hint_source,
        )
        published = metadata.published_at
        if historical and as_of:
            if published is None and snapshot_at is None:
                return _rejected(url, "unverifiable_as_of", now=now)
            if published is not None and published > as_of:
                return _rejected(url, "published_after_as_of", now=now, published=published)
            if snapshot_at is not None and snapshot_at > as_of:
                return _rejected(
                    url,
                    "snapshot_after_as_of",
                    now=now,
                    published=published,
                    snapshot_url=snapshot_url,
                    snapshot_at=snapshot_at,
                )
        if historical:
            source_available_at = snapshot_at or published
            temporal_basis = "snapshot_date" if snapshot_at is not None else "publication_date"
        elif mode == "demo" and published is not None:
            source_available_at = published
            temporal_basis = "publication_date"
        else:
            source_available_at = now
            temporal_basis = "retrieval_date"
        if source_available_at is None:
            source_available_at = now
            temporal_basis = "retrieval_date"
        return FetchedDocument(
            url=url,
            final_url=snapshot_url or url,
            title=title or path.stem.replace("-", " ").title(),
            publisher="ForecastLab fixtures",
            published_at=published,
            retrieved_at=now,
            source_available_at=source_available_at,
            temporal_basis=temporal_basis,
            publication_date_source=metadata.publication_date_source,
            publication_date_verified=metadata.publication_date_verified,
            publication_date_hint=retained_hint,
            publication_date_hint_source=(
                publication_date_hint_source or default_hint_source
                if retained_hint is not None
                else None
            ),
            modified_at=metadata.modified_at,
            modified_date_source=metadata.modified_date_source,
            text=text[:20_000],
            content_hash=content_hash(text),
            raw_content_hash=sha256_bytes(raw_bytes),
            extracted_text_hash=content_hash(text[:20_000]),
            content_type="text/html",
            byte_length=len(raw_bytes),
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
    if retain_bytes:
        retain_bytes(data, content_type)
    if "pdf" in content_type or target.lower().endswith(".pdf"):
        text, metadata = _extract_pdf(data)
        title = urlparse(url).path.rsplit("/", 1)[-1]
    else:
        raw = data.decode("utf-8", errors="replace")
        text, title = _extract_html(raw, url)
        metadata = _date_metadata_from_html(raw)

    access_wall_reason = _access_wall_reason(text, title)
    if access_wall_reason is not None:
        return _rejected(
            url,
            access_wall_reason,
            now=now,
            snapshot_url=snapshot_url,
            snapshot_at=snapshot_at,
            requested_snapshot_url=snapshot_url,
            requested_snapshot_at=snapshot_at,
            final_snapshot_url=final_url,
            final_snapshot_at=final_at,
            archived_original_url=archived_original,
            snapshot_verification_status=verification_status,
            status_code=response.status_code,
        )

    metadata, retained_hint, default_hint_source = _with_search_hint(
        metadata,
        publication_date_hint,
        publication_date_hint_source,
    )
    published = metadata.published_at

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

    source_available_at = final_at if historical and final_at is not None else now
    temporal_basis = "snapshot_date" if historical and final_at is not None else "retrieval_date"
    return FetchedDocument(
        url=url,
        final_url=final_url,
        title=title or url,
        publisher=urlparse(url).hostname,
        published_at=published,
        retrieved_at=now,
        source_available_at=source_available_at,
        temporal_basis=temporal_basis,
        publication_date_source=metadata.publication_date_source,
        publication_date_verified=metadata.publication_date_verified,
        publication_date_hint=retained_hint,
        publication_date_hint_source=(
            publication_date_hint_source or default_hint_source
            if retained_hint is not None
            else None
        ),
        modified_at=metadata.modified_at,
        modified_date_source=metadata.modified_date_source,
        text=text[:20_000],
        content_hash=content_hash(text),
        raw_content_hash=sha256_bytes(data),
        extracted_text_hash=content_hash(text[:20_000]),
        content_type=content_type,
        byte_length=len(data),
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
    return _date_metadata_from_html(raw).published_at
