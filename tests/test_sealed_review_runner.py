from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from forecastlab.procedural_ai_review import (
    OUTCOME_ADJUDICATION_RUBRIC_V2,
    QUESTION_REVIEW_RUBRIC_V2,
    OutcomeAdjudicationManifest,
    OutcomeAdjudicationSource,
    QuestionReviewManifest,
    QuestionReviewSource,
    rubric_hash,
)
from forecastlab.sealed_review_runner import (
    SealedReviewRunnerError,
    append_outer_receipt,
    materialize_review_campaign,
    run_sealed_role,
    sandbox_network_probe,
    sandbox_read_probe,
)

NOW = datetime(2026, 8, 31, tzinfo=UTC)
HASH = "a" * 64


def _question_manifest() -> QuestionReviewManifest:
    return QuestionReviewManifest(
        evaluation_release_id="tranche-not-a-release",
        evaluation_release_question_id="tranche-question-1",
        evaluation_question_id="candidate-1",
        normalized_question_hash=HASH,
        contract_hash="b" * 64,
        question="Will an official release occur?",
        yes_condition="An official release occurs.",
        no_condition="No official release occurs.",
        forecast_date=NOW,
        resolution_date=datetime(2026, 9, 30, tzinfo=UTC),
        authoritative_resolver="Example resolver",
        event_family_id="family-1",
        leakage_group_id="group-1",
        sources=[
            QuestionReviewSource(
                source_id="origin-1",
                url="https://example.com/origin",
                title="Origin record",
                publisher="Example institution",
                source_role="pre_outcome_origin",
                source_available_at=NOW,
                retrieved_at=NOW,
                published_at_unknown=True,
                temporal_basis="snapshot_date",
                content_sha256=HASH,
                extracted_text_sha256="c" * 64,
                evidence_note="Verified archived origin record.",
            )
        ],
        rubric_version=QUESTION_REVIEW_RUBRIC_V2.version,
        rubric_hash=rubric_hash(QUESTION_REVIEW_RUBRIC_V2),
    )


def _outcome_manifest() -> OutcomeAdjudicationManifest:
    return OutcomeAdjudicationManifest(
        evaluation_release_id="tranche-not-a-release",
        evaluation_release_question_id="tranche-question-1",
        evaluation_question_id="candidate-1",
        contract_hash="b" * 64,
        yes_condition="An official release occurs.",
        no_condition="No official release occurs.",
        resolution_date=datetime(2026, 9, 30, tzinfo=UTC),
        candidate_outcome=1,
        outcome_known_at=datetime(2026, 10, 1, tzinfo=UTC),
        sources=[
            OutcomeAdjudicationSource(
                source_id="outcome-1",
                url="https://example.com/outcome",
                title="Official outcome",
                publisher="Example institution",
                source_role="authoritative_resolution",
                source_available_at=datetime(2026, 10, 1, tzinfo=UTC),
                retrieved_at=datetime(2026, 10, 1, tzinfo=UTC),
                published_at_unknown=True,
                temporal_basis="immutable_version",
                content_sha256="d" * 64,
                extracted_text_sha256="e" * 64,
                evidence_note="Recorded immutable outcome version.",
            )
        ],
        rubric_version=OUTCOME_ADJUDICATION_RUBRIC_V2.version,
        rubric_hash=rubric_hash(OUTCOME_ADJUDICATION_RUBRIC_V2),
    )


@pytest.mark.skipif(Path("/usr/bin/sandbox-exec").exists() is False, reason="macOS sandbox-exec unavailable")
def test_campaign_bundles_enforce_cross_role_and_repository_read_denial(tmp_path: Path) -> None:
    bundles = materialize_review_campaign(
        root=tmp_path / "sealed",
        question_manifests=[_question_manifest()],
        outcome_manifests=[_outcome_manifest()],
        repository_root=Path("/Users/nico-yardlogix/Projects/forecastlab"),
    )
    question = bundles["question_review"]
    outcome = bundles["outcome_adjudication"]
    assert sandbox_read_probe(question, question.manifest_paths[0]).returncode == 0
    assert sandbox_read_probe(question, outcome.manifest_paths[0]).returncode != 0
    assert sandbox_read_probe(outcome, outcome.manifest_paths[0]).returncode == 0
    assert sandbox_read_probe(outcome, question.manifest_paths[0]).returncode != 0
    assert sandbox_read_probe(
        outcome, Path("/Users/nico-yardlogix/Projects/forecastlab/pyproject.toml")
    ).returncode != 0
    assert sandbox_read_probe(question, tmp_path / "sealed" / "bundle.json").returncode != 0
    sealed_scoring = tmp_path / "sealed" / "sealed_scoring.json"
    sealed_scoring.write_text('{"outcome": 1}\n', encoding="utf-8")
    assert sandbox_read_probe(question, sealed_scoring).returncode != 0
    assert sandbox_network_probe(question).returncode != 0
    assert run_sealed_role(question, ["/bin/cat", str(question.manifest_paths[0])]).returncode == 0
    assert run_sealed_role(
        question,
        ["/bin/sh", "-c", f"printf x > {tmp_path / 'sandbox-write-denied'}"],
    ).returncode != 0
    assert not (tmp_path / "sandbox-write-denied").exists()
    with pytest.raises(SealedReviewRunnerError, match="sealed_role_command_required"):
        run_sealed_role(question, [])

    receipt = {
        "role": "question_review",
        "input_manifest_hash": question.manifest_hash,
        "status": "received_without_review_artifact",
    }
    first = append_outer_receipt(
        bundle_root=tmp_path / "sealed",
        role="question_review",
        manifest_hash=question.manifest_hash,
        receipt_json=receipt,
    )
    assert first.exists()
    assert sandbox_read_probe(question, first).returncode != 0
    with pytest.raises(SealedReviewRunnerError, match="receipt_role_mismatch"):
        append_outer_receipt(
            bundle_root=tmp_path / "sealed",
            role="outcome_adjudication",
            manifest_hash=outcome.manifest_hash,
            receipt_json=receipt,
        )


def test_runner_refuses_source_tree_and_wrong_role_manifest(tmp_path: Path) -> None:
    with pytest.raises(SealedReviewRunnerError, match="outside_repository"):
        materialize_review_campaign(
            root=Path("/Users/nico-yardlogix/Projects/forecastlab/sealed-test"),
            question_manifests=[_question_manifest()],
            outcome_manifests=[_outcome_manifest()],
            repository_root=Path("/Users/nico-yardlogix/Projects/forecastlab"),
        )
