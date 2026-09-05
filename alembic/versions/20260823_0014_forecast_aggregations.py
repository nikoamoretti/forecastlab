"""Add first-class graph probability aggregation records.

Revision ID: 20260823_0014
Revises: 20260822_0013
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260823_0014"
down_revision = "20260822_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "forecast_runs" not in tables:
        raise RuntimeError("forecast_runs table is required before 20260823_0014")
    if "forecast_aggregations" in tables:
        return
    op.create_table(
        "forecast_aggregations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("forecast_run_id", sa.String(length=36), nullable=False),
        sa.Column("method", sa.String(length=128), nullable=False),
        sa.Column("final_probability", sa.Float(), nullable=False),
        sa.Column("calculation_trace_json", sa.Text(), nullable=False),
        sa.Column("node_contributions_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["forecast_run_id"], ["forecast_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("forecast_run_id", name="uq_forecast_aggregation_run"),
    )


def downgrade() -> None:
    raise NotImplementedError("Forecast aggregation migration 20260823_0014 is not reversible.")
