"""Freeze benchmark profile snapshots and per-dataset import uniqueness.

Revision ID: 20260819_0003
Revises: 20260819_0002
Create Date: 2026-08-19

Inspect-and-alter. A no-op on databases whose current metadata already includes
these columns and constraints (for example after 20260818_0001 create_all).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20260819_0003"
down_revision = "20260819_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    _create_snapshots_table(bind)
    _add_missing_columns()
    _backfill(bind)
    _replace_import_uniqueness(bind)


def downgrade() -> None:
    raise NotImplementedError("Experiment snapshot revision 20260819_0003 is not reversible.")


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


def _unique_name(bind, table: str, columns: set[str]) -> str | None:
    inspector = _inspector(bind)
    if table not in inspector.get_table_names():
        return None
    for item in inspector.get_unique_constraints(table):
        if set(item.get("column_names") or []) == columns:
            return item.get("name")
    for item in inspector.get_indexes(table):
        if item.get("unique") and set(item.get("column_names") or []) == columns:
            return item.get("name")
    return None


def _add_column(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if table not in _inspector(bind).get_table_names():
        return
    if column.name in _columns(bind, table):
        return
    op.add_column(table, column)


def _create_snapshots_table(bind) -> None:
    if "benchmark_profile_snapshots" in _inspector(bind).get_table_names():
        return
    op.create_table(
        "benchmark_profile_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("experiment_id", sa.String(36), sa.ForeignKey("benchmark_experiments.id"), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("source_profile_json", sa.Text(), nullable=False),
        sa.Column("effective_profile_json", sa.Text(), nullable=False),
        sa.Column("profile_hash", sa.String(64), nullable=False),
        sa.Column("prompt_bundle_json", sa.Text(), nullable=False),
        sa.Column("prompt_versions_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("prompt_hashes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("model_provider", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("model_base_url", sa.Text(), nullable=True),
        sa.Column("search_provider", sa.String(64), nullable=False),
        sa.Column("model_timeout_seconds", sa.Float(), nullable=False, server_default="60"),
        sa.Column("evidence_policy", sa.String(64), nullable=False),
        sa.Column("effective_max_cost_usd", sa.Float(), nullable=False),
        sa.Column("effective_max_tokens", sa.Integer(), nullable=False),
        sa.Column("effective_max_model_calls", sa.Integer(), nullable=False),
        sa.Column("effective_max_search_calls", sa.Integer(), nullable=False),
        sa.Column("effective_max_fetched_documents", sa.Integer(), nullable=False),
        sa.Column("effective_max_wall_clock_seconds", sa.Integer(), nullable=False),
        sa.Column("pricing_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("pricing_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("execution_context_json", sa.Text(), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("experiment_id", "profile_id", name="uq_benchmark_profile_snapshot"),
    )


def _add_missing_columns() -> None:
    _add_column("benchmark_experiments", sa.Column("model_base_url", sa.Text(), nullable=True))
    _add_column(
        "benchmark_experiments",
        sa.Column("model_timeout_seconds", sa.Float(), nullable=False, server_default="60"),
    )
    _add_column("benchmark_questions", sa.Column("exact_yes", sa.Text(), nullable=False, server_default=""))
    _add_column("benchmark_questions", sa.Column("exact_no", sa.Text(), nullable=False, server_default=""))
    _add_column("benchmark_questions", sa.Column("resolution_deadline", sa.DateTime(timezone=True), nullable=True))
    _add_column(
        "benchmark_questions",
        sa.Column("authoritative_source", sa.Text(), nullable=False, server_default=""),
    )
    _add_column(
        "benchmark_questions",
        sa.Column("fallback_sources_json", sa.Text(), nullable=False, server_default="[]"),
    )
    _add_column("benchmark_questions", sa.Column("geography", sa.String(128), nullable=True))
    _add_column("benchmark_questions", sa.Column("units", sa.String(128), nullable=True))
    _add_column("benchmark_questions", sa.Column("ambiguity_notes", sa.Text(), nullable=False, server_default=""))
    _add_column(
        "benchmark_questions",
        sa.Column("cancellation_conditions", sa.Text(), nullable=False, server_default=""),
    )
    _add_column(
        "benchmark_questions",
        sa.Column("resolver_risk_notes", sa.Text(), nullable=False, server_default=""),
    )


def _backfill(bind) -> None:
    names = set(_inspector(bind).get_table_names())
    if "benchmark_questions" not in names:
        return
    cols = _columns(bind, "benchmark_questions")
    if {"question", "resolution_source", "resolution_date"} <= cols:
        bind.execute(
            text(
                """
                UPDATE benchmark_questions
                SET exact_yes = CASE
                        WHEN exact_yes IS NULL OR exact_yes = '' THEN 'Yes if: ' || question
                        ELSE exact_yes
                    END,
                    exact_no = CASE
                        WHEN exact_no IS NULL OR exact_no = '' THEN 'No if not: ' || question
                        ELSE exact_no
                    END,
                    resolution_deadline = COALESCE(resolution_deadline, resolution_date),
                    authoritative_source = CASE
                        WHEN authoritative_source IS NULL OR authoritative_source = '' THEN resolution_source
                        ELSE authoritative_source
                    END,
                    fallback_sources_json = COALESCE(NULLIF(fallback_sources_json, ''), '[]'),
                    ambiguity_notes = COALESCE(ambiguity_notes, ''),
                    cancellation_conditions = COALESCE(cancellation_conditions, ''),
                    resolver_risk_notes = COALESCE(resolver_risk_notes, '')
                """
            )
        )


def _replace_import_uniqueness(bind) -> None:
    if "benchmark_questions" not in _inspector(bind).get_table_names():
        return
    old_name = _unique_name(bind, "benchmark_questions", {"import_hash"})
    if old_name:
        inspector = _inspector(bind)
        unique_names = {item.get("name") for item in inspector.get_unique_constraints("benchmark_questions")}
        if old_name in unique_names:
            with op.batch_alter_table("benchmark_questions") as batch:
                batch.drop_constraint(old_name, type_="unique")
        else:
            op.drop_index(old_name, table_name="benchmark_questions")
    if not _has_unique(bind, "benchmark_questions", {"dataset_id", "import_hash"}):
        with op.batch_alter_table("benchmark_questions") as batch:
            batch.create_unique_constraint("uq_benchmark_dataset_import", ["dataset_id", "import_hash"])
