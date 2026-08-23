"""Add internal forecast failure classifications and annotations.

Revision ID: 20260823_0018
Revises: 20260823_0017
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260823_0018"
down_revision = "20260823_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    required = {"evaluation_experiments", "evaluation_runs"}
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError(f"Required tables missing before 20260823_0018: {','.join(missing)}")
    if "forecast_failures" in tables:
        return
    op.create_table(
        "forecast_failures",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("experiment_id", sa.String(length=36), nullable=False),
        sa.Column("evaluation_run_id", sa.String(length=36), nullable=False),
        sa.Column("failure_group", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("annotation", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "failure_group IN ('question', 'research', 'reasoning', 'aggregation', 'operational')",
            name="ck_forecast_failure_group",
        ),
        sa.CheckConstraint(
            "category IN ("
            "'ambiguous_resolution', 'incorrect_contract', 'wrong_resolver', "
            "'missing_evidence', 'poor_source_quality', 'cutoff_failure', "
            "'bad_prior', 'overconfidence', 'ignored_counterargument', 'narrative_bias', "
            "'incorrect_weighting', 'dependency_failure', "
            "'provider_failure', 'timeout', 'budget_failure'"
            ")",
            name="ck_forecast_failure_category",
        ),
        sa.ForeignKeyConstraint(["evaluation_run_id"], ["evaluation_runs.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["evaluation_experiments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evaluation_run_id",
            "category",
            name="uq_forecast_failure_run_category",
        ),
    )


def downgrade() -> None:
    raise NotImplementedError("Evaluation analysis migration 20260823_0018 is not reversible.")
