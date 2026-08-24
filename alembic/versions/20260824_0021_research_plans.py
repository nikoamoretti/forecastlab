"""Add graph research plans.

Revision ID: 20260824_0021
Revises: 20260823_0020
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260824_0021"
down_revision = "20260823_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "forecast_runs" not in tables:
        raise RuntimeError("forecast_runs missing before 20260824_0021")
    if "research_plans" not in tables:
        op.create_table(
            "research_plans",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("forecast_run_id", sa.String(length=36), nullable=False),
            sa.Column("selected_nodes_json", sa.Text(), nullable=False),
            sa.Column("skipped_nodes_json", sa.Text(), nullable=False),
            sa.Column("priority_scores_json", sa.Text(), nullable=False),
            sa.Column("budget_allocation_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["forecast_run_id"], ["forecast_runs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "forecast_run_id",
                name="uq_research_plan_forecast_run",
            ),
        )


def downgrade() -> None:
    raise NotImplementedError(
        "Research planner migration 20260824_0021 is not reversible."
    )
