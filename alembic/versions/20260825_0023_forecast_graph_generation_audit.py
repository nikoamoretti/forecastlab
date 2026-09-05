"""Persist sanitized successful Forecast Graph generation audits.

Revision ID: 20260825_0023
Revises: 20260825_0022
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260825_0023"
down_revision = "20260825_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "forecast_graphs" not in inspector.get_table_names():
        raise RuntimeError("forecast graph audit prerequisite table missing:forecast_graphs")
    columns = {column["name"] for column in inspector.get_columns("forecast_graphs")}
    if "generation_audit_json" not in columns:
        op.add_column(
            "forecast_graphs",
            sa.Column("generation_audit_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    raise NotImplementedError(
        "Forecast Graph generation audit migration 20260825_0023 is not reversible."
    )
