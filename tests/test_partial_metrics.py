from __future__ import annotations

from forecastlab.evaluation import brier_score
from forecastlab_api.experiments import experiment_summary
from forecastlab_api.models import (
    BenchmarkDataset,
    BenchmarkExperiment,
    BenchmarkQuestion,
    BenchmarkResult,
    BenchmarkTask,
)


def _result(**kwargs) -> BenchmarkResult:
    payload = {
        "id": kwargs.get("id"),
        "experiment_id": "exp",
        "benchmark_task_id": kwargs.get("id"),
        "benchmark_question_id": kwargs.get("qid"),
        "run_id": kwargs.get("id"),
        "profile_id": kwargs.get("profile_id", "p1"),
        "probability": kwargs.get("probability"),
        "brier": kwargs.get("brier"),
        "log_loss_value": kwargs.get("log_loss_value", 0.2),
        "cost_usd": kwargs.get("cost_usd", 1.0),
        "latency_ms": kwargs.get("latency_ms", 100),
        "failed": kwargs.get("failed", False),
        "partial": kwargs.get("partial", False),
    }
    return BenchmarkResult(**payload)


def test_full_partial_failed_counts_and_metric_sets(client) -> None:
    import uuid

    from forecastlab.timeutil import utcnow
    from forecastlab_api.db import SessionLocal

    with SessionLocal() as session:
        dataset = BenchmarkDataset(
            id=str(uuid.uuid4()),
            name="partial-metrics",
            description="test",
            dataset_hash="h-partial-metrics",
            provenance="test",
            is_synthetic=True,
            question_count=3,
        )
        session.add(dataset)
        questions = []
        for index, text in enumerate(["q1", "q2", "q3"]):
            item = BenchmarkQuestion(
                id=str(uuid.uuid4()),
                dataset_id=dataset.id,
                question=text,
                forecast_date=utcnow(),
                resolution_date=utcnow(),
                outcome=1,
                resolution_source="fixture",
                category="test",
                provenance="test",
                import_hash=f"imp-{index}-{dataset.id}",
                is_synthetic=True,
                exact_yes="yes",
                exact_no="no",
                resolution_deadline=utcnow(),
                authoritative_source="fixture",
            )
            session.add(item)
            questions.append(item)
        experiment = BenchmarkExperiment(
            id=str(uuid.uuid4()),
            dataset_id=dataset.id,
            status="completed",
            profile_ids_json='["alpha","beta"]',
            profile_hashes_json="{}",
            prompt_hashes_json="{}",
            is_synthetic=True,
        )
        session.add(experiment)
        session.flush()
        rows = [
            ("alpha", questions[0].id, 0.8, False, False),
            ("alpha", questions[1].id, 0.6, True, False),
            ("alpha", questions[2].id, None, False, True),
            ("beta", questions[0].id, 0.7, False, False),
            ("beta", questions[1].id, 0.4, False, False),
        ]
        for profile, qid, prob, partial, failed in rows:
            task = BenchmarkTask(
                id=str(uuid.uuid4()),
                experiment_id=experiment.id,
                benchmark_question_id=qid,
                profile_id=profile,
                status="failed" if failed else "completed",
            )
            session.add(task)
            session.flush()
            session.add(
                BenchmarkResult(
                    id=str(uuid.uuid4()),
                    experiment_id=experiment.id,
                    benchmark_task_id=task.id,
                    benchmark_question_id=qid,
                    run_id=None,
                    profile_id=profile,
                    probability=prob,
                    brier=None if failed or prob is None else brier_score(prob, 1),
                    log_loss_value=None if failed or prob is None else 0.1,
                    cost_usd=2.0 if failed else 1.0,
                    latency_ms=50,
                    failed=failed,
                    partial=partial,
                )
            )
        session.commit()
        summary = experiment_summary(session, experiment)
        alpha = next(item for item in summary["profiles"] if item["profile_id"] == "alpha")
        assert alpha["total_count"] == 3
        assert alpha["full_count"] == 1
        assert alpha["partial_count"] == 1
        assert alpha["failed_count"] == 1
        assert alpha["all_valid"]["n"] == 2
        assert alpha["full_only"]["n"] == 1
        assert alpha["all_valid"]["brier"] != alpha["full_only"]["brier"]
        statuses = {item["question"]: item["status"] for item in summary["rows"] if item["profile_id"] == "alpha"}
        assert statuses["q1"] == "full"
        assert statuses["q2"] == "partial"
        assert statuses["q3"] == "failed"
        assert summary["paired_comparisons_all_valid"]
        all_n = summary["paired_comparisons_all_valid"][0]["n"]
        full_n = summary["paired_comparisons_full_only"][0]["n"]
        assert all_n >= full_n
        assert full_n == 1
