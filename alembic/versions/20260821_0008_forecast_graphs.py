"""Add Forecast Graphs and research nodes without changing forecast execution records.

Revision ID: 20260821_0008
Revises: 20260821_0007
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260821_0008"
down_revision = "20260821_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "forecast_graphs" not in tables:
        op.create_table(
            "forecast_graphs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("contract_id", sa.String(36), sa.ForeignKey("forecast_contracts.id"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("generation_model", sa.String(256), nullable=False),
            sa.Column("root_question", sa.Text(), nullable=False),
            sa.UniqueConstraint("contract_id", "version", name="uq_forecast_graph_contract_version"),
        )
    if "forecast_nodes" not in tables:
        op.create_table(
            "forecast_nodes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("graph_id", sa.String(36), sa.ForeignKey("forecast_graphs.id"), nullable=False),
            sa.Column("parent_node_id", sa.String(36), sa.ForeignKey("forecast_nodes.id"), nullable=True),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("node_type", sa.String(32), nullable=False),
            sa.Column("importance_weight", sa.Float(), nullable=False),
            sa.Column("dependencies_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("preferred_sources_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("required_output_type", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        )


def downgrade() -> None:
    raise NotImplementedError("Forecast Graph migration 20260821_0008 is not reversible.")
