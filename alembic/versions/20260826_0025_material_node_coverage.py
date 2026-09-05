"""Add immutable private-V1 material-node coverage assessments.

Revision ID: 20260826_0025
Revises: 20260826_0024
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260826_0025"
down_revision = "20260826_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    required = {
        "forecast_runs",
        "forecast_graphs",
        "research_plans",
        "evidence_sufficiency_assessments",
    }
    if not required.issubset(tables):
        raise RuntimeError("material node coverage prerequisite tables missing")
    if "material_node_coverage_assessments" in tables:
        return

    op.create_table(
        "material_node_coverage_assessments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("forecast_run_id", sa.String(length=36), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("graph_id", sa.String(length=36), nullable=False),
        sa.Column("graph_version", sa.Integer(), nullable=False),
        sa.Column("research_plan_id", sa.String(length=36), nullable=False),
        sa.Column(
            "evidence_sufficiency_assessment_id",
            sa.String(length=36),
            nullable=True,
        ),
        sa.Column(
            "evidence_sufficiency_assessment_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("graph_node_count", sa.Integer(), nullable=False),
        sa.Column("selected_node_count", sa.Integer(), nullable=False),
        sa.Column("included_node_count", sa.Integer(), nullable=False),
        sa.Column("excluded_node_count", sa.Integer(), nullable=False),
        sa.Column("total_graph_weight", sa.String(length=64), nullable=False),
        sa.Column("included_graph_weight", sa.String(length=64), nullable=False),
        sa.Column("excluded_graph_weight", sa.String(length=64), nullable=False),
        sa.Column("included_frontier_weight", sa.String(length=64), nullable=True),
        sa.Column("maximum_excluded_weight", sa.String(length=64), nullable=True),
        sa.Column("selected_node_ids_json", sa.Text(), nullable=False),
        sa.Column("included_node_ids_json", sa.Text(), nullable=False),
        sa.Column("excluded_node_ids_json", sa.Text(), nullable=False),
        sa.Column(
            "higher_importance_excluded_node_ids_json",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "frontier_tie_excluded_node_ids_json",
            sa.Text(),
            nullable=False,
        ),
        sa.Column("missing_parent_relationships_json", sa.Text(), nullable=False),
        sa.Column(
            "missing_dependency_relationships_json",
            sa.Text(),
            nullable=False,
        ),
        sa.Column("reasons_json", sa.Text(), nullable=False),
        sa.Column("warnings_json", sa.Text(), nullable=False),
        sa.Column("policy_snapshot_json", sa.Text(), nullable=False),
        sa.Column("assessment_input_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "status IN ('passed', 'failed')",
            name="ck_material_node_coverage_assessment_status",
        ),
        sa.ForeignKeyConstraint(["forecast_run_id"], ["forecast_runs.id"]),
        sa.ForeignKeyConstraint(["graph_id"], ["forecast_graphs.id"]),
        sa.ForeignKeyConstraint(["research_plan_id"], ["research_plans.id"]),
        sa.ForeignKeyConstraint(
            ["evidence_sufficiency_assessment_id"],
            ["evidence_sufficiency_assessments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "forecast_run_id",
            name="uq_material_node_coverage_assessment_run",
        ),
    )


def downgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "material_node_coverage_assessments" in tables:
        op.drop_table("material_node_coverage_assessments")
