"""Add versioned frozen real evaluation datasets.

Revision ID: 20260823_0016
Revises: 20260823_0015
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260823_0016"
down_revision = "20260823_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "evaluation_datasets" not in tables:
        op.create_table(
            "evaluation_datasets",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("version", sa.String(length=64), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("dataset_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("question_count", sa.Integer(), nullable=False),
            sa.Column("provenance", sa.Text(), nullable=False),
            sa.CheckConstraint(
                "status IN ('draft', 'reviewed', 'frozen')",
                name="ck_evaluation_dataset_status",
            ),
            sa.CheckConstraint(
                "question_count >= 0",
                name="ck_evaluation_dataset_question_count",
            ),
            sa.CheckConstraint(
                "status != 'frozen' OR frozen_at IS NOT NULL",
                name="ck_evaluation_dataset_frozen_at",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("dataset_hash", name="uq_evaluation_datasets_dataset_hash"),
            sa.UniqueConstraint("name", "version", name="uq_evaluation_dataset_name_version"),
        )
    tables = set(inspect(bind).get_table_names())
    if "evaluation_questions" not in tables:
        if "evaluation_datasets" not in tables:
            raise RuntimeError("evaluation_datasets table missing before evaluation_questions")
        op.create_table(
            "evaluation_questions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("dataset_id", sa.String(length=36), nullable=False),
            sa.Column("question_text", sa.Text(), nullable=False),
            sa.Column("normalized_question", sa.Text(), nullable=False),
            sa.Column("resolution_contract", sa.Text(), nullable=False),
            sa.Column("forecast_date", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolution_date", sa.DateTime(timezone=True), nullable=False),
            sa.Column("outcome", sa.Integer(), nullable=False),
            sa.Column("resolution_source", sa.Text(), nullable=False),
            sa.Column("category", sa.String(length=128), nullable=False),
            sa.Column("domain", sa.String(length=128), nullable=False),
            sa.Column("question_hash", sa.String(length=64), nullable=False),
            sa.CheckConstraint(
                "outcome IN (0, 1)",
                name="ck_evaluation_question_binary_outcome",
            ),
            sa.CheckConstraint(
                "forecast_date < resolution_date",
                name="ck_evaluation_question_date_order",
            ),
            sa.ForeignKeyConstraint(["dataset_id"], ["evaluation_datasets.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "dataset_id",
                "question_hash",
                name="uq_evaluation_dataset_question_hash",
            ),
        )


def downgrade() -> None:
    raise NotImplementedError("Real evaluation dataset migration 20260823_0016 is not reversible.")
