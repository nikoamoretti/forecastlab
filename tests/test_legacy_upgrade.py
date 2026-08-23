from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from tests.legacy_mvp import (
    BENCH_Q_ID,
    BENCH_RESULT_ID,
    CONTRACT_ID,
    EVIDENCE_DATED_ID,
    EVIDENCE_UNDATED_ID,
    JOB_ID,
    LEGACY_MVP_COMMIT,
    LEGACY_QUESTION,
    QUESTION_ID,
    RUN_ID,
    TRACK_BASE_ID,
    TRACK_CURRENT_ID,
    TRACK_SKEPTIC_ID,
    VERSION_ID,
    WATCH_ID,
    create_legacy_mvp_schema,
    insert_legacy_mvp_records,
    stamp_baseline,
)

from forecastlab_api.migrate import apply_schema

REQUIRED_QUESTION_COLUMNS = {"requested_mode", "requested_profile_id", "requested_as_of", "is_benchmark"}
REQUIRED_RUN_COLUMNS = {
    "execution_context_json",
    "configuration_hash",
    "evidence_policy",
    "fixture_evidence_used",
    "code_commit",
    "synthetic_fixture_run",
    "benchmark_task_id",
}
REQUIRED_TABLES = {
    "benchmark_datasets",
    "benchmark_experiments",
    "benchmark_tasks",
    "benchmark_profile_snapshots",
    "forecast_run_attempts",
    "provider_call_ledger",
    "forecast_contracts",
    "forecast_graphs",
    "forecast_nodes",
    "evidence_claims",
    "forecast_node_runs",
}


def _engine(path: Path) -> Engine:
    return create_engine(f"sqlite:///{path}", future=True)


def _build_legacy_db(path: Path, *, stamped: bool) -> Engine:
    engine = _engine(path)
    create_legacy_mvp_schema(engine)
    insert_legacy_mvp_records(engine)
    if stamped:
        stamp_baseline(engine)
    return engine


def _assert_legacy_rows_survived(engine: Engine) -> None:
    with engine.connect() as connection:
        question = connection.execute(text("SELECT original_text, requested_mode, requested_profile_id, requested_as_of, is_benchmark FROM questions WHERE id = :id"), {"id": QUESTION_ID}).one()
        assert question.original_text == LEGACY_QUESTION
        assert question.requested_mode == "demo"
        assert question.requested_profile_id == "three_track_ensemble"
        assert question.requested_as_of is None
        assert int(question.is_benchmark) == 0

        assert connection.execute(text("SELECT COUNT(*) FROM resolution_contracts WHERE id = :id"), {"id": CONTRACT_ID}).scalar_one() == 1
        run = connection.execute(
            text(
                "SELECT execution_context_json, configuration_hash, evidence_policy, fixture_evidence_used, "
                "code_commit, synthetic_fixture_run FROM forecast_runs WHERE id = :id"
            ),
            {"id": RUN_ID},
        ).one()
        assert run.execution_context_json == "{}"
        assert run.configuration_hash is None
        assert run.evidence_policy is None
        assert int(run.fixture_evidence_used) == 0
        assert run.code_commit is None
        assert int(run.synthetic_fixture_run) == 0

        track_ids = {row[0] for row in connection.execute(text("SELECT id FROM research_tracks WHERE run_id = :id"), {"id": RUN_ID})}
        assert track_ids == {TRACK_BASE_ID, TRACK_CURRENT_ID, TRACK_SKEPTIC_ID}

        dated = connection.execute(text("SELECT published_at_unknown FROM evidence_items WHERE id = :id"), {"id": EVIDENCE_DATED_ID}).scalar_one()
        undated = connection.execute(text("SELECT published_at_unknown FROM evidence_items WHERE id = :id"), {"id": EVIDENCE_UNDATED_ID}).scalar_one()
        assert int(dated) == 0
        assert int(undated) == 1

        assert connection.execute(text("SELECT ensemble_probability FROM forecast_versions WHERE id = :id"), {"id": VERSION_ID}).scalar_one() == 0.374
        job = connection.execute(text("SELECT available_at, error_history_json FROM jobs WHERE id = :id"), {"id": JOB_ID}).one()
        assert job.available_at is not None
        assert job.error_history_json == "[]"
        bench = connection.execute(
            text("SELECT exact_yes, exact_no, authoritative_source FROM benchmark_questions WHERE id = :id"),
            {"id": BENCH_Q_ID},
        ).one()
        assert bench.exact_yes
        assert bench.exact_no
        assert bench.authoritative_source
        assert connection.execute(text("SELECT id FROM benchmark_results WHERE id = :id"), {"id": BENCH_RESULT_ID}).scalar_one() == BENCH_RESULT_ID
        assert connection.execute(text("SELECT id FROM watches WHERE id = :id"), {"id": WATCH_ID}).scalar_one() == WATCH_ID
        assert connection.execute(text("SELECT COUNT(*) FROM evidence_claims")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM forecast_node_runs")).scalar_one() == 0


def _assert_integrity_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    names = set(inspector.get_table_names())
    assert REQUIRED_TABLES <= names
    question_cols = {column["name"] for column in inspector.get_columns("questions")}
    run_cols = {column["name"] for column in inspector.get_columns("forecast_runs")}
    evidence_cols = {column["name"] for column in inspector.get_columns("evidence_items")}
    job_cols = {column["name"] for column in inspector.get_columns("jobs")}
    result_cols = {column["name"] for column in inspector.get_columns("benchmark_results")}
    assert REQUIRED_QUESTION_COLUMNS <= question_cols
    assert REQUIRED_RUN_COLUMNS <= run_cols
    assert "published_at_unknown" in evidence_cols
    assert {"lease_owner", "lease_expires_at", "available_at", "error_category", "error_history_json"} <= job_cols
    assert {"experiment_id", "benchmark_task_id"} <= result_cols
    version = inspect(engine).get_table_names()
    assert "alembic_version" in version
    with engine.connect() as connection:
        current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert current == "20260822_0012"


def _assert_uniqueness(engine: Engine) -> None:
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO research_tracks (id, run_id, track_type, plan_json, key_drivers_json, "
                    "counterarguments_json, unresolved_json, status, independent) "
                    "VALUES ('dup-track', :run, 'base_rate', '{}', '[]', '[]', '[]', 'completed', 1)"
                ),
                {"run": RUN_ID},
            )
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO forecast_versions (id, question_id, run_id, raw_track_probabilities_json, "
                    "aggregation_json, shrinkage, key_drivers_json, counterarguments_json, evidence_ids_json, "
                    "created_at, trigger_event) VALUES ('dup-version', :qid, :run, '{}', '{}', 0.1, '[]', '[]', '[]', "
                    "'2026-01-16 00:00:00', 'run')"
                ),
                {"qid": QUESTION_ID, "run": RUN_ID},
            )


