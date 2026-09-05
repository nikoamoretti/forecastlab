from __future__ import annotations

import csv
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from forecastlab_api.evaluation_datasets import (
    EvaluationDatasetValidationError,
    evaluation_dataset_hash,
)
from forecastlab_api.models import EvaluationQuestion, FrozenEvaluationDatasetError
from forecastlab_api.pilot_benchmark import (
    PILOT_DATASET_DESCRIPTION,
    PILOT_DATASET_NAME,
    PILOT_DATASET_PROVENANCE,
    PILOT_DATASET_VERSION,
    PILOT_DOMAIN_COUNTS,
    PILOT_FIXTURE_PATH,
    PILOT_OUTCOME_COUNTS,
    import_pilot_benchmark,
    load_pilot_rows,
    validate_pilot_release,
)

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def test_pilot_fixture_has_frozen_shape_mix_and_resolution_windows() -> None:
    with PILOT_FIXTURE_PATH.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == [
            "question",
            "yes_condition",
            "no_condition",
            "forecast_date",
            "resolution_date",
            "outcome",
            "resolution_source",
            "domain",
            "category",
            "authoritative_resolver",
        ]

    canonical = validate_pilot_release(load_pilot_rows(), now=NOW)

    assert len(canonical) == 20
    assert Counter(item["domain"] for item in canonical) == PILOT_DOMAIN_COUNTS
    assert Counter(item["outcome"] for item in canonical) == PILOT_OUTCOME_COUNTS
    assert len({item["resolution_source"] for item in canonical}) == 20
    assert all(item["category"] for item in canonical)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("outcome", "", "row_1:outcome_required"),
        ("authoritative_resolver", "", "row_1:authoritative_resolver_required"),
        ("outcome", "yes", "row_1:invalid_outcome"),
        ("question", "Will AI change everything?", "row_1:ambiguous_question"),
    ],
)
def test_pilot_validation_rejects_invalid_rows(field: str, value: str, reason: str) -> None:
    rows = deepcopy(load_pilot_rows())
    rows[0][field] = value

    with pytest.raises(EvaluationDatasetValidationError) as exc_info:
        validate_pilot_release(rows, now=NOW)

    assert reason in exc_info.value.reasons


def test_pilot_hash_is_reproducible_and_covers_category() -> None:
    rows = load_pilot_rows()
    canonical = validate_pilot_release(rows, now=NOW)
    digest = evaluation_dataset_hash(
        name=PILOT_DATASET_NAME,
        version=PILOT_DATASET_VERSION,
        description=PILOT_DATASET_DESCRIPTION,
        provenance=PILOT_DATASET_PROVENANCE,
        questions=canonical,
    )
    reversed_digest = evaluation_dataset_hash(
        name=PILOT_DATASET_NAME,
        version=PILOT_DATASET_VERSION,
        description=PILOT_DATASET_DESCRIPTION,
        provenance=PILOT_DATASET_PROVENANCE,
        questions=list(reversed(canonical)),
    )
    changed_rows = deepcopy(rows)
    changed_rows[0]["category"] = "reclassified"
    changed_digest = evaluation_dataset_hash(
        name=PILOT_DATASET_NAME,
        version=PILOT_DATASET_VERSION,
        description=PILOT_DATASET_DESCRIPTION,
        provenance=PILOT_DATASET_PROVENANCE,
        questions=validate_pilot_release(changed_rows, now=NOW),
    )

    assert len(digest) == 64
    assert digest == reversed_digest
    assert digest != changed_digest


def test_pilot_import_workflow_freezes_and_is_idempotent(client) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        dataset = import_pilot_benchmark(session, now=NOW)
        session.commit()
        dataset_id = dataset.id
        dataset_hash = dataset.hash
        assert dataset.status == "frozen"
        assert dataset.question_count == 20
        assert dataset.frozen_at == NOW
        assert {question.category for question in dataset.questions} == {
            item["category"] for item in validate_pilot_release(load_pilot_rows(), now=NOW)
        }

    with main_mod.SessionLocal() as session:
        repeated = import_pilot_benchmark(session, now=NOW)
        session.commit()
        assert repeated.id == dataset_id
        assert repeated.hash == dataset_hash
        assert repeated.status == "frozen"

    with main_mod.SessionLocal() as session:
        question = session.scalar(
            select(EvaluationQuestion).where(EvaluationQuestion.dataset_id == dataset_id)
        )
        assert question is not None
        question.category = "outcome_aware_reclassification"
        with pytest.raises(
            FrozenEvaluationDatasetError,
            match="frozen_evaluation_dataset_immutable",
        ):
            session.flush()
        session.rollback()
