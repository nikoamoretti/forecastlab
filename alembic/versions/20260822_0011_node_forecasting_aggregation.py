"""Persist deterministic node weights and probability contributions.

Revision ID: 20260822_0011
Revises: 20260822_0010
Create Date: 2026-08-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260822_0011"
down_revision = "20260822_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "forecast_node_runs" not in tables:
        raise RuntimeError("forecast_node_runs table is required before 20260822_0011")
    columns = {column["name"] for column in inspect(bind).get_columns("forecast_node_runs")}
    additions = {
        "confidence": sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        "raw_importance_weight": sa.Column(
            "raw_importance_weight", sa.Float(), nullable=False, server_default="0"
        ),
        "dependency_factor": sa.Column("dependency_factor", sa.Float(), nullable=False, server_default="1"),
        "normalized_weight": sa.Column("normalized_weight", sa.Float(), nullable=False, server_default="0"),
        "probability_contribution": sa.Column(
            "probability_contribution", sa.Float(), nullable=False, server_default="0"
        ),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("forecast_node_runs", column)


def downgrade() -> None:
    raise NotImplementedError("Node forecasting migration 20260822_0011 is not reversible.")
