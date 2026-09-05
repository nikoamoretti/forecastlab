"""Add attempt-specific provider request counts.

Revision ID: 20260819_0006
Revises: 20260819_0005
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260819_0006"
down_revision = "20260819_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "forecast_run_attempts" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("forecast_run_attempts")}
    if "provider_request_count" not in columns:
        op.add_column(
            "forecast_run_attempts",
            sa.Column("provider_request_count", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    raise NotImplementedError("Live readiness 20260819_0006 is not reversible.")
