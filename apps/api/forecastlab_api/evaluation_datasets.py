from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.timeutil import as_utc, parse_datetime, utcnow
from forecastlab_api.models import EvaluationDataset, EvaluationQuestion

EVALUATION_SCHEMA_VERSION = 1
EVALUATION_DATASET_STATUSES = ("draft", "reviewed", "frozen")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_BINARY_QUESTION = re.compile(
    r"\b(will|would|does|do|did|is|are|was|were|has|have|can|could|should)\b",
    re.IGNORECASE,
)
_VAGUE_QUESTION_PHRASES = (
    "change everything",
    "do well",
    "be successful",
    "get better",
    "make things better",
    "have a big impact",
    "transform society",
)
_GENERIC_CONDITIONS = {
    "yes",
    "no",
    "true",
    "false",
    "it happens",
    "it does not happen",
    "the event happens",
    "the event does not happen",
}


class EvaluationDatasetValidationError(ValueError):
    """A dataset file, row, or lifecycle transition failed closed."""

    def __init__(self, reasons: list[str], message: str = "Evaluation dataset is invalid") -> None:
        self.reasons = list(dict.fromkeys(reasons))
        super().__init__(message)


def parse_evaluation_dataset_file(raw: str, filename: str) -> list[dict[str, Any]]:
    """Parse a CSV or JSON artifact without persisting partial rows."""

    stripped = raw.lstrip()
    if filename.lower().endswith(".json") or stripped.startswith(("[", "{")):
        payload = json.loads(raw)
        if isinstance(payload, dict):
            if "questions" in payload:
                payload = payload["questions"]
            elif "rows" in payload:
                payload = payload["rows"]
            else:
                payload = [payload]
        if not isinstance(payload, list):
            raise EvaluationDatasetValidationError(["json_questions_array_required"])
        if not all(isinstance(item, dict) for item in payload):
            raise EvaluationDatasetValidationError(["json_question_objects_required"])
        return [dict(item) for item in payload]
    return [dict(item) for item in csv.DictReader(io.StringIO(raw))]


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _required_text(row: dict[str, Any], field: str) -> str:
    value = _normalize_text(row.get(field))
    if not value:
        raise EvaluationDatasetValidationError([f"{field}_required"])
    return value


def _parse_binary_outcome(value: Any) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise EvaluationDatasetValidationError(["outcome_required"])
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    if isinstance(value, str) and value.strip() in {"0", "1"}:
        return int(value.strip())
    raise EvaluationDatasetValidationError(["invalid_outcome"])


def _parse_required_date(row: dict[str, Any], field: str) -> datetime:
    value = parse_datetime(str(row.get(field) or ""))
    if value is None:
        raise EvaluationDatasetValidationError([f"{field}_required"])
    return as_utc(value)


def _resolution_ambiguity_reasons(
    question: str,
    yes_condition: str,
    no_condition: str,
) -> list[str]:
    reasons: list[str] = []
    lowered_question = question.casefold()
    if len(question) < 12 or not _BINARY_QUESTION.search(question):
        reasons.append("ambiguous_question")
    if any(phrase in lowered_question for phrase in _VAGUE_QUESTION_PHRASES):
        reasons.append("ambiguous_question")
    lowered_yes = yes_condition.casefold().strip(" .")
    lowered_no = no_condition.casefold().strip(" .")
    if (
        len(yes_condition) < 12
        or len(no_condition) < 12
        or lowered_yes in _GENERIC_CONDITIONS
        or lowered_no in _GENERIC_CONDITIONS
        or lowered_yes == lowered_no
    ):
        reasons.append("ambiguous_resolution_rule")
    return list(dict.fromkeys(reasons))


