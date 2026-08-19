"""Add missing integrity columns, tables, and uniqueness to legacy MVP databases.

Revision ID: 20260819_0002
Revises: 20260818_0001
Create Date: 2026-08-19

This revision inspects the live schema before altering it. It is a no-op on an
empty database that already received the current metadata from 20260818_0001.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20260819_0002"
down_revision = "20260818_0001"
branch_labels = None
depends_on = None


class LegacyUpgradeError(RuntimeError):
    """Raised when a uniqueness constraint would discard distinct forecast history."""


def upgrade() -> None:
    bind = op.get_bind()
    _create_missing_tables(bind)
    _add_missing_columns()
    _backfill(bind)
    _install_unique_constraints(bind)


def downgrade() -> None:
    raise NotImplementedError("Legacy integrity upgrade 20260819_0002 is not reversible.")


def _inspector(bind):
    return inspect(bind)


def _columns(bind, table: str) -> set[str]:
    if table not in _inspector(bind).get_table_names():
        return set()
    return {column["name"] for column in _inspector(bind).get_columns(table)}


def _has_unique(bind, table: str, columns: set[str]) -> bool:
    inspector = _inspector(bind)
    if table not in inspector.get_table_names():
        return False
    for item in inspector.get_unique_constraints(table):
        if set(item.get("column_names") or []) == columns:
            return True
    for item in inspector.get_indexes(table):
        if item.get("unique") and set(item.get("column_names") or []) == columns:
            return True
    return False


def _add_column(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if table not in _inspector(bind).get_table_names():
        return
    if column.name in _columns(bind, table):
        return
    op.add_column(table, column)


def _create_missing_tables(bind) -> None:
    names = set(_inspector(bind).get_table_names())
    if "benchmark_datasets" not in names:
        op.create_table(
            "benchmark_datasets",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("dataset_hash", sa.String(64), nullable=False),
            sa.Column("provenance", sa.String(128), nullable=False, server_default="user_import"),
            sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("question_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.UniqueConstraint("dataset_hash", name="uq_benchmark_datasets_hash"),
        )
    if "benchmark_experiments" not in names:
        op.create_table(
            "benchmark_experiments",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("dataset_id", sa.String(36), sa.ForeignKey("benchmark_datasets.id"), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("code_commit", sa.String(64), nullable=True),
            sa.Column("execution_context_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("model_provider", sa.String(64), nullable=False, server_default="mock"),
            sa.Column("model_name", sa.String(128), nullable=False, server_default="mock-forecast-v1"),
            sa.Column("search_provider", sa.String(64), nullable=False, server_default="mock"),
            sa.Column("profile_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("profile_hashes_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("prompt_hashes_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("evidence_policy", sa.String(64), nullable=False, server_default="synthetic_historical_fixtures"),
            sa.Column("experiment_hash", sa.String(64), nullable=False, server_default=""),
            sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("total_tasks", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completed_tasks", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_tasks", sa.Integer(), nullable=False, server_default="0"),
        )
    if "benchmark_tasks" not in names:
        op.create_table(
            "benchmark_tasks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("experiment_id", sa.String(36), sa.ForeignKey("benchmark_experiments.id"), nullable=False),
            sa.Column("benchmark_question_id", sa.String(36), sa.ForeignKey("benchmark_questions.id"), nullable=False),
            sa.Column("profile_id", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("run_id", sa.String(36), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("experiment_id", "benchmark_question_id", "profile_id", name="uq_benchmark_task"),
        )


def _add_missing_columns() -> None:
    _add_column("questions", sa.Column("requested_mode", sa.String(32), nullable=False, server_default="demo"))
    _add_column(
        "questions",
        sa.Column("requested_profile_id", sa.String(64), nullable=False, server_default="three_track_ensemble"),
    )
    _add_column("questions", sa.Column("requested_as_of", sa.DateTime(timezone=True), nullable=True))
    _add_column("questions", sa.Column("is_benchmark", sa.Boolean(), nullable=False, server_default=sa.false()))

    _add_column("forecast_runs", sa.Column("execution_context_json", sa.Text(), nullable=False, server_default="{}"))
    _add_column("forecast_runs", sa.Column("configuration_hash", sa.String(64), nullable=True))
    _add_column("forecast_runs", sa.Column("evidence_policy", sa.String(64), nullable=True))
    _add_column("forecast_runs", sa.Column("fixture_evidence_used", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add_column("forecast_runs", sa.Column("code_commit", sa.String(64), nullable=True))
    _add_column("forecast_runs", sa.Column("synthetic_fixture_run", sa.Boolean(), nullable=False, server_default=sa.false()))

    _add_column("evidence_items", sa.Column("published_at_unknown", sa.Boolean(), nullable=False, server_default=sa.false()))

    _add_column("jobs", sa.Column("lease_owner", sa.String(64), nullable=True))
    _add_column("jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    _add_column("jobs", sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
    _add_column("jobs", sa.Column("error_category", sa.String(64), nullable=True))
    _add_column("jobs", sa.Column("error_history_json", sa.Text(), nullable=False, server_default="[]"))

    _add_column("benchmark_questions", sa.Column("dataset_id", sa.String(36), nullable=True))
    _add_column("benchmark_results", sa.Column("experiment_id", sa.String(36), nullable=True))
    _add_column("benchmark_results", sa.Column("benchmark_task_id", sa.String(36), nullable=True))


def _backfill(bind) -> None:
    names = set(_inspector(bind).get_table_names())
    if "questions" in names:
        if "forecast_runs" in names:
            bind.execute(
                text(
                    """
                    UPDATE questions
                    SET requested_profile_id = COALESCE(
                        (
                            SELECT profile_id FROM forecast_runs
                            WHERE forecast_runs.question_id = questions.id
                            ORDER BY started_at ASC
                            LIMIT 1
                        ),
                        requested_profile_id,
                        'three_track_ensemble'
                    )
                    """
                )
            )
        bind.execute(text("UPDATE questions SET requested_mode = 'demo' WHERE requested_mode IS NULL OR requested_mode = ''"))
        bind.execute(
            text(
                "UPDATE questions SET requested_profile_id = 'three_track_ensemble' "
                "WHERE requested_profile_id IS NULL OR requested_profile_id = ''"
            )
        )
        bind.execute(text("UPDATE questions SET is_benchmark = 0 WHERE is_benchmark IS NULL"))
    if "forecast_runs" in names:
        bind.execute(text("UPDATE forecast_runs SET execution_context_json = '{}' WHERE execution_context_json IS NULL OR execution_context_json = ''"))
        bind.execute(text("UPDATE forecast_runs SET fixture_evidence_used = 0 WHERE fixture_evidence_used IS NULL"))
        bind.execute(text("UPDATE forecast_runs SET synthetic_fixture_run = 0 WHERE synthetic_fixture_run IS NULL"))
        # Leave configuration_hash, evidence_policy, and code_commit null. Do not invent them.
    if "evidence_items" in names:
        bind.execute(text("UPDATE evidence_items SET published_at_unknown = CASE WHEN published_at IS NULL THEN 1 ELSE 0 END"))
    if "jobs" in names:
        bind.execute(text("UPDATE jobs SET error_history_json = '[]' WHERE error_history_json IS NULL OR error_history_json = ''"))
        bind.execute(text("UPDATE jobs SET available_at = COALESCE(available_at, created_at) WHERE available_at IS NULL"))


def _install_unique_constraints(bind) -> None:
    _prepare_unique(bind, "research_tracks", ["run_id", "track_type"], "uq_track_run_type")
    _prepare_unique(bind, "forecast_versions", ["run_id"], "uq_forecast_version_run")
    _prepare_unique(bind, "benchmark_tasks", ["experiment_id", "benchmark_question_id", "profile_id"], "uq_benchmark_task")
    _prepare_unique(bind, "benchmark_results", ["benchmark_task_id"], "uq_benchmark_result_task")


def _prepare_unique(bind, table: str, key_columns: list[str], name: str) -> None:
    inspector = _inspector(bind)
    if table not in inspector.get_table_names():
        return
    present = _columns(bind, table)
    if any(column not in present for column in key_columns):
        return
    _collapse_or_fail_duplicates(bind, table, key_columns)
    if _has_unique(bind, table, set(key_columns)):
        return
    with op.batch_alter_table(table) as batch:
        batch.create_unique_constraint(name, key_columns)


def _collapse_or_fail_duplicates(bind, table: str, key_columns: list[str]) -> None:
    column_names = [column["name"] for column in _inspector(bind).get_columns(table)]
    key_sql = ", ".join(key_columns)
    not_null = " AND ".join(f"{column} IS NOT NULL" for column in key_columns)
    groups = bind.execute(
        text(f"SELECT {key_sql}, COUNT(*) AS n FROM {table} WHERE {not_null} GROUP BY {key_sql} HAVING n > 1")
    ).fetchall()
    if not groups:
        return
    for group in groups:
        where = " AND ".join(f"{column} = :{column}" for column in key_columns)
        params = {column: group._mapping[column] for column in key_columns}
        rows = bind.execute(text(f"SELECT * FROM {table} WHERE {where}"), params).mappings().all()
        signatures = [_row_signature(row, column_names) for row in rows]
        if len(set(signatures)) != 1:
            raise LegacyUpgradeError(
                f"Cannot add uniqueness on {table}({', '.join(key_columns)}): "
                f"found {len(rows)} distinct rows for {params}. "
                "Refusing to discard forecast history. Resolve the duplicates and retry the migration."
            )
        keep_id = sorted(row["id"] for row in rows)[0]
        bind.execute(text(f"DELETE FROM {table} WHERE {where} AND id != :keep_id"), {**params, "keep_id": keep_id})


def _row_signature(row, column_names: list[str]) -> tuple:
    return tuple(row[name] for name in column_names if name != "id")
