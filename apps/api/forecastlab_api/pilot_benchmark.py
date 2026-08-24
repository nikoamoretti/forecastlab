from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from forecastlab.timeutil import parse_datetime
from forecastlab_api.config import ROOT
from forecastlab_api.evaluation_datasets import (
    EvaluationDatasetValidationError,
    canonical_evaluation_question,
    freeze_evaluation_dataset,
    import_evaluation_dataset,
    parse_evaluation_dataset_file,
    review_evaluation_dataset,
)
from forecastlab_api.models import EvaluationDataset

PILOT_DATASET_NAME = "ForecastLab Pilot Benchmark"
PILOT_DATASET_VERSION = "1"
PILOT_DATASET_DESCRIPTION = (
    "Twenty resolved binary questions for validating ForecastLab's real-question evaluation workflow."
)
PILOT_DATASET_PROVENANCE = (
    "Questions were retrospectively selected under docs/PILOT_BENCHMARK_PROTOCOL.md and resolved "
    "against the linked first-party records frozen in fixtures/benchmarks/pilot_v1.csv."
)
PILOT_FIXTURE_PATH = ROOT / "fixtures" / "benchmarks" / "pilot_v1.csv"
PILOT_DOMAIN_COUNTS = {
    "economics": 8,
    "business": 5,
    "technology": 4,
    "regulation": 3,
}
PILOT_OUTCOME_COUNTS = {0: 10, 1: 10}
PILOT_ALLOWED_SOURCE_HOSTS = frozenset(
    {
        "docs.fcc.gov",
        "ir.netflix.net",
        "ir.tesla.com",
        "kubernetes.io",
        "nvidianews.nvidia.com",
        "openai.com",
        "www.apple.com",
        "www.bea.gov",
        "www.bls.gov",
        "www.federalreserve.gov",
        "www.ftc.gov",
        "www.microsoft.com",
        "www.python.org",
        "www.sec.gov",
    }
)


def load_pilot_rows(path: Path = PILOT_FIXTURE_PATH) -> list[dict[str, Any]]:
    return parse_evaluation_dataset_file(path.read_text(encoding="utf-8"), path.name)


def validate_pilot_release(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Apply the generic fail-closed checks plus the frozen pilot manifest contract."""

    reasons: list[str] = []
    canonical: list[dict[str, Any]] = []
    if len(rows) != 20:
        reasons.append("pilot_question_count_must_be_20")

    for index, row in enumerate(rows, start=1):
        try:
            item = canonical_evaluation_question(row, now=now)
        except EvaluationDatasetValidationError as exc:
            reasons.extend(f"row_{index}:{reason}" for reason in exc.reasons)
            continue
        category = str(item.get("category") or "").strip()
        if not category:
            reasons.append(f"row_{index}:category_required")
        hostname = (urlparse(str(item["resolution_source"])).hostname or "").casefold()
        if hostname not in PILOT_ALLOWED_SOURCE_HOSTS:
            reasons.append(f"row_{index}:pilot_source_must_be_allowlisted_first_party")
        forecast_date = parse_datetime(str(item["forecast_date"]))
        resolution_date = parse_datetime(str(item["resolution_date"]))
        if forecast_date is None or resolution_date is None:  # pragma: no cover - canonical invariant
            reasons.append(f"row_{index}:pilot_date_missing")
        elif not 30 <= (resolution_date - forecast_date).days <= 120:
            reasons.append(f"row_{index}:pilot_resolution_window_out_of_range")
        canonical.append(item)

    if not reasons:
        domains = Counter(str(item["domain"]) for item in canonical)
        outcomes = Counter(int(item["outcome"]) for item in canonical)
        if dict(domains) != PILOT_DOMAIN_COUNTS:
            reasons.append("pilot_domain_mix_invalid")
        if dict(outcomes) != PILOT_OUTCOME_COUNTS:
            reasons.append("pilot_outcome_balance_invalid")
    if reasons:
        raise EvaluationDatasetValidationError(reasons, "Pilot benchmark is invalid")
    return canonical


def import_pilot_benchmark(
    session: Session,
    *,
    path: Path = PILOT_FIXTURE_PATH,
    now: datetime | None = None,
) -> EvaluationDataset:
    """Idempotently import, review, and freeze the curated pilot release."""

    rows = load_pilot_rows(path)
    validate_pilot_release(rows, now=now)
    dataset = import_evaluation_dataset(
        session,
        name=PILOT_DATASET_NAME,
        version=PILOT_DATASET_VERSION,
        description=PILOT_DATASET_DESCRIPTION,
        provenance=PILOT_DATASET_PROVENANCE,
        rows=rows,
        now=now,
    )
    if dataset.status == "draft":
        review_evaluation_dataset(session, dataset, now=now)
    if dataset.status == "reviewed":
        freeze_evaluation_dataset(session, dataset, now=now)
    if dataset.status != "frozen":  # pragma: no cover - lifecycle invariant
        raise EvaluationDatasetValidationError(["pilot_dataset_not_frozen"])
    return dataset
