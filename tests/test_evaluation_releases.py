from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from forecastlab.evaluation_releases import (
    PRIVATE_V1_REAL_EVALUATION_RELEASE_V1,
    BlindedEvaluationQuestion,
    EvaluationProviderIdentity,
    EvaluationReleasePolicy,
    EvaluationReleaseQuestionInput,
)
from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.historical_evidence_releases import (
    HistoricalEvidenceCandidateInput,
    HistoricalEvidencePacketInput,
)
from forecastlab.procedural_ai_review import (
    OUTCOME_ADJUDICATION_RUBRIC,
    OUTCOME_ADJUDICATION_RUBRIC_HASH,
    OUTCOME_ADJUDICATION_RUBRIC_VERSION,
    PROCEDURAL_AI_RELEASE_LABEL,
    PROCEDURAL_AI_REVIEW_POLICY_VERSION,
    QUESTION_REVIEW_RUBRIC,
    QUESTION_REVIEW_RUBRIC_HASH,
    QUESTION_REVIEW_RUBRIC_VERSION,
    review_artifact_payload_errors,
)
from forecastlab.prompts import PromptBundle
from forecastlab_api.evaluation_datasets import (
    freeze_evaluation_dataset,
    import_evaluation_dataset,
    review_evaluation_dataset,
)
from forecastlab_api.evaluation_releases import (
    EvaluationReleaseValidationError,
    _reserve_order_payload,
    assert_release_environment,
    create_evaluation_release,
    evaluation_release_artifact_hashes,
    freeze_evaluation_release,
    get_blinded_execution_manifest,
    get_sealed_scoring_manifest,
    procedural_ai_review_gate_reasons,
    record_procedural_ai_review_artifact,
    review_evaluation_release,
    validate_evaluation_release,
)
from forecastlab_api.forecast_experiments import (
    _sealed_outcome_for_terminal_run,
    claim_evaluation_test_split_once,
    create_forecast_experiment,
)
from forecastlab_api.historical_evidence_releases import (
    create_historical_evidence_release,
    freeze_historical_evidence_release,
    review_historical_evidence_release,
)
from forecastlab_api.models import (
    EvaluationDataset,
    EvaluationQuestion,
    EvaluationRelease,
    ForecastExperiment,
    ForecastExperimentRun,
    ForecastRun,
    FrozenEvaluationReleaseError,
    ImmutableProceduralAIReviewError,
    ProceduralAIReviewArtifactRecord,
    ProviderCallLedger,
)

NOW = datetime(2026, 8, 26, tzinfo=UTC)
REVIEW_STARTED_AT = NOW + timedelta(minutes=1)
REVIEW_COMPLETED_AT = NOW + timedelta(minutes=2)
REVIEW_RECORDED_AT = NOW + timedelta(minutes=3)
PROVIDER = EvaluationProviderIdentity(
    model_provider="mock",
    model="mock-forecast-v1",
    search_provider="mock",
)
SMALL_POLICY = PRIVATE_V1_REAL_EVALUATION_RELEASE_V1.model_copy(
    update={
        "required_included_counts": {
            "development": 1,
            "validation": 1,
            "test": 1,
        }
    }
)


def _row(split: str, index: int, **overrides: object) -> dict[str, object]:
    value = index + {"development": 100, "validation": 200, "test": 300}[split]
    row: dict[str, object] = {
        "question": (
            f"Did the official {split} indicator {value} reach {value} units "
            "before 1 June 2020?"
        ),
        "yes_condition": (
            f"The authoritative {split} record reports indicator {value} at or above "
            f"{value} units before 2020-06-01."
        ),
        "no_condition": (
            f"The authoritative {split} record reports indicator {value} below "
            f"{value} units through 2020-06-01."
        ),
        "forecast_date": "2019-01-01T00:00:00Z",
        "resolution_date": "2020-06-01T00:00:00Z",
        "outcome": value % 2,
        "resolution_source": f"https://records.example/{split}/{value}",
        "authoritative_resolver": f"Official {split} registry",
        "domain": "economics",
        "category": f"{split}_indicator",
    }
    row.update(overrides)
    return row


def _frozen_dataset(
    session,
    *,
    split: str,
    count: int,
    version: str = "1",
    provenance: str = "Independently curated public authoritative records.",
    row_overrides: dict[int, dict[str, object]] | None = None,
) -> EvaluationDataset:
    rows = [
        _row(split, index, **((row_overrides or {}).get(index) or {}))
        for index in range(count)
    ]
    dataset = import_evaluation_dataset(
        session,
        name=f"Historical {split} corpus {version}",
        version=version,
        description="Resolved historical questions selected before system comparison.",
        provenance=provenance,
        rows=rows,
        now=NOW,
    )
    review_evaluation_dataset(session, dataset, now=NOW)
    freeze_evaluation_dataset(session, dataset, now=NOW)
    session.flush()
    return dataset


def _release_inputs(
    datasets: dict[str, EvaluationDataset],
    *,
    excluded_ids: set[str] | None = None,
) -> list[EvaluationReleaseQuestionInput]:
    result: list[EvaluationReleaseQuestionInput] = []
    excluded_ids = excluded_ids or set()
    for split in ("development", "validation", "test"):
        dataset = datasets[split]
        for index, question in enumerate(dataset.questions):
            excluded = question.id in excluded_ids
            result.append(
                EvaluationReleaseQuestionInput(
                    evaluation_question_id=question.id,
                    split=split,
                    event_family_id=f"event:{split}:{index}",
                    leakage_group_id=f"leakage:{split}:{index}",
                    inclusion_status="excluded" if excluded else "included",
                    exclusion_reason="Predeclared source eligibility exclusion" if excluded else None,
                    question_author_id=f"author_{split}_{index}",
                    question_reviewer_id=None,
                    outcome_adjudicator_id=None,
                    review_completed_at=None,
                    outcome_known_at=datetime(2020, 6, 2, tzinfo=UTC),
                    source_license_status="public_domain",
                    source_use_basis="Official public metadata and resolution record.",
                    redistribution_allowed=False,
                    adjudication_notes=None,
                    adjudication_record_hash=None,
                )
            )
    return result


