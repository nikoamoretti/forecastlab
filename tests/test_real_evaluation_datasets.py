from __future__ import annotations

import csv
import io
import json
import uuid
from copy import deepcopy

import pytest
from sqlalchemy import func, select

from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.timeutil import parse_datetime
from forecastlab_api.models import (
    EvaluationDataset,
    EvaluationQuestion,
    FrozenEvaluationDatasetError,
)


def _resolved_row(**overrides) -> dict[str, object]:
    row: dict[str, object] = {
        "question": "Did NASA launch Artemis I before 1 December 2022?",
        "yes_condition": "NASA's Artemis I mission launched before 2022-12-01 00:00 UTC.",
        "no_condition": "NASA's Artemis I mission did not launch before 2022-12-01 00:00 UTC.",
        "forecast_date": "2022-01-01T00:00:00Z",
        "resolution_date": "2022-12-01T00:00:00Z",
        "outcome": 1,
        "resolution_source": "NASA Artemis I mission record",
        "domain": "space",
        "category": "launches",
    }
    row.update(overrides)
    return row


def _second_resolved_row() -> dict[str, object]:
    return {
        "question": "Did Argentina win the 2022 FIFA World Cup?",
        "yes_condition": "Argentina was recorded by FIFA as the winner of the 2022 men's World Cup final.",
        "no_condition": "Argentina was not recorded by FIFA as the winner of the 2022 men's World Cup final.",
        "forecast_date": "2022-11-01T00:00:00Z",
        "resolution_date": "2022-12-19T00:00:00Z",
        "outcome": 1,
        "resolution_source": "FIFA official 2022 World Cup results",
        "domain": "sports",
        "category": "tournaments",
    }


def _csv_bytes(rows: list[dict[str, object]]) -> bytes:
    stream = io.StringIO()
    writer = csv.DictWriter(
        stream,
        fieldnames=[
            "question",
            "yes_condition",
            "no_condition",
            "forecast_date",
            "resolution_date",
            "outcome",
            "resolution_source",
            "domain",
            "category",
        ],
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def _import_csv(client, rows: list[dict[str, object]], **metadata):
    data = {
        "name": "Resolved questions",
        "version": "1.0.0",
        "description": "Reviewed historical questions for framework tests.",
        "provenance": "Official NASA and FIFA resolution records.",
        **metadata,
    }
    return client.post(
        "/api/evaluation/datasets/import",
        data=data,
        files={"file": ("resolved.csv", _csv_bytes(rows), "text/csv")},
    )


def test_valid_real_dataset_csv_import_and_read_apis(client) -> None:
    response = _import_csv(client, [_resolved_row()])

    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "Resolved questions"
    assert payload["version"] == "1.0.0"
    assert payload["status"] == "frozen"
    assert payload["frozen_at"] is not None
    assert payload["question_count"] == 1
    assert len(payload["dataset_hash"]) == 64
    assert payload["questions"][0]["outcome"] == 1
    assert payload["questions"][0]["resolution_contract"] == {
        "authoritative_source": "NASA Artemis I mission record",
        "forecast_type": "binary",
        "no_condition": "NASA's Artemis I mission did not launch before 2022-12-01 00:00 UTC.",
        "resolution_date": "2022-12-01T00:00:00+00:00",
        "schema_version": 1,
        "yes_condition": "NASA's Artemis I mission launched before 2022-12-01 00:00 UTC.",
    }

    listed = client.get("/api/evaluation/datasets")
    assert listed.status_code == 200
    assert listed.json()["execution_supported"] is False
    assert listed.json()["datasets"] == [{key: value for key, value in payload.items() if key != "questions"}]

    detail = client.get(f"/api/evaluation/datasets/{payload['id']}")
    assert detail.status_code == 200
    assert detail.json() == payload


def test_valid_real_dataset_json_import(client) -> None:
    raw = json.dumps({"questions": [_resolved_row()]})
    response = client.post(
        "/api/evaluation/datasets/import",
        data={"name": "JSON release", "version": "2022.1", "provenance": "Official record review."},
        files={"file": ("resolved.json", raw.encode(), "application/json")},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "frozen"
    assert response.json()["question_count"] == 1


@pytest.mark.parametrize("outcome", [None, "", 2, -1, 0.5, "yes"])
def test_invalid_or_missing_outcome_is_rejected_atomically(client, outcome) -> None:
    response = _import_csv(client, [_resolved_row(outcome=outcome)])

    assert response.status_code == 400
    reasons = response.json()["detail"]["reasons"]
    assert any(reason.endswith("outcome_required") or reason.endswith("invalid_outcome") for reason in reasons)
    assert client.get("/api/evaluation/datasets").json()["datasets"] == []


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("resolution_date", "resolution_date_required"),
        ("resolution_source", "resolution_source_required"),
        ("yes_condition", "yes_condition_required"),
        ("no_condition", "no_condition_required"),
    ],
)
def test_missing_resolution_fields_are_rejected(client, field: str, reason: str) -> None:
    response = _import_csv(client, [_resolved_row(**{field: ""})])

    assert response.status_code == 400
    assert f"row_1:{reason}" in response.json()["detail"]["reasons"]


