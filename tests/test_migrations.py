from sqlalchemy import create_engine, inspect, text

from forecastlab_api.migrate import apply_migrations, apply_schema


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
    assert "alembic_version" in tables
    question_cols = {column["name"] for column in inspect(engine).get_columns("questions")}
    assert "requested_mode" in question_cols
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260819_0006"
        assert "benchmark_profile_snapshots" in tables
        question_cols = {column["name"] for column in inspect(engine).get_columns("benchmark_questions")}
        assert "exact_yes" in question_cols
        assert "exact_no" in question_cols
        run_cols = {column["name"] for column in inspect(engine).get_columns("forecast_runs")}
        assert "benchmark_task_id" in run_cols


def test_apply_schema_upgrades_empty_database(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path}/schema.db"
    from forecastlab_api.config import settings

    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    apply_schema(db_url)
    engine = create_engine(db_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260819_0006"
