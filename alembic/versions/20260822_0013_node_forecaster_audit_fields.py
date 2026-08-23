"""Add model identity and uncertainty notes to node forecasts.

Revision ID: 20260822_0013
Revises: 20260822_0012
Create Date: 2026-08-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260822_0013"
down_revision = "20260822_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "forecast_node_runs" not in tables:
        raise RuntimeError("forecast_node_runs table is required before 20260822_0013")
    columns = {column["name"] for column in inspect(bind).get_columns("forecast_node_runs")}
    additions = {
        "uncertainty_notes_json": sa.Column(
            "uncertainty_notes_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        "model_used": sa.Column(
            "model_used",
            sa.String(length=255),
            nullable=False,
            server_default="legacy:deterministic-node-v1",
        ),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("forecast_node_runs", column)


def downgrade() -> None:
    raise NotImplementedError("Node forecaster audit migration 20260822_0013 is not reversible.")
