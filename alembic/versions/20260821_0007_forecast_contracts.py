"""Add versioned Forecast Contracts without changing legacy resolution contracts.

Revision ID: 20260821_0007
Revises: 20260819_0006
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260821_0007"
down_revision = "20260819_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "forecast_contracts" in inspect(bind).get_table_names():
        return
    op.create_table(
        "forecast_contracts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("question_id", sa.String(36), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(128), nullable=False, server_default="user"),
        sa.Column("original_question", sa.Text(), nullable=False),
        sa.Column("normalized_question", sa.Text(), nullable=False),
        sa.Column("yes_condition", sa.Text(), nullable=False, server_default=""),
        sa.Column("no_condition", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolution_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authoritative_source", sa.Text(), nullable=False, server_default=""),
        sa.Column("fallback_sources_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("resolution_method", sa.Text(), nullable=False, server_default=""),
        sa.Column("ambiguity_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("cancellation_conditions", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolver_risk_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("forecast_type", sa.String(32), nullable=False, server_default="binary"),
        sa.Column("geography", sa.String(128), nullable=True),
        sa.Column("units", sa.String(128), nullable=True),
        sa.Column("domain", sa.String(128), nullable=True),
        sa.Column("initial_reference_class", sa.Text(), nullable=False, server_default=""),
        sa.Column("suggested_drivers_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("known_dependencies_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.UniqueConstraint("question_id", "version", name="uq_forecast_contract_question_version"),
    )


def downgrade() -> None:
    raise NotImplementedError("Forecast Contract migration 20260821_0007 is not reversible.")
