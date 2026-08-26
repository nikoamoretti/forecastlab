from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.frozen_evidence import (
    FrozenEvidenceDocumentStore,
    FrozenEvidenceSearchProvider,
)
from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.historical_evidence_releases import (
    PRIVATE_V1_HISTORICAL_EVIDENCE_RELEASE_V1,
    BlindedHistoricalEvidenceDocument,
    BlindedHistoricalEvidenceExecutionManifest,
    BlindedHistoricalEvidencePacket,
    HistoricalEvidenceAuditManifest,
    HistoricalEvidenceBundleManifest,
    HistoricalEvidenceBundleVerification,
    HistoricalEvidenceCandidateAudit,
    HistoricalEvidenceDocumentInput,
    HistoricalEvidencePacketAudit,
    HistoricalEvidencePacketInput,
    HistoricalEvidenceReleaseIdentity,
    HistoricalEvidenceReleasePolicy,
    historical_evidence_manifest_hash,
    verify_historical_evidence_bundle,
)
from forecastlab.timeutil import as_utc, utcnow
from forecastlab.wayback import WAYBACK_HOSTS, canonicalize_url, parse_wayback_url
from forecastlab_api.evaluation_releases import get_blinded_execution_manifest
from forecastlab_api.models import (
    EvaluationRelease,
    HistoricalEvidenceCandidate,
    HistoricalEvidenceDocument,
    HistoricalEvidencePacket,
    HistoricalEvidencePacketDocument,
    HistoricalEvidenceRelease,
)

RELEASE_STATUSES = ("draft", "reviewed", "frozen")
REGISTERED_IMMUTABLE_ADAPTERS = frozenset(
    {
        "bls_vintage_file_v1",
        "official_versioned_dataset_v1",
        "sec_edgar_filing_v1",
    }
)
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FORBIDDEN_EXECUTION_KEYS = {
    "outcome",
    "resolution_source",
    "post_resolution_source",
    "outcome_known_at",
    "adjudication",
    "adjudication_notes",
    "scoring_manifest",
    "scoring_manifest_hash",
    "brier_score",
    "log_loss",
}


class HistoricalEvidenceReleaseValidationError(ValueError):
    def __init__(
        self,
        reasons: list[str],
        message: str = "Historical evidence release is invalid",
    ) -> None:
        self.reasons = list(dict.fromkeys(reasons))
        super().__init__(message)


