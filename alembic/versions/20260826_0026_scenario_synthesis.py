"""Add immutable grounded scenario-synthesis artifacts.

Revision ID: 20260826_0026
Revises: 20260826_0025
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260826_0026"
down_revision = "20260826_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    required = {
        "forecast_runs",
        "evidence_sufficiency_assessments",
        "material_node_coverage_assessments",
    }
    if not required.issubset(tables):
        raise RuntimeError("scenario synthesis prerequisite tables missing")
    if "scenario_syntheses" in tables:
        return

    op.create_table(
        "scenario_syntheses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("forecast_run_id", sa.String(length=36), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("policy_snapshot_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("scenarios_json", sa.Text(), nullable=False),
        sa.Column("coverage_audit_json", sa.Text(), nullable=False),
        sa.Column("failure_reasons_json", sa.Text(), nullable=False),
        sa.Column("diagnostics_json", sa.Text(), nullable=False),
        sa.Column(
            "evidence_sufficiency_assessment_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "evidence_sufficiency_assessment_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "material_node_coverage_assessment_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "material_node_coverage_assessment_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'failed')",
            name="ck_scenario_synthesis_status",
        ),
        sa.ForeignKeyConstraint(["forecast_run_id"], ["forecast_runs.id"]),
        sa.ForeignKeyConstraint(
            ["evidence_sufficiency_assessment_id"],
            ["evidence_sufficiency_assessments.id"],
        ),
        sa.ForeignKeyConstraint(
            ["material_node_coverage_assessment_id"],
            ["material_node_coverage_assessments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "forecast_run_id",
            name="uq_scenario_synthesis_forecast_run",
        ),
    )


def downgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "scenario_syntheses" in tables:
        op.drop_table("scenario_syntheses")
