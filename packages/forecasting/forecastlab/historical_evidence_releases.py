from __future__ import annotations

from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from forecastlab.hashing import canonical_json, sha256_bytes, sha256_text

POLICY_VERSION = "private_v1_historical_evidence_release_v1"

HistoricalEvidenceReleaseStatus = Literal["draft", "reviewed", "frozen"]
HistoricalEvidencePacketStatus = Literal["ready", "no_eligible_evidence"]
HistoricalEvidenceSourceKind = Literal["wayback_final_capture", "immutable_version"]
HistoricalEvidenceTemporalBasis = Literal["snapshot_date", "immutable_version"]
HistoricalEvidenceCandidateStatus = Literal["accepted", "rejected"]
SourceLicenseStatus = Literal[
    "public_domain",
    "licensed",
    "metadata_use_permitted",
    "unknown",
]


class HistoricalEvidenceReleasePolicy(BaseModel):
    """Immutable cutoff, provenance, and missingness policy for real evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["private_v1_historical_evidence_release_v1"] = POLICY_VERSION
    evaluation_release_status_required: Literal["frozen"] = "frozen"
    exactly_one_packet_per_included_question: bool = True
    executable_packets_for_excluded_questions: bool = False
    packet_statuses: tuple[HistoricalEvidencePacketStatus, ...] = (
        "ready",
        "no_eligible_evidence",
    )
    collector_reviewer_separation_required: bool = True
    known_license_required: bool = True
    verified_wayback_final_capture_required: bool = True
    registered_immutable_adapter_required: bool = True
    retrieval_date_eligibility_allowed: bool = False
    current_live_fallback_allowed: bool = False
    snippets_are_evidence: bool = False
    missingness_remains_in_denominator: bool = True
    missingness_probability_imputation_allowed: bool = False


PRIVATE_V1_HISTORICAL_EVIDENCE_RELEASE_V1 = HistoricalEvidenceReleasePolicy()


def _sha256(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("sha256_required")
    return normalized


def _relative_content_locator(value: str, *, prefix: str, digest: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("unsafe_content_locator")
    expected = f"{prefix}/{digest}"
    if path.as_posix() != expected:
        raise ValueError(f"content_locator_must_equal:{expected}")
    return expected


class HistoricalEvidenceReleaseIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    version: str
    policy_version: str
    evaluation_release_id: str
    evaluation_execution_manifest_hash: str
    release_hash: str


class HistoricalEvidenceDocumentInput(BaseModel):
    """Reviewed metadata for bytes already present in an external bundle."""

    model_config = ConfigDict(extra="forbid")

    local_id: str = Field(min_length=1, max_length=128)
    canonical_url: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    source_class: Literal["primary", "secondary"]
    source_kind: HistoricalEvidenceSourceKind
    temporal_basis: HistoricalEvidenceTemporalBasis
    published_at: datetime | None = None
    source_available_at: datetime
    final_capture_url: str | None = None
    final_capture_at: datetime | None = None
    archived_original_url: str | None = None
    final_capture_verified: bool = False
    immutable_adapter_id: str | None = None
    immutable_version_id: str | None = None
    immutable_availability_verified: bool = False
    mime_type: str = "text/html"
    content_sha256: str
    extracted_text_sha256: str
    byte_length: int = Field(ge=1)
    text_length: int = Field(ge=1)
    blob_locator: str
    text_locator: str
    source_license_status: SourceLicenseStatus
    source_use_basis: str = Field(min_length=1)
    redistribution_allowed: bool
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content_sha256", "extracted_text_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        return _sha256(value)

    @field_validator("blob_locator")
    @classmethod
    def validate_blob_locator(cls, value: str, info) -> str:
        digest = str(info.data.get("content_sha256") or "")
        return _relative_content_locator(value, prefix="blobs", digest=digest)

    @field_validator("text_locator")
    @classmethod
    def validate_text_locator(cls, value: str, info) -> str:
        digest = str(info.data.get("extracted_text_sha256") or "")
        return _relative_content_locator(value, prefix="text", digest=digest)


class HistoricalEvidenceCandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_url: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    rank: int = Field(ge=1)
    search_query: str = Field(min_length=1)
    search_provider: str = Field(min_length=1)
    status: HistoricalEvidenceCandidateStatus
    rejection_reason: str | None = None
    archive_check_status: str = Field(min_length=1)
    document_local_id: str | None = None


class HistoricalEvidencePacketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_question_id: str
    split: Literal["development", "validation", "test"]
    evidence_cutoff: datetime
    status: HistoricalEvidencePacketStatus
    collector_id: str = Field(min_length=3, max_length=128)
    reviewer_id: str = Field(min_length=3, max_length=128)
    reviewed_at: datetime
    searches: list[dict[str, Any]] = Field(default_factory=list)
    archive_checks: list[dict[str, Any]] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    candidates: list[HistoricalEvidenceCandidateInput] = Field(default_factory=list)
    document_local_ids: list[str] = Field(default_factory=list)


class BlindedHistoricalEvidenceDocument(BaseModel):
    """Worker-safe document identity; it cannot represent outcomes or scoring data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    canonical_url: str
    source_url: str
    title: str
    publisher: str
    source_class: Literal["primary", "secondary"]
    source_kind: HistoricalEvidenceSourceKind
    temporal_basis: HistoricalEvidenceTemporalBasis
    published_at: datetime | None = None
    source_available_at: datetime
    final_capture_url: str | None = None
    final_capture_at: datetime | None = None
    archived_original_url: str | None = None
    immutable_adapter_id: str | None = None
    immutable_version_id: str | None = None
    mime_type: str
    content_sha256: str
    extracted_text_sha256: str
    byte_length: int
    text_length: int
    blob_locator: str
    text_locator: str

    @field_validator("content_sha256", "extracted_text_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        return _sha256(value)

    @field_validator("blob_locator")
    @classmethod
    def validate_blob_locator(cls, value: str, info) -> str:
        digest = str(info.data.get("content_sha256") or "")
        return _relative_content_locator(value, prefix="blobs", digest=digest)

    @field_validator("text_locator")
    @classmethod
    def validate_text_locator(cls, value: str, info) -> str:
        digest = str(info.data.get("extracted_text_sha256") or "")
        return _relative_content_locator(value, prefix="text", digest=digest)


class BlindedHistoricalEvidencePacket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    packet_id: str
    evaluation_question_id: str
    split: Literal["development", "validation", "test"]
    evidence_cutoff: datetime
    status: HistoricalEvidencePacketStatus
    documents: list[BlindedHistoricalEvidenceDocument]


class BlindedHistoricalEvidenceExecutionManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    policy_version: str
    evaluation_release_id: str
    evaluation_execution_manifest_hash: str
    packets: list[BlindedHistoricalEvidencePacket]


class HistoricalEvidenceCandidateAudit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    canonical_url: str
    source_url: str
    rank: int
    search_query: str
    search_provider: str
    status: HistoricalEvidenceCandidateStatus
    rejection_reason: str | None = None
    archive_check_status: str
    document_id: str | None = None


class HistoricalEvidencePacketAudit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    packet_id: str
    evaluation_question_id: str
    split: Literal["development", "validation", "test"]
    evidence_cutoff: datetime
    status: HistoricalEvidencePacketStatus
    collector_id: str
    reviewer_id: str
    reviewed_at: datetime
    searches: list[dict[str, Any]]
    archive_checks: list[dict[str, Any]]
    rejection_reasons: list[str]
    candidates: list[HistoricalEvidenceCandidateAudit]
    document_ids: list[str]


class HistoricalEvidenceAuditManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    policy_version: str
    evaluation_release_id: str
    evaluation_execution_manifest_hash: str
    creation_request_hash: str
    packets: list[HistoricalEvidencePacketAudit]
    documents: list[dict[str, Any]]


class HistoricalEvidenceBundleManifest(BaseModel):
    """Portable bundle index. All locators are relative and content addressed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    release: HistoricalEvidenceReleaseIdentity
    execution_manifest_hash: str
    documents: list[BlindedHistoricalEvidenceDocument]


class HistoricalEvidenceBundleVerification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    release_hash: str
    manifest_hash: str
    document_count: int
    unique_blob_count: int
    unique_text_count: int
    reasons: list[str] = Field(default_factory=list)


def historical_evidence_manifest_hash(value: BaseModel | dict[str, Any]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return sha256_text(canonical_json(payload))


def verify_historical_evidence_bundle(
    manifest: HistoricalEvidenceBundleManifest,
    bundle_root: Path,
    *,
    expected_release_hash: str,
) -> HistoricalEvidenceBundleVerification:
    """Strictly offline byte/text verification for one content-addressed bundle."""

    reasons: list[str] = []
    root = bundle_root.resolve()
    if not root.is_dir():
        reasons.append("bundle_root_missing")
    if manifest.release.release_hash != expected_release_hash:
        reasons.append("historical_evidence_release_hash_mismatch")

    referenced: set[str] = set()
    blob_hashes: set[str] = set()
    text_hashes: set[str] = set()
    for document in manifest.documents:
        for locator, prefix, digest in (
            (document.blob_locator, "blobs", document.content_sha256),
            (document.text_locator, "text", document.extracted_text_sha256),
        ):
            try:
                safe_locator = _relative_content_locator(
                    locator,
                    prefix=prefix,
                    digest=digest,
                )
            except ValueError as exc:
                reasons.append(f"unsafe_or_noncanonical_locator:{document.document_id}:{exc}")
                continue
            referenced.add(safe_locator)
            path = (root / safe_locator).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                reasons.append(f"locator_escapes_bundle:{document.document_id}:{prefix}")
                continue
            if not path.is_file():
                reasons.append(f"bundle_content_missing:{document.document_id}:{prefix}")
                continue
            data = path.read_bytes()
            if sha256_bytes(data) != digest:
                reasons.append(f"bundle_content_hash_mismatch:{document.document_id}:{prefix}")
            if prefix == "blobs":
                blob_hashes.add(digest)
                if len(data) != document.byte_length:
                    reasons.append(f"bundle_byte_length_mismatch:{document.document_id}")
            else:
                text_hashes.add(digest)
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    reasons.append(f"bundle_text_not_utf8:{document.document_id}")
                else:
                    if len(text) != document.text_length:
                        reasons.append(f"bundle_text_length_mismatch:{document.document_id}")

    if root.is_dir():
        for prefix in ("blobs", "text"):
            directory = root / prefix
            if directory.exists():
                for path in sorted(item for item in directory.rglob("*") if item.is_file()):
                    relative = path.relative_to(root).as_posix()
                    if relative not in referenced:
                        reasons.append(f"extra_executable_bundle_content:{relative}")

    deduplicated = list(dict.fromkeys(reasons))
    return HistoricalEvidenceBundleVerification(
        passed=not deduplicated,
        release_hash=expected_release_hash,
        manifest_hash=historical_evidence_manifest_hash(manifest),
        document_count=len(manifest.documents),
        unique_blob_count=len(blob_hashes),
        unique_text_count=len(text_hashes),
        reasons=deduplicated,
    )