def test_future_resolution_and_ambiguous_question_are_rejected(client) -> None:
    future = _resolved_row(
        forecast_date="2998-01-01T00:00:00Z",
        resolution_date="2999-01-01T00:00:00Z",
    )
    future_response = _import_csv(client, [future])
    assert future_response.status_code == 400
    assert "row_1:future_resolution_date" in future_response.json()["detail"]["reasons"]

    vague = _resolved_row(question="Will AI change everything?")
    vague_response = _import_csv(client, [vague], version="1.0.1")
    assert vague_response.status_code == 400
    assert "row_1:ambiguous_question" in vague_response.json()["detail"]["reasons"]


def test_dataset_hash_is_order_independent_and_reimport_is_idempotent(client) -> None:
    rows = [_resolved_row(), _second_resolved_row()]
    first = _import_csv(client, rows)
    second = _import_csv(client, list(reversed(rows)))

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["dataset_hash"] == second.json()["dataset_hash"]
    assert first.json()["question_count"] == 2

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(EvaluationDataset)) == 1
        assert session.scalar(select(func.count()).select_from(EvaluationQuestion)) == 2


def test_dataset_versioning_requires_new_identity_for_changes(client) -> None:
    first = _import_csv(client, [_resolved_row()])
    second = _import_csv(client, [_resolved_row()], version="2.0.0")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["dataset_hash"] != second.json()["dataset_hash"]

    changed = deepcopy(_resolved_row())
    changed["category"] = "spaceflight"
    conflict = _import_csv(client, [changed])
    assert conflict.status_code == 400
    assert conflict.json()["detail"]["reasons"] == ["dataset_version_conflict"]


def test_frozen_dataset_and_questions_are_immutable(client) -> None:
    imported = _import_csv(client, [_resolved_row()]).json()
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        dataset = session.get(EvaluationDataset, imported["id"])
        assert dataset is not None
        dataset.description = "Outcome-aware edit"
        with pytest.raises(FrozenEvaluationDatasetError, match="frozen_evaluation_dataset_immutable"):
            session.flush()
        session.rollback()

    with main_mod.SessionLocal() as session:
        question = session.scalar(
            select(EvaluationQuestion).where(EvaluationQuestion.dataset_id == imported["id"])
        )
        assert question is not None
        question.outcome = 0
        with pytest.raises(FrozenEvaluationDatasetError, match="frozen_evaluation_dataset_immutable"):
            session.flush()
        session.rollback()

    with main_mod.SessionLocal() as session:
        new_question_payload = _second_resolved_row()
        question_hash = sha256_text(canonical_json(new_question_payload))
        session.add(
            EvaluationQuestion(
                id=str(uuid.uuid4()),
                dataset_id=imported["id"],
                question_text=str(new_question_payload["question"]),
                normalized_question=str(new_question_payload["question"]),
                resolution_contract="{}",
                forecast_date=parse_datetime(str(new_question_payload["forecast_date"])),
                resolution_date=parse_datetime(str(new_question_payload["resolution_date"])),
                outcome=1,
                resolution_source=str(new_question_payload["resolution_source"]),
                category="tournaments",
                domain="sports",
                question_hash=question_hash,
            )
        )
        with pytest.raises(FrozenEvaluationDatasetError, match="frozen_evaluation_dataset_immutable"):
            session.flush()
        session.rollback()


def test_real_evaluation_template_is_header_only() -> None:
    from pathlib import Path

    template = Path("fixtures/benchmarks/real_evaluation_template.csv").read_text(encoding="utf-8")
    assert template.splitlines() == [
        "question,yes_condition,no_condition,forecast_date,resolution_date,outcome,resolution_source,domain,category"
    ]
