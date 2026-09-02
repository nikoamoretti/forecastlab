from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from forecastlab.native_provenance_corpus import (
    NATIVE_RECORD_TEMPORAL_BASIS,
    NativeCorpusError,
    attach_split,
    native_candidate_from_market,
    native_document_from_packet,
    provisional_split,
    validate_native_v2_readiness,
)


def _market(index: int, *, result: str = "yes") -> dict[str, object]:
    created = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index)
    settled = created + timedelta(days=21)
    return {
        "ticker": f"KXTEST-{index}",
        "event_ticker": f"KXEVENT-{index}",
        "series_ticker": "KXTEST",
        "market_type": "binary",
        "result": result,
        "title": f"Will the official test series {index} exceed the published threshold?",
        "rules_primary": (
            "This market resolves Yes if the official test series exceeds the published "
            "threshold on the stated release date. It resolves No otherwise."
        ),
        "created_time": created.isoformat(),
        "settlement_ts": settled.isoformat(),
    }


def _candidate(index: int) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    return native_candidate_from_market(
        market=_market(index),
        category="Economics",
        retrieved_at=datetime(2026, 9, 1, tzinfo=UTC),
        raw_record_hash=f"{index:064x}",
    )


def test_native_record_keeps_blinded_outcome_and_timestamps_separate() -> None:
    candidate, sealed, packet = _candidate(1)

    assert "provisional_observed_outcome" not in candidate
    assert sealed["provisional_observed_outcome"] == 1
    assert candidate["origin_timestamp"] < sealed["outcome_known_at"]
    source = candidate["question_source"]
    assert isinstance(source, dict)
    assert source["temporal_basis"] == NATIVE_RECORD_TEMPORAL_BASIS
    assert source["retrieved_at"] != source["source_available_at"]
    document = native_document_from_packet(packet)
    assert document["temporal_basis"] == NATIVE_RECORD_TEMPORAL_BASIS
    assert document["source_available_at"] == candidate["origin_timestamp"]


def test_native_receipts_pass_v2_input_checks_without_creating_review_artifacts() -> None:
    candidate, sealed, packet = _candidate(2)
    document = native_document_from_packet(packet)
    split = provisional_split([candidate])
    candidate = attach_split([candidate], split)[0]

    receipt = validate_native_v2_readiness(
        candidates=[candidate],
        sealed_outcomes=[sealed],
        packets=[packet],
        documents=[document],
    )

    assert receipt["ready"] is True
    assert receipt["question_manifest_inputs_validated"] == 1
    assert receipt["outcome_manifest_inputs_validated"] == 1
    assert receipt["review_artifacts_created"] is False
    assert receipt["release_frozen"] is False


def test_native_receipts_reject_retrieval_as_cutoff_proof() -> None:
    candidate, sealed, packet = _candidate(3)
    document = native_document_from_packet(packet)
    document["source_available_at"] = document["retrieved_at"]
    candidate = attach_split([candidate], provisional_split([candidate]))[0]

    receipt = validate_native_v2_readiness(
        candidates=[candidate],
        sealed_outcomes=[sealed],
        packets=[packet],
        documents=[document],
    )

    assert receipt["ready"] is False
    assert receipt["errors"] == [f"native_cutoff_timestamp_mismatch:{candidate['candidate_id']}"]


def test_native_candidate_rejects_invalid_binary_and_short_horizon() -> None:
    invalid_binary = _market(4)
    invalid_binary["market_type"] = "scalar"
    with pytest.raises(NativeCorpusError, match="native_market_not_binary"):
        native_candidate_from_market(
            market=invalid_binary,
            category="Economics",
            retrieved_at=datetime(2026, 9, 1, tzinfo=UTC),
            raw_record_hash="a" * 64,
        )

    short_horizon = _market(5)
    short_horizon["settlement_ts"] = (
        datetime.fromisoformat(str(short_horizon["created_time"])) + timedelta(days=1)
    ).isoformat()
    with pytest.raises(NativeCorpusError, match="native_market_forecast_horizon_under_minimum"):
        native_candidate_from_market(
            market=short_horizon,
            category="Economics",
            retrieved_at=datetime(2026, 9, 1, tzinfo=UTC),
            raw_record_hash="a" * 64,
        )


def test_split_is_deterministic_outcome_independent_and_leakage_safe() -> None:
    candidates: list[dict[str, object]] = []
    for index in range(260):
        candidate, _, _ = _candidate(index + 10)
        candidates.append(candidate)

    first = provisional_split(candidates)
    reversed_candidates = list(reversed(candidates))
    second = provisional_split(reversed_candidates)

    assert first == second
    assert first["counts"] == {"development": 60, "validation": 40, "test": 100}
    assert len(first["reserve_candidate_ids"]) == 60
    assert first["complete"] is True
    assert first["outcome_used"] is False
    assignments = first["assignments"]
    assert len({row["leakage_group_id"] for row in assignments}) == len(assignments)
