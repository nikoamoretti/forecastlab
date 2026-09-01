"""Offline-only readiness checks for private internal evaluation policy V2."""

from __future__ import annotations

from collections import Counter
from typing import Any

from forecastlab.hashing import canonical_json, sha256_text

V2_PREFLIGHT_VERSION = "private_internal_evaluation_gate_v2_preflight_v1"
REQUIRED_CANDIDATE_FIELDS = (
    "candidate_id",
    "original_question",
    "normalized_question",
    "yes_condition",
    "no_condition",
    "forecast_date",
    "resolution_date",
    "authoritative_resolver",
    "origin_url",
    "origin_timestamp",
    "event_family_id",
    "leakage_group_id",
)
REQUIRED_DOCUMENT_PROVENANCE_FIELDS = (
    "canonical_url",
    "content_sha256",
    "extracted_text_sha256",
    "source_available_at",
    "temporal_basis",
)


def _count_missing(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[str, int]:
    return {
        field: sum(not row.get(field) for row in rows)
        for field in fields
    }


def build_private_internal_evaluation_v2_preflight(
    *,
    summary: dict[str, Any],
    candidates: list[dict[str, Any]],
    packets: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return an immutable-workspace V2 readiness receipt without changing it.

    Acquisition records are not review artifacts, so this intentionally reports
    their remaining receipt, reviewer, and evidence gaps instead of converting
    them into a release or inventing source provenance.
    """

    candidate_ids = [str(row.get("candidate_id", "")) for row in candidates]
    included = [row for row in candidates if row.get("provisional_split") != "reserve"]
    split_counts = Counter(str(row.get("provisional_split", "")) for row in candidates)
    event_splits: dict[str, set[str]] = {}
    leakage_splits: dict[str, set[str]] = {}
    for row in included:
        split = str(row.get("provisional_split", ""))
        event_splits.setdefault(str(row.get("event_family_id", "")), set()).add(split)
        leakage_splits.setdefault(str(row.get("leakage_group_id", "")), set()).add(split)
    cross_split_events = sorted(
        group for group, splits in event_splits.items() if group and len(splits) > 1
    )
    cross_split_leakage = sorted(
        group for group, splits in leakage_splits.items() if group and len(splits) > 1
    )
    packet_counts = Counter(str(row.get("status", "")) for row in packets)
    document_provenance_missing = _count_missing(
        documents, REQUIRED_DOCUMENT_PROVENANCE_FIELDS
    )
    unknown_license_count = sum(
        row.get("source_use", {}).get("status") == "unknown_pending_human_review"
        for row in candidates
        if isinstance(row.get("source_use"), dict)
    )
    blocking = {
        "missing_candidate_contract_or_origin_fields": _count_missing(
            candidates, REQUIRED_CANDIDATE_FIELDS
        ),
        "duplicate_candidate_ids": len(candidate_ids) - len(set(candidate_ids)),
        "cross_split_event_family_count": len(cross_split_events),
        "cross_split_leakage_group_count": len(cross_split_leakage),
        "awaiting_question_review": sum(
            row.get("review_status") != "reviewed" for row in candidates
        ),
        "awaiting_independent_outcome_adjudication": sum(
            row.get("adjudication_status") != "adjudicated" for row in candidates
        ),
        "no_persisted_v2_question_review_artifacts": len(included),
        "no_persisted_v2_outcome_adjudication_artifacts": len(included),
        "no_persisted_v2_review_source_provenance": len(included),
        "no_eligible_evidence_packets": packet_counts.get("no_eligible_evidence", 0),
        "document_provenance_fields_missing": document_provenance_missing,
    }
    audit_only = {
        "unknown_pending_human_review_license_metadata": unknown_license_count,
        "redistribution_false_or_unset_document_metadata": sum(
            row.get("redistribution_allowed") is not True for row in documents
        ),
    }
    receipt: dict[str, Any] = {
        "artifact_version": V2_PREFLIGHT_VERSION,
        "policy_version": "private_v1_real_evaluation_release_v2",
        "procedural_review_policy_version": "private_v1_procedural_ai_review_v2",
        "candidate_count": len(candidates),
        "split_counts": {
            split: split_counts.get(split, 0)
            for split in ("development", "validation", "test", "reserve")
        },
        "packet_counts": dict(sorted(packet_counts.items())),
        "accepted_document_count": len(documents),
        "cross_split_event_families": cross_split_events,
        "cross_split_leakage_groups": cross_split_leakage,
        "blocking": blocking,
        "audit_only_licensing_metadata": audit_only,
        "release_ready": False,
        "notes": [
            "This preflight is read-only and cannot create review receipts or a release.",
            "Unknown licensing or a false redistribution flag is audit metadata under V2, not a private-internal pass/fail reason.",
            "No-evidence packets remain in denominators and are never replaced with an imputed probability.",
        ],
    }
    receipt["preflight_hash"] = sha256_text(canonical_json(receipt))
    return receipt
