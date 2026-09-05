#!/usr/bin/env python3
"""Import, review, and freeze ForecastLab Pilot Benchmark v1 without running forecasts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from forecastlab_api.db import SessionLocal  # noqa: E402
from forecastlab_api.evaluation_datasets import EvaluationDatasetValidationError  # noqa: E402
from forecastlab_api.migrate import apply_schema  # noqa: E402
from forecastlab_api.pilot_benchmark import import_pilot_benchmark  # noqa: E402


def main() -> int:
    apply_schema()
    with SessionLocal() as session:
        try:
            dataset = import_pilot_benchmark(session)
            session.commit()
            session.refresh(dataset)
        except EvaluationDatasetValidationError as exc:
            session.rollback()
            print(json.dumps({"status": "rejected", "reasons": exc.reasons}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "dataset_id": dataset.id,
                    "dataset_hash": dataset.hash,
                    "name": dataset.name,
                    "question_count": dataset.question_count,
                    "status": dataset.status,
                    "version": dataset.version,
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
