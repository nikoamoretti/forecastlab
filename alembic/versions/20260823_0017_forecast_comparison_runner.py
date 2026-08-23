"""Add controlled real-evaluation experiments, runs, and results.

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

    if "evaluation_experiments" not in tables:
        op.create_table(
            "evaluation_experiments",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("dataset_id", sa.String(length=36), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("profiles", sa.Text(), nullable=False),
            sa.Column("configuration_hash", sa.String(length=64), nullable=False),
            sa.Column("configuration_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "status IN ('pending', 'running', 'completed', 'completed_with_failures', 'failed')",
                name="ck_evaluation_experiment_status",
            ),
            sa.ForeignKeyConstraint(["dataset_id"], ["evaluation_datasets.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    tables = set(inspect(bind).get_table_names())
    if "evaluation_runs" not in tables:
        op.create_table(
            "evaluation_runs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("experiment_id", sa.String(length=36), nullable=False),
            sa.Column("question_id", sa.String(length=36), nullable=False),
            sa.Column("profile_id", sa.String(length=64), nullable=False),
            sa.Column("forecast_run_id", sa.String(length=36), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "status IN ('pending', 'running', 'completed', 'partial', 'failed')",
                name="ck_evaluation_run_status",
            ),
            sa.ForeignKeyConstraint(["experiment_id"], ["evaluation_experiments.id"]),
            sa.ForeignKeyConstraint(["forecast_run_id"], ["forecast_runs.id"]),
            sa.ForeignKeyConstraint(["question_id"], ["evaluation_questions.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("forecast_run_id", name="uq_evaluation_run_forecast_run"),
            sa.UniqueConstraint(
                "experiment_id",
                "question_id",
                "profile_id",
                name="uq_evaluation_run_question_profile",
            ),
        )

    tables = set(inspect(bind).get_table_names())
    if "evaluation_results" not in tables:
        op.create_table(
            "evaluation_results",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("probability", sa.Float(), nullable=True),
            sa.Column("outcome", sa.Integer(), nullable=False),
            sa.Column("brier_score", sa.Float(), nullable=True),
            sa.Column("log_loss", sa.Float(), nullable=True),
            sa.Column("cost", sa.Float(), nullable=False),
            sa.Column("latency", sa.Float(), nullable=False),
            sa.Column("evidence_coverage", sa.Float(), nullable=True),
            sa.Column("completion_status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "probability IS NULL OR (probability >= 0 AND probability <= 1)",
                name="ck_evaluation_result_probability",
            ),
            sa.CheckConstraint("outcome IN (0, 1)", name="ck_evaluation_result_binary_outcome"),
            sa.CheckConstraint(
                "completion_status IN ('completed', 'partial', 'failed')",
                name="ck_evaluation_result_completion_status",
            ),
            sa.CheckConstraint("cost >= 0", name="ck_evaluation_result_cost"),
            sa.CheckConstraint("latency >= 0", name="ck_evaluation_result_latency"),
            sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", name="uq_evaluation_result_run"),
        )


def downgrade() -> None:
    raise NotImplementedError("Forecast comparison runner migration 20260823_0017 is not reversible.")
