from __future__ import annotations

from datetime import UTC, datetime

from forecastlab.review_provenance import (
    candidate_terminal_reasons,
    restored_snapshot_source,
)
from forecastlab.schemas import FetchedDocument

NOW = datetime(2026, 8, 31, tzinfo=UTC)
HASH = "a" * 64
TEXT_HASH = "b" * 64


def _fetched(*, raw_hash: str = HASH, final_url: str = "https://web.archive.org/web/20260601000000/https://example.com/a") -> FetchedDocument:
    return FetchedDocument(
        url="https://example.com/a",
        final_url=final_url,
        title="Official release calendar",
        publisher="Example institution",
        published_at=None,
        retrieved_at=NOW,
        source_available_at=datetime(2026, 6, 1, tzinfo=UTC),
        temporal_basis="snapshot_date",
        text="verified archival text",
        content_hash=TEXT_HASH,
        raw_content_hash=raw_hash,
        extracted_text_hash="different-truncated-extraction-hash",
        snapshot_url=final_url,
        snapshot_at=datetime(2026, 6, 1, tzinfo=UTC),
        final_snapshot_url=final_url,
        final_snapshot_at=datetime(2026, 6, 1, tzinfo=UTC),
        archived_original_url="https://example.com/a",
    )


def _document() -> dict[str, object]:
    return {
        "document_id": "doc-1",
        "canonical_url": "https://example.com/a",
        "temporal_basis": "snapshot_date",
        "source_available_at": "2026-06-01T00:00:00+00:00",
        "cutoff_verified": True,
        "archive_original_verified": True,
        "content_sha256": HASH,
        "extracted_text_sha256": TEXT_HASH,
    }


def _attempt() -> dict[str, object]:
    return {
        "status": "accepted",
        "cutoff": "2026-06-05T00:00:00+00:00",
        "final_snapshot_url": "https://web.archive.org/web/20260601000000/https://example.com/a",
    }


def test_restored_snapshot_retains_frozen_extracted_hash_not_truncated_fetch_projection() -> None:
    receipt = restored_snapshot_source(
        candidate_id="candidate-1",
        document=_document(),
        attempt=_attempt(),
        fetched=_fetched(),
        stored_blob_hash_verified=True,
        stored_text_hash_verified=True,
    )
    assert receipt["status"] == "complete"
    assert receipt["source"]["extracted_text_sha256"] == TEXT_HASH
    assert receipt["source"]["temporal_basis"] == "snapshot_date"
    assert receipt["source"]["retrieved_at"] == NOW.isoformat()


def test_hash_drift_and_missing_origin_proof_remain_terminal_failures() -> None:
    receipt = restored_snapshot_source(
        candidate_id="candidate-1",
        document=_document(),
        attempt=_attempt(),
        fetched=_fetched(raw_hash="c" * 64),
        stored_blob_hash_verified=False,
        stored_text_hash_verified=False,
    )
    assert receipt["status"] == "failed"
    assert "historical_content_hash_drift" in receipt["reasons"]
    assert "frozen_blob_hash_drift" in receipt["reasons"]
    reasons = candidate_terminal_reasons(origin_attempts=[], restored_sources=[receipt])
    assert "no_reverified_historical_evidence_source" in reasons
    assert "pre_outcome_origin_not_independently_verified" in reasons
    assert "outcome_source_availability_not_independently_verified" in reasons


def test_rejected_or_post_cutoff_capture_never_becomes_review_source() -> None:
    rejected = _fetched()
    rejected.rejected = True
    rejected.rejection_reason = "historical_fetch_failed:TimeoutError"
    document = _document()
    document["source_available_at"] = "2026-06-07T00:00:00+00:00"
    receipt = restored_snapshot_source(
        candidate_id="candidate-1",
        document=document,
        attempt=_attempt(),
        fetched=rejected,
        stored_blob_hash_verified=True,
        stored_text_hash_verified=True,
    )
    assert receipt["status"] == "failed"
    assert "historical_refetch_rejected:historical_fetch_failed:TimeoutError" in receipt[
        "reasons"
    ]
    assert "historical_snapshot_after_cutoff" in receipt["reasons"]
