from datetime import UTC, datetime

from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from forecastlab_api.evidence_claims import evidence_claim_from_row
from forecastlab_api.migrate import alembic_config, apply_migrations, apply_schema
from forecastlab_api.models import EvidenceClaimRow, ForecastGraphRow


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
    assert "forecast_failures" in tables
    assert "research_plans" in tables
    assert "evidence_sufficiency_assessments" in tables
    assert "material_node_coverage_assessments" in tables
    assert "scenario_syntheses" in tables
    assert "evaluation_releases" in tables
    assert "evaluation_release_questions" in tables
    assert "historical_evidence_releases" in tables
    assert "historical_evidence_packets" in tables
    assert "historical_evidence_candidates" in tables
    assert "historical_evidence_documents" in tables
    assert "historical_evidence_packet_documents" in tables
    assert "manual_evidence_attachments" in tables
    assert "procedural_ai_review_artifacts" in tables
    assert "evaluation_test_split_executions" in tables
    assert "alembic_version" in tables
    question_cols = {column["name"] for column in inspect(engine).get_columns("questions")}
    assert "requested_mode" in question_cols
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260904_0031"
        assert {"personal_forecasts", "prospective_cohorts", "prospective_entries", "prospective_assignments", "prospective_outcomes"} <= set(tables)
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
        evidence_item_cols = {
            column["name"] for column in inspect(engine).get_columns("evidence_items")
        }
        assert {
            "source_available_at",
            "temporal_basis",
            "publication_date_source",
            "publication_date_verified",
            "publication_date_hint",
            "publication_date_hint_source",
            "modified_at",
            "modified_date_source",
            "manual_evidence_attachment_id",
        } <= evidence_item_cols
        evidence_claim_cols = {
            column["name"] for column in inspect(engine).get_columns("evidence_claims")
        }
        assert {
            "publication_date",
            "publication_date_source",
            "publication_date_verified",
            "retrieval_date",
            "source_available_at",
            "temporal_basis",
            "source_class",
            "extraction_method",
            "source_host",
        } <= evidence_claim_cols
        publication_column = next(
            column
            for column in inspect(engine).get_columns("evidence_claims")
            if column["name"] == "publication_date"
        )
        assert publication_column["nullable"] is True
        graph_cols = {column["name"] for column in inspect(engine).get_columns("forecast_graphs")}
        assert "generation_audit_json" in graph_cols
        evaluation_release_cols = {
            column["name"]
            for column in inspect(engine).get_columns("evaluation_releases")
        }
        assert {
            "procedural_review_policy_version",
            "reserve_order_json",
            "reserve_order_hash",
            "reserve_order_frozen_at",
            "review_manifest_json",
            "review_manifest_hash",
        } <= evaluation_release_cols
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
            "research_plan_json",
            "queries_attempted_json",
            "sources_checked_json",
            "critical_node",
            "impact",
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
            "category",
            "question_hash",
        } == evaluation_question_cols
        forecast_experiment_cols = {
            column["name"] for column in inspect(engine).get_columns("forecast_experiments")
        }
        assert {
            "id",
            "dataset_id",
            "evaluation_release_id",
            "evaluation_split",
            "historical_evidence_release_id",
            "historical_evidence_release_hash",
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
        forecast_failure_cols = {
            column["name"] for column in inspect(engine).get_columns("forecast_failures")
        }
        assert {
            "id",
            "experiment_id",
            "forecast_experiment_run_id",
            "category",
            "annotation",
            "created_by",
            "created_at",
            "updated_at",
        } == forecast_failure_cols
        research_plan_cols = {
            column["name"] for column in inspect(engine).get_columns("research_plans")
        }
        assert {
            "id",
            "forecast_run_id",
            "selected_nodes_json",
            "skipped_nodes_json",
            "priority_scores_json",
            "budget_allocation_json",
            "created_at",
        } == research_plan_cols
        material_cols = {
            column["name"]
            for column in inspect(engine).get_columns(
                "material_node_coverage_assessments"
            )
        }
        assert {
            "id",
            "forecast_run_id",
            "policy_version",
            "status",
            "graph_id",
            "graph_version",
            "research_plan_id",
            "evidence_sufficiency_assessment_id",
            "evidence_sufficiency_assessment_hash",
            "included_frontier_weight",
            "maximum_excluded_weight",
            "higher_importance_excluded_node_ids_json",
            "missing_parent_relationships_json",
            "missing_dependency_relationships_json",
            "policy_snapshot_json",
            "assessment_input_hash",
        } <= material_cols
        scenario_cols = {
            column["name"]
            for column in inspect(engine).get_columns("scenario_syntheses")
        }
        assert {
            "id",
            "forecast_run_id",
            "policy_version",
            "policy_snapshot_json",
            "status",
            "created_at",
            "completed_at",
            "prompt_version",
            "provider",
            "model",
            "input_hash",
            "output_hash",
            "scenarios_json",
            "coverage_audit_json",
            "failure_reasons_json",
            "diagnostics_json",
            "evidence_sufficiency_assessment_id",
            "evidence_sufficiency_assessment_hash",
            "material_node_coverage_assessment_id",
            "material_node_coverage_assessment_hash",
        } == scenario_cols
        release_cols = {
            column["name"]
            for column in inspect(engine).get_columns("evaluation_releases")
        }
        assert {
            "id",
            "name",
            "version",
            "policy_version",
            "status",
            "development_dataset_id",
            "validation_dataset_id",
            "test_dataset_id",
            "created_at",
            "reviewed_at",
            "frozen_at",
            "correction_of_release_id",
            "correction_summary",
            "execution_manifest_json",
            "execution_manifest_hash",
            "scoring_manifest_json",
            "scoring_manifest_hash",
            "preregistration_json",
            "preregistration_hash",
            "procedural_review_policy_version",
            "reserve_order_json",
            "reserve_order_hash",
            "reserve_order_frozen_at",
            "review_manifest_json",
            "review_manifest_hash",
            "release_hash",
        } == release_cols
        release_question_cols = {
            column["name"]
            for column in inspect(engine).get_columns("evaluation_release_questions")
        }
        assert {
            "id",
            "release_id",
            "evaluation_question_id",
            "split",
            "event_family_id",
            "leakage_group_id",
            "inclusion_status",
            "exclusion_reason",
            "question_author_id",
            "question_reviewer_id",
            "outcome_adjudicator_id",
            "review_completed_at",
            "outcome_known_at",
            "source_license_status",
            "source_use_basis",
            "redistribution_allowed",
            "adjudication_notes",
            "adjudication_record_hash",
        } == release_question_cols
        procedural_artifact_cols = {
            column["name"]
            for column in inspect(engine).get_columns(
                "procedural_ai_review_artifacts"
            )
        }
        assert {
            "evaluation_release_id",
            "evaluation_release_question_id",
            "evaluation_question_id",
            "artifact_type",
            "rubric_version",
            "rubric_hash",
            "model_provider",
            "model_id",
            "model_version",
            "tool_name",
            "tool_version",
            "run_id",
            "input_manifest_hash",
            "output_hash",
            "gate_status",
            "source_citation_ids_json",
        } <= procedural_artifact_cols


def test_material_node_coverage_migration_does_not_backfill_historical_runs(
    tmp_path,
) -> None:
    db_url = f"sqlite:///{tmp_path}/material-node.db"
    command.upgrade(alembic_config(db_url), "20260826_0024")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO questions "
                "(id, original_text, question_type, created_at, status, stale, "
                "requested_mode, requested_profile_id, is_benchmark) VALUES "
                "('material-question', 'Will it occur?', 'binary', "
                "'2026-08-26 00:00:00', 'complete', 0, 'demo', "
                "'graph_forecaster_v1', 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO forecast_runs "
                "(id, question_id, profile_id, mode, status, cost_usd, tokens, "
                "latency_ms, provider_json, prompt_versions_json, budget_json, "
                "aggregation_json, progress_pct, progress_stage, progress_message) "
                "VALUES ('material-run', 'material-question', "
                "'graph_forecaster_v1', 'demo', 'completed', 0, 0, 0, '{}', "
                "'{}', '{}', '{}', 100, 'report', 'Forecast ready')"
            )
        )

    command.upgrade(alembic_config(db_url), "head")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT id FROM forecast_runs WHERE id = 'material-run'")
        ).scalar_one() == "material-run"
        assert connection.execute(
            text("SELECT COUNT(*) FROM material_node_coverage_assessments")
        ).scalar_one() == 0
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    command.downgrade(alembic_config(db_url), "20260826_0024")
    assert "material_node_coverage_assessments" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT id FROM forecast_runs WHERE id = 'material-run'")
        ).scalar_one() == "material-run"


