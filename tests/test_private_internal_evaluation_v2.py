from __future__ import annotations

from forecastlab.private_internal_evaluation_v2 import (
    build_private_internal_evaluation_v2_preflight,
)


def _candidate(*, identifier: str, split: str, license_status: str) -> dict[str, object]:
    return {
        "candidate_id": identifier,
        "original_question": "Will the official value cross the published threshold?",
        "normalized_question": "will the official value cross the published threshold",
        "yes_condition": "The value is at or above the threshold.",
        "no_condition": "The value remains below the threshold.",
        "forecast_date": "2020-01-01T00:00:00Z",
        "resolution_date": "2020-02-01T00:00:00Z",
        "authoritative_resolver": "Official record",
        "origin_url": "https://origin.example/question",
        "origin_timestamp": "2020-01-01T00:00:00Z",
        "event_family_id": f"family:{identifier}",
        "leakage_group_id": f"group:{identifier}",
        "provisional_split": split,
        "review_status": "awaiting_human_review",
        "adjudication_status": "awaiting_independent_adjudication",
        "source_use": {"status": license_status},
    }


def test_v2_preflight_keeps_unknown_licensing_audit_only() -> None:
    receipt = build_private_internal_evaluation_v2_preflight(
        summary={},
        candidates=[
            _candidate(
                identifier="a",
                split="development",
                license_status="unknown_pending_human_review",
            )
        ],
        packets=[{"status": "no_eligible_evidence"}],
        documents=[],
    )
    assert receipt["audit_only_licensing_metadata"][
        "unknown_pending_human_review_license_metadata"
    ] == 1
    assert receipt["blocking"]["no_persisted_v2_question_review_artifacts"] == 1
    assert receipt["blocking"]["no_persisted_v2_review_source_provenance"] == 1
    assert receipt["blocking"]["no_eligible_evidence_packets"] == 1
    assert receipt["release_ready"] is False


def test_v2_preflight_is_deterministic_and_detects_split_leakage() -> None:
    first = _candidate(identifier="a", split="development", license_status="unknown")
    second = _candidate(identifier="b", split="validation", license_status="unknown")
    second["event_family_id"] = first["event_family_id"]
    one = build_private_internal_evaluation_v2_preflight(
        summary={}, candidates=[first, second], packets=[], documents=[]
    )
    two = build_private_internal_evaluation_v2_preflight(
        summary={}, candidates=[first, second], packets=[], documents=[]
    )
    assert one == two
    assert one["blocking"]["cross_split_event_family_count"] == 1
