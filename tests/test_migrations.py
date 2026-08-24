from alembic import command
from sqlalchemy import create_engine, inspect, text

from forecastlab_api.migrate import alembic_config, apply_migrations, apply_schema


def test_alembic_creates_integrity_tables(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path}/migrate.db"
    from forecastlab_api.config import settings

    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    apply_migrations(db_url)
    engine = create_engine(db_url)
    tables = inspect(engine).get_table_names()
    assert "forecast_runs" in tables
    assert "benchmark_datasets" in tables
    assert "benchmark_experiments" in tables
    assert "benchmark_tasks" in tables
    assert "forecast_run_attempts" in tables
    assert "provider_call_ledger" in tables
    assert "forecast_contracts" in tables
    assert "forecast_graphs" in tables
    assert "forecast_nodes" in tables
    assert "evidence_claims" in tables
    assert "forecast_node_runs" in tables
    assert "forecast_aggregations" in tables
    assert "graph_execution_failures" in tables
    assert "evaluation_datasets" in tables
    assert "evaluation_questions" in tables
    assert "forecast_experiments" in tables
    assert "forecast_experiment_runs" in tables
    assert "forecast_experiment_results" in tables
    assert "alembic_version" in tables
    question_cols = {column["name"] for column in inspect(engine).get_columns("questions")}
    assert "requested_mode" in question_cols
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260823_0017"
        assert "benchmark_profile_snapshots" in tables
        question_cols = {column["name"] for column in inspect(engine).get_columns("benchmark_questions")}
        assert "exact_yes" in question_cols
        assert "exact_no" in question_cols
        run_cols = {column["name"] for column in inspect(engine).get_columns("forecast_runs")}
        assert "benchmark_task_id" in run_cols
        node_run_cols = {column["name"] for column in inspect(engine).get_columns("forecast_node_runs")}
        assert {
            "confidence",
            "raw_importance_weight",
            "dependency_factor",
            "normalized_weight",
            "probability_contribution",
            "uncertainty_notes_json",
            "model_used",
        } <= node_run_cols
        result_cols = {column["name"] for column in inspect(engine).get_columns("benchmark_results")}
        assert {"evidence_coverage", "evidence_covered_units", "evidence_total_units"} <= result_cols
        aggregation_cols = {
            column["name"] for column in inspect(engine).get_columns("forecast_aggregations")
        }
        assert {
            "forecast_run_id",
            "method",
            "final_probability",
            "calculation_trace_json",
            "node_contributions_json",
            "created_at",
        } <= aggregation_cols
        failure_cols = {
            column["name"] for column in inspect(engine).get_columns("graph_execution_failures")
        }
        assert {
            "forecast_run_id",
            "node_id",
            "stage",
            "error_code",
            "error_message",
            "created_at",
        } <= failure_cols
        dataset_cols = {
            column["name"] for column in inspect(engine).get_columns("evaluation_datasets")
        }
        assert {
            "id",
            "name",
            "version",
            "hash",
            "description",
            "provenance",
            "status",
            "created_at",
            "frozen_at",
            "question_count",
        } == dataset_cols
        evaluation_question_cols = {
            column["name"] for column in inspect(engine).get_columns("evaluation_questions")
        }
        assert {
            "id",
            "dataset_id",
            "question",
            "resolution_contract",
            "forecast_date",
            "resolution_date",
            "outcome",
            "resolution_source",
            "domain",
            "question_hash",
        } == evaluation_question_cols
        forecast_experiment_cols = {
            column["name"] for column in inspect(engine).get_columns("forecast_experiments")
        }
        assert {
            "id",
            "dataset_id",
            "status",
            "profiles_json",
            "configuration_hash",
            "configuration_json",
            "created_at",
            "completed_at",
        } == forecast_experiment_cols
        forecast_experiment_run_cols = {
            column["name"]
            for column in inspect(engine).get_columns("forecast_experiment_runs")
        }
        assert {
            "id",
            "experiment_id",
            "evaluation_question_id",
            "profile_id",
            "forecast_run_id",
            "status",
            "error",
            "error_category",
            "created_at",
            "started_at",
            "completed_at",
        } == forecast_experiment_run_cols
        forecast_experiment_result_cols = {
            column["name"]
            for column in inspect(engine).get_columns("forecast_experiment_results")
        }
        assert {
            "id",
            "experiment_run_id",
            "probability",
            "outcome",
            "brier_score",
            "log_loss",
            "cost_usd",
            "latency_ms",
            "evidence_coverage",
            "evidence_covered_units",
            "evidence_total_units",
            "completion_status",
            "created_at",
        } == forecast_experiment_result_cols