def test_scenario_synthesis_migration_is_empty_and_reversible(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/scenario-synthesis.db"
    command.upgrade(alembic_config(db_url), "20260826_0025")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO questions "
                "(id, original_text, question_type, created_at, status, stale, "
                "requested_mode, requested_profile_id, is_benchmark) VALUES "
                "('scenario-question', 'Will it occur?', 'binary', "
                "'2026-08-26 00:00:00', 'complete', 0, 'demo', "
                "'graph_forecaster_v1', 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO forecast_runs "
                "(id, question_id, profile_id, mode, status, cost_usd, tokens, "
                "latency_ms, provider_json, prompt_versions_json, budget_json, "
                "aggregation_json, progress_pct, progress_stage, progress_message) "
                "VALUES ('scenario-run', 'scenario-question', "
                "'graph_forecaster_v1', 'demo', 'completed', 0, 0, 0, '{}', "
                "'{}', '{}', '{}', 100, 'report', 'Forecast ready')"
            )
        )

    command.upgrade(alembic_config(db_url), "head")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM scenario_syntheses")
        ).scalar_one() == 0
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    command.downgrade(alembic_config(db_url), "20260826_0025")
    assert "scenario_syntheses" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT id FROM forecast_runs WHERE id = 'scenario-run'")
        ).scalar_one() == "scenario-run"

    command.upgrade(alembic_config(db_url), "head")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM scenario_syntheses")
        ).scalar_one() == 0
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def test_real_evaluation_release_migration_is_empty_reversible_and_foreign_key_clean(
    tmp_path,
) -> None:
    db_url = f"sqlite:///{tmp_path}/evaluation-release.db"
    command.upgrade(alembic_config(db_url), "20260826_0026")
    engine = create_engine(db_url)

    command.upgrade(alembic_config(db_url), "head")
    assert "evaluation_releases" in inspect(engine).get_table_names()
    assert "evaluation_release_questions" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM evaluation_releases")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM evaluation_release_questions")
        ).scalar_one() == 0
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    command.downgrade(alembic_config(db_url), "20260826_0026")
    tables = inspect(engine).get_table_names()
    assert "evaluation_releases" not in tables
    assert "evaluation_release_questions" not in tables
    assert "historical_evidence_releases" not in tables
    assert "historical_evidence_packets" not in tables
    assert "historical_evidence_candidates" not in tables
    assert "historical_evidence_documents" not in tables
    assert "historical_evidence_packet_documents" not in tables
    experiment_columns = {
        column["name"]
        for column in inspect(engine).get_columns("forecast_experiments")
    }
    assert "evaluation_release_id" not in experiment_columns
    assert "evaluation_split" not in experiment_columns

    command.upgrade(alembic_config(db_url), "head")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM evaluation_releases")
        ).scalar_one() == 0
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def test_apply_schema_upgrades_empty_database(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path}/schema.db"
    from forecastlab_api.config import settings

    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    apply_schema(db_url)
    engine = create_engine(db_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260904_0031"


def test_manual_evidence_migration_is_empty_reversible_and_disables_legacy_auto_rerun(
    tmp_path,
) -> None:
    db_url = f"sqlite:///{tmp_path}/manual-evidence.db"
    command.upgrade(alembic_config(db_url), "20260826_0028")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO questions "
                "(id, original_text, question_type, created_at, status, stale, "
                "requested_mode, requested_profile_id, is_benchmark) VALUES "
                "('manual-question', 'Will it occur?', 'binary', "
                "'2026-08-29 00:00:00', 'complete', 0, 'demo', "
                "'three_track_ensemble', 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO watches "
                "(id, question_id, endpoint_url, endpoint_type, json_path, poll_seconds, "
                "status, auto_rerun) VALUES "
                "('legacy-auto-watch', 'manual-question', 'https://example.org/status', "
                "'json', '$.value', 300, 'active', 1)"
            )
        )

    command.upgrade(alembic_config(db_url), "head")
    inspector = inspect(engine)
    assert "manual_evidence_attachments" in inspector.get_table_names()
    evidence_columns = {
        column["name"] for column in inspector.get_columns("evidence_items")
    }
    assert "manual_evidence_attachment_id" in evidence_columns
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT auto_rerun FROM watches WHERE id = 'legacy-auto-watch'")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM manual_evidence_attachments")
        ).scalar_one() == 0
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    command.downgrade(alembic_config(db_url), "20260826_0028")
    inspector = inspect(engine)
    assert "manual_evidence_attachments" not in inspector.get_table_names()
    assert "manual_evidence_attachment_id" not in {
        column["name"] for column in inspector.get_columns("evidence_items")
    }
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def test_mode_aware_temporal_migration_preserves_existing_evidence_claims(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/evidence-temporal.db"
    command.upgrade(alembic_config(db_url), "20260824_0021")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO questions "
                "(id, original_text, question_type, created_at, status, stale, requested_mode, "
                "requested_profile_id, is_benchmark) VALUES "
                "('question-temporal', 'Will the outcome occur?', 'binary', "
                "'2026-08-24 00:00:00', 'complete', 0, 'live', 'graph_forecaster_v1', 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO forecast_contracts "
                "(id, question_id, original_question, normalized_question, status) VALUES "
                "('contract-temporal', 'question-temporal', 'Will the outcome occur?', "
                "'Will the defined outcome occur?', 'approved')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO forecast_graphs "
                "(id, contract_id, status, generation_model, root_question) VALUES "
                "('graph-temporal', 'contract-temporal', 'approved', 'fixture', "
                "'Will the defined outcome occur?')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO forecast_nodes "
                "(id, graph_id, question, node_type, importance_weight, required_output_type) VALUES "
                "('node-temporal', 'graph-temporal', 'What evidence bears on the outcome?', "
                "'driver', 1.0, 'probability')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO forecast_runs "
                "(id, question_id, profile_id, mode, started_at, finished_at, status, cost_usd, tokens, "
                "latency_ms, provider_json, prompt_versions_json, budget_json, aggregation_json, "
                "progress_pct, progress_stage, progress_message) VALUES "
                "('run-temporal', 'question-temporal', 'graph_forecaster_v1', 'live', "
                "'2026-08-24 00:00:00', '2026-08-24 00:02:00', 'completed', 0, 0, 0, "
                "'{}', '{}', '{}', '{}', 100, 'report', 'Forecast ready')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO evidence_items "
                "(id, run_id, url, title, publisher, published_at, retrieved_at, excerpt, content_hash, "
                "source_class, as_of_eligible, rejected, status_code, published_at_unknown) VALUES "
                "('item-temporal', 'run-temporal', 'https://example.org/source', 'Source', 'Publisher', "
                "'2026-08-23 12:00:00', '2026-08-24 00:01:00', 'Exact excerpt', :hash, "
                "'secondary', 1, 0, 200, 0)"
            ),
            {"hash": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO evidence_claims "
                "(id, evidence_item_id, forecast_node_id, claim, excerpt, source_url, source_title, "
                "publisher, publication_date, retrieval_date, supports_or_refutes, confidence, "
                "source_quality, primary_source, as_of_eligible, cutoff_verified) VALUES "
                "('claim-temporal', 'item-temporal', 'node-temporal', 'Exact claim', 'Exact excerpt', "
                "'https://example.org/source', 'Source', 'Publisher', '2026-08-23 12:00:00', "
                "'2026-08-24 00:01:00', 'supports', 0.8, 0.7, 0, 1, 1)"
            )
        )

    command.upgrade(alembic_config(db_url), "head")

    with Session(engine) as session:
        row = session.get(EvidenceClaimRow, "claim-temporal")
        assert row is not None
        claim = evidence_claim_from_row(row)
        assert claim.publication_date == datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
        assert claim.retrieval_date == datetime(2026, 8, 24, 0, 1, tzinfo=UTC)
        assert claim.source_available_at == claim.retrieval_date
        assert claim.temporal_basis == "retrieval_date"
        assert claim.publication_date_verified is False
        assert claim.publication_date_source == "legacy_unverified_published_at"
        assert claim.source_class == "secondary"
        assert claim.extraction_method == "unknown_legacy"
        assert claim.source_host == "example.org"
        assert claim.forecasting_errors(mode="live", run_completion_time=claim.retrieval_date) == []
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def test_graph_generation_audit_migration_preserves_existing_graphs_without_fabricating_history(
    tmp_path,
) -> None:
    db_url = f"sqlite:///{tmp_path}/graph-audit.db"
    command.upgrade(alembic_config(db_url), "20260825_0022")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO questions "
                "(id, original_text, question_type, created_at, status, stale, requested_mode, "
                "requested_profile_id, is_benchmark) VALUES "
                "('question-graph-audit', 'Will the outcome occur?', 'binary', "
                "'2026-08-25 00:00:00', 'complete', 0, 'live', 'graph_live_smoke_v1', 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO forecast_contracts "
                "(id, question_id, original_question, normalized_question, status) VALUES "
                "('contract-graph-audit', 'question-graph-audit', 'Will the outcome occur?', "
                "'Will the defined outcome occur?', 'approved')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO forecast_graphs "
                "(id, contract_id, version, status, generation_model, root_question) VALUES "
                "('graph-audit-existing', 'contract-graph-audit', 4, 'approved', "
                "'openai:gpt-5-mini-2025-08-07', 'Will the defined outcome occur?')"
            )
        )

    command.upgrade(alembic_config(db_url), "head")

    with Session(engine) as session:
        graph = session.get(ForecastGraphRow, "graph-audit-existing")
        assert graph is not None
        assert graph.version == 4
        assert graph.generation_model == "openai:gpt-5-mini-2025-08-07"
        assert graph.generation_audit_json is None
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260904_0031"


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
            == "20260904_0031"
        )


