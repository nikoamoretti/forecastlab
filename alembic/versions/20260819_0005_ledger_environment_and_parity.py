"""Provider ledger, Wayback metadata, environment identity, builtin datasets, FKs.

Revision ID: 20260819_0005
Revises: 20260819_0004
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20260819_0005"
down_revision = "20260819_0004"
branch_labels = None
depends_on = None


class SchemaParityError(RuntimeError):
    """Raised when a constraint would orphan child rows."""


def upgrade() -> None:
    bind = op.get_bind()
    _add_missing_columns()
    _create_ledger_tables(bind)
    _install_uniques(bind)
    _install_foreign_keys(bind)


def downgrade() -> None:
    raise NotImplementedError("Integrity repair 20260819_0005 is not reversible.")


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


def _has_fk(bind, table: str, columns: list[str], referred: str) -> bool:
    inspector = _inspector(bind)
    if table not in inspector.get_table_names():
        return False
    wanted = (tuple(columns), referred)
    for item in inspector.get_foreign_keys(table):
        if (tuple(item.get("constrained_columns") or []), item.get("referred_table")) == wanted:
            return True
    return False


def _add_column(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if table not in _inspector(bind).get_table_names():
        return
    if column.name in _columns(bind, table):
        return
    op.add_column(table, column)


def _add_missing_columns() -> None:
    _add_column("forecast_runs", sa.Column("model_cost_usd", sa.Float(), nullable=False, server_default="0"))
    _add_column("forecast_runs", sa.Column("search_cost_usd", sa.Float(), nullable=False, server_default="0"))
    _add_column("forecast_runs", sa.Column("failed_attempt_cost_usd", sa.Float(), nullable=False, server_default="0"))
    _add_column("forecast_runs", sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0"))
    _add_column("forecast_runs", sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"))
    _add_column("forecast_runs", sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"))
    _add_column("forecast_runs", sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"))
    _add_column("forecast_runs", sa.Column("provider_request_count", sa.Integer(), nullable=False, server_default="0"))
    _add_column("forecast_runs", sa.Column("run_attempt_count", sa.Integer(), nullable=False, server_default="0"))
    _add_column("forecast_runs", sa.Column("cost_source", sa.String(32), nullable=False, server_default="estimated"))

    _add_column("evidence_items", sa.Column("requested_snapshot_url", sa.Text(), nullable=True))
    _add_column("evidence_items", sa.Column("requested_snapshot_at", sa.DateTime(timezone=True), nullable=True))
    _add_column("evidence_items", sa.Column("final_snapshot_url", sa.Text(), nullable=True))
    _add_column("evidence_items", sa.Column("final_snapshot_at", sa.DateTime(timezone=True), nullable=True))
    _add_column("evidence_items", sa.Column("archived_original_url", sa.Text(), nullable=True))
    _add_column("evidence_items", sa.Column("snapshot_verification_status", sa.String(64), nullable=True))

    _add_column("benchmark_datasets", sa.Column("builtin_key", sa.String(128), nullable=True))
    _add_column("benchmark_datasets", sa.Column("builtin_version", sa.String(32), nullable=True))
    _add_column("benchmark_datasets", sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add_column("benchmark_datasets", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))

    _add_column("benchmark_experiments", sa.Column("environment_identity_json", sa.Text(), nullable=False, server_default="{}"))
    _add_column("benchmark_experiments", sa.Column("tracked_source_hash", sa.String(64), nullable=True))
    _add_column("benchmark_experiments", sa.Column("pyproject_hash", sa.String(64), nullable=True))
    _add_column("benchmark_experiments", sa.Column("dependency_hash", sa.String(64), nullable=True))
    _add_column("benchmark_experiments", sa.Column("package_lock_hash", sa.String(64), nullable=True))
    _add_column("benchmark_experiments", sa.Column("working_tree_dirty", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add_column("benchmark_experiments", sa.Column("application_version", sa.String(64), nullable=True))
    _add_column("benchmark_experiments", sa.Column("container_image_digest", sa.String(128), nullable=True))


def _create_ledger_tables(bind) -> None:
    names = set(_inspector(bind).get_table_names())
    if "forecast_run_attempts" not in names:
        op.create_table(
            "forecast_run_attempts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("forecast_runs.id"), nullable=False),
            sa.Column("job_id", sa.String(36), nullable=True),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_category", sa.String(64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("model_cost_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("search_cost_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("search_calls", sa.Integer(), nullable=False, server_default="0"),
            sa.UniqueConstraint("run_id", "attempt_number", name="uq_forecast_run_attempt"),
        )
    if "provider_call_ledger" not in names:
        op.create_table(
            "provider_call_ledger",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("forecast_runs.id"), nullable=False),
            sa.Column("run_attempt_id", sa.String(36), sa.ForeignKey("forecast_run_attempts.id"), nullable=True),
            sa.Column("logical_call_id", sa.String(36), nullable=False),
            sa.Column("physical_attempt_number", sa.Integer(), nullable=False),
            sa.Column("stage", sa.String(64), nullable=False),
            sa.Column("provider_type", sa.String(16), nullable=False),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column("model", sa.String(128), nullable=True),
            sa.Column("request_started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("request_completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("reserved_input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reserved_output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reserved_cost_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("actual_prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("actual_completion_tokens", sa.Integer(), nullable=True),
            sa.Column("actual_cost_usd", sa.Float(), nullable=True),
            sa.Column("cost_source", sa.String(32), nullable=False, server_default="reserved"),
            sa.Column("provider_request_id", sa.String(128), nullable=True),
            sa.Column("error_category", sa.String(64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.UniqueConstraint("run_id", "logical_call_id", "physical_attempt_number", name="uq_provider_call_physical"),
        )


def _install_uniques(bind) -> None:
    if "benchmark_datasets" not in _inspector(bind).get_table_names():
        return
    if _has_unique(bind, "benchmark_datasets", {"builtin_key", "builtin_version"}):
        return
    _fail_if_distinct_builtin_duplicates(bind)
    with op.batch_alter_table("benchmark_datasets") as batch:
        batch.create_unique_constraint("uq_benchmark_dataset_builtin", ["builtin_key", "builtin_version"])


def _fail_if_distinct_builtin_duplicates(bind) -> None:
    if not {"builtin_key", "builtin_version"} <= _columns(bind, "benchmark_datasets"):
        return
    rows = bind.execute(
        text(
            """
            SELECT builtin_key, builtin_version, COUNT(*) AS n
            FROM benchmark_datasets
            WHERE builtin_key IS NOT NULL AND builtin_version IS NOT NULL
            GROUP BY builtin_key, builtin_version
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    if rows:
        raise SchemaParityError(
            "Cannot add unique built-in dataset identity: duplicate key/version rows exist. "
            "Retarget or archive them before upgrading."
        )