def _artifact_payload(
    release: EvaluationRelease,
    release_question,
    question: EvaluationQuestion,
    *,
    artifact_type: str,
) -> dict[str, object]:
    contract = json.loads(question.resolution_contract)
    contract_hash = sha256_text(canonical_json(contract))
    source_id = f"source:{artifact_type}:{question.id}"
    if artifact_type == "question_review":
        manifest: dict[str, object] = {
            "schema_version": 1,
            "manifest_type": "question_review_manifest",
            "evaluation_release_id": release.id,
            "evaluation_release_question_id": release_question.id,
            "evaluation_question_id": question.id,
            "normalized_question_hash": sha256_text(
                " ".join(question.question.split()).strip().casefold()
            ),
            "contract_hash": contract_hash,
            "question": question.question,
            "yes_condition": contract["yes_condition"],
            "no_condition": contract["no_condition"],
            "forecast_date": question.forecast_date.isoformat(),
            "resolution_date": question.resolution_date.isoformat(),
            "authoritative_resolver": contract["authoritative_resolver"],
            "event_family_id": release_question.event_family_id,
            "leakage_group_id": release_question.leakage_group_id,
            "inclusion_status": "included",
            "declared_source_license_status": release_question.source_license_status,
            "declared_source_use_basis": release_question.source_use_basis,
            "declared_redistribution_allowed": release_question.redistribution_allowed,
            "sources": [
                {
                    "source_id": source_id,
                    "url": f"https://origin.example/{question.id}",
                    "title": "Timestamped pre-outcome question record",
                    "source_role": "pre_outcome_origin",
                    "source_available_at": "2018-12-01T00:00:00Z",
                    "temporal_basis": "snapshot_date",
                    "content_sha256": sha256_text(f"origin:{question.id}"),
                    "source_license_status": release_question.source_license_status,
                    "source_use_basis": release_question.source_use_basis,
                    "redistribution_allowed": release_question.redistribution_allowed,
                }
            ],
            "rubric_version": QUESTION_REVIEW_RUBRIC_VERSION,
            "rubric_hash": QUESTION_REVIEW_RUBRIC_HASH,
        }
        output: dict[str, object] = {
            "schema_version": 1,
            "output_type": "question_review_output",
            "decision": "accepted",
            "findings": [
                {
                    "code": code,
                    "status": "pass",
                    "conclusion": f"Fixture-grounded procedural check: {code}.",
                    "source_ids": [source_id],
                }
                for code in QUESTION_REVIEW_RUBRIC.required_finding_codes
            ],
            "uncertainties": [],
            "conflicts": [],
        }
        role = "question_review"
        rubric_version = QUESTION_REVIEW_RUBRIC_VERSION
        rubric_digest = QUESTION_REVIEW_RUBRIC_HASH
    else:
        manifest = {
            "schema_version": 1,
            "manifest_type": "outcome_adjudication_manifest",
            "evaluation_release_id": release.id,
            "evaluation_release_question_id": release_question.id,
            "evaluation_question_id": question.id,
            "contract_hash": contract_hash,
            "yes_condition": contract["yes_condition"],
            "no_condition": contract["no_condition"],
            "resolution_date": question.resolution_date.isoformat(),
            "candidate_outcome": question.outcome,
            "outcome_known_at": release_question.outcome_known_at.isoformat(),
            "sources": [
                {
                    "source_id": source_id,
                    "url": question.resolution_source,
                    "title": "Authoritative resolution record",
                    "source_role": "authoritative_resolution",
                    "source_available_at": release_question.outcome_known_at.isoformat(),
                    "temporal_basis": "publication_date",
                    "content_sha256": sha256_text(f"resolution:{question.id}"),
                }
            ],
            "rubric_version": OUTCOME_ADJUDICATION_RUBRIC_VERSION,
            "rubric_hash": OUTCOME_ADJUDICATION_RUBRIC_HASH,
        }
        output = {
            "schema_version": 1,
            "output_type": "outcome_adjudication_output",
            "decision": "confirmed",
            "adjudicated_outcome": question.outcome,
            "findings": [
                {
                    "code": code,
                    "status": "pass",
                    "conclusion": f"Fixture-grounded procedural check: {code}.",
                    "source_ids": [source_id],
                }
                for code in OUTCOME_ADJUDICATION_RUBRIC.required_finding_codes
            ],
            "uncertainties": [],
            "conflicts": [],
        }
        role = "outcome_adjudication"
        rubric_version = OUTCOME_ADJUDICATION_RUBRIC_VERSION
        rubric_digest = OUTCOME_ADJUDICATION_RUBRIC_HASH
    return {
        "schema_version": 1,
        "artifact_type": artifact_type,
        "policy_version": PROCEDURAL_AI_REVIEW_POLICY_VERSION,
        "rubric_version": rubric_version,
        "rubric_hash": rubric_digest,
        "run_identity": {
            "role": role,
            "model_provider": "openai",
            "model_id": "codex-test-model",
            "model_version": "fixture-v1",
            "tool_name": "codex",
            "tool_version": "fixture-v1",
            "run_id": f"codex:{artifact_type}:{question.id}",
        },
        "started_at": REVIEW_STARTED_AT.isoformat(),
        "completed_at": REVIEW_COMPLETED_AT.isoformat(),
        "input_manifest": manifest,
        "input_manifest_hash": sha256_text(canonical_json(manifest)),
        "output": output,
        "output_hash": sha256_text(canonical_json(output)),
    }


def _rehash_artifact(payload: dict[str, object]) -> dict[str, object]:
    payload["input_manifest_hash"] = sha256_text(
        canonical_json(payload["input_manifest"])
    )
    payload["output_hash"] = sha256_text(canonical_json(payload["output"]))
    return payload