def test_historical_evidence_migration_preserves_existing_experiments_without_fabricating_corpus(
    tmp_path,
) -> None:
    db_url = f"sqlite:///{tmp_path}/historical-evidence-release.db"
    command.upgrade(alembic_config(db_url), "20260826_0027")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO evaluation_datasets "
                "(id, name, version, hash, description, provenance, status, created_at, "
                "frozen_at, question_count) VALUES "
                "('dataset-before-evidence', 'Existing frozen dataset', '1', :hash, "
                "'description', 'source', 'frozen', '2026-08-26 00:00:00', "
                "'2026-08-26 00:00:00', 0)"
            ),
            {"hash": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO forecast_experiments "
                "(id, dataset_id, evaluation_release_id, evaluation_split, status, "
                "profiles_json, configuration_hash, configuration_json, created_at, completed_at) "
                "VALUES ('experiment-before-evidence', 'dataset-before-evidence', NULL, NULL, "
                "'completed', '[]', :hash, '{}', '2026-08-26 00:00:00', "
                "'2026-08-26 00:01:00')"
            ),
            {"hash": "b" * 64},
        )

    command.upgrade(alembic_config(db_url), "head")
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT historical_evidence_release_id, "
                "historical_evidence_release_hash FROM forecast_experiments "
                "WHERE id = 'experiment-before-evidence'"
            )
        ).one() == (None, None)
        for table in (
            "historical_evidence_releases",
            "historical_evidence_packets",
            "historical_evidence_candidates",
            "historical_evidence_documents",
            "historical_evidence_packet_documents",
        ):
            assert connection.execute(
                text(f"SELECT COUNT(*) FROM {table}")
            ).scalar_one() == 0
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    command.downgrade(alembic_config(db_url), "20260826_0027")
    assert "historical_evidence_releases" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT status FROM forecast_experiments "
                "WHERE id = 'experiment-before-evidence'"
            )
        ).scalar_one() == "completed"
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    command.upgrade(alembic_config(db_url), "head")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "20260904_0031"
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def test_pilot_category_migration_preserves_existing_frozen_question_hash(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/pilot-category.db"
    command.upgrade(alembic_config(db_url), "20260823_0018")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO evaluation_datasets "
                "(id, name, version, hash, description, provenance, status, created_at, "
                "frozen_at, question_count) VALUES "
                "('dataset-1', 'Historical release', '1', :dataset_hash, 'description', "
                "'source', 'frozen', '2026-08-23 00:00:00', '2026-08-23 00:00:00', 1)"
            ),
            {"dataset_hash": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO evaluation_questions "
                "(id, dataset_id, question, resolution_contract, forecast_date, resolution_date, "
                "outcome, resolution_source, domain, question_hash) VALUES "
                "('question-1', 'dataset-1', 'Did the recorded event occur?', '{}', "
                "'2024-01-01 00:00:00', '2024-04-01 00:00:00', 1, 'resolver', "
                "'test', :question_hash)"
            ),
            {"question_hash": "b" * 64},
        )

    command.upgrade(alembic_config(db_url), "head")

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT q.question_hash, q.category, d.hash "
                "FROM evaluation_questions q JOIN evaluation_datasets d ON d.id = q.dataset_id"
            )
        ).one()
        assert tuple(row) == ("b" * 64, None, "a" * 64)
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260904_0031"


