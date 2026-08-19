"""Baseline schema including integrity and experiment tables.

Revision ID: 20260818_0001
Revises:
Create Date: 2026-08-18
"""

from __future__ import annotations

from alembic import op

from forecastlab_api.models import Base

revision = "20260818_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind)
