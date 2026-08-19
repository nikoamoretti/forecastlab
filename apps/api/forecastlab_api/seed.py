from __future__ import annotations

import csv
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.hashing import import_hash
from forecastlab.providers.mock import SAMPLE_QUESTION
from forecastlab.timeutil import parse_datetime
from forecastlab_api.config import ROOT
from forecastlab_api.models import BenchmarkQuestion, Question
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
    )
    session.add(question)
    session.flush()
    attach_demo_watch(session, question)
    return question


def seed_synthetic_benchmarks(session: Session) -> int:
    path = ROOT / "fixtures" / "benchmarks" / "synthetic_binary.csv"
    created = 0
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            digest = import_hash(
                {
                    "question": row["question"],
                    "forecast_date": row["forecast_date"],
                    "resolution_date": row["resolution_date"],
                    "outcome": row["outcome"],
                    "resolution_source": row["resolution_source"],
                }
            )
            if session.scalar(select(BenchmarkQuestion).where(BenchmarkQuestion.import_hash == digest)):
                continue
            session.add(
                BenchmarkQuestion(
                    id=str(uuid.uuid4()),
                    question=row["question"],
                    forecast_date=parse_datetime(row["forecast_date"]),
                    resolution_date=parse_datetime(row["resolution_date"]),
                    outcome=int(row["outcome"]),
                    resolution_source=row["resolution_source"],
                    category=row["category"],
                    provenance=row["provenance"],
                    import_hash=digest,
                    is_synthetic=row["is_synthetic"].lower() == "true",
                )
            )
            created += 1
    return created


seed_sample_question = seed_sample_question
seed_synthetic_benchmarks = seed_synthetic_benchmarks