def test_procedural_review_migration_preserves_existing_release_without_fabrication(
    tmp_path,
) -> None:
    db_url = f"sqlite:///{tmp_path}/procedural-review-upgrade.db"
    command.upgrade(alembic_config(db_url), "20260829_0029")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        for index, split in enumerate(("development", "validation", "test"), start=1):
            connection.execute(
                text(
                    "INSERT INTO evaluation_datasets "
                    "(id, name, version, hash, description, provenance, status, "
                    "created_at, frozen_at, question_count) VALUES "
                    "(:id, :name, '1', :hash, 'description', 'source', 'frozen', "
                    "'2026-08-31 00:00:00', '2026-08-31 00:00:00', 0)"
                ),
                {
                    "id": f"dataset-{index}",
                    "name": f"{split} dataset",
                    "hash": str(index) * 64,
                },
            )
        connection.execute(
            text(
                "INSERT INTO evaluation_releases "
                "(id, name, version, policy_version, status, development_dataset_id, "
                "validation_dataset_id, test_dataset_id, created_at, reviewed_at, frozen_at, "
                "correction_of_release_id, correction_summary, execution_manifest_json, "
                "execution_manifest_hash, scoring_manifest_json, scoring_manifest_hash, "
                "preregistration_json, preregistration_hash, release_hash) VALUES "
                "('legacy-release', 'Legacy release', '1', 'legacy-policy', 'draft', "
                "'dataset-1', 'dataset-2', 'dataset-3', '2026-08-31 00:00:00', NULL, NULL, "
                "NULL, NULL, '{}', :a, '{}', :b, '{}', :c, :d)"
            ),
            {"a": "a" * 64, "b": "b" * 64, "c": "c" * 64, "d": "d" * 64},
        )

    command.upgrade(alembic_config(db_url), "head")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT name, procedural_review_policy_version, reserve_order_json, "
                "reserve_order_hash, review_manifest_json, review_manifest_hash "
                "FROM evaluation_releases WHERE id = 'legacy-release'"
            )
        ).one()
        assert tuple(row) == ("Legacy release", None, None, None, None, None)
        assert connection.execute(
            text("SELECT COUNT(*) FROM procedural_ai_review_artifacts")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM evaluation_test_split_executions")
        ).scalar_one() == 0
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    command.downgrade(alembic_config(db_url), "20260829_0029")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT name FROM evaluation_releases WHERE id = 'legacy-release'")
        ).scalar_one() == "Legacy release"
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    command.upgrade(alembic_config(db_url), "head")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "20260904_0031"
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []


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


