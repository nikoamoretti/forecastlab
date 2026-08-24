"""Add internal forecast failure classifications for research analysis.

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
    required = {"forecast_experiments", "forecast_experiment_runs"}
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError(f"Required tables missing before 20260823_0018: {','.join(missing)}")

    if "forecast_failures" not in tables:
        op.create_table(
            "forecast_failures",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("experiment_id", sa.String(length=36), nullable=False),
            sa.Column("forecast_experiment_run_id", sa.String(length=36), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("annotation", sa.Text(), nullable=False),
            sa.Column("created_by", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "category IN ("
                "'bad_contract', 'bad_evidence', 'bad_decomposition', "
                "'bad_node_forecast', 'bad_aggregation', 'operational_failure'"
                ")",
                name="ck_forecast_failure_category",
            ),
            sa.ForeignKeyConstraint(["experiment_id"], ["forecast_experiments.id"]),
            sa.ForeignKeyConstraint(
                ["forecast_experiment_run_id"],
                ["forecast_experiment_runs.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "forecast_experiment_run_id",
                "category",
                name="uq_forecast_failure_run_category",
            ),
        )


def downgrade() -> None:
    raise NotImplementedError("Forecast analysis migration 20260823_0018 is not reversible.")
