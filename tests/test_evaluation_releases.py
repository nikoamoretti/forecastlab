from __future__ import annotations

import json
from datetime import UTC, datetime
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
from forecastlab.prompts import PromptBundle
from forecastlab_api.evaluation_datasets import (
    freeze_evaluation_dataset,
    import_evaluation_dataset,
    review_evaluation_dataset,
)
from forecastlab_api.evaluation_releases import (
    EvaluationReleaseValidationError,
    assert_release_environment,
    create_evaluation_release,
    evaluation_release_artifact_hashes,
    freeze_evaluation_release,
    get_blinded_execution_manifest,
    get_sealed_scoring_manifest,
    review_evaluation_release,
    validate_evaluation_release,
)
from forecastlab_api.forecast_experiments import (
    _sealed_outcome_for_terminal_run,
    create_forecast_experiment,
)
from forecastlab_api.models import (
    EvaluationDataset,
    EvaluationQuestion,
    EvaluationRelease,
    ForecastExperimentRun,
    ForecastRun,
    FrozenEvaluationReleaseError,
    ProviderCallLedger,
)

NOW = datetime(2026, 8, 26, tzinfo=UTC)
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
                    question_reviewer_id=f"reviewer_{split}_{index}",
                    outcome_adjudicator_id=f"adjudicator_{split}_{index}",
                    review_completed_at=datetime(2020, 6, 2, tzinfo=UTC),
                    outcome_known_at=datetime(2020, 6, 2, tzinfo=UTC),
                    source_license_status="public_domain",
                    source_use_basis="Official public metadata and resolution record.",
                    redistribution_allowed=False,
                    adjudication_notes="Outcome independently checked against the resolver.",
                    adjudication_record_hash=sha256_text(
                        f"adjudication:{split}:{index}"
                    ),
                )
            )
    return result


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
    review_evaluation_release(session, release, now=NOW, policy=policy)
    freeze_evaluation_release(session, release, now=NOW, policy=policy)
    session.flush()


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
            lambda rows, _datasets: setattr(rows[0], "question_reviewer_id", None),
            "question_reviewer_id_required_or_not_opaque:",
        ),
        (
            lambda rows, _datasets: setattr(rows[0], "outcome_adjudicator_id", None),
            "outcome_adjudicator_id_required_or_not_opaque:",
        ),
        (
            lambda rows, _datasets: setattr(
                rows[0], "outcome_adjudicator_id", rows[0].question_reviewer_id
            ),
            "reviewer_adjudicator_must_differ:",
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
def test_review_rejects_leakage_review_licensing_and_temporal_defects(
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


def test_outcome_changes_only_scoring_and_release_hashes(client) -> None:
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
        assert changed["scoring_manifest_hash"] != original["scoring_manifest_hash"]
        assert changed["release_hash"] != original["release_hash"]
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
) -> None:
    from forecastlab_api import forecast_experiments as module
    from forecastlab_api import main as main_mod

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
            }

        monkeypatch.setattr(module, "_freeze_configuration", frozen_configuration)
        monkeypatch.setattr(module, "working_tree_dirty", lambda: False)
        experiment = create_forecast_experiment(
            session,
            dataset_id=None,
            profile_ids=list(module.CONTROLLED_FORECAST_PROFILES),
            synthetic_test=False,
            evaluation_release_id=release.id,
            evaluation_split="development",
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
