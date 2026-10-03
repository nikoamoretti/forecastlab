"""Bounded retrieval of original official release documents (DOL, BLS, Federal Reserve)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

import httpx

from forecastlab.fred_release_documents import (
    CONTENT_TYPES,
    DOCUMENT_URLS,
    EXTRACTION_METHODS,
    TITLES,
    ReleaseDocument,
    document_text,
    parse_release_document,
    release_claim,
    verify_release_target,
)
from forecastlab.macro import SERIES, MacroDataError, MacroSpec
from forecastlab.official_releases import (
    DOL_INDEX,
    PUBLIC_DATA_USER_AGENT,
    dol_release_links,
    fed_calendar_url,
    release_announcements,
    verify_fed_schedule,
)
from forecastlab.root_event import digest
from forecastlab.timeutil import utcnow
from forecastlab_api.artifact_store import put_bytes

MAX_DOCUMENT_BYTES = 2_000_000
RELEASE_DOCUMENT_UNAVAILABLE = "official_release_document_unavailable"
RELEASE_DOCUMENT_NOT_RETAINED = "official_release_document_not_retained"


def fetch_dol_schedules(http: httpx.Client, now: datetime):
    documents: dict[str, dict] = {}
    content: dict[str, bytes] = {}

    def read(url: str) -> bytes:
        if url in content:
            return content[url]
        with http.stream("GET", url) as response:
            response.raise_for_status()
            mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            expected = "application/pdf" if url.endswith(".pdf") else "text/html"
            if mime != expected:
                raise MacroDataError("official_source_content_type_mismatch")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 2_000_000:
                    raise MacroDataError("official_source_response_too_large")
        content[url] = bytes(data)
        documents[url] = put_bytes(content[url], content_type=mime)
        return content[url]

    links = dol_release_links(read(DOL_INDEX).decode("utf-8"))
    releases, gaps = [], []
    for family in ("empsit", "cpi"):
        try:
            url = links.get(family)
            if url is None:
                raise MacroDataError("official_release_index_entry_missing")
            current, upcoming = release_announcements(read(url), source_url=url, checked_at=now)
            calendar_url = fed_calendar_url(upcoming)
            verified = verify_fed_schedule(read(calendar_url).decode("utf-8"), upcoming, source_url=calendar_url)
            releases.extend([current, verified])
        except (httpx.HTTPError, ValueError) as exc:
            reason = (f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError)
                      else str(exc) if isinstance(exc, MacroDataError) else type(exc).__name__)
            gaps.append(f"{family}: Official release schedule unavailable ({reason}).")
    return releases, documents, gaps


@dataclass(frozen=True)
class RetainedReleaseDocument:
    """An official FRED-series publication document, parsed and retained as original bytes."""
    indicator: str
    content: bytes
    artifact: dict
    retrieved_at: datetime
    document: ReleaseDocument


def _read_document(http: httpx.Client, url: str, expected_type: str) -> bytes:
    # Fixed official URL, no redirects, bounded size, exact content type.
    with http.stream("GET", url) as response:
        response.raise_for_status()
        mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if mime != expected_type:
            raise MacroDataError("official_source_content_type_mismatch")
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_DOCUMENT_BYTES:
                raise MacroDataError("official_source_response_too_large")
    return bytes(data)


def fetch_fred_release_documents(indicators: set[str], *, client: httpx.Client | None = None
                                 ) -> dict[str, RetainedReleaseDocument | str]:
    """One keyless request per FRED indicator for its official publication document.

    Sources: ``https://www.dol.gov/ui/data.pdf`` (ICSA) and
    ``https://www.federalreserve.gov/releases/h15/`` (DGS10). Both paths are
    allowed by the hosts' robots.txt and are public U.S. government documents.
    The original bytes are retained before parsing. Each value is either the
    retained, parsed document or a gap code; nothing is inferred when a
    document is unavailable or unrecognized.
    """
    owned = client is None
    http = client or httpx.Client(timeout=12, follow_redirects=False, headers={"User-Agent": PUBLIC_DATA_USER_AGENT})
    result: dict[str, RetainedReleaseDocument | str] = {}
    try:
        for indicator in sorted(indicators):
            if indicator not in DOCUMENT_URLS:
                result[indicator] = "official_release_document_not_applicable"
                continue
            try:
                content = _read_document(http, DOCUMENT_URLS[indicator], CONTENT_TYPES[indicator])
            except (httpx.HTTPError, MacroDataError):
                result[indicator] = RELEASE_DOCUMENT_UNAVAILABLE
                continue
            retrieved = utcnow()
            try:
                artifact = put_bytes(content, content_type=CONTENT_TYPES[indicator])
            except Exception:  # Unretained evidence is never attached.
                result[indicator] = RELEASE_DOCUMENT_NOT_RETAINED
                continue
            try:
                document = parse_release_document(indicator, content)
            except MacroDataError as exc:
                result[indicator] = str(exc)
                continue
            result[indicator] = RetainedReleaseDocument(indicator=indicator, content=content, artifact=artifact,
                                                        retrieved_at=retrieved, document=document)
    finally:
        if owned:
            http.close()
    return result


def release_document_evidence(spec: MacroSpec, retained: RetainedReleaseDocument) -> list[dict]:
    """``resolution`` evidence for a FRED entry from its retained official publication document.

    The packet item has the same shape as the BLS schedule item. The quotation
    is re-checked verbatim against text re-extracted from the retained bytes,
    whose hash must match the stored artifact. Raises ``MacroDataError`` with a
    gap code when any check fails.
    """
    document = retained.document
    if spec.indicator != retained.indicator or document.indicator != spec.indicator:
        raise MacroDataError("official_release_document_series_mismatch")
    if hashlib.sha256(retained.content).hexdigest() != retained.artifact.get("sha256"):
        raise MacroDataError("official_release_document_hash_mismatch")
    if not document.quote or document.quote not in document_text(spec.indicator, retained.content):
        raise MacroDataError("official_release_quote_not_verbatim")
    pattern = verify_release_target(document, spec, checked_at=retained.retrieved_at)
    meta = SERIES[spec.indicator]
    identity = {"indicator": spec.indicator, "url": document.source_url, "sha256": retained.artifact["sha256"],
                "quote": document.quote, "target": spec.observation_period}
    return [{"schema_version": "evidence_assessment_v2", "claim_id": "release:" + digest(identity),
        "classification": "background", "relevant": True, "usable": True, "required_sections": ["resolution"],
        "reason": ("Deterministically verified official publication document; original retained and hash-checked. "
                   "It does not state the target's release date; the contract's expected release follows the "
                   "document's own publication pattern."),
        "claim": release_claim(document), "quote": document.quote, "url": document.source_url,
        "title": TITLES[spec.indicator], "publisher": meta["publisher"], "primary_source": True,
        "source_lineage": meta["lineage"], "source_available_at": retained.retrieved_at.isoformat(),
        "extraction_method": EXTRACTION_METHODS[spec.indicator], "artifact": retained.artifact,
        "corroboration": [], "release_pattern": pattern}]
