from sqlalchemy import create_engine, inspect

from forecastlab_api.migrate import apply_migrations


def test_alembic_creates_integrity_tables(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path}/migrate.db"
    from forecastlab_api.config import settings

    monkeypatch.setattr(settings, "database_url", db_url)
    apply_migrations(db_url)
    engine = create_engine(db_url)
    tables = inspect(engine).get_table_names()
    assert "forecast_runs" in tables
    assert "benchmark_datasets" in tables
    assert "benchmark_experiments" in tables
    assert "benchmark_tasks" in tables
    assert "alembic_version" in tables
