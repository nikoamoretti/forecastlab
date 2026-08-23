"""Add node-level outputs for opt-in graph forecasting execution.

Revision ID: 20260822_0010
Revises: 20260821_0009
Create Date: 2026-08-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260822_0010"
down_revision = "20260821_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "forecast_node_runs" not in tables:
        op.create_table(
            "forecast_node_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("forecast_run_id", sa.String(36), sa.ForeignKey("forecast_runs.id"), nullable=False),
            sa.Column("node_id", sa.String(36), sa.ForeignKey("forecast_nodes.id"), nullable=False),
            sa.Column("probability", sa.Float(), nullable=False),
            sa.Column("reasoning", sa.Text(), nullable=False),
            sa.Column("supporting_claim_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("opposing_claim_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("uncertainty", sa.Float(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("forecast_run_id", "node_id", name="uq_forecast_node_run"),
        )


def downgrade() -> None:
    raise NotImplementedError("Forecast node run migration 20260822_0010 is not reversible.")
