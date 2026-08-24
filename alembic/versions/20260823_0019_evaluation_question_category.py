"""Preserve optional evaluation question categories.

Revision ID: 20260823_0019
Revises: 20260823_0018
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260823_0019"
down_revision = "20260823_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "evaluation_questions" not in inspector.get_table_names():
        raise RuntimeError("evaluation_questions missing before 20260823_0019")
    columns = {column["name"] for column in inspector.get_columns("evaluation_questions")}
    if "category" not in columns:
        op.add_column(
            "evaluation_questions",
            sa.Column("category", sa.String(length=128), nullable=True),
        )


def downgrade() -> None:
    raise NotImplementedError("Evaluation question category migration 20260823_0019 is not reversible.")