def test_forecast_analysis_migration_preserves_experiment_results(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/forecast-analysis.db"
    command.upgrade(alembic_config(db_url), "20260823_0017")
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO evaluation_datasets "
                "(id, name, version, hash, description, provenance, status, created_at, "
                "frozen_at, question_count) VALUES "
                "('dataset-1', 'Resolved release', '1', :hash, 'description', 'source', "
                "'frozen', '2026-08-23 00:00:00', '2026-08-23 00:00:00', 1)"
            ),
            {"hash": "b" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO evaluation_questions "
                "(id, dataset_id, question, resolution_contract, forecast_date, resolution_date, "
                "outcome, resolution_source, domain, question_hash) VALUES "
                "('evaluation-question-1', 'dataset-1', 'Did it happen?', '{}', "
                "'2025-01-01 00:00:00', '2025-12-31 00:00:00', 1, 'resolver', "
                "'test', :hash)"
            ),
            {"hash": "c" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO forecast_experiments "
                "(id, dataset_id, status, profiles_json, configuration_hash, configuration_json, "
                "created_at, completed_at) VALUES "
                "('experiment-1', 'dataset-1', 'completed', '[]', :hash, '{}', "
                "'2026-08-23 00:00:00', '2026-08-23 00:01:00')"
            ),
            {"hash": "d" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO forecast_experiment_runs "
                "(id, experiment_id, evaluation_question_id, profile_id, forecast_run_id, status, "
                "error, error_category, created_at, started_at, completed_at) VALUES "
                "('experiment-run-1', 'experiment-1', 'evaluation-question-1', "
                "'single_model_forecaster_v1', NULL, 'completed', NULL, NULL, "
                "'2026-08-23 00:00:00', '2026-08-23 00:00:00', '2026-08-23 00:01:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO forecast_experiment_results "
                "(id, experiment_run_id, probability, outcome, brier_score, log_loss, cost_usd, "
                "latency_ms, evidence_coverage, evidence_covered_units, evidence_total_units, "
                "completion_status, created_at) VALUES "
                "('result-1', 'experiment-run-1', 0.4, 1, 0.36, 0.916, 0.0, 12, 1.0, "
                "1, 1, 'completed', '2026-08-23 00:01:00')"
            )
        )

    command.upgrade(alembic_config(db_url), "head")

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT probability FROM forecast_experiment_results WHERE id = 'result-1'")
        ).scalar_one() == 0.4
        assert connection.execute(text("SELECT COUNT(*) FROM forecast_failures")).scalar_one() == 0


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
