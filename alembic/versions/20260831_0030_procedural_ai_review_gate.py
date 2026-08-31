"""Add procedural AI review artifacts and one-shot test claims.

Revision ID: 20260831_0030
Revises: 20260829_0029
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260831_0030"
down_revision = "20260829_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    required = {
        "evaluation_releases",
        "evaluation_release_questions",
        "evaluation_questions",
        "forecast_experiments",
    }
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError(
            "procedural AI review prerequisites missing: " + ",".join(missing)
        )

    release_columns = {
        column["name"] for column in inspect(bind).get_columns("evaluation_releases")
    }
    additions = (
        sa.Column(
            "procedural_review_policy_version", sa.String(length=64), nullable=True
        ),
        sa.Column("reserve_order_json", sa.Text(), nullable=True),
        sa.Column("reserve_order_hash", sa.String(length=64), nullable=True),
        sa.Column("reserve_order_frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_manifest_json", sa.Text(), nullable=True),
        sa.Column("review_manifest_hash", sa.String(length=64), nullable=True),
    )
    with op.batch_alter_table("evaluation_releases") as batch:
        for column in additions:
            if column.name not in release_columns:
                batch.add_column(column)

    tables = set(inspect(bind).get_table_names())
    if "procedural_ai_review_artifacts" not in tables:
        op.create_table(
            "procedural_ai_review_artifacts",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("evaluation_release_id", sa.String(length=36), nullable=False),
            sa.Column(
                "evaluation_release_question_id", sa.String(length=36), nullable=False
            ),
            sa.Column("evaluation_question_id", sa.String(length=36), nullable=False),
            sa.Column("artifact_type", sa.String(length=32), nullable=False),
            sa.Column("policy_version", sa.String(length=64), nullable=False),
            sa.Column("rubric_version", sa.String(length=64), nullable=False),
            sa.Column("rubric_hash", sa.String(length=64), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("model_provider", sa.String(length=128), nullable=False),
            sa.Column("model_id", sa.String(length=255), nullable=False),
            sa.Column("model_version", sa.String(length=128), nullable=False),
            sa.Column("tool_name", sa.String(length=32), nullable=False),
            sa.Column("tool_version", sa.String(length=128), nullable=False),
            sa.Column("run_id", sa.String(length=255), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("input_manifest_json", sa.Text(), nullable=False),
            sa.Column("input_manifest_hash", sa.String(length=64), nullable=False),
            sa.Column("output_json", sa.Text(), nullable=False),
            sa.Column("output_hash", sa.String(length=64), nullable=False),
            sa.Column("decision", sa.String(length=32), nullable=False),
            sa.Column("gate_status", sa.String(length=16), nullable=False),
            sa.Column("gate_reasons_json", sa.Text(), nullable=False),
            sa.Column("source_citation_ids_json", sa.Text(), nullable=False),
            sa.CheckConstraint(
                "artifact_type IN ('question_review', 'outcome_adjudication')",
                name="ck_procedural_ai_review_artifact_type",
            ),
            sa.CheckConstraint(
                "role IN ('question_review', 'outcome_adjudication')",
                name="ck_procedural_ai_review_role",
            ),
            sa.CheckConstraint(
                "gate_status IN ('passed', 'failed')",
                name="ck_procedural_ai_review_gate_status",
            ),
            sa.CheckConstraint(
                "tool_name = 'codex'", name="ck_procedural_ai_review_tool"
            ),
            sa.ForeignKeyConstraint(
                ["evaluation_release_id"],
                ["evaluation_releases.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["evaluation_release_question_id"],
                ["evaluation_release_questions.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["evaluation_question_id"], ["evaluation_questions.id"]
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "evaluation_release_question_id",
                "artifact_type",
                name="uq_procedural_ai_review_artifact_role",
            ),
            sa.UniqueConstraint("run_id", name="uq_procedural_ai_review_run_id"),
        )

    tables = set(inspect(bind).get_table_names())
    if "evaluation_test_split_executions" not in tables:
        op.create_table(
            "evaluation_test_split_executions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("evaluation_release_id", sa.String(length=36), nullable=False),
            sa.Column("forecast_experiment_id", sa.String(length=36), nullable=False),
            sa.Column("execution_manifest_hash", sa.String(length=64), nullable=False),
            sa.Column("preregistration_hash", sa.String(length=64), nullable=False),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["evaluation_release_id"], ["evaluation_releases.id"]
            ),
            sa.ForeignKeyConstraint(
                ["forecast_experiment_id"], ["forecast_experiments.id"]
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "evaluation_release_id", name="uq_evaluation_test_split_release"
            ),
            sa.UniqueConstraint(
                "forecast_experiment_id", name="uq_evaluation_test_split_experiment"
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "evaluation_test_split_executions" in tables:
        op.drop_table("evaluation_test_split_executions")
    tables = set(inspect(bind).get_table_names())
    if "procedural_ai_review_artifacts" in tables:
        op.drop_table("procedural_ai_review_artifacts")
    tables = set(inspect(bind).get_table_names())
    if "evaluation_releases" not in tables:
        return
    columns = {
        column["name"] for column in inspect(bind).get_columns("evaluation_releases")
    }
    with op.batch_alter_table("evaluation_releases") as batch:
        for name in (
            "review_manifest_hash",
            "review_manifest_json",
            "reserve_order_frozen_at",
            "reserve_order_hash",
            "reserve_order_json",
            "procedural_review_policy_version",
        ):
            if name in columns:
                batch.drop_column(name)