def _install_foreign_keys(bind) -> None:
    wanted = (
        ("resolution_contracts", ["question_id"], "questions", ["id"], False),
        ("forecast_runs", ["question_id"], "questions", ["id"], False),
        ("forecast_runs", ["benchmark_task_id"], "benchmark_tasks", ["id"], True),
        ("research_tracks", ["run_id"], "forecast_runs", ["id"], False),
        ("subquestions", ["track_id"], "research_tracks", ["id"], False),
        ("evidence_items", ["run_id"], "forecast_runs", ["id"], False),
        ("evidence_items", ["track_id"], "research_tracks", ["id"], True),
        ("forecast_versions", ["question_id"], "questions", ["id"], False),
        ("forecast_versions", ["run_id"], "forecast_runs", ["id"], False),
        ("job_events", ["job_id"], "jobs", ["id"], False),
        ("benchmark_questions", ["dataset_id"], "benchmark_datasets", ["id"], True),
        ("benchmark_experiments", ["dataset_id"], "benchmark_datasets", ["id"], False),
        ("benchmark_tasks", ["experiment_id"], "benchmark_experiments", ["id"], False),
        ("benchmark_tasks", ["benchmark_question_id"], "benchmark_questions", ["id"], False),
        ("benchmark_tasks", ["question_id"], "questions", ["id"], True),
        ("benchmark_results", ["experiment_id"], "benchmark_experiments", ["id"], True),
        ("benchmark_results", ["benchmark_task_id"], "benchmark_tasks", ["id"], True),
        ("benchmark_results", ["benchmark_question_id"], "benchmark_questions", ["id"], False),
        ("benchmark_results", ["run_id"], "forecast_runs", ["id"], True),
        ("watches", ["question_id"], "questions", ["id"], False),
        ("watch_events", ["watch_id"], "watches", ["id"], False),
    )
    for table, columns, referred, referred_cols, nullable in wanted:
        if table not in _inspector(bind).get_table_names() or referred not in _inspector(bind).get_table_names():
            continue
        if any(column not in _columns(bind, table) for column in columns):
            continue
        if _has_fk(bind, table, columns, referred):
            continue
        _assert_fk_targets_exist(bind, table, columns[0], referred)
        with op.batch_alter_table(table) as batch:
            batch.create_foreign_key(
                f"fk_{table}_{columns[0]}",
                referred,
                columns,
                referred_cols,
            )
        del nullable


def _assert_fk_targets_exist(bind, table: str, column: str, referred: str) -> None:
    orphans = bind.execute(
        text(
            f"""
            SELECT COUNT(*) FROM {table}
            WHERE {column} IS NOT NULL
              AND {column} NOT IN (SELECT id FROM {referred})
            """
        )
    ).scalar_one()
    if orphans:
        raise SchemaParityError(
            f"Cannot install foreign key {table}.{column} -> {referred}.id: "
            f"{orphans} child rows reference missing parents. Retarget them first."
        )