def _migrate(tmp_path: Path, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from forecastlab_api.config import settings

    db_url = str(engine.url)
    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    apply_schema(db_url)


def test_legacy_schema_is_not_current_metadata() -> None:
    sql = Path(__file__).parent.joinpath("fixtures", "legacy_mvp_schema.sql").read_text(encoding="utf-8")
    assert "requested_mode" not in sql
    assert "execution_context_json" not in sql
    assert "benchmark_datasets" not in sql
    assert "benchmark_experiments" not in sql
    assert LEGACY_MVP_COMMIT in sql


def test_unversioned_mvp_database_upgrades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _build_legacy_db(tmp_path / "legacy.db", stamped=False)
    _migrate(tmp_path, engine, monkeypatch)
    _assert_integrity_schema(engine)
    _assert_legacy_rows_survived(engine)
    _assert_uniqueness(engine)


def test_stamped_baseline_missing_integrity_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _build_legacy_db(tmp_path / "stamped.db", stamped=True)
    before = {column["name"] for column in inspect(engine).get_columns("questions")}
    assert "requested_mode" not in before
    _migrate(tmp_path, engine, monkeypatch)
    _assert_integrity_schema(engine)
    _assert_legacy_rows_survived(engine)
    _assert_uniqueness(engine)


def test_forecast_contract_migration_preserves_existing_resolution_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _build_legacy_db(tmp_path / "forecast-contract.db", stamped=True)
    _migrate(tmp_path, engine, monkeypatch)
    with engine.connect() as connection:
        legacy_count = connection.execute(
            text("SELECT COUNT(*) FROM resolution_contracts WHERE id = :id"),
            {"id": CONTRACT_ID},
        ).scalar_one()
        new_count = connection.execute(text("SELECT COUNT(*) FROM forecast_contracts")).scalar_one()
        graph_count = connection.execute(text("SELECT COUNT(*) FROM forecast_graphs")).scalar_one()
    assert legacy_count == 1
    assert new_count == 0
    assert graph_count == 0
    assert "resolution_method" in {
        column["name"] for column in inspect(engine).get_columns("forecast_contracts")
    }


def test_empty_database_reaches_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(tmp_path / "empty.db")
    _migrate(tmp_path, engine, monkeypatch)
    _assert_integrity_schema(engine)


def test_identical_duplicate_versions_collapse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _build_legacy_db(tmp_path / "dups.db", stamped=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO forecast_versions (id, question_id, run_id, raw_track_probabilities_json, "
                "ensemble_probability, aggregation_json, shrinkage, track_spread, key_drivers_json, "
                "counterarguments_json, evidence_ids_json, created_at, trigger_event, previous_version_id) "
                "SELECT '66666666-6666-6666-6666-666666666667', question_id, run_id, raw_track_probabilities_json, "
                "ensemble_probability, aggregation_json, shrinkage, track_spread, key_drivers_json, "
                "counterarguments_json, evidence_ids_json, created_at, trigger_event, previous_version_id "
                "FROM forecast_versions WHERE id = :id"
            ),
            {"id": VERSION_ID},
        )
    _migrate(tmp_path, engine, monkeypatch)
    with engine.connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM forecast_versions WHERE run_id = :id"), {"id": RUN_ID}).scalar_one()
        assert count == 1


def test_distinct_duplicate_versions_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _build_legacy_db(tmp_path / "unsafe.db", stamped=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO forecast_versions (id, question_id, run_id, raw_track_probabilities_json, "
                "ensemble_probability, aggregation_json, shrinkage, track_spread, key_drivers_json, "
                "counterarguments_json, evidence_ids_json, created_at, trigger_event, previous_version_id) "
                "VALUES ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', :qid, :run, '{}', 0.5, '{}', 0.1, 0.2, '[]', '[]', '[]', "
                "'2026-01-16 00:00:00', 'rerun', NULL)"
            ),
            {"qid": QUESTION_ID, "run": RUN_ID},
        )
    with pytest.raises(RuntimeError, match="Refusing to discard forecast history"):
        _migrate(tmp_path, engine, monkeypatch)


def test_migration_failure_stops_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from forecastlab_api import migrate as migrate_mod
    from forecastlab_api.config import settings

    engine = _build_legacy_db(tmp_path / "fail.db", stamped=True)
    monkeypatch.setattr(settings, "database_url", str(engine.url))
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    def boom(_cfg, _rev) -> None:
        raise RuntimeError("synthetic migration failure")

    monkeypatch.setattr(migrate_mod.command, "upgrade", boom)
    with pytest.raises(RuntimeError, match="synthetic migration failure"):
        apply_schema(str(engine.url))
    cols = {column["name"] for column in inspect(engine).get_columns("questions")}
    assert "requested_mode" not in cols


@pytest.fixture()
def upgraded_legacy_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    db_path = tmp_path / "api.db"
    engine = _build_legacy_db(db_path, stamped=False)
    db_url = str(engine.url)

    monkeypatch.setenv("FORECASTLAB_DATABASE_URL", db_url)
    monkeypatch.setenv("FORECASTLAB_EMBEDDED_WORKER", "true")
    monkeypatch.setenv("FORECASTLAB_ALLOW_LOCAL_FIXTURES", "true")

    from forecastlab_api import db as db_module
    from forecastlab_api import main as main_mod
    from forecastlab_api.config import settings

    session_engine = create_engine(db_url, future=True, connect_args={"check_same_thread": False, "timeout": 30})
    from forecastlab_api.db import configure_sqlite_engine

    configure_sqlite_engine(session_engine)
    SessionLocal = sessionmaker(bind=session_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "embedded_worker", True)
    monkeypatch.setattr(settings, "allow_local_fixtures", True)
    monkeypatch.setattr(settings, "credentials_path", tmp_path / "credentials.json")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(db_module, "engine", session_engine)
    monkeypatch.setattr(db_module, "SessionLocal", SessionLocal)
    monkeypatch.setattr(main_mod, "SessionLocal", SessionLocal)

    def get_db():
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    main_mod.app.dependency_overrides[main_mod.get_db] = get_db
    with TestClient(main_mod.app) as test_client:
        yield test_client
    main_mod.app.dependency_overrides.clear()


def test_api_starts_and_demo_forecast_works_after_legacy_upgrade(upgraded_legacy_client: TestClient) -> None:
    health = upgraded_legacy_client.get("/health")
    assert health.status_code == 200
    dashboard = upgraded_legacy_client.get("/api/dashboard")
    assert dashboard.status_code == 200
    texts = [item.get("original_text") for item in dashboard.json()["questions"]]
    assert LEGACY_QUESTION in texts

    created = upgraded_legacy_client.post(
        "/api/questions",
        json={"question": "Will the US unemployment rate exceed 5% before 30 June 2027?", "mode": "demo", "start": False},
    )
    assert created.status_code == 200
    qid = created.json()["id"]
    assert upgraded_legacy_client.post(f"/api/questions/{qid}/operationalize").status_code == 200
    run = upgraded_legacy_client.post(f"/api/questions/{qid}/runs", json={"profile_id": "three_track_ensemble", "mode": "demo"})
    assert run.status_code == 200
    assert run.json()["status"] == "completed"
    report = upgraded_legacy_client.get(f"/api/questions/{qid}/report").json()
    assert report["latest_run"]["execution_context"]["effective_mode"] == "demo"
    assert len(report["latest_run"]["tracks"]) == 3
