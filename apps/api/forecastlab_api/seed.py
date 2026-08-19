from __future__ import annotations

import csv
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.providers.mock import SAMPLE_QUESTION
from forecastlab_api.config import ROOT
from forecastlab_api.experiments import SYNTHETIC_DATASET_NAME, ensure_dataset
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


def seed_synthetic_benchmarks(session: Session) -> BenchmarkDataset:
    existing = session.scalar(select(BenchmarkDataset).where(BenchmarkDataset.name == SYNTHETIC_DATASET_NAME))
    path = ROOT / "fixtures" / "benchmarks" / "synthetic_binary.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    dataset, _, _, _ = ensure_dataset(
        session,
        name=SYNTHETIC_DATASET_NAME,
        description="Built-in synthetic fixture questions used only to verify scoring software.",
        rows=rows,
        provenance="ForecastLab synthetic fixture",
        is_synthetic=True,
    )
    if existing and existing.id != dataset.id:
        return existing
    return dataset