def canonical_evaluation_question(
    row: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate and canonicalize one resolved historical binary question."""

    question = _required_text(row, "question")
    yes_condition = _required_text(row, "yes_condition")
    no_condition = _required_text(row, "no_condition")
    resolution_source = _required_text(row, "resolution_source")
    authoritative_resolver = _required_text(row, "authoritative_resolver")
    forecast_date = _parse_required_date(row, "forecast_date")
    resolution_date = _parse_required_date(row, "resolution_date")
    outcome = _parse_binary_outcome(row.get("outcome"))
    reasons = _resolution_ambiguity_reasons(question, yes_condition, no_condition)
    if forecast_date >= resolution_date:
        reasons.append("forecast_date_must_precede_resolution_date")
    if resolution_date > as_utc(now or utcnow()):
        reasons.append("future_resolution_date")
    if reasons:
        raise EvaluationDatasetValidationError(reasons)

    contract = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "forecast_type": "binary",
        "yes_condition": yes_condition,
        "no_condition": no_condition,
        "resolution_date": resolution_date.isoformat(),
        "authoritative_resolver": authoritative_resolver,
        "resolution_source": resolution_source,
    }
    canonical: dict[str, Any] = {
        "question": question,
        "resolution_contract": contract,
        "forecast_date": forecast_date.isoformat(),
        "resolution_date": resolution_date.isoformat(),
        "outcome": outcome,
        "resolution_source": resolution_source,
        "domain": _normalize_text(row.get("domain")) or "general",
    }
    canonical["question_hash"] = sha256_text(canonical_json(canonical))
    return canonical


def evaluation_dataset_hash(
    *,
    name: str,
    version: str,
    description: str,
    provenance: str,
    questions: list[dict[str, Any]],
) -> str:
    payload = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "name": name,
        "version": version,
        "description": description,
        "provenance": provenance,
        "questions": sorted(questions, key=lambda item: str(item["question_hash"])),
    }
    return sha256_text(canonical_json(payload))


def _validate_dataset_metadata(
    *,
    name: str,
    version: str,
    provenance: str,
    question_count: int,
) -> None:
    reasons: list[str] = []
    if not name:
        reasons.append("dataset_name_required")
    if len(name) > 255:
        reasons.append("dataset_name_too_long")
    if not _VERSION_PATTERN.fullmatch(version):
        reasons.append("invalid_dataset_version")
    if not provenance:
        reasons.append("dataset_provenance_required")
    if question_count <= 0:
        reasons.append("dataset_questions_required")
    if reasons:
        raise EvaluationDatasetValidationError(reasons)


def import_evaluation_dataset(
    session: Session,
    *,
    name: str,
    version: str,
    description: str,
    provenance: str,
    rows: list[dict[str, Any]],
    now: datetime | None = None,
) -> EvaluationDataset:
    """Atomically validate and persist a draft dataset release."""

    normalized_name = _normalize_text(name)
    normalized_version = version.strip()
    normalized_description = _normalize_text(description)
    normalized_provenance = _normalize_text(provenance)
    _validate_dataset_metadata(
        name=normalized_name,
        version=normalized_version,
        provenance=normalized_provenance,
        question_count=len(rows),
    )

    canonical_questions: list[dict[str, Any]] = []
    row_reasons: list[str] = []
    for index, row in enumerate(rows, start=1):
        try:
            canonical_questions.append(canonical_evaluation_question(row, now=now))
        except EvaluationDatasetValidationError as exc:
            row_reasons.extend(f"row_{index}:{reason}" for reason in exc.reasons)
    question_hashes = [str(item["question_hash"]) for item in canonical_questions]
    question_texts = [str(item["question"]).casefold() for item in canonical_questions]
    if len(question_hashes) != len(set(question_hashes)) or len(question_texts) != len(
        set(question_texts)
    ):
        row_reasons.append("duplicate_evaluation_question")
    if row_reasons:
        raise EvaluationDatasetValidationError(row_reasons)

    digest = evaluation_dataset_hash(
        name=normalized_name,
        version=normalized_version,
        description=normalized_description,
        provenance=normalized_provenance,
        questions=canonical_questions,
    )
    existing = session.scalar(
        select(EvaluationDataset).where(
            EvaluationDataset.name == normalized_name,
            EvaluationDataset.version == normalized_version,
        )
    )
    if existing is not None:
        if existing.hash != digest:
            raise EvaluationDatasetValidationError(["dataset_version_conflict"])
        stored = _canonical_dataset_questions(existing, now=now)
        stored_digest = evaluation_dataset_hash(
            name=existing.name,
            version=existing.version,
            description=existing.description,
            provenance=existing.provenance,
            questions=[item for _, item in stored],
        )
        hashes_match = all(
            question.question_hash == str(item["question_hash"]) for question, item in stored
        )
        if (
            stored_digest != digest
            or existing.question_count != len(stored)
            or not hashes_match
        ):
            raise EvaluationDatasetValidationError(["dataset_version_conflict"])
        return existing

    dataset = EvaluationDataset(
        id=str(uuid.uuid4()),
        name=normalized_name,
        version=normalized_version,
        hash=digest,
        description=normalized_description,
        provenance=normalized_provenance,
        status="draft",
        frozen_at=None,
        question_count=len(canonical_questions),
    )
    session.add(dataset)
    session.flush()
    for item in canonical_questions:
        forecast_date = parse_datetime(str(item["forecast_date"]))
        resolution_date = parse_datetime(str(item["resolution_date"]))
        if forecast_date is None or resolution_date is None:  # pragma: no cover - canonical invariant
            raise RuntimeError("canonical_evaluation_date_missing")
        session.add(
            EvaluationQuestion(
                id=str(uuid.uuid4()),
                dataset_id=dataset.id,
                question=str(item["question"]),
                resolution_contract=canonical_json(item["resolution_contract"]),
                forecast_date=forecast_date,
                resolution_date=resolution_date,
                outcome=int(item["outcome"]),
                resolution_source=str(item["resolution_source"]),
                domain=str(item["domain"]),
                question_hash=str(item["question_hash"]),
            )
        )
    session.flush()
    return dataset


def _canonical_stored_question(
    question: EvaluationQuestion,
    *,
    now: datetime | None,
) -> dict[str, Any]:
    try:
        contract = json.loads(question.resolution_contract)
    except (json.JSONDecodeError, TypeError) as exc:
        raise EvaluationDatasetValidationError(["resolution_contract_invalid"]) from exc
    if not isinstance(contract, dict):
        raise EvaluationDatasetValidationError(["resolution_contract_invalid"])
    canonical = canonical_evaluation_question(
        {
            "question": question.question,
            "yes_condition": contract.get("yes_condition"),
            "no_condition": contract.get("no_condition"),
            "authoritative_resolver": contract.get("authoritative_resolver"),
            "forecast_date": question.forecast_date,
            "resolution_date": question.resolution_date,
            "outcome": question.outcome,
            "resolution_source": question.resolution_source,
            "domain": question.domain,
        },
        now=now,
    )
    if canonical_json(contract) != canonical_json(canonical["resolution_contract"]):
        raise EvaluationDatasetValidationError(["resolution_contract_not_canonical"])
    return canonical


def _canonical_dataset_questions(
    dataset: EvaluationDataset,
    *,
    now: datetime | None,
) -> list[tuple[EvaluationQuestion, dict[str, Any]]]:
    if not dataset.questions:
        raise EvaluationDatasetValidationError(["dataset_questions_required"])
    canonical: list[tuple[EvaluationQuestion, dict[str, Any]]] = []
    reasons: list[str] = []
    for index, question in enumerate(dataset.questions, start=1):
        try:
            canonical.append((question, _canonical_stored_question(question, now=now)))
        except EvaluationDatasetValidationError as exc:
            reasons.extend(f"row_{index}:{reason}" for reason in exc.reasons)
    hashes = [str(item[1]["question_hash"]) for item in canonical]
    texts = [str(item[1]["question"]).casefold() for item in canonical]
    if len(hashes) != len(set(hashes)) or len(texts) != len(set(texts)):
        reasons.append("duplicate_evaluation_question")
    if reasons:
        raise EvaluationDatasetValidationError(reasons)
    return canonical


def review_evaluation_dataset(
    session: Session,
    dataset: EvaluationDataset,
    *,
    now: datetime | None = None,
) -> EvaluationDataset:
    """Validate current draft contents, refresh their hash, and mark them reviewed."""

    if dataset.status == "frozen":
        return dataset
    if dataset.status == "reviewed":
        return dataset
    if dataset.status != "draft":
        raise EvaluationDatasetValidationError(["dataset_status_invalid"])
    _validate_dataset_metadata(
        name=dataset.name,
        version=dataset.version,
        provenance=dataset.provenance,
        question_count=len(dataset.questions),
    )
    canonical = _canonical_dataset_questions(dataset, now=now)
    for question, item in canonical:
        question.question_hash = str(item["question_hash"])
    dataset.question_count = len(canonical)
    dataset.hash = evaluation_dataset_hash(
        name=dataset.name,
        version=dataset.version,
        description=dataset.description,
        provenance=dataset.provenance,
        questions=[item for _, item in canonical],
    )
    dataset.status = "reviewed"
    session.flush()
    return dataset


def freeze_evaluation_dataset(
    session: Session,
    dataset: EvaluationDataset,
    *,
    now: datetime | None = None,
) -> EvaluationDataset:
    """Freeze a reviewed release only when its reviewed hash still matches its contents."""

    if dataset.status == "frozen":
        return dataset
    if dataset.status != "reviewed":
        raise EvaluationDatasetValidationError(["dataset_review_required"])
    canonical = _canonical_dataset_questions(dataset, now=now)
    expected_hash = evaluation_dataset_hash(
        name=dataset.name,
        version=dataset.version,
        description=dataset.description,
        provenance=dataset.provenance,
        questions=[item for _, item in canonical],
    )
    hashes_match = all(
        question.question_hash == str(item["question_hash"]) for question, item in canonical
    )
    if (
        expected_hash != dataset.hash
        or dataset.question_count != len(canonical)
        or not hashes_match
    ):
        raise EvaluationDatasetValidationError(["dataset_changed_after_review"])
    dataset.status = "frozen"
    dataset.frozen_at = as_utc(now or utcnow())
    session.flush()
    return dataset


def serialize_evaluation_question(question: EvaluationQuestion) -> dict[str, Any]:
    return {
        "id": question.id,
        "dataset_id": question.dataset_id,
        "question": question.question,
        "resolution_contract": json.loads(question.resolution_contract),
        "forecast_date": as_utc(question.forecast_date).isoformat(),
        "resolution_date": as_utc(question.resolution_date).isoformat(),
        "outcome": question.outcome,
        "resolution_source": question.resolution_source,
        "domain": question.domain,
    }


def serialize_evaluation_dataset(
    dataset: EvaluationDataset,
    *,
    include_questions: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": dataset.id,
        "name": dataset.name,
        "version": dataset.version,
        "hash": dataset.hash,
        "description": dataset.description,
        "provenance": dataset.provenance,
        "status": dataset.status,
        "created_at": as_utc(dataset.created_at).isoformat(),
        "frozen_at": as_utc(dataset.frozen_at).isoformat() if dataset.frozen_at else None,
        "question_count": dataset.question_count,
    }
    if include_questions:
        payload["questions"] = [
            serialize_evaluation_question(question) for question in dataset.questions
        ]
    return payload
