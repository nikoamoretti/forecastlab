"""Add node-linked Evidence Claims without changing forecast execution records.

Revision ID: 20260821_0009
Revises: 20260821_0008
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260821_0009"
down_revision = "20260821_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "evidence_claims" not in tables:
        op.create_table(
            "evidence_claims",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("evidence_item_id", sa.String(36), sa.ForeignKey("evidence_items.id"), nullable=False),
            sa.Column("forecast_node_id", sa.String(36), sa.ForeignKey("forecast_nodes.id"), nullable=False),
            sa.Column("claim", sa.Text(), nullable=False),
            sa.Column("excerpt", sa.Text(), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("source_title", sa.Text(), nullable=False),
            sa.Column("publisher", sa.String(256), nullable=False),
            sa.Column("publication_date", sa.DateTime(timezone=True), nullable=False),
            sa.Column("retrieval_date", sa.DateTime(timezone=True), nullable=False),
            sa.Column("supports_or_refutes", sa.String(16), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("source_quality", sa.Float(), nullable=False),
            sa.Column("primary_source", sa.Boolean(), nullable=False),
            sa.Column("as_of_eligible", sa.Boolean(), nullable=False),
            sa.Column("cutoff_verified", sa.Boolean(), nullable=False),
        )


def downgrade() -> None:
    raise NotImplementedError("Evidence Claims migration 20260821_0009 is not reversible.")
