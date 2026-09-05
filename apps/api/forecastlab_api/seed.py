from __future__ import annotations

import csv
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.providers.mock import SAMPLE_QUESTION
from forecastlab.timeutil import utcnow
from forecastlab_api.config import ROOT
from forecastlab_api.experiments import (
    CURRENT_BUILTIN_KEY,
    CURRENT_BUILTIN_VERSION,
    SYNTHETIC_DATASET_NAME,
    V1_EVALUATION_DATASET_KEY,
    V1_EVALUATION_DATASET_VERSION,
    current_builtin_dataset,
    dataset_hash_for_rows,
    ensure_dataset,
)
from forecastlab_api.models import BenchmarkDataset, Question
from forecastlab_api.watches import attach_demo_watch


def seed_sample_question(session: Session) -> Question:
    existing = session.scalar(select(Question).where(Question.original_text == SAMPLE_QUESTION))
    if existing:
        return existing
    question = Question(
        id=str(uuid.uuid4()),
        original_text=SAMPLE_QUESTION,
        notes="Sample binary question for the local demo. Fixture sources only.",
        status="draft",
        requested_mode="demo",
        requested_profile_id="three_track_ensemble",
        is_benchmark=False,
    )
    session.add(question)
    session.flush()
    attach_demo_watch(session, question)
    return question


def _fixture_rows() -> list[dict]:
    path = ROOT / "fixtures" / "benchmarks" / "synthetic_binary.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _v1_evaluation_rows() -> list[dict]:
    path = ROOT / "fixtures" / "benchmarks" / "v1_evaluation_binary.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def seed_synthetic_benchmarks(session: Session) -> BenchmarkDataset:
    rows = _fixture_rows()
    digest = dataset_hash_for_rows(rows, is_synthetic=True, name=SYNTHETIC_DATASET_NAME)
    current = current_builtin_dataset(session)
    if current is not None and current.dataset_hash == digest:
        return current
    if current is not None and current.dataset_hash != digest:
        current.archived_at = utcnow()
        current.builtin_version = f"{current.builtin_version}+archived+{current.dataset_hash[:8]}"
        current.name = f"{current.name} (archived)"
        session.flush()
    legacy = session.scalar(
        select(BenchmarkDataset).where(
            BenchmarkDataset.name == SYNTHETIC_DATASET_NAME,
            BenchmarkDataset.archived_at.is_(None),
        )
    )
    if legacy is not None and (legacy.builtin_key is None or not legacy.is_builtin):
        if legacy.dataset_hash == digest:
            legacy.builtin_key = CURRENT_BUILTIN_KEY
            legacy.builtin_version = CURRENT_BUILTIN_VERSION
            legacy.is_builtin = True
            legacy.archived_at = None
            return legacy
        legacy.archived_at = utcnow()
        legacy.name = f"{legacy.name} (archived)"
        session.flush()
    dataset, _, _, _ = ensure_dataset(
        session,
        name=SYNTHETIC_DATASET_NAME,
        description="Built-in synthetic fixture questions used only to verify scoring software.",
        rows=rows,
        provenance="ForecastLab synthetic fixture",
        is_synthetic=True,
        builtin_key=CURRENT_BUILTIN_KEY,
        builtin_version=CURRENT_BUILTIN_VERSION,
        is_builtin=True,
    )
    return dataset


def seed_v1_evaluation_benchmarks(session: Session) -> BenchmarkDataset:
    rows = _v1_evaluation_rows()
    digest = dataset_hash_for_rows(rows, is_synthetic=True, name=V1_EVALUATION_DATASET_KEY)
    current = session.scalar(
        select(BenchmarkDataset).where(
            BenchmarkDataset.builtin_key == V1_EVALUATION_DATASET_KEY,
            BenchmarkDataset.builtin_version == V1_EVALUATION_DATASET_VERSION,
            BenchmarkDataset.is_builtin.is_(True),
        )
    )
    if current is not None:
        if current.dataset_hash != digest:
            raise RuntimeError("V1 evaluation fixture changed without a built-in version bump")
        return current
    dataset, _, _, _ = ensure_dataset(
        session,
        name="forecastlab_v1_evaluation_10q",
        description="Ten synthetic binary questions for the first V1 profile comparison workflow.",
        rows=rows,
        provenance="ForecastLab V1 evaluation fixture",
        is_synthetic=True,
        builtin_key=V1_EVALUATION_DATASET_KEY,
        builtin_version=V1_EVALUATION_DATASET_VERSION,
        is_builtin=True,
    )
    if dataset.question_count != 10:
        raise RuntimeError(f"V1 evaluation dataset must contain exactly 10 questions, found {dataset.question_count}")
    return dataset
