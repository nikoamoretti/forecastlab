"""Add immutable real-evaluation releases and preregistration boundaries.

Revision ID: 20260826_0027
Revises: 20260826_0026
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260826_0027"
down_revision = "20260826_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    required = {
        "evaluation_datasets",
        "evaluation_questions",
        "forecast_experiments",
    }
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError(
            "real evaluation release prerequisite tables missing: " + ",".join(missing)
        )

    if "evaluation_releases" not in tables:
        op.create_table(
            "evaluation_releases",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("version", sa.String(length=64), nullable=False),
            sa.Column("policy_version", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("development_dataset_id", sa.String(length=36), nullable=False),
            sa.Column("validation_dataset_id", sa.String(length=36), nullable=False),
            sa.Column("test_dataset_id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("correction_of_release_id", sa.String(length=36), nullable=True),
            sa.Column("correction_summary", sa.Text(), nullable=True),
            sa.Column("execution_manifest_json", sa.Text(), nullable=False),
            sa.Column("execution_manifest_hash", sa.String(length=64), nullable=False),
            sa.Column("scoring_manifest_json", sa.Text(), nullable=False),
            sa.Column("scoring_manifest_hash", sa.String(length=64), nullable=False),
            sa.Column("preregistration_json", sa.Text(), nullable=False),
            sa.Column("preregistration_hash", sa.String(length=64), nullable=False),
            sa.Column("release_hash", sa.String(length=64), nullable=False),
            sa.CheckConstraint(
                "status IN ('draft', 'reviewed', 'frozen')",
                name="ck_evaluation_release_status",
            ),
            sa.CheckConstraint(
                "status != 'frozen' OR frozen_at IS NOT NULL",
                name="ck_evaluation_release_frozen_at",
            ),
            sa.CheckConstraint(
                "development_dataset_id != validation_dataset_id "
                "AND development_dataset_id != test_dataset_id "
                "AND validation_dataset_id != test_dataset_id",
                name="ck_evaluation_release_distinct_datasets",
            ),
            sa.ForeignKeyConstraint(
                ["development_dataset_id"], ["evaluation_datasets.id"]
            ),
            sa.ForeignKeyConstraint(
                ["validation_dataset_id"], ["evaluation_datasets.id"]
            ),
            sa.ForeignKeyConstraint(["test_dataset_id"], ["evaluation_datasets.id"]),
            sa.ForeignKeyConstraint(
                ["correction_of_release_id"], ["evaluation_releases.id"]
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "name", "version", name="uq_evaluation_release_name_version"
            ),
            sa.UniqueConstraint(
                "release_hash", name="uq_evaluation_releases_release_hash"
            ),
        )

    tables = set(inspect(bind).get_table_names())
    if "evaluation_release_questions" not in tables:
        op.create_table(
            "evaluation_release_questions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("release_id", sa.String(length=36), nullable=False),
            sa.Column("evaluation_question_id", sa.String(length=36), nullable=False),
            sa.Column("split", sa.String(length=32), nullable=False),
            sa.Column("event_family_id", sa.String(length=128), nullable=False),
            sa.Column("leakage_group_id", sa.String(length=128), nullable=False),
            sa.Column("inclusion_status", sa.String(length=32), nullable=False),
            sa.Column("exclusion_reason", sa.Text(), nullable=True),
            sa.Column("question_author_id", sa.String(length=128), nullable=True),
            sa.Column("question_reviewer_id", sa.String(length=128), nullable=True),
            sa.Column("outcome_adjudicator_id", sa.String(length=128), nullable=True),
            sa.Column("review_completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("outcome_known_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("source_license_status", sa.String(length=32), nullable=False),
            sa.Column("source_use_basis", sa.Text(), nullable=True),
            sa.Column("redistribution_allowed", sa.Boolean(), nullable=True),
            sa.Column("adjudication_notes", sa.Text(), nullable=True),
            sa.Column("adjudication_record_hash", sa.String(length=64), nullable=True),
            sa.CheckConstraint(
                "split IN ('development', 'validation', 'test')",
                name="ck_evaluation_release_question_split",
            ),
            sa.CheckConstraint(
                "inclusion_status IN ('included', 'excluded')",
                name="ck_evaluation_release_question_inclusion",
            ),
            sa.CheckConstraint(
                "source_license_status IN "
                "('public_domain', 'licensed', 'metadata_use_permitted', 'unknown')",
                name="ck_evaluation_release_question_license",
            ),
            sa.ForeignKeyConstraint(
                ["release_id"], ["evaluation_releases.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["evaluation_question_id"], ["evaluation_questions.id"]
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "release_id",
                "evaluation_question_id",
                name="uq_evaluation_release_question",
            ),
        )

    experiment_columns = {
        column["name"]
        for column in inspect(bind).get_columns("forecast_experiments")
    }
    if "evaluation_release_id" not in experiment_columns:
        with op.batch_alter_table("forecast_experiments") as batch:
            batch.add_column(
                sa.Column("evaluation_release_id", sa.String(length=36), nullable=True)
            )
            batch.create_foreign_key(
                "fk_forecast_experiments_evaluation_release_id",
                "evaluation_releases",
                ["evaluation_release_id"],
                ["id"],
            )
    experiment_columns = {
        column["name"]
        for column in inspect(bind).get_columns("forecast_experiments")
    }
    if "evaluation_split" not in experiment_columns:
        with op.batch_alter_table("forecast_experiments") as batch:
            batch.add_column(
                sa.Column("evaluation_split", sa.String(length=32), nullable=True)
            )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "forecast_experiments" in tables:
        columns = {
            column["name"]
            for column in inspect(bind).get_columns("forecast_experiments")
        }
        with op.batch_alter_table("forecast_experiments") as batch:
            if "evaluation_split" in columns:
                batch.drop_column("evaluation_split")
            if "evaluation_release_id" in columns:
                batch.drop_constraint(
                    "fk_forecast_experiments_evaluation_release_id",
                    type_="foreignkey",
                )
                batch.drop_column("evaluation_release_id")
    tables = set(inspect(bind).get_table_names())
    if "evaluation_release_questions" in tables:
        op.drop_table("evaluation_release_questions")
    tables = set(inspect(bind).get_table_names())
    if "evaluation_releases" in tables:
        op.drop_table("evaluation_releases")

