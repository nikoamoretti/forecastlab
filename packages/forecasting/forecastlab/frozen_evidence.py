from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from forecastlab.hashing import sha256_bytes
from forecastlab.historical_evidence_releases import (
    BlindedHistoricalEvidenceDocument,
    BlindedHistoricalEvidenceExecutionManifest,
    HistoricalEvidenceBundleManifest,
    historical_evidence_manifest_hash,
    verify_historical_evidence_bundle,
)
from forecastlab.schemas import FetchedDocument, SearchHit
from forecastlab.timeutil import as_utc, utcnow
from forecastlab.wayback import WaybackSnapshot, canonicalize_url

_TOKEN_PATTERN = re.compile(r"[a-z0-9]{2,}")


class FrozenEvidenceIntegrityError(RuntimeError):
    pass


def _tokens(value: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(value.casefold()))


class FrozenEvidenceDocumentStore:
    """Verified, question-scoped document access with no network fallback."""

    offline_frozen_evidence = True

    def __init__(
        self,
        *,
        manifest: HistoricalEvidenceBundleManifest,
        execution_manifest: BlindedHistoricalEvidenceExecutionManifest,
        bundle_root: Path,
        expected_release_hash: str,
        split: str,
        evaluation_question_id: str,
        evidence_cutoff: datetime,
    ) -> None:
        verification = verify_historical_evidence_bundle(
            manifest,
            bundle_root,
            expected_release_hash=expected_release_hash,
        )
        if not verification.passed:
            raise FrozenEvidenceIntegrityError(
                "frozen_evidence_bundle_invalid:" + ",".join(verification.reasons)
            )
        if (
            historical_evidence_manifest_hash(execution_manifest)
            != manifest.execution_manifest_hash
            or execution_manifest.evaluation_release_id
            != manifest.release.evaluation_release_id
            or execution_manifest.evaluation_execution_manifest_hash
            != manifest.release.evaluation_execution_manifest_hash
        ):
            raise FrozenEvidenceIntegrityError(
                "frozen_evidence_execution_manifest_identity_mismatch"
            )
        packet = next(
            (
                item
                for item in execution_manifest.packets
                if item.evaluation_question_id == evaluation_question_id
                and item.split == split
            ),
            None,
        )
        if packet is None:
            raise FrozenEvidenceIntegrityError("frozen_evidence_packet_not_found")
        if as_utc(packet.evidence_cutoff) != as_utc(evidence_cutoff):
            raise FrozenEvidenceIntegrityError("frozen_evidence_cutoff_mismatch")
        manifest_documents = {item.document_id: item for item in manifest.documents}
        self.release_hash = expected_release_hash
        self.split = split
        self.evaluation_question_id = evaluation_question_id
        self.evidence_cutoff = as_utc(evidence_cutoff)
        self.packet_status = packet.status
        self._root = bundle_root.resolve()
        self._documents: dict[str, BlindedHistoricalEvidenceDocument] = {}
        for document in packet.documents:
            if manifest_documents.get(document.document_id) != document:
                raise FrozenEvidenceIntegrityError(
                    f"frozen_evidence_document_identity_mismatch:{document.document_id}"
                )
            key = canonicalize_url(document.canonical_url)
            if key in self._documents:
                raise FrozenEvidenceIntegrityError(
                    f"duplicate_frozen_evidence_url:{document.canonical_url}"
                )
            self._documents[key] = document

    @property
    def documents(self) -> tuple[BlindedHistoricalEvidenceDocument, ...]:
        return tuple(self._documents[key] for key in sorted(self._documents))

    def snapshots_for(self, url: str, *, as_of: datetime) -> list[WaybackSnapshot]:
        document = self._documents.get(canonicalize_url(url))
        if document is None or as_utc(document.source_available_at) > as_utc(as_of):
            return []
        snapshot_url = document.final_capture_url or document.source_url
        return [
            WaybackSnapshot(
                url=document.canonical_url,
                snapshot_url=snapshot_url,
                timestamp=as_utc(document.source_available_at),
                status="200",
                discovery="frozen_historical_evidence_release",
            )
        ]

    def fetch(self, url: str, **_kwargs) -> FetchedDocument:
        document = self._documents.get(canonicalize_url(url))
        if document is None:
            raise FrozenEvidenceIntegrityError("cross_question_or_unlinked_document_access")
        blob = (self._root / document.blob_locator).read_bytes()
        text_bytes = (self._root / document.text_locator).read_bytes()
        if sha256_bytes(blob) != document.content_sha256:
            raise FrozenEvidenceIntegrityError("frozen_evidence_blob_hash_mismatch")
        if sha256_bytes(text_bytes) != document.extracted_text_sha256:
            raise FrozenEvidenceIntegrityError("frozen_evidence_text_hash_mismatch")
        text = text_bytes.decode("utf-8")
        if len(blob) != document.byte_length or len(text) != document.text_length:
            raise FrozenEvidenceIntegrityError("frozen_evidence_length_mismatch")
        if as_utc(document.source_available_at) > self.evidence_cutoff:
            raise FrozenEvidenceIntegrityError("frozen_evidence_after_cutoff")
        is_snapshot = document.source_kind == "wayback_final_capture"
        return FetchedDocument(
            url=document.canonical_url,
            title=document.title,
            publisher=document.publisher,
            published_at=document.published_at,
            retrieved_at=utcnow(),
            source_available_at=document.source_available_at,
            temporal_basis=("snapshot_date" if is_snapshot else "immutable_version"),
            publication_date_source=(
                "frozen_immutable_version" if not is_snapshot and document.published_at else None
            ),
            publication_date_verified=bool(document.published_at and not is_snapshot),
            text=text,
            content_hash=document.extracted_text_sha256,
            snapshot_url=document.final_capture_url if is_snapshot else None,
            snapshot_at=document.final_capture_at if is_snapshot else None,
            requested_snapshot_url=document.final_capture_url if is_snapshot else None,
            requested_snapshot_at=document.final_capture_at if is_snapshot else None,
            final_snapshot_url=document.final_capture_url if is_snapshot else None,
            final_snapshot_at=document.final_capture_at if is_snapshot else None,
            archived_original_url=document.archived_original_url if is_snapshot else None,
            snapshot_verification_status=(
                "verified_frozen_final_capture"
                if is_snapshot
                else "immutable_historical_timestamp_verified"
            ),
            status_code=200,
            rejected=False,
            rejection_reason=None,
            as_of_eligible=True,
            published_at_unknown=document.published_at is None,
        )