def _record_release_artifacts(
    session,
    release: EvaluationRelease,
    *,
    policy: EvaluationReleasePolicy = SMALL_POLICY,
) -> None:
    questions = {
        item.id: item
        for item in session.scalars(
            select(EvaluationQuestion).where(
                EvaluationQuestion.id.in_(
                    [row.evaluation_question_id for row in release.questions]
                )
            )
        ).all()
    }
    for release_question in release.questions:
        if release_question.inclusion_status != "included":
            continue
        question = questions[release_question.evaluation_question_id]
        for artifact_type in ("question_review", "outcome_adjudication"):
            record_procedural_ai_review_artifact(
                session,
                release,
                artifact_type=artifact_type,
                payload=_artifact_payload(
                    release,
                    release_question,
                    question,
                    artifact_type=artifact_type,
                ),
                now=REVIEW_RECORDED_AT,
                policy=policy,
            )


def _draft_release(
    session,
    *,
    counts: tuple[int, int, int] = (1, 1, 1),
    policy: EvaluationReleasePolicy = SMALL_POLICY,
    version: str = "1",
    provenance: dict[str, str] | None = None,
    row_overrides: dict[str, dict[int, dict[str, object]]] | None = None,
    excluded_ids: set[str] | None = None,
    input_mutator=None,
    correction_of_release_id: str | None = None,
    correction_summary: str | None = None,
) -> tuple[EvaluationRelease, dict[str, EvaluationDataset]]:
    datasets = {
        split: _frozen_dataset(
            session,
            split=split,
            count=count,
            version=version,
            provenance=(provenance or {}).get(
                split, "Independently curated public authoritative records."
            ),
            row_overrides=(row_overrides or {}).get(split),
        )
        for split, count in zip(
            ("development", "validation", "test"), counts, strict=True
        )
    }
    inputs = _release_inputs(datasets, excluded_ids=excluded_ids)
    if input_mutator is not None:
        input_mutator(inputs, datasets)
    release = create_evaluation_release(
        session,
        name="ForecastLab private V1 real evaluation",
        version=version,
        development_dataset_id=datasets["development"].id,
        validation_dataset_id=datasets["validation"].id,
        test_dataset_id=datasets["test"].id,
        questions=inputs,
        provider_identity=PROVIDER,
        correction_of_release_id=correction_of_release_id,
        correction_summary=correction_summary,
        now=NOW,
        policy=policy,
    )
    return release, datasets


def _review_and_freeze(session, release: EvaluationRelease, policy=SMALL_POLICY) -> None:
    if not release.procedural_review_artifacts:
        _record_release_artifacts(session, release, policy=policy)
    review_evaluation_release(session, release, now=REVIEW_RECORDED_AT, policy=policy)
    freeze_evaluation_release(session, release, now=REVIEW_RECORDED_AT, policy=policy)
    session.flush()