def _normalized(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def _forbidden_paths(value: Any, *, prefix: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}.{key}"
            if key.casefold() in _FORBIDDEN_EXECUTION_KEYS:
                paths.append(path)
            paths.extend(_forbidden_paths(nested, prefix=path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            paths.extend(_forbidden_paths(nested, prefix=f"{prefix}[{index}]"))
    return paths


def _document_payload(item: HistoricalEvidenceDocumentInput) -> dict[str, Any]:
    payload = item.model_dump(mode="json")
    payload.pop("local_id", None)
    payload["canonical_url"] = canonicalize_url(item.canonical_url)
    payload["source_use_basis"] = _normalized(item.source_use_basis)
    return payload


def _document_db_payload(item: HistoricalEvidenceDocumentInput) -> dict[str, Any]:
    payload = item.model_dump(mode="python")
    payload.pop("local_id", None)
    payload["canonical_url"] = canonicalize_url(item.canonical_url)
    payload["source_use_basis"] = _normalized(item.source_use_basis)
    return payload


def _document_hash(item: HistoricalEvidenceDocumentInput) -> str:
    return sha256_text(canonical_json(_document_payload(item)))


def _candidate_hash(payload: dict[str, Any]) -> str:
    return sha256_text(canonical_json(payload))


def _document_dto(item: HistoricalEvidenceDocument) -> BlindedHistoricalEvidenceDocument:
    return BlindedHistoricalEvidenceDocument(
        document_id=item.id,
        canonical_url=item.canonical_url,
        source_url=item.source_url,
        title=item.title,
        publisher=item.publisher,
        source_class=item.source_class,  # type: ignore[arg-type]
        source_kind=item.source_kind,  # type: ignore[arg-type]
        temporal_basis=item.temporal_basis,  # type: ignore[arg-type]
        published_at=as_utc(item.published_at) if item.published_at else None,
        source_available_at=as_utc(item.source_available_at),
        final_capture_url=item.final_capture_url,
        final_capture_at=as_utc(item.final_capture_at) if item.final_capture_at else None,
        archived_original_url=item.archived_original_url,
        immutable_adapter_id=item.immutable_adapter_id,
        immutable_version_id=item.immutable_version_id,
        mime_type=item.mime_type,
        content_sha256=item.content_sha256,
        extracted_text_sha256=item.extracted_text_sha256,
        byte_length=item.byte_length,
        text_length=item.text_length,
        blob_locator=item.blob_locator,
        text_locator=item.text_locator,
    )


def _release_identity(
    release: HistoricalEvidenceRelease,
) -> HistoricalEvidenceReleaseIdentity:
    return HistoricalEvidenceReleaseIdentity(
        id=release.id,
        name=release.name,
        version=release.version,
        policy_version=release.policy_version,
        evaluation_release_id=release.evaluation_release_id,
        evaluation_execution_manifest_hash=release.evaluation_execution_manifest_hash,
        release_hash=release.release_hash,
    )


def _build_execution_manifest(
    release: HistoricalEvidenceRelease,
) -> BlindedHistoricalEvidenceExecutionManifest:
    packets: list[BlindedHistoricalEvidencePacket] = []
    for packet in sorted(
        release.packets,
        key=lambda item: (item.split, item.evaluation_question_id),
    ):
        documents = sorted(
            [_document_dto(link.document) for link in packet.document_links],
            key=lambda item: item.document_id,
        )
        packets.append(
            BlindedHistoricalEvidencePacket(
                packet_id=packet.id,
                evaluation_question_id=packet.evaluation_question_id,
                split=packet.split,  # type: ignore[arg-type]
                evidence_cutoff=as_utc(packet.evidence_cutoff),
                status=packet.status,  # type: ignore[arg-type]
                documents=documents,
            )
        )
    return BlindedHistoricalEvidenceExecutionManifest(
        policy_version=release.policy_version,
        evaluation_release_id=release.evaluation_release_id,
        evaluation_execution_manifest_hash=release.evaluation_execution_manifest_hash,
        packets=packets,
    )


def _build_audit_manifest(
    release: HistoricalEvidenceRelease,
) -> HistoricalEvidenceAuditManifest:
    packets: list[HistoricalEvidencePacketAudit] = []
    for packet in sorted(
        release.packets,
        key=lambda item: (item.split, item.evaluation_question_id),
    ):
        candidates = [
            HistoricalEvidenceCandidateAudit(
                candidate_id=item.id,
                canonical_url=item.canonical_url,
                source_url=item.source_url,
                rank=item.rank,
                search_query=item.search_query,
                search_provider=item.search_provider,
                status=item.status,  # type: ignore[arg-type]
                rejection_reason=item.rejection_reason,
                archive_check_status=item.archive_check_status,
                document_id=item.document_id,
            )
            for item in sorted(packet.candidates, key=lambda candidate: candidate.rank)
        ]
        packets.append(
            HistoricalEvidencePacketAudit(
                packet_id=packet.id,
                evaluation_question_id=packet.evaluation_question_id,
                split=packet.split,  # type: ignore[arg-type]
                evidence_cutoff=as_utc(packet.evidence_cutoff),
                status=packet.status,  # type: ignore[arg-type]
                collector_id=packet.collector_id,
                reviewer_id=packet.reviewer_id,
                reviewed_at=as_utc(packet.reviewed_at),
                searches=json.loads(packet.searches_json),
                archive_checks=json.loads(packet.archive_checks_json),
                rejection_reasons=json.loads(packet.rejection_reasons_json),
                candidates=candidates,
                document_ids=sorted(link.document_id for link in packet.document_links),
            )
        )
    documents = [
        {
            **_document_dto(item).model_dump(mode="json"),
            "source_license_status": item.source_license_status,
            "source_use_basis": item.source_use_basis,
            "redistribution_allowed": item.redistribution_allowed,
            "final_capture_verified": item.final_capture_verified,
            "immutable_availability_verified": item.immutable_availability_verified,
            "metadata": json.loads(item.metadata_json),
            "document_hash": item.document_hash,
        }
        for item in sorted(release.documents, key=lambda document: document.id)
    ]
    return HistoricalEvidenceAuditManifest(
        policy_version=release.policy_version,
        evaluation_release_id=release.evaluation_release_id,
        evaluation_execution_manifest_hash=release.evaluation_execution_manifest_hash,
        creation_request_hash=release.creation_request_hash,
        packets=packets,
        documents=documents,
    )


def _release_hash(
    *,
    release: HistoricalEvidenceRelease,
    execution_hash: str,
    audit_hash: str,
    policy: HistoricalEvidenceReleasePolicy,
) -> str:
    payload = {
        "schema_version": 1,
        "identity": {
            "id": release.id,
            "name": release.name,
            "version": release.version,
            "policy_version": release.policy_version,
            "evaluation_release_id": release.evaluation_release_id,
            "evaluation_execution_manifest_hash": (
                release.evaluation_execution_manifest_hash
            ),
            "correction_of_release_id": release.correction_of_release_id,
            "correction_summary": release.correction_summary,
        },
        "policy": policy.model_dump(mode="json"),
        "packet_hashes": sorted(item.packet_hash for item in release.packets),
        "document_hashes": sorted(item.document_hash for item in release.documents),
        "execution_manifest_hash": execution_hash,
        "audit_manifest_hash": audit_hash,
    }
    return sha256_text(canonical_json(payload))


def _refresh_artifacts(
    release: HistoricalEvidenceRelease,
    *,
    policy: HistoricalEvidenceReleasePolicy = (
        PRIVATE_V1_HISTORICAL_EVIDENCE_RELEASE_V1
    ),
) -> dict[str, str]:
    execution = _build_execution_manifest(release)
    audit = _build_audit_manifest(release)
    execution_hash = historical_evidence_manifest_hash(execution)
    audit_hash = historical_evidence_manifest_hash(audit)
    release_hash = _release_hash(
        release=release,
        execution_hash=execution_hash,
        audit_hash=audit_hash,
        policy=policy,
    )
    identity = HistoricalEvidenceReleaseIdentity(
        id=release.id,
        name=release.name,
        version=release.version,
        policy_version=release.policy_version,
        evaluation_release_id=release.evaluation_release_id,
        evaluation_execution_manifest_hash=release.evaluation_execution_manifest_hash,
        release_hash=release_hash,
    )
    bundle = HistoricalEvidenceBundleManifest(
        release=identity,
        execution_manifest_hash=execution_hash,
        documents=sorted(
            [_document_dto(item) for item in release.documents],
            key=lambda item: item.document_id,
        ),
    )
    return {
        "execution_manifest_json": canonical_json(execution.model_dump(mode="json")),
        "execution_manifest_hash": execution_hash,
        "audit_manifest_json": canonical_json(audit.model_dump(mode="json")),
        "audit_manifest_hash": audit_hash,
        "bundle_manifest_json": canonical_json(bundle.model_dump(mode="json")),
        "bundle_manifest_hash": historical_evidence_manifest_hash(bundle),
        "release_hash": release_hash,
    }


def _assert_correction(
    session: Session,
    *,
    evaluation_release_id: str,
    name: str,
    version: str,
    correction_of_release_id: str | None,
    correction_summary: str | None,
) -> None:
    reasons: list[str] = []
    if correction_of_release_id is None:
        if _normalized(correction_summary):
            reasons.append("correction_release_id_required")
    else:
        previous = session.get(HistoricalEvidenceRelease, correction_of_release_id)
        if previous is None:
            reasons.append("correction_release_not_found")
        else:
            if previous.status != "frozen":
                reasons.append("correction_source_release_must_be_frozen")
            if previous.evaluation_release_id != evaluation_release_id:
                reasons.append("correction_evaluation_release_mismatch")
            if previous.name != name:
                reasons.append("correction_release_name_mismatch")
            if previous.version == version:
                reasons.append("correction_requires_new_version")
        if not _normalized(correction_summary):
            reasons.append("correction_summary_required")
    if reasons:
        raise HistoricalEvidenceReleaseValidationError(reasons)


def create_historical_evidence_release(
    session: Session,
    *,
    evaluation_release_id: str,
    name: str,
    version: str,
    documents: list[HistoricalEvidenceDocumentInput],
    packets: list[HistoricalEvidencePacketInput],
    correction_of_release_id: str | None = None,
    correction_summary: str | None = None,
    now: datetime | None = None,
    policy: HistoricalEvidenceReleasePolicy = (
        PRIVATE_V1_HISTORICAL_EVIDENCE_RELEASE_V1
    ),
) -> HistoricalEvidenceRelease:
    normalized_name = _normalized(name)
    normalized_version = version.strip()
    reasons: list[str] = []
    evaluation_release = session.get(EvaluationRelease, evaluation_release_id)
    if evaluation_release is None:
        reasons.append("evaluation_release_not_found")
    elif evaluation_release.status != "frozen":
        reasons.append("evaluation_release_must_be_frozen")
    if not normalized_name:
        reasons.append("historical_evidence_release_name_required")
    if not _VERSION_PATTERN.fullmatch(normalized_version):
        reasons.append("historical_evidence_release_version_invalid")
    local_ids = [item.local_id for item in documents]
    if len(local_ids) != len(set(local_ids)):
        reasons.append("duplicate_document_local_id")
    known_local_ids = set(local_ids)
    packet_question_ids = [item.evaluation_question_id for item in packets]
    if len(packet_question_ids) != len(set(packet_question_ids)):
        reasons.append("duplicate_historical_evidence_packet_question")
    for packet in packets:
        unknown_document_ids = sorted(
            set(packet.document_local_ids) - known_local_ids
        )
        reasons.extend(
            f"packet_document_not_found:{packet.evaluation_question_id}:{local_id}"
            for local_id in unknown_document_ids
        )
        candidate_ranks = [item.rank for item in packet.candidates]
        if len(candidate_ranks) != len(set(candidate_ranks)):
            reasons.append(
                f"duplicate_historical_evidence_candidate_rank:{packet.evaluation_question_id}"
            )
        for candidate in packet.candidates:
            if (
                candidate.document_local_id is not None
                and candidate.document_local_id not in known_local_ids
            ):
                reasons.append(
                    "candidate_document_not_found:"
                    f"{packet.evaluation_question_id}:{candidate.document_local_id}"
                )
    if evaluation_release is not None:
        release_question_ids = {
            item.evaluation_question_id for item in evaluation_release.questions
        }
        reasons.extend(
            f"evaluation_release_question_not_found:{question_id}"
            for question_id in sorted(set(packet_question_ids) - release_question_ids)
        )
    if reasons:
        raise HistoricalEvidenceReleaseValidationError(reasons)
    assert evaluation_release is not None
    _assert_correction(
        session,
        evaluation_release_id=evaluation_release_id,
        name=normalized_name,
        version=normalized_version,
        correction_of_release_id=correction_of_release_id,
        correction_summary=correction_summary,
    )
    existing = session.scalar(
        select(HistoricalEvidenceRelease).where(
            HistoricalEvidenceRelease.name == normalized_name,
            HistoricalEvidenceRelease.version == normalized_version,
        )
    )
    request_hash = sha256_text(
        canonical_json(
            {
                "evaluation_release_id": evaluation_release_id,
                "evaluation_execution_manifest_hash": (
                    evaluation_release.execution_manifest_hash
                ),
                "documents": sorted(
                    [_document_payload(item) for item in documents],
                    key=lambda item: canonical_json(item),
                ),
                "packets": sorted(
                    [item.model_dump(mode="json") for item in packets],
                    key=lambda item: (
                        item["split"],
                        item["evaluation_question_id"],
                    ),
                ),
                "correction_of_release_id": correction_of_release_id,
                "correction_summary": _normalized(correction_summary) or None,
            }
        )
    )
    if existing is not None:
        if existing.creation_request_hash == request_hash:
            return existing
        raise HistoricalEvidenceReleaseValidationError(
            ["historical_evidence_release_version_conflict"]
        )

    timestamp = as_utc(now or utcnow())
    release = HistoricalEvidenceRelease(
        id=str(uuid.uuid4()),
        evaluation_release_id=evaluation_release_id,
        evaluation_execution_manifest_hash=evaluation_release.execution_manifest_hash,
        name=normalized_name,
        version=normalized_version,
        policy_version=policy.version,
        status="draft",
        created_at=timestamp,
        reviewed_at=None,
        frozen_at=None,
        correction_of_release_id=correction_of_release_id,
        correction_summary=_normalized(correction_summary) or None,
        creation_request_hash=request_hash,
        execution_manifest_json="{}",
        execution_manifest_hash="",
        audit_manifest_json="{}",
        audit_manifest_hash="",
        bundle_manifest_json="{}",
        bundle_manifest_hash="",
        release_hash=str(uuid.uuid4()),
    )
    session.add(release)
    session.flush()

    document_ids: dict[str, str] = {}
    for item in documents:
        payload = _document_db_payload(item)
        metadata = payload.pop("metadata", {})
        document = HistoricalEvidenceDocument(
            id=str(uuid.uuid4()),
            historical_evidence_release_id=release.id,
            **payload,
            metadata_json=canonical_json(metadata),
            document_hash=_document_hash(item),
        )
        session.add(document)
        document_ids[item.local_id] = document.id
    session.flush()

    release_questions = {
        item.evaluation_question_id: item
        for item in evaluation_release.questions
    }
    for item in packets:
        release_question = release_questions.get(item.evaluation_question_id)
        if release_question is None:
            raise HistoricalEvidenceReleaseValidationError(
                [f"evaluation_release_question_not_found:{item.evaluation_question_id}"]
            )
        packet_payload = {
            "evaluation_question_id": item.evaluation_question_id,
            "split": item.split,
            "evidence_cutoff": as_utc(item.evidence_cutoff).isoformat(),
            "status": item.status,
            "collector_id": item.collector_id,
            "reviewer_id": item.reviewer_id,
            "reviewed_at": as_utc(item.reviewed_at).isoformat(),
            "searches": item.searches,
            "archive_checks": item.archive_checks,
            "rejection_reasons": item.rejection_reasons,
            "document_local_ids": sorted(item.document_local_ids),
            "candidates": [candidate.model_dump(mode="json") for candidate in item.candidates],
        }
        packet = HistoricalEvidencePacket(
            id=str(uuid.uuid4()),
            historical_evidence_release_id=release.id,
            evaluation_release_question_id=release_question.id,
            evaluation_question_id=item.evaluation_question_id,
            split=item.split,
            evidence_cutoff=as_utc(item.evidence_cutoff),
            status=item.status,
            collector_id=item.collector_id,
            reviewer_id=item.reviewer_id,
            reviewed_at=as_utc(item.reviewed_at),
            searches_json=canonical_json(item.searches),
            archive_checks_json=canonical_json(item.archive_checks),
            rejection_reasons_json=canonical_json(item.rejection_reasons),
            packet_hash=sha256_text(canonical_json(packet_payload)),
        )
        session.add(packet)
        session.flush()
        for local_id in item.document_local_ids:
            document_id = document_ids.get(local_id)
            if document_id is None:
                raise HistoricalEvidenceReleaseValidationError(
                    [f"packet_document_not_found:{item.evaluation_question_id}:{local_id}"]
                )
            session.add(
                HistoricalEvidencePacketDocument(
                    id=str(uuid.uuid4()),
                    packet_id=packet.id,
                    document_id=document_id,
                )
            )
        for candidate in item.candidates:
            document_id = (
                document_ids.get(candidate.document_local_id)
                if candidate.document_local_id
                else None
            )
            candidate_payload = candidate.model_dump(mode="json")
            session.add(
                HistoricalEvidenceCandidate(
                    id=str(uuid.uuid4()),
                    packet_id=packet.id,
                    document_id=document_id,
                    canonical_url=canonicalize_url(candidate.canonical_url),
                    source_url=candidate.source_url,
                    rank=candidate.rank,
                    search_query=candidate.search_query,
                    search_provider=candidate.search_provider,
                    status=candidate.status,
                    rejection_reason=_normalized(candidate.rejection_reason) or None,
                    archive_check_status=candidate.archive_check_status,
                    candidate_hash=_candidate_hash(candidate_payload),
                )
            )
    session.flush()
    artifacts = _refresh_artifacts(release, policy=policy)
    for field, value in artifacts.items():
        setattr(release, field, value)
    session.flush()
    return release


def get_historical_evidence_execution_manifest(
    release: HistoricalEvidenceRelease,
    *,
    require_frozen: bool = True,
) -> BlindedHistoricalEvidenceExecutionManifest:
    if require_frozen and release.status != "frozen":
        raise HistoricalEvidenceReleaseValidationError(
            ["historical_evidence_release_must_be_frozen"]
        )
    try:
        manifest = BlindedHistoricalEvidenceExecutionManifest.model_validate_json(
            release.execution_manifest_json
        )
    except ValidationError as exc:
        raise HistoricalEvidenceReleaseValidationError(
            ["historical_evidence_execution_manifest_invalid"]
        ) from exc
    if historical_evidence_manifest_hash(manifest) != release.execution_manifest_hash:
        raise HistoricalEvidenceReleaseValidationError(
            ["historical_evidence_execution_manifest_hash_mismatch"]
        )
    forbidden = _forbidden_paths(manifest.model_dump(mode="json"))
    if forbidden:
        raise HistoricalEvidenceReleaseValidationError(
            [f"historical_evidence_execution_manifest_forbidden_field:{path}" for path in forbidden]
        )
    return manifest


def get_historical_evidence_bundle_manifest(
    release: HistoricalEvidenceRelease,
) -> HistoricalEvidenceBundleManifest:
    try:
        manifest = HistoricalEvidenceBundleManifest.model_validate_json(
            release.bundle_manifest_json
        )
    except ValidationError as exc:
        raise HistoricalEvidenceReleaseValidationError(
            ["historical_evidence_bundle_manifest_invalid"]
        ) from exc
    if historical_evidence_manifest_hash(manifest) != release.bundle_manifest_hash:
        raise HistoricalEvidenceReleaseValidationError(
            ["historical_evidence_bundle_manifest_hash_mismatch"]
        )
    if manifest.release.release_hash != release.release_hash:
        raise HistoricalEvidenceReleaseValidationError(
            ["historical_evidence_bundle_release_hash_mismatch"]
        )
    return manifest


def validate_historical_evidence_release(
    session: Session,
    release: HistoricalEvidenceRelease,
    *,
    bundle_root: Path | None = None,
    policy: HistoricalEvidenceReleasePolicy = (
        PRIVATE_V1_HISTORICAL_EVIDENCE_RELEASE_V1
    ),
) -> list[str]:
    reasons: list[str] = []
    evaluation_release = session.get(EvaluationRelease, release.evaluation_release_id)
    if evaluation_release is None:
        return ["evaluation_release_not_found"]
    if evaluation_release.status != "frozen":
        reasons.append("evaluation_release_must_be_frozen")
    if release.policy_version != policy.version:
        reasons.append("historical_evidence_policy_version_mismatch")
    if release.evaluation_execution_manifest_hash != evaluation_release.execution_manifest_hash:
        reasons.append("evaluation_execution_manifest_hash_mismatch")

    evaluation_manifest = get_blinded_execution_manifest(evaluation_release)
    included = {
        item.evaluation_question_id: item for item in evaluation_manifest.questions
    }
    excluded = {
        item.evaluation_question_id
        for item in evaluation_release.questions
        if item.inclusion_status == "excluded"
    }
    packet_ids = [item.evaluation_question_id for item in release.packets]
    counts = Counter(packet_ids)
    for question_id in sorted(included):
        if counts[question_id] != 1:
            reasons.append(f"historical_evidence_packet_count:{question_id}:{counts[question_id]}")
    for question_id in sorted(set(packet_ids) - set(included)):
        reasons.append(
            f"extra_or_excluded_historical_evidence_packet:{question_id}"
        )
    if excluded & set(packet_ids):
        reasons.append("excluded_question_has_executable_evidence_packet")

    linked_document_ids: set[str] = set()
    release_documents_by_id = {item.id: item for item in release.documents}
    for packet in release.packets:
        expected = included.get(packet.evaluation_question_id)
        if expected is None:
            continue
        if packet.split != expected.split:
            reasons.append(f"historical_evidence_packet_split_mismatch:{packet.id}")
        if as_utc(packet.evidence_cutoff) != as_utc(expected.evidence_cutoff):
            reasons.append(f"historical_evidence_packet_cutoff_mismatch:{packet.id}")
        if packet.collector_id == packet.reviewer_id:
            reasons.append(f"historical_evidence_reviewer_conflict:{packet.id}")
        if packet.reviewed_at is None:
            reasons.append(f"historical_evidence_review_required:{packet.id}")
        document_ids = [link.document_id for link in packet.document_links]
        linked_document_ids.update(document_ids)
        outside_release_document_ids = sorted(
            set(document_ids) - set(release_documents_by_id)
        )
        reasons.extend(
            f"packet_document_outside_historical_evidence_release:{packet.id}:{document_id}"
            for document_id in outside_release_document_ids
        )
        if packet.status == "ready" and not document_ids:
            reasons.append(f"ready_packet_requires_document:{packet.id}")
        accepted_document_ids = {
            candidate.document_id
            for candidate in packet.candidates
            if candidate.status == "accepted" and candidate.document_id is not None
        }
        accepted_candidate_count = sum(
            candidate.status == "accepted" for candidate in packet.candidates
        )
        if packet.status == "ready" and accepted_candidate_count == 0:
            reasons.append(f"ready_packet_accepted_candidate_required:{packet.id}")
        for document_id in sorted(set(document_ids) - accepted_document_ids):
            reasons.append(
                f"packet_document_accepted_candidate_required:{packet.id}:{document_id}"
            )
        if packet.status == "no_eligible_evidence":
            if document_ids:
                reasons.append(f"no_evidence_packet_contains_document:{packet.id}")
            if not json.loads(packet.searches_json):
                reasons.append(f"no_evidence_packet_search_audit_required:{packet.id}")
            if not json.loads(packet.archive_checks_json):
                reasons.append(f"no_evidence_packet_archive_audit_required:{packet.id}")
            if not json.loads(packet.rejection_reasons_json):
                reasons.append(f"no_evidence_packet_rejection_required:{packet.id}")
            if not packet.candidates:
                reasons.append(f"no_evidence_packet_candidates_required:{packet.id}")
        for candidate in packet.candidates:
            if candidate.status == "accepted" and not candidate.document_id:
                reasons.append(f"accepted_candidate_document_required:{candidate.id}")
            if (
                candidate.status == "accepted"
                and candidate.document_id not in document_ids
            ):
                reasons.append(
                    f"accepted_candidate_document_not_linked_to_packet:{candidate.id}"
                )
            candidate_document = (
                release_documents_by_id.get(candidate.document_id)
                if candidate.document_id is not None
                else None
            )
            if (
                candidate.status == "accepted"
                and candidate.document_id is not None
                and candidate_document is None
            ):
                reasons.append(
                    f"accepted_candidate_document_outside_release:{candidate.id}"
                )
            if candidate.status == "accepted" and candidate_document is not None:
                if canonicalize_url(candidate.canonical_url) != canonicalize_url(
                    candidate_document.canonical_url
                ):
                    reasons.append(
                        f"accepted_candidate_canonical_url_mismatch:{candidate.id}"
                    )
            if candidate.status == "rejected" and not _normalized(candidate.rejection_reason):
                reasons.append(f"rejected_candidate_reason_required:{candidate.id}")
            if candidate.status == "rejected" and candidate.document_id is not None:
                reasons.append(f"rejected_candidate_document_forbidden:{candidate.id}")

    for document in release.documents:
        if document.id not in linked_document_ids:
            reasons.append(f"unlinked_historical_evidence_document:{document.id}")
        if document.source_license_status == "unknown":
            reasons.append(f"historical_evidence_license_unknown:{document.id}")
        if not _normalized(document.source_use_basis):
            reasons.append(f"historical_evidence_source_use_basis_required:{document.id}")
        packet_cutoffs = [
            as_utc(link.packet.evidence_cutoff) for link in document.packet_links
        ]
        for cutoff in packet_cutoffs:
            if as_utc(document.source_available_at) > cutoff:
                reasons.append(f"historical_evidence_after_cutoff:{document.id}")
            if document.published_at and as_utc(document.published_at) > cutoff:
                reasons.append(f"historical_evidence_publication_after_cutoff:{document.id}")
        if document.source_kind == "wayback_final_capture":
            if document.temporal_basis != "snapshot_date":
                reasons.append(f"wayback_temporal_basis_invalid:{document.id}")
            if not document.final_capture_verified:
                reasons.append(f"wayback_final_capture_unverified:{document.id}")
            if not document.final_capture_url or not document.final_capture_at:
                reasons.append(f"wayback_final_capture_metadata_missing:{document.id}")
            else:
                parsed = parse_wayback_url(document.final_capture_url)
                host = (urlsplit(document.final_capture_url).hostname or "").casefold()
                if parsed is None or host not in WAYBACK_HOSTS:
                    reasons.append(f"wayback_final_capture_url_invalid:{document.id}")
                elif as_utc(parsed.timestamp) != as_utc(document.final_capture_at):
                    reasons.append(f"wayback_capture_timestamp_mismatch:{document.id}")
                elif canonicalize_url(parsed.archived_original_url) != canonicalize_url(
                    document.canonical_url
                ):
                    reasons.append(f"wayback_capture_original_mismatch:{document.id}")
                if as_utc(document.final_capture_at) != as_utc(document.source_available_at):
                    reasons.append(f"wayback_availability_timestamp_mismatch:{document.id}")
                if _normalized(document.source_url) != _normalized(
                    document.final_capture_url
                ):
                    reasons.append(f"wayback_source_not_final_capture:{document.id}")
            if (
                not document.archived_original_url
                or canonicalize_url(document.archived_original_url)
                != canonicalize_url(document.canonical_url)
            ):
                reasons.append(f"wayback_archived_original_mismatch:{document.id}")
        elif document.source_kind == "immutable_version":
            if document.temporal_basis != "immutable_version":
                reasons.append(f"immutable_temporal_basis_invalid:{document.id}")
            if document.immutable_adapter_id not in REGISTERED_IMMUTABLE_ADAPTERS:
                reasons.append(f"immutable_adapter_unregistered:{document.id}")
            if not document.immutable_version_id:
                reasons.append(f"immutable_version_id_required:{document.id}")
            if not document.immutable_availability_verified:
                reasons.append(f"immutable_availability_unverified:{document.id}")
        else:
            reasons.append(f"historical_evidence_source_kind_invalid:{document.id}")

    try:
        refreshed = _refresh_artifacts(release, policy=policy)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        reasons.append(f"historical_evidence_artifact_invalid:{exc.__class__.__name__}")
        refreshed = {}
    for field in (
        "execution_manifest_json",
        "execution_manifest_hash",
        "audit_manifest_json",
        "audit_manifest_hash",
        "bundle_manifest_json",
        "bundle_manifest_hash",
        "release_hash",
    ):
        if field in refreshed and getattr(release, field) != refreshed[field]:
            reasons.append(f"{field}_mismatch")
    try:
        execution = get_historical_evidence_execution_manifest(
            release, require_frozen=False
        )
    except HistoricalEvidenceReleaseValidationError as exc:
        reasons.extend(exc.reasons)
    else:
        for path in _forbidden_paths(execution.model_dump(mode="json")):
            reasons.append(f"historical_evidence_execution_manifest_forbidden_field:{path}")
    try:
        audit = json.loads(release.audit_manifest_json)
    except json.JSONDecodeError:
        reasons.append("historical_evidence_audit_manifest_invalid")
    else:
        for path in _forbidden_paths(audit):
            reasons.append(
                f"historical_evidence_audit_manifest_forbidden_field:{path}"
            )
    try:
        bundle = get_historical_evidence_bundle_manifest(release)
    except HistoricalEvidenceReleaseValidationError as exc:
        reasons.extend(exc.reasons)
    else:
        if bundle_root is None:
            reasons.append("historical_evidence_bundle_root_required")
        else:
            verification = verify_historical_evidence_bundle(
                bundle,
                bundle_root,
                expected_release_hash=release.release_hash,
            )
            reasons.extend(verification.reasons)
    return list(dict.fromkeys(reasons))


def review_historical_evidence_release(
    session: Session,
    release: HistoricalEvidenceRelease,
    *,
    bundle_root: Path,
    now: datetime | None = None,
    policy: HistoricalEvidenceReleasePolicy = (
        PRIVATE_V1_HISTORICAL_EVIDENCE_RELEASE_V1
    ),
) -> HistoricalEvidenceRelease:
    reasons = validate_historical_evidence_release(
        session, release, bundle_root=bundle_root, policy=policy
    )
    if reasons:
        raise HistoricalEvidenceReleaseValidationError(reasons)
    if release.status in {"reviewed", "frozen"}:
        return release
    if release.status != "draft":
        raise HistoricalEvidenceReleaseValidationError(
            ["historical_evidence_release_status_invalid"]
        )
    release.status = "reviewed"
    release.reviewed_at = as_utc(now or utcnow())
    session.flush()
    return release


def freeze_historical_evidence_release(
    session: Session,
    release: HistoricalEvidenceRelease,
    *,
    bundle_root: Path,
    now: datetime | None = None,
    policy: HistoricalEvidenceReleasePolicy = (
        PRIVATE_V1_HISTORICAL_EVIDENCE_RELEASE_V1
    ),
) -> HistoricalEvidenceRelease:
    if release.status == "draft":
        raise HistoricalEvidenceReleaseValidationError(
            ["historical_evidence_release_review_required"]
        )
    reasons = validate_historical_evidence_release(
        session, release, bundle_root=bundle_root, policy=policy
    )
    if reasons:
        raise HistoricalEvidenceReleaseValidationError(reasons)
    if release.status == "frozen":
        return release
    if release.status != "reviewed":
        raise HistoricalEvidenceReleaseValidationError(
            ["historical_evidence_release_status_invalid"]
        )
    release.status = "frozen"
    release.frozen_at = as_utc(now or utcnow())
    session.flush()
    return release


def verify_release_bundle(
    release: HistoricalEvidenceRelease,
    bundle_root: Path,
) -> HistoricalEvidenceBundleVerification:
    bundle = get_historical_evidence_bundle_manifest(release)
    return verify_historical_evidence_bundle(
        bundle,
        bundle_root,
        expected_release_hash=release.release_hash,
    )


def build_frozen_evidence_runtime(
    release: HistoricalEvidenceRelease,
    *,
    bundle_root: Path,
    split: str,
    evaluation_question_id: str,
    evidence_cutoff: datetime,
) -> tuple[FrozenEvidenceSearchProvider, FrozenEvidenceDocumentStore]:
    if release.status != "frozen":
        raise HistoricalEvidenceReleaseValidationError(
            ["historical_evidence_release_must_be_frozen"]
        )
    execution = get_historical_evidence_execution_manifest(release)
    bundle = get_historical_evidence_bundle_manifest(release)
    store = FrozenEvidenceDocumentStore(
        manifest=bundle,
        execution_manifest=execution,
        bundle_root=bundle_root,
        expected_release_hash=release.release_hash,
        split=split,
        evaluation_question_id=evaluation_question_id,
        evidence_cutoff=evidence_cutoff,
    )
    return FrozenEvidenceSearchProvider(store), store


def serialize_historical_evidence_release(
    release: HistoricalEvidenceRelease,
    *,
    include_audit: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": release.id,
        "evaluation_release_id": release.evaluation_release_id,
        "evaluation_execution_manifest_hash": (
            release.evaluation_execution_manifest_hash
        ),
        "name": release.name,
        "version": release.version,
        "policy_version": release.policy_version,
        "status": release.status,
        "created_at": as_utc(release.created_at).isoformat(),
        "reviewed_at": as_utc(release.reviewed_at).isoformat() if release.reviewed_at else None,
        "frozen_at": as_utc(release.frozen_at).isoformat() if release.frozen_at else None,
        "correction_of_release_id": release.correction_of_release_id,
        "correction_summary": release.correction_summary,
        "execution_manifest_hash": release.execution_manifest_hash,
        "audit_manifest_hash": release.audit_manifest_hash,
        "bundle_manifest_hash": release.bundle_manifest_hash,
        "release_hash": release.release_hash,
        "packet_count": len(release.packets),
        "document_count": len(release.documents),
        "ready_packet_count": sum(item.status == "ready" for item in release.packets),
        "no_eligible_evidence_packet_count": sum(
            item.status == "no_eligible_evidence" for item in release.packets
        ),
    }
    if include_audit:
        payload["audit_manifest"] = json.loads(release.audit_manifest_json)
    return payload
