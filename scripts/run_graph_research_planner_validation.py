#!/usr/bin/env python3
"""Run the mock-only graph Research Planner validation exactly once."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from forecastlab_api.config import settings  # noqa: E402
from forecastlab_api.db import SessionLocal  # noqa: E402
from forecastlab_api.graph_planner_validation import (  # noqa: E402
    DEFAULT_ARTIFACT_PATH,
    DEFAULT_REPORT_PATH,
    execute_graph_planner_validation,
    freeze_graph_planner_validation,
    validation_already_started,
    write_graph_planner_validation_artifacts,
)
from forecastlab_api.migrate import apply_schema  # noqa: E402


def main() -> int:
    if os.environ.get("FORECASTLAB_RUN_GRAPH_RESEARCH_PLANNER_VALIDATION") != "1":
        print(
            json.dumps(
                {
                    "status": "not_authorized",
                    "provider_invocations": 0,
                    "validation_runs": 0,
                }
            )
        )
        return 2
    if "graph_execution_validation" not in settings.database_url:
        print(json.dumps({"status": "prior_validation_database_required"}))
        return 2
    if DEFAULT_ARTIFACT_PATH.exists() or DEFAULT_REPORT_PATH.exists():
        print(json.dumps({"status": "validation_artifact_already_exists"}))
        return 2

    apply_schema()
    with SessionLocal() as session:
        if validation_already_started(session):
            print(json.dumps({"status": "validation_already_started"}))
            return 2
        frozen = freeze_graph_planner_validation(session)

    print(
        json.dumps(
            {
                "status": "preflight_passed",
                "validation_id": frozen["validation_id"],
                "source_validation_id": frozen["source_validation"]["validation_id"],
                "question_count": frozen["dataset"]["selected_question_count"],
                "question_ids": [item["evaluation_question_id"] for item in frozen["dataset"]["questions"]],
                "profile": frozen["profile"],
                "provider": frozen["provider"],
                "budget": frozen["budget"],
                "database": "existing_isolated_graph_execution_validation",
                "live_provider_calls_authorized": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    artifact = execute_graph_planner_validation(SessionLocal, frozen)
    write_graph_planner_validation_artifacts(artifact)
    print(
        json.dumps(
            {
                "status": "completed",
                "validation_id": artifact["validation_id"],
                "passed": artifact["passed"],
                "summary": artifact["summary"],
                "artifact": str(DEFAULT_ARTIFACT_PATH),
                "report": str(DEFAULT_REPORT_PATH),
            },
            sort_keys=True,
        )
    )
    return 0 if artifact["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
