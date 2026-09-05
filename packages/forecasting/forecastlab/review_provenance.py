"""Deterministic V2 review-provenance restoration helpers.

The helpers deliberately separate a successfully re-verified historical
document from a review-ready candidate.  A current fetch cannot repair a
missing pre-outcome origin proof or outcome-source availability proof.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from forecastlab.schemas import FetchedDocument
from forecastlab.timeutil import as_utc


def source_host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.").casefold()


def restored_snapshot_source(
    *,
    candidate_id: str,
    document: dict[str, Any],
    attempt: dict[str, Any],
    fetched: FetchedDocument,
    stored_blob_hash_verified: bool,
    stored_text_hash_verified: bool,
) -> dict[str, Any]:
    """Create a source receipt only when the frozen content re-verifies exactly."""

    reasons: list[str] = []
    cutoff = attempt.get("cutoff")
    if attempt.get("status") != "accepted":
        reasons.append("recorded_archive_attempt_not_accepted")
    if document.get("temporal_basis") != "snapshot_date":
        reasons.append("historical_temporal_basis_not_snapshot_date")
    if not document.get("cutoff_verified") or not document.get("archive_original_verified"):
        reasons.append("historical_capture_not_verified")
    if fetched.rejected or not fetched.as_of_eligible:
        reasons.append(f"historical_refetch_rejected:{fetched.rejection_reason or 'unknown'}")
    if fetched.raw_content_hash != document.get("content_sha256"):
        reasons.append("historical_content_hash_drift")
    if not stored_blob_hash_verified:
        reasons.append("frozen_blob_hash_drift")
    if not stored_text_hash_verified:
        reasons.append("frozen_extracted_text_hash_drift")
    if str(fetched.final_url or "") != str(attempt.get("final_snapshot_url") or ""):
        reasons.append("historical_final_snapshot_url_drift")
    source_available = document.get("source_available_at")
    if source_available is None:
        reasons.append("historical_source_available_at_missing")
    else:
        try:
            if cutoff is not None and as_utc(datetime.fromisoformat(source_available)) > as_utc(
                datetime.fromisoformat(cutoff)
            ):
                reasons.append("historical_snapshot_after_cutoff")
        except ValueError:
            reasons.append("historical_timestamp_invalid")
    title = (fetched.title or "").strip()
    if not title:
        reasons.append("source_title_missing")
    host = source_host(str(document.get("canonical_url") or ""))
    if not host:
        reasons.append("source_host_missing")
    return {
        "candidate_id": candidate_id,
        "document_id": document.get("document_id"),
        "status": "complete" if not reasons else "failed",
        "reasons": reasons,
        "source": {
            "url": document.get("canonical_url"),
            "final_snapshot_url": attempt.get("final_snapshot_url"),
            "title": title or None,
            "publisher": fetched.publisher or host or None,
            "publisher_basis": "document_metadata" if fetched.publisher else "canonical_host",
            "published_at": (
                fetched.published_at.isoformat() if fetched.published_at is not None else None
            ),
            "published_at_unknown": fetched.published_at is None,
            "retrieved_at": fetched.retrieved_at.isoformat(),
            "source_available_at": source_available,
            "temporal_basis": document.get("temporal_basis"),
            "content_sha256": fetched.raw_content_hash,
            "extracted_text_sha256": document.get("extracted_text_sha256"),
            "source_role": "supporting_pre_outcome_evidence",
            "evidence_note": (
                "Exact recorded Wayback final capture re-fetched and content-addressed "
                "hashes re-verified against the frozen H017 document record."
            ),
        },
    }


def candidate_terminal_reasons(
    *,
    origin_attempts: list[dict[str, Any]],
    restored_sources: list[dict[str, Any]],
) -> list[str]:
    """Return only facts that prevent a valid V2 review manifest.

    Market-creation timestamps are retained as provisional origin proof, but
    remain insufficient for V2 until a verified snapshot or registered immutable
    version proves the pre-outcome contract surface.
    """

    reasons: list[str] = []
    if not any(item.get("status") == "complete" for item in restored_sources):
        reasons.append("no_reverified_historical_evidence_source")
    if not any(item.get("status") == "accepted" for item in origin_attempts):
        reasons.append("pre_outcome_origin_not_independently_verified")
    reasons.append("outcome_source_availability_not_independently_verified")
    return reasons
