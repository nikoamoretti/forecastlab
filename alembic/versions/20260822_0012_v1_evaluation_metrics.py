"""Persist evidence coverage for V1 evaluation results.

Revision ID: 20260822_0012
Revises: 20260822_0011
Create Date: 2026-08-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260822_0012"
down_revision = "20260822_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "benchmark_results" not in tables:
        raise RuntimeError("benchmark_results table is required before 20260822_0012")
    columns = {column["name"] for column in inspect(bind).get_columns("benchmark_results")}
    additions = {
        "evidence_coverage": sa.Column("evidence_coverage", sa.Float(), nullable=True),
        "evidence_covered_units": sa.Column(
            "evidence_covered_units",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        "evidence_total_units": sa.Column(
            "evidence_total_units",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("benchmark_results", column)


def downgrade() -> None:
    raise NotImplementedError("V1 evaluation migration 20260822_0012 is not reversible.")
