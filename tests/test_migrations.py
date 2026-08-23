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
    assert "alembic_version" in tables
    question_cols = {column["name"] for column in inspect(engine).get_columns("questions")}
    assert "requested_mode" in question_cols
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260823_0015"
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


def test_apply_schema_upgrades_empty_database(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path}/schema.db"
    from forecastlab_api.config import settings

    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    apply_schema(db_url)
    engine = create_engine(db_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260823_0015"


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