class FrozenEvidenceSearchProvider:
    """Question-scoped deterministic ranking over a verified frozen packet."""

    name = "frozen_evidence"
    offline_frozen_evidence = True

    def __init__(self, store: FrozenEvidenceDocumentStore) -> None:
        self.store = store

    def discover_frozen_snapshots(
        self,
        url: str,
        *,
        as_of: datetime,
    ) -> list[WaybackSnapshot]:
        return self.store.snapshots_for(url, as_of=as_of)

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        if self.store.packet_status == "no_eligible_evidence":
            return []
        query_tokens = _tokens(query)
        ranked: list[tuple[int, int, str, BlindedHistoricalEvidenceDocument]] = []
        for document in self.store.documents:
            haystack = _tokens(
                " ".join(
                    (
                        document.title,
                        document.publisher,
                        document.canonical_url,
                    )
                )
            )
            overlap = len(query_tokens & haystack)
            source_priority = 1 if document.source_class == "primary" else 0
            ranked.append(
                (-overlap, -source_priority, document.canonical_url, document)
            )
        return [
            SearchHit(
                title=document.title,
                url=document.canonical_url,
                snippet="Frozen cutoff-safe document; content is available only through the verified store.",
                published_at=document.published_at,
                published_at_source=(
                    "frozen_historical_evidence_manifest"
                    if document.published_at
                    else None
                ),
                score=float(max(0, -rank_key[0])),
                source_class=document.source_class,
            )
            for rank_key in sorted(ranked)[: max(0, max_results)]
            for document in (rank_key[3],)
        ]

    @property
    def bound_hostnames(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    (urlsplit(item.canonical_url).hostname or "").casefold()
                    for item in self.store.documents
                }
            )
        )