def test_procedural_review_requires_two_role_separated_artifacts_per_question(
    client,
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        first = release.questions[0]
        question = session.get(EvaluationQuestion, first.evaluation_question_id)
        assert question is not None
        question_review = _artifact_payload(
            release,
            first,
            question,
            artifact_type="question_review",
        )
        stored = record_procedural_ai_review_artifact(
            session,
            release,
            artifact_type="question_review",
            payload=question_review,
            now=REVIEW_RECORDED_AT,
            policy=SMALL_POLICY,
        )
        assert stored.gate_status == "passed"
        reasons = procedural_ai_review_gate_reasons(session, release)
        assert (
            f"procedural_review_artifact_missing:{question.id}:outcome_adjudication"
            in reasons
        )
        with pytest.raises(EvaluationReleaseValidationError):
            review_evaluation_release(
                session,
                release,
                now=REVIEW_RECORDED_AT,
                policy=SMALL_POLICY,
            )


def test_procedural_review_manifests_are_blind_disjoint_and_source_grounded(
    client,
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        _record_release_artifacts(session, release)
        rows = session.scalars(
            select(ProceduralAIReviewArtifactRecord).where(
                ProceduralAIReviewArtifactRecord.evaluation_release_id == release.id
            )
        ).all()
        assert len(rows) == 6
        assert len({row.run_id for row in rows}) == 6
        for row in rows:
            manifest = json.loads(row.input_manifest_json)
            output = json.loads(row.output_json)
            cited = {
                source_id
                for finding in output["findings"]
                for source_id in finding["source_ids"]
            }
            assert cited == {item["source_id"] for item in manifest["sources"]}
            if row.artifact_type == "question_review":
                for forbidden in (
                    "outcome",
                    "candidate_outcome",
                    "outcome_known_at",
                    "resolution_source",
                    "adjudication_notes",
                ):
                    assert forbidden not in manifest
            else:
                for forbidden in (
                    "question",
                    "normalized_question_hash",
                    "event_family_id",
                    "leakage_group_id",
                    "split",
                    "reserve_order",
                    "question_review_output",
                ):
                    assert forbidden not in manifest
        _review_and_freeze(session, release)
        assert release.status == "frozen"
        audit = json.loads(release.review_manifest_json or "{}")
        assert audit["label"] == PROCEDURAL_AI_RELEASE_LABEL
        assert audit["human_review_claimed"] is False
        assert audit["independent_validation_claimed"] is False
        assert audit["publication_grade_claimed"] is False


@pytest.mark.parametrize(
    ("artifact_type", "mutation", "expected"),
    [
        (
            "question_review",
            lambda payload: payload["input_manifest"].update({"outcome": 1}),
            "disallowed_data:input_manifest.outcome",
        ),
        (
            "outcome_adjudication",
            lambda payload: payload["input_manifest"].update(
                {"event_family_id": "leaked-family"}
            ),
            "disallowed_data:input_manifest.event_family_id",
        ),
        (
            "question_review",
            lambda payload: payload["input_manifest"].pop("contract_hash"),
            "required_or_schema_invalid:input_manifest.contract_hash:missing",
        ),
    ],
)
def test_role_manifest_validator_rejects_disallowed_or_missing_data(
    client,
    artifact_type: str,
    mutation,
    expected: str,
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        release_question = release.questions[0]
        question = session.get(
            EvaluationQuestion, release_question.evaluation_question_id
        )
        assert question is not None
        payload = _artifact_payload(
            release,
            release_question,
            question,
            artifact_type=artifact_type,
        )
        mutation(payload)
        _rehash_artifact(payload)
        errors = review_artifact_payload_errors(
            payload,
            artifact_type=artifact_type,  # type: ignore[arg-type]
        )
        assert expected in errors
        with pytest.raises(EvaluationReleaseValidationError) as exc_info:
            record_procedural_ai_review_artifact(
                session,
                release,
                artifact_type=artifact_type,  # type: ignore[arg-type]
                payload=payload,
                now=REVIEW_RECORDED_AT,
                policy=SMALL_POLICY,
            )
        assert expected in exc_info.value.reasons
        assert session.scalars(select(ProceduralAIReviewArtifactRecord)).all() == []


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda payload: payload["output"].update(
                {"uncertainties": ["Source interpretation remains uncertain."]}
            ),
            "artifact_uncertainty_present",
        ),
        (
            lambda payload: payload["output"].update(
                {"conflicts": ["Two source records disagree."]}
            ),
            "artifact_conflict_present",
        ),
        (
            lambda payload: payload["output"]["findings"][0].update(
                {"status": "uncertain"}
            ),
            "required_finding_not_passed",
        ),
    ],
)
def test_uncertainty_conflict_and_rubric_failure_are_preserved_and_fail_closed(
    client,
    mutation,
    expected: str,
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        release_question = release.questions[0]
        question = session.get(
            EvaluationQuestion, release_question.evaluation_question_id
        )
        assert question is not None
        payload = _artifact_payload(
            release,
            release_question,
            question,
            artifact_type="question_review",
        )
        mutation(payload)
        _rehash_artifact(payload)
        row = record_procedural_ai_review_artifact(
            session,
            release,
            artifact_type="question_review",
            payload=payload,
            now=REVIEW_RECORDED_AT,
            policy=SMALL_POLICY,
        )
        assert row.gate_status == "failed"
        assert any(
            reason.startswith(expected)
            for reason in json.loads(row.gate_reasons_json)
        )
        with pytest.raises(EvaluationReleaseValidationError):
            review_evaluation_release(
                session,
                release,
                now=REVIEW_RECORDED_AT,
                policy=SMALL_POLICY,
            )


def test_unknown_citation_and_rubric_hash_mismatch_are_rejected_before_storage(
    client,
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        release_question = release.questions[0]
        question = session.get(
            EvaluationQuestion, release_question.evaluation_question_id
        )
        assert question is not None
        payload = _artifact_payload(
            release,
            release_question,
            question,
            artifact_type="question_review",
        )
        payload["output"]["findings"][0]["source_ids"] = ["not-in-manifest"]
        _rehash_artifact(payload)
        with pytest.raises(EvaluationReleaseValidationError) as citation_error:
            record_procedural_ai_review_artifact(
                session,
                release,
                artifact_type="question_review",
                payload=payload,
                now=REVIEW_RECORDED_AT,
                policy=SMALL_POLICY,
            )
        assert any(
            reason.startswith("citation_not_in_input_manifest")
            for reason in citation_error.value.reasons
        )

        rubric_payload = _artifact_payload(
            release,
            release_question,
            question,
            artifact_type="question_review",
        )
        rubric_payload["rubric_hash"] = "0" * 64
        with pytest.raises(EvaluationReleaseValidationError) as rubric_error:
            record_procedural_ai_review_artifact(
                session,
                release,
                artifact_type="question_review",
                payload=rubric_payload,
                now=REVIEW_RECORDED_AT,
                policy=SMALL_POLICY,
            )
        assert "rubric_hash_mismatch" in rubric_error.value.reasons

        license_payload = _artifact_payload(
            release,
            release_question,
            question,
            artifact_type="question_review",
        )
        license_payload["input_manifest"]["declared_source_license_status"] = (
            "licensed"
        )
        _rehash_artifact(license_payload)
        with pytest.raises(EvaluationReleaseValidationError) as license_error:
            record_procedural_ai_review_artifact(
                session,
                release,
                artifact_type="question_review",
                payload=license_payload,
                now=REVIEW_RECORDED_AT,
                policy=SMALL_POLICY,
            )
        assert "question_review_license_mismatch" in license_error.value.reasons


def test_codex_run_id_cannot_be_reused_across_review_roles(client) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        release_question = release.questions[0]
        question = session.get(
            EvaluationQuestion, release_question.evaluation_question_id
        )
        assert question is not None
        question_payload = _artifact_payload(
            release,
            release_question,
            question,
            artifact_type="question_review",
        )
        record_procedural_ai_review_artifact(
            session,
            release,
            artifact_type="question_review",
            payload=question_payload,
            now=REVIEW_RECORDED_AT,
            policy=SMALL_POLICY,
        )
        outcome_payload = _artifact_payload(
            release,
            release_question,
            question,
            artifact_type="outcome_adjudication",
        )
        outcome_payload["run_identity"]["run_id"] = question_payload["run_identity"][
            "run_id"
        ]
        with pytest.raises(EvaluationReleaseValidationError) as exc_info:
            record_procedural_ai_review_artifact(
                session,
                release,
                artifact_type="outcome_adjudication",
                payload=outcome_payload,
                now=REVIEW_RECORDED_AT,
                policy=SMALL_POLICY,
            )
        assert exc_info.value.reasons == ["procedural_ai_review_run_id_reused"]


def test_artifacts_are_idempotent_immutable_and_conflicts_require_new_release(
    client,
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        release_question = release.questions[0]
        question = session.get(
            EvaluationQuestion, release_question.evaluation_question_id
        )
        assert question is not None
        payload = _artifact_payload(
            release,
            release_question,
            question,
            artifact_type="question_review",
        )
        first = record_procedural_ai_review_artifact(
            session,
            release,
            artifact_type="question_review",
            payload=payload,
            now=REVIEW_RECORDED_AT,
            policy=SMALL_POLICY,
        )
        repeated = record_procedural_ai_review_artifact(
            session,
            release,
            artifact_type="question_review",
            payload=copy.deepcopy(payload),
            now=REVIEW_RECORDED_AT,
            policy=SMALL_POLICY,
        )
        assert repeated.id == first.id
        conflicting = copy.deepcopy(payload)
        conflicting["output"]["findings"][0]["conclusion"] = "Changed conclusion."
        _rehash_artifact(conflicting)
        with pytest.raises(EvaluationReleaseValidationError) as conflict_error:
            record_procedural_ai_review_artifact(
                session,
                release,
                artifact_type="question_review",
                payload=conflicting,
                now=REVIEW_RECORDED_AT,
                policy=SMALL_POLICY,
            )
        assert conflict_error.value.reasons == [
            "procedural_ai_review_artifact_conflict_requires_new_release"
        ]
        first.decision = "rejected"
        with pytest.raises(
            ImmutableProceduralAIReviewError,
            match="procedural_ai_review_artifact_immutable",
        ):
            session.flush()
        session.rollback()


def test_reserve_order_is_outcome_blind_deterministic_and_precedes_adjudication(
    client,
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        development = _frozen_dataset(session, split="development", count=2)
        validation = _frozen_dataset(session, split="validation", count=1)
        test = _frozen_dataset(session, split="test", count=1)
        datasets = {
            "development": development,
            "validation": validation,
            "test": test,
        }
        excluded_id = development.questions[-1].id
        release = create_evaluation_release(
            session,
            name="Reserve-order procedural release",
            version="1",
            development_dataset_id=development.id,
            validation_dataset_id=validation.id,
            test_dataset_id=test.id,
            questions=_release_inputs(datasets, excluded_ids={excluded_id}),
            provider_identity=PROVIDER,
            now=NOW,
            policy=SMALL_POLICY,
        )
        frozen = json.loads(release.reserve_order_json or "{}")
        assert frozen["outcome_fields_used"] is False
        assert [entry["reserve_rank"] for entry in frozen["entries"]] == [1]
        assert all("outcome" not in canonical_json(entry) for entry in frozen["entries"])
        initial_hash = release.reserve_order_hash
        session.execute(
            text(
                "UPDATE evaluation_questions SET outcome = CASE outcome WHEN 1 THEN 0 ELSE 1 END"
            )
        )
        session.expire_all()
        refreshed = session.get(EvaluationRelease, release.id)
        assert refreshed is not None
        assert _reserve_order_payload(session, refreshed) == frozen
        assert refreshed.reserve_order_hash == initial_hash

        release_question = next(
            item for item in refreshed.questions if item.inclusion_status == "included"
        )
        question = session.get(
            EvaluationQuestion, release_question.evaluation_question_id
        )
        assert question is not None
        premature = _artifact_payload(
            refreshed,
            release_question,
            question,
            artifact_type="outcome_adjudication",
        )
        premature["started_at"] = (NOW - timedelta(seconds=1)).isoformat()
        premature["completed_at"] = NOW.isoformat()
        with pytest.raises(EvaluationReleaseValidationError) as exc_info:
            record_procedural_ai_review_artifact(
                session,
                refreshed,
                artifact_type="outcome_adjudication",
                payload=premature,
                now=REVIEW_RECORDED_AT,
                policy=SMALL_POLICY,
            )
        assert "outcome_adjudication_precedes_reserve_order" in exc_info.value.reasons
        session.rollback()


def test_test_split_claim_is_database_enforced_one_shot_and_immutable(client) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        _review_and_freeze(session, release)

        def experiment() -> ForecastExperiment:
            row = ForecastExperiment(
                id=str(uuid.uuid4()),
                dataset_id=release.test_dataset_id,
                evaluation_release_id=release.id,
                historical_evidence_release_id=None,
                historical_evidence_release_hash=None,
                evaluation_split="test",
                status="pending",
                profiles_json="[]",
                configuration_hash=sha256_text(str(uuid.uuid4())),
                configuration_json="{}",
            )
            session.add(row)
            session.flush()
            return row

        first_experiment = experiment()
        claim = claim_evaluation_test_split_once(
            session,
            release=release,
            experiment=first_experiment,
        )
        repeated = claim_evaluation_test_split_once(
            session,
            release=release,
            experiment=first_experiment,
        )
        assert repeated.id == claim.id
        second_experiment = experiment()
        with pytest.raises(
            ValueError,
            match="evaluation_test_split_one_shot_already_claimed",
        ):
            claim_evaluation_test_split_once(
                session,
                release=release,
                experiment=second_experiment,
            )
        claim.execution_manifest_hash = "0" * 64
        with pytest.raises(
            ImmutableProceduralAIReviewError,
            match="procedural_ai_review_artifact_immutable",
        ):
            session.flush()
        session.rollback()


def test_valid_generated_60_40_100_release_reviews_freezes_and_api_is_blinded(client) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(
            session,
            counts=(60, 40, 100),
            policy=PRIVATE_V1_REAL_EVALUATION_RELEASE_V1,
        )
        _review_and_freeze(
            session, release, policy=PRIVATE_V1_REAL_EVALUATION_RELEASE_V1
        )
        release_id = release.id
        expected_hash = release.release_hash
        session.commit()

    detail = client.get(f"/api/evaluation/releases/{release_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["status"] == "frozen"
    assert payload["release_label"] == PROCEDURAL_AI_RELEASE_LABEL
    assert payload["human_reviewed"] is False
    assert payload["independently_validated"] is False
    assert payload["release_hash"] == expected_hash
    assert payload["counts"] == {
        "development": {"included": 60, "excluded": 0},
        "validation": {"included": 40, "excluded": 0},
        "test": {"included": 100, "excluded": 0},
    }
    manifest_response = client.get(
        f"/api/evaluation/releases/{release_id}/execution-manifest"
    )
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert len(manifest["questions"]) == 200
    serialized = canonical_json(manifest)
    for forbidden in (
        '"outcome"',
        '"resolution_source"',
        '"adjudication',
        '"outcome_known_at"',
        '"brier_score"',
        '"log_loss"',
    ):
        assert forbidden not in serialized
    assert client.get("/api/evaluation/releases").json()["real_corpus_populated"] is False


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            lambda rows, _datasets: (
                setattr(rows[1], "event_family_id", rows[0].event_family_id)
            ),
            "cross_split_event_family:",
        ),
        (
            lambda rows, _datasets: (
                setattr(rows[1], "leakage_group_id", rows[0].leakage_group_id)
            ),
            "cross_split_leakage_group:",
        ),
        (
            lambda rows, _datasets: setattr(rows[0], "source_license_status", "unknown"),
            "known_source_license_required:",
        ),
        (
            lambda rows, _datasets: setattr(
                rows[0], "outcome_known_at", datetime(2019, 6, 1, tzinfo=UTC)
            ),
            "resolution_date_after_outcome_known:",
        ),
    ],
)
def test_review_rejects_leakage_licensing_and_temporal_defects(
    client,
    mutator,
    expected: str,
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session, input_mutator=mutator)
        reasons = validate_evaluation_release(session, release, policy=SMALL_POLICY)
        assert any(reason.startswith(expected) for reason in reasons), reasons
        with pytest.raises(EvaluationReleaseValidationError):
            review_evaluation_release(session, release, now=NOW, policy=SMALL_POLICY)


def test_duplicate_normalized_question_and_contract_are_rejected(client) -> None:
    from forecastlab_api import main as main_mod

    common_question = "Did the common official indicator reach 10 before 1 June 2020?"
    common_contract = {
        "yes_condition": "The common official indicator reached 10 before 2020-06-01.",
        "no_condition": "The common official indicator remained below 10 through 2020-06-01.",
        "resolution_source": "https://records.example/common",
        "authoritative_resolver": "Common official registry",
    }
    overrides = {
        "development": {0: {"question": common_question}},
        "validation": {0: {"question": common_question}},
        "test": {0: common_contract},
    }
    overrides["development"][0].update(common_contract)
    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session, row_overrides=overrides)
        reasons = validate_evaluation_release(session, release, policy=SMALL_POLICY)
        assert "duplicate_normalized_question_hash" in reasons
        assert "duplicate_resolution_contract_hash" in reasons


def test_wrong_split_count_and_non_real_dataset_fail_closed(client) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(
            session,
            policy=SMALL_POLICY.model_copy(
                update={
                    "required_included_counts": {
                        "development": 2,
                        "validation": 1,
                        "test": 1,
                    }
                }
            ),
            provenance={"development": "Synthetic fixture data."},
        )
        reasons = validate_evaluation_release(
            session,
            release,
            policy=SMALL_POLICY.model_copy(
                update={
                    "required_included_counts": {
                        "development": 2,
                        "validation": 1,
                        "test": 1,
                    }
                }
            ),
        )
        assert "included_split_count_mismatch:development:1:2" in reasons
        assert "synthetic_or_fixture_dataset_forbidden:development" in reasons


def test_unfrozen_source_dataset_is_rejected_before_release_creation(client) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        development = import_evaluation_dataset(
            session,
            name="Unfrozen development corpus",
            version="1",
            description="Historical questions pending final freeze.",
            provenance="Independently curated public authoritative records.",
            rows=[_row("development", 0)],
            now=NOW,
        )
        review_evaluation_dataset(session, development, now=NOW)
        datasets = {
            "development": development,
            "validation": _frozen_dataset(session, split="validation", count=1),
            "test": _frozen_dataset(session, split="test", count=1),
        }
        session.flush()
        with pytest.raises(EvaluationReleaseValidationError) as exc_info:
            create_evaluation_release(
                session,
                name="Unfrozen release",
                version="1",
                development_dataset_id=datasets["development"].id,
                validation_dataset_id=datasets["validation"].id,
                test_dataset_id=datasets["test"].id,
                questions=_release_inputs(datasets),
                provider_identity=PROVIDER,
                policy=SMALL_POLICY,
                now=NOW,
            )
        assert any(
            reason.startswith("source_dataset_not_frozen")
            for reason in exc_info.value.reasons
        )


def test_excluded_rows_remain_audited_but_are_not_executed_or_scored(client) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        dev = _frozen_dataset(session, split="development", count=2)
        validation = _frozen_dataset(session, split="validation", count=1)
        test = _frozen_dataset(session, split="test", count=1)
        datasets = {"development": dev, "validation": validation, "test": test}
        excluded_id = dev.questions[-1].id
        release = create_evaluation_release(
            session,
            name="Release with predeclared exclusion",
            version="1",
            development_dataset_id=dev.id,
            validation_dataset_id=validation.id,
            test_dataset_id=test.id,
            questions=_release_inputs(datasets, excluded_ids={excluded_id}),
            provider_identity=PROVIDER,
            now=NOW,
            policy=SMALL_POLICY,
        )
        _review_and_freeze(session, release)
        execution = get_blinded_execution_manifest(release)
        scoring = get_sealed_scoring_manifest(release)
        assert excluded_id not in {
            item.evaluation_question_id for item in execution.questions
        }
        assert excluded_id not in {
            item.evaluation_question_id for item in scoring.questions
        }
        excluded = next(
            item for item in release.questions if item.evaluation_question_id == excluded_id
        )
        assert excluded.inclusion_status == "excluded"
        assert excluded.exclusion_reason


def test_outcome_change_conflicts_with_immutable_adjudication(client) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        _review_and_freeze(session, release)
        original = evaluation_release_artifact_hashes(
            session, release, policy=SMALL_POLICY
        )
        question_id = release.questions[0].evaluation_question_id
        session.execute(
            text(
                "UPDATE evaluation_questions SET outcome = CASE outcome WHEN 1 THEN 0 ELSE 1 END "
                "WHERE id = :question_id"
            ),
            {"question_id": question_id},
        )
        session.expire_all()
        changed_release = session.get(EvaluationRelease, release.id)
        assert changed_release is not None
        changed = evaluation_release_artifact_hashes(
            session, changed_release, policy=SMALL_POLICY
        )
        assert changed["execution_manifest_hash"] == original["execution_manifest_hash"]
        assert changed["scoring_manifest_hash"] == original["scoring_manifest_hash"]
        assert changed["release_hash"] == original["release_hash"]
        reasons = procedural_ai_review_gate_reasons(session, changed_release)
        assert any(
            reason.startswith("outcome_adjudication_candidate_outcome_mismatch")
            for reason in reasons
        )
        session.rollback()


@pytest.mark.parametrize("field", ["question", "resolution_contract"])
def test_question_or_contract_change_alters_execution_and_release_hashes(
    client,
    field: str,
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        _review_and_freeze(session, release)
        original = evaluation_release_artifact_hashes(
            session, release, policy=SMALL_POLICY
        )
        question_id = release.questions[0].evaluation_question_id
        if field == "question":
            value = "Did the changed official indicator reach 999 before 1 June 2020?"
        else:
            question = session.get(EvaluationQuestion, question_id)
            assert question is not None
            contract = json.loads(question.resolution_contract)
            contract["yes_condition"] += " The changed threshold is authoritative."
            value = canonical_json(contract)
        session.execute(
            text(f"UPDATE evaluation_questions SET {field} = :value WHERE id = :question_id"),
            {"value": value, "question_id": question_id},
        )
        session.expire_all()
        changed_release = session.get(EvaluationRelease, release.id)
        assert changed_release is not None
        changed = evaluation_release_artifact_hashes(
            session, changed_release, policy=SMALL_POLICY
        )
        assert changed["execution_manifest_hash"] != original["execution_manifest_hash"]
        assert changed["release_hash"] != original["release_hash"]
        session.rollback()


def test_worker_dto_cannot_deserialize_outcome_or_scoring_fields(client) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        _review_and_freeze(session, release)
        question = get_blinded_execution_manifest(release).questions[0]
        payload = question.model_dump(mode="json")
        payload["outcome"] = 1
        with pytest.raises(ValidationError):
            BlindedEvaluationQuestion.model_validate(payload)


def test_release_experiment_assigns_blinded_questions_and_scores_only_terminal_run(
    client,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from forecastlab_api import forecast_experiments as module
    from forecastlab_api import main as main_mod
    from forecastlab_api.config import settings

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        _review_and_freeze(session, release)

        def frozen_configuration(**kwargs):
            blinded = kwargs["blinded_questions"]
            assert blinded and all(
                "outcome" not in item.model_dump(mode="json") for item in blinded
            )
            return {
                "schema_version": 1,
                "dataset": {
                    "id": kwargs["dataset"].id,
                    "hash": kwargs["dataset"].hash,
                },
                "questions": [item.model_dump(mode="json") for item in blinded],
                "profiles": {},
                "profile_ids": kwargs["profile_ids"],
                "provider": {
                    "model_provider": "mock",
                    "model": "mock-forecast-v1",
                    "search_provider": "mock",
                },
                "common_budget": {},
                "prompts": {},
                "pricing": {},
                "code": {},
                "synthetic_test": False,
                "evaluation_release": kwargs["evaluation_release"],
                "historical_evidence_release": kwargs[
                    "historical_evidence_release"
                ],
            }

        monkeypatch.setattr(module, "_freeze_configuration", frozen_configuration)
        monkeypatch.setattr(module, "working_tree_dirty", lambda: False)
        monkeypatch.setattr(settings, "historical_evidence_bundle_root", tmp_path)
        blinded = get_blinded_execution_manifest(release)
        evidence_release = create_historical_evidence_release(
            session,
            evaluation_release_id=release.id,
            name="Generated no-evidence corpus",
            version="1",
            documents=[],
            packets=[
                HistoricalEvidencePacketInput(
                    evaluation_question_id=item.evaluation_question_id,
                    split=item.split,
                    evidence_cutoff=item.evidence_cutoff,
                    status="no_eligible_evidence",
                    collector_id=f"collector:{item.split}",
                    reviewer_id=f"reviewer:{item.split}",
                    reviewed_at=NOW,
                    searches=[{"query": f"archive search {item.split}"}],
                    archive_checks=[{"status": "no_capture_before_cutoff"}],
                    rejection_reasons=["no_verified_pre_cutoff_capture"],
                    candidates=[
                        HistoricalEvidenceCandidateInput(
                            canonical_url=(
                                f"https://archive.example/{item.evaluation_question_id}"
                            ),
                            source_url=(
                                f"https://archive.example/{item.evaluation_question_id}"
                            ),
                            rank=1,
                            search_query=f"archive search {item.split}",
                            search_provider="offline_fixture",
                            status="rejected",
                            rejection_reason="no_verified_pre_cutoff_capture",
                            archive_check_status="no_capture_before_cutoff",
                        )
                    ],
                )
                for item in blinded.questions
            ],
            now=NOW,
        )
        review_historical_evidence_release(
            session, evidence_release, bundle_root=tmp_path, now=NOW
        )
        freeze_historical_evidence_release(
            session, evidence_release, bundle_root=tmp_path, now=NOW
        )
        experiment = create_forecast_experiment(
            session,
            dataset_id=None,
            profile_ids=list(module.CONTROLLED_FORECAST_PROFILES),
            synthetic_test=False,
            evaluation_release_id=release.id,
            evaluation_split="development",
            historical_evidence_release_id=evidence_release.id,
        )
        session.flush()
        configuration = json.loads(experiment.configuration_json)
        assert configuration["evaluation_release"]["preregistration_hash"]
        assert all("outcome" not in row for row in configuration["questions"])
        runs = session.scalars(
            select(ForecastExperimentRun).where(
                ForecastExperimentRun.experiment_id == experiment.id
            )
        ).all()
        assert len(runs) == 3
        assert {item.evaluation_question_id for item in runs} == {
            get_blinded_execution_manifest(release).questions[0].evaluation_question_id
        }
        assert session.scalars(select(ForecastRun)).all() == []
        assert session.scalars(select(ProviderCallLedger)).all() == []
        with pytest.raises(RuntimeError, match="terminal"):
            _sealed_outcome_for_terminal_run(
                session,
                experiment=experiment,
                experiment_run=runs[0],
                forecast_run=None,
            )
        runs[0].status = "failed"
        assert _sealed_outcome_for_terminal_run(
            session,
            experiment=experiment,
            experiment_run=runs[0],
            forecast_run=None,
        ) in (0, 1)


def test_production_dataset_only_experiment_fails_closed(client) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        dataset = _frozen_dataset(session, split="development", count=1)
        with pytest.raises(ValueError, match="production_evaluation_release_required"):
            create_forecast_experiment(
                session,
                dataset_id=dataset.id,
                profile_ids=[
                    "single_model_forecaster_v1",
                    "three_track_forecaster",
                    "graph_forecaster_v1",
                ],
                synthetic_test=False,
            )


def test_frozen_release_is_immutable_and_freeze_is_idempotent(client) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        _review_and_freeze(session, release)
        frozen_at = release.frozen_at
        assert freeze_evaluation_release(
            session, release, now=NOW, policy=SMALL_POLICY
        ).id == release.id
        assert release.frozen_at == frozen_at
        release.correction_summary = "Outcome-aware rewrite"
        with pytest.raises(
            FrozenEvaluationReleaseError,
            match="frozen_evaluation_release_immutable",
        ):
            session.flush()
        session.rollback()


def test_same_version_conflict_and_versioned_correction(client) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, datasets = _draft_release(session)
        inputs = _release_inputs(datasets)
        same = create_evaluation_release(
            session,
            name=release.name,
            version=release.version,
            development_dataset_id=release.development_dataset_id,
            validation_dataset_id=release.validation_dataset_id,
            test_dataset_id=release.test_dataset_id,
            questions=inputs,
            provider_identity=PROVIDER,
            now=NOW,
            policy=SMALL_POLICY,
        )
        assert same.id == release.id
        with pytest.raises(EvaluationReleaseValidationError) as conflict:
            create_evaluation_release(
                session,
                name=release.name,
                version=release.version,
                development_dataset_id=release.development_dataset_id,
                validation_dataset_id=release.validation_dataset_id,
                test_dataset_id=release.test_dataset_id,
                questions=inputs,
                provider_identity=PROVIDER.model_copy(update={"model": "changed"}),
                now=NOW,
                policy=SMALL_POLICY,
            )
        assert conflict.value.reasons == ["evaluation_release_version_conflict"]
        _review_and_freeze(session, release)
        correction = create_evaluation_release(
            session,
            name=release.name,
            version="2",
            development_dataset_id=release.development_dataset_id,
            validation_dataset_id=release.validation_dataset_id,
            test_dataset_id=release.test_dataset_id,
            questions=inputs,
            provider_identity=PROVIDER,
            correction_of_release_id=release.id,
            correction_summary="Corrected adjudication metadata under a new version.",
            now=NOW,
            policy=SMALL_POLICY,
        )
        assert correction.correction_of_release_id == release.id
        assert correction.version == "2"
        with pytest.raises(EvaluationReleaseValidationError) as same_version:
            create_evaluation_release(
                session,
                name=release.name,
                version="1",
                development_dataset_id=release.development_dataset_id,
                validation_dataset_id=release.validation_dataset_id,
                test_dataset_id=release.test_dataset_id,
                questions=inputs,
                provider_identity=PROVIDER,
                correction_of_release_id=release.id,
                correction_summary="Invalid same-version correction.",
                now=NOW,
                policy=SMALL_POLICY,
            )
        assert "correction_requires_new_version" in same_version.value.reasons


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("source_code_sha", "preregistration_source_code_sha_mismatch"),
        ("dependency_lock_hash", "preregistration_dependency_lock_hash_mismatch"),
        ("package_lock_hash", "preregistration_package_lock_hash_mismatch"),
    ],
)
def test_source_and_lock_drift_fail_review(
    client,
    field: str,
    expected: str,
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        prereg = json.loads(release.preregistration_json)
        prereg[field] = "0" * 64
        release.preregistration_json = canonical_json(prereg)
        session.flush()
        reasons = validate_evaluation_release(session, release, policy=SMALL_POLICY)
        assert expected in reasons


def test_profile_and_prompt_drift_fail_environment_recreation(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from forecastlab_api import evaluation_releases as module
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release, _datasets = _draft_release(session)
        real_loader = module.load_profile
        monkeypatch.setattr(
            module,
            "load_profile",
            lambda profile_id: real_loader(profile_id).model_copy(
                update={"version": real_loader(profile_id).version + 1}
            ),
        )
        with pytest.raises(EvaluationReleaseValidationError) as profile_error:
            assert_release_environment(release)
        assert any(
            reason.startswith("profile_version_mismatch")
            for reason in profile_error.value.reasons
        )
        monkeypatch.setattr(module, "load_profile", real_loader)
        real_bundle = module.load_prompt_bundle()
        changed_prompts = dict(real_bundle.prompts)
        first_name = sorted(changed_prompts)[0]
        changed_prompts[first_name] = changed_prompts[first_name].model_copy(
            update={"sha256": "0" * 64}
        )
        monkeypatch.setattr(
            module,
            "load_prompt_bundle",
            lambda: PromptBundle(prompts=changed_prompts),
        )
        with pytest.raises(EvaluationReleaseValidationError) as prompt_error:
            assert_release_environment(release)
        assert "prompt_hashes_mismatch" in prompt_error.value.reasons


def test_existing_pilot_is_unchanged_and_cannot_satisfy_release_policy(
    client,
) -> None:
    from forecastlab_api import main as main_mod
    from forecastlab_api.pilot_benchmark import import_pilot_benchmark

    path = Path("fixtures/benchmarks/pilot_v1.csv")
    assert sha256_text(path.read_text(encoding="utf-8")) == (
        "7e9c20ae94401591d8fdcf67b536782674d6faf9e54f1054a799d78b1a048fa2"
    )
    with main_mod.SessionLocal() as session:
        pilot = import_pilot_benchmark(session, now=NOW)
        validation = _frozen_dataset(session, split="validation", count=40)
        test = _frozen_dataset(session, split="test", count=100)
        datasets = {"development": pilot, "validation": validation, "test": test}
        release = create_evaluation_release(
            session,
            name="Pilot cannot be production release",
            version="1",
            development_dataset_id=pilot.id,
            validation_dataset_id=validation.id,
            test_dataset_id=test.id,
            questions=_release_inputs(datasets),
            provider_identity=PROVIDER,
            now=NOW,
        )
        reasons = validate_evaluation_release(session, release)
        assert "included_split_count_mismatch:development:20:60" in reasons
