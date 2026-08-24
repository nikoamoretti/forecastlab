from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = ROOT / "artifacts" / "pilot_benchmark_v1" / "experiment_results.json"
REPORT_PATH = ROOT / "docs" / "PILOT_BENCHMARK_V1_REPORT.md"
DATASET_HASH = "c9cd748c1114eff5d16321ecf7d78b18be4a0b65b49ff7719aa5276e302f7888"
EXPERIMENT_ID = "462fe718-64ca-4fab-bed2-f9ecf9c28433"
CONFIGURATION_HASH = "a6873069bb2b7388cd8e78ce54676fac1425f03abaa73f6705dbc61c87c8e7aa"
CODE_COMMIT = "458c57a1c325b12f5b3f133086f6189d49fde731"
PROFILES = {
    "single_model_forecaster_v1",
    "three_track_forecaster",
    "graph_forecaster_v1",
}


def _artifact() -> dict:
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


def test_pilot_experiment_artifact_has_reproducible_identity() -> None:
    first = _artifact()
    second = _artifact()

    assert first == second
    assert first["artifact_schema_version"] == 1
    assert first["experiment_id"] == EXPERIMENT_ID
    assert first["dataset"]["hash"] == DATASET_HASH
    assert first["freeze"]["configuration_hash"] == CONFIGURATION_HASH
    assert first["freeze"]["code"]["git_commit"] == CODE_COMMIT
    assert first["freeze"]["evaluation_configuration"] == {
        "bootstrap_method": "paired_percentile_bootstrap",
        "bootstrap_samples": 2000,
        "calibration_adjustment": "none",
        "calibration_bucket_edges": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        "confidence_level": 0.95,
        "difference_direction": "profile_a_minus_profile_b",
        "minimum_paired_questions": 20,
        "paired_population": "completed_questions_available_for_both_profiles",
        "random_seed": 20260823,
    }


def test_pilot_experiment_configuration_is_frozen_and_provider_safe() -> None:
    artifact = _artifact()
    frozen = artifact["freeze"]

    assert artifact["dataset"]["question_count"] == 20
    assert len(frozen["question_hashes"]) == len(set(frozen["question_hashes"])) == 20
    assert len(frozen["evidence_cutoffs"]) == 20
    assert set(frozen["profiles"]) == PROFILES
    assert {
        profile_id: snapshot["version"]
        for profile_id, snapshot in frozen["profiles"].items()
    } == {
        "single_model_forecaster_v1": 1,
        "three_track_forecaster": 1,
        "graph_forecaster_v1": 4,
    }
    assert all(
        snapshot["effective_budget"] == frozen["common_budget"]
        for snapshot in frozen["profiles"].values()
    )
    assert frozen["provider"] == {
        "evidence_policy": "synthetic_historical_fixtures",
        "model": "mock-forecast-v1",
        "model_api_key_set": True,
        "model_base_url": None,
        "model_provider": "mock",
        "model_timeout_seconds": 60.0,
        "search_api_key_set": True,
        "search_provider": "mock",
    }
    assert frozen["synthetic_test"] is True
    assert len(frozen["prompts"]["hashes"]) == len(frozen["prompts"]["versions"]) == 16
    assert artifact["execution_audit"]["live_provider_calls"] == 0
    assert artifact["execution_audit"]["actual_cost_usd"] == 0.0


def test_pilot_assigns_each_identical_question_to_all_profiles() -> None:
    artifact = _artifact()
    rows = artifact["rows"]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["evaluation_question_id"]].append(row)

    assert len(rows) == 60
    assert len(grouped) == 20
    assert artifact["execution_audit"]["expected_runs"] == 60
    assert artifact["execution_audit"]["actual_runs"] == 60
    for question_rows in grouped.values():
        assert {row["profile_id"] for row in question_rows} == PROFILES
        for field in (
            "question_hash",
            "forecast_date",
            "resolution_date",
            "evidence_cutoff",
            "outcome",
        ):
            assert len({row[field] for row in question_rows}) == 1


def test_pilot_metrics_reconcile_from_question_level_results() -> None:
    artifact = _artifact()
    completed_by_profile: dict[str, list[dict]] = defaultdict(list)
    status_counts: Counter[tuple[str, str]] = Counter()

    for row in artifact["rows"]:
        status_counts[(row["profile_id"], row["completion_status"])] += 1
        if row["completion_status"] == "completed":
            probability = row["probability"]
            outcome = row["outcome"]
            assert row["brier_score"] == pytest.approx((probability - outcome) ** 2)
            expected_log_loss = -(
                outcome * math.log(probability)
                + (1 - outcome) * math.log(1 - probability)
            )
            assert row["log_loss"] == pytest.approx(expected_log_loss)
            completed_by_profile[row["profile_id"]].append(row)
        else:
            assert row["completion_status"] == "failed"
            assert row["probability"] is None
            assert row["brier_score"] is None
            assert row["log_loss"] is None

    metrics = {item["profile_id"]: item for item in artifact["profile_metrics"]}
    for profile_id, rows in completed_by_profile.items():
        assert metrics[profile_id]["brier_score"] == pytest.approx(
            sum(row["brier_score"] for row in rows) / len(rows)
        )
        assert metrics[profile_id]["log_loss"] == pytest.approx(
            sum(row["log_loss"] for row in rows) / len(rows)
        )

    assert status_counts == Counter(
        {
            ("single_model_forecaster_v1", "completed"): 20,
            ("three_track_forecaster", "completed"): 20,
            ("graph_forecaster_v1", "completed"): 10,
            ("graph_forecaster_v1", "failed"): 10,
        }
    )
    failed = [row for row in artifact["rows"] if row["completion_status"] == "failed"]
    assert all(
        [item["report_label"] for item in row["failure_classifications"]]
        == ["evidence failure"]
        for row in failed
    )


def test_pilot_report_is_complete_and_uses_observed_difference_language() -> None:
    artifact = _artifact()
    report = REPORT_PATH.read_text(encoding="utf-8")

    assert report.startswith("# ForecastLab Pilot Benchmark V1 Report")
    assert EXPERIMENT_ID in report
    assert DATASET_HASH in report
    assert "Paired observed differences" in report
    assert "Insufficient evidence" in report
    assert "Live provider calls: 0" in report
    assert not re.search(r"\b(?:winner|best|superior)\b", report, re.IGNORECASE)

    comparisons = artifact["statistical_comparisons"]
    assert len(comparisons) == 3
    assert comparisons[0]["question_count"] == 20
    assert comparisons[0]["brier_confidence_interval"] == pytest.approx(
        [-0.001844343055086539, 0.009184290295647402]
    )
    assert all(item["bootstrap_samples"] == 2000 for item in comparisons)
    assert all(item["random_seed"] == 20260823 for item in comparisons)
    assert [item["evidence_status"] for item in comparisons] == [
        "interval estimate available",
        "insufficient evidence",
        "insufficient evidence",
    ]