def test_apply_schema_upgrades_empty_database(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path}/schema.db"
    from forecastlab_api.config import settings

    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    apply_schema(db_url)
    engine = create_engine(db_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260823_0017"


def test_real_evaluation_migration_preserves_existing_forecast_rows(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/real-evaluation.db"
    command.upgrade(alembic_config(db_url), "20260823_0015")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO questions "
                "(id, original_text, normalized_text, question_type, created_at, forecast_deadline, status, "
                "notes, stale, requested_mode, requested_profile_id, requested_as_of, is_benchmark) VALUES "
                "('existing-question', 'Will the record survive?', NULL, 'binary', "
                "'2026-08-23 00:00:00', NULL, 'draft', NULL, 0, 'demo', "
                "'single_model_forecaster_v1', NULL, 0)"
            )
        )

    command.upgrade(alembic_config(db_url), "head")

    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT original_text FROM questions WHERE id = 'existing-question'")
            ).scalar_one()
            == "Will the record survive?"
        )
        assert connection.execute(text("SELECT COUNT(*) FROM evaluation_datasets")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM evaluation_questions")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM forecast_experiments")).scalar_one() == 0
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "20260823_0017"
        )


def test_forecast_experiment_migration_preserves_frozen_dataset(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/forecast-experiment.db"
    command.upgrade(alembic_config(db_url), "20260823_0016")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO evaluation_datasets "
                "(id, name, version, hash, description, provenance, status, created_at, "
                "frozen_at, question_count) VALUES "
                "('dataset-1', 'Resolved release', '1', :hash, 'description', 'source', "
                "'frozen', '2026-08-23 00:00:00', '2026-08-23 00:00:00', 0)"
            ),
            {"hash": "a" * 64},
        )

    command.upgrade(alembic_config(db_url), "head")

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT name FROM evaluation_datasets WHERE id = 'dataset-1'")
        ).scalar_one() == "Resolved release"
        assert connection.execute(text("SELECT COUNT(*) FROM forecast_experiments")).scalar_one() == 0


def test_node_forecasting_migration_backfills_existing_node_run(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/node-run.db"
    command.upgrade(alembic_config(db_url), "20260822_0010")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO forecast_node_runs "
                "(id, forecast_run_id, node_id, probability, reasoning, supporting_claim_ids_json, "
                "opposing_claim_ids_json, uncertainty, created_at) VALUES "
                "('node-run-1', 'missing-run', 'missing-node', 0.55, 'legacy output', '[]', '[]', 0.4, "
                "'2026-08-22 00:00:00')"
            )
        )

    command.upgrade(alembic_config(db_url), "head")

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT confidence, raw_importance_weight, dependency_factor, normalized_weight, "
                "probability_contribution, uncertainty_notes_json, model_used "
                "FROM forecast_node_runs WHERE id = 'node-run-1'"
            )
        ).one()
        assert tuple(row) == (0.0, 0.0, 1.0, 0.0, 0.0, "[]", "legacy:deterministic-node-v1")


def test_v1_evaluation_migration_preserves_existing_benchmark_results(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/evaluation-result.db"
    command.upgrade(alembic_config(db_url), "20260822_0011")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO benchmark_results "
                "(id, benchmark_question_id, profile_id, cost_usd, latency_ms, failed, partial, created_at) VALUES "
                "('result-1', 'missing-question', 'legacy-profile', 0.25, 120, 0, 0, '2026-08-22 00:00:00')"
            )
        )

    command.upgrade(alembic_config(db_url), "head")

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT evidence_coverage, evidence_covered_units, evidence_total_units "
                "FROM benchmark_results WHERE id = 'result-1'"
            )
        ).one()
        assert tuple(row) == (None, 0, 0)
