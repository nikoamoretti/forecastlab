"""Add structured graph evidence failure audit fields.

Revision ID: 20260823_0020
Revises: 20260823_0019
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260823_0020"
down_revision = "20260823_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "graph_execution_failures" not in inspector.get_table_names():
        raise RuntimeError("graph_execution_failures missing before 20260823_0020")
    columns = {
        column["name"] for column in inspector.get_columns("graph_execution_failures")
    }
    additions = {
        "research_plan_json": sa.Column(
            "research_plan_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
        "queries_attempted_json": sa.Column(
            "queries_attempted_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        "sources_checked_json": sa.Column(
            "sources_checked_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        "critical_node": sa.Column(
            "critical_node",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        "impact": sa.Column(
            "impact",
            sa.String(length=64),
            nullable=False,
            server_default="forecast_failed",
        ),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("graph_execution_failures", column)


def downgrade() -> None:
    raise NotImplementedError(
        "Graph evidence failure audit migration 20260823_0020 is not reversible."
    )
