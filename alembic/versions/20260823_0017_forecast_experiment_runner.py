"""Add controlled forecast experiments, runs, and measured results.

Revision ID: 20260823_0017
Revises: 20260823_0016
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260823_0017"
down_revision = "20260823_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    required = {"evaluation_datasets", "evaluation_questions", "forecast_runs"}
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError(f"Required tables missing before 20260823_0017: {','.join(missing)}")

    if "forecast_experiments" not in tables:
        op.create_table(
            "forecast_experiments",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("dataset_id", sa.String(length=36), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("profiles_json", sa.Text(), nullable=False),
            sa.Column("configuration_hash", sa.String(length=64), nullable=False),
            sa.Column("configuration_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "status IN ('pending', 'running', 'completed', 'completed_with_failures', 'failed')",
                name="ck_forecast_experiment_status",
            ),
            sa.ForeignKeyConstraint(["dataset_id"], ["evaluation_datasets.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    tables = set(inspect(bind).get_table_names())
    if "forecast_experiment_runs" not in tables:
        op.create_table(
            "forecast_experiment_runs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("experiment_id", sa.String(length=36), nullable=False),
            sa.Column("evaluation_question_id", sa.String(length=36), nullable=False),
            sa.Column("profile_id", sa.String(length=64), nullable=False),
            sa.Column("forecast_run_id", sa.String(length=36), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("error_category", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "status IN ('pending', 'running', 'completed', 'partial', 'failed')",
                name="ck_forecast_experiment_run_status",
            ),
            sa.ForeignKeyConstraint(["evaluation_question_id"], ["evaluation_questions.id"]),
            sa.ForeignKeyConstraint(["experiment_id"], ["forecast_experiments.id"]),
            sa.ForeignKeyConstraint(["forecast_run_id"], ["forecast_runs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "experiment_id",
                "evaluation_question_id",
                "profile_id",
                name="uq_forecast_experiment_run_cell",
            ),
            sa.UniqueConstraint(
                "forecast_run_id",
                name="uq_forecast_experiment_run_forecast_run",
            ),
        )

    tables = set(inspect(bind).get_table_names())
    if "forecast_experiment_results" not in tables:
        op.create_table(
            "forecast_experiment_results",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("experiment_run_id", sa.String(length=36), nullable=False),
            sa.Column("probability", sa.Float(), nullable=True),
            sa.Column("outcome", sa.Integer(), nullable=False),
            sa.Column("brier_score", sa.Float(), nullable=True),
            sa.Column("log_loss", sa.Float(), nullable=True),
            sa.Column("cost_usd", sa.Float(), nullable=False),
            sa.Column("latency_ms", sa.Integer(), nullable=False),
            sa.Column("evidence_coverage", sa.Float(), nullable=True),
            sa.Column("evidence_covered_units", sa.Integer(), nullable=False),
            sa.Column("evidence_total_units", sa.Integer(), nullable=False),
            sa.Column("completion_status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "probability IS NULL OR (probability >= 0 AND probability <= 1)",
                name="ck_forecast_experiment_result_probability",
            ),
            sa.CheckConstraint(
                "outcome IN (0, 1)",
                name="ck_forecast_experiment_result_outcome",
            ),
            sa.CheckConstraint(
                "completion_status IN ('completed', 'partial', 'failed')",
                name="ck_forecast_experiment_result_completion_status",
            ),
            sa.CheckConstraint("cost_usd >= 0", name="ck_forecast_experiment_result_cost"),
            sa.CheckConstraint("latency_ms >= 0", name="ck_forecast_experiment_result_latency"),
            sa.CheckConstraint(
                "evidence_coverage IS NULL OR (evidence_coverage >= 0 AND evidence_coverage <= 1)",
                name="ck_forecast_experiment_result_evidence_coverage",
            ),
            sa.CheckConstraint(
                "evidence_covered_units >= 0 AND evidence_total_units >= 0",
                name="ck_forecast_experiment_result_evidence_units",
            ),
            sa.ForeignKeyConstraint(["experiment_run_id"], ["forecast_experiment_runs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "experiment_run_id",
                name="uq_forecast_experiment_result_run",
            ),
        )


def downgrade() -> None:
    raise NotImplementedError("Forecast experiment runner migration 20260823_0017 is not reversible.")
