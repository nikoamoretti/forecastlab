"""Add durable graph execution failure records.

Revision ID: 20260823_0015
Revises: 20260823_0014
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260823_0015"
down_revision = "20260823_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    required = {"forecast_runs", "forecast_nodes"}
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError(f"Required tables missing before 20260823_0015: {','.join(missing)}")
    if "graph_execution_failures" in tables:
        return
    op.create_table(
        "graph_execution_failures",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("forecast_run_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["forecast_run_id"], ["forecast_runs.id"]),
        sa.ForeignKeyConstraint(["node_id"], ["forecast_nodes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    raise NotImplementedError("Graph execution failure migration 20260823_0015 is not reversible.")
