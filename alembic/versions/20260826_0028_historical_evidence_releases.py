"""Add immutable frozen historical-evidence release boundaries.

Revision ID: 20260826_0028
Revises: 20260826_0027
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260826_0028"
down_revision = "20260826_0027"
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
            "historical evidence release prerequisite tables missing: "
            + ",".join(missing)
        )

    if "historical_evidence_releases" not in tables:
        op.create_table(
            "historical_evidence_releases",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("evaluation_release_id", sa.String(length=36), nullable=False),
            sa.Column(
                "evaluation_execution_manifest_hash",
                sa.String(length=64),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("version", sa.String(length=64), nullable=False),
            sa.Column("policy_version", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("correction_of_release_id", sa.String(length=36), nullable=True),
            sa.Column("correction_summary", sa.Text(), nullable=True),
            sa.Column("creation_request_hash", sa.String(length=64), nullable=False),
            sa.Column("execution_manifest_json", sa.Text(), nullable=False),
            sa.Column("execution_manifest_hash", sa.String(length=64), nullable=False),
            sa.Column("audit_manifest_json", sa.Text(), nullable=False),
            sa.Column("audit_manifest_hash", sa.String(length=64), nullable=False),
            sa.Column("bundle_manifest_json", sa.Text(), nullable=False),
            sa.Column("bundle_manifest_hash", sa.String(length=64), nullable=False),
            sa.Column("release_hash", sa.String(length=64), nullable=False),
            sa.CheckConstraint(
                "status IN ('draft', 'reviewed', 'frozen')",
                name="ck_historical_evidence_release_status",
            ),
            sa.CheckConstraint(
                "status != 'frozen' OR frozen_at IS NOT NULL",
                name="ck_historical_evidence_release_frozen_at",
            ),
            sa.ForeignKeyConstraint(
                ["evaluation_release_id"], ["evaluation_releases.id"]
            ),
            sa.ForeignKeyConstraint(
                ["correction_of_release_id"], ["historical_evidence_releases.id"]
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "name", "version", name="uq_historical_evidence_release_name_version"
            ),
            sa.UniqueConstraint(
                "release_hash", name="uq_historical_evidence_releases_release_hash"
            ),
        )

    tables = set(inspect(bind).get_table_names())
    if "historical_evidence_packets" not in tables:
        op.create_table(
            "historical_evidence_packets",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column(
                "historical_evidence_release_id", sa.String(length=36), nullable=False
            ),
            sa.Column(
                "evaluation_release_question_id", sa.String(length=36), nullable=False
            ),
            sa.Column("evaluation_question_id", sa.String(length=36), nullable=False),
            sa.Column("split", sa.String(length=32), nullable=False),
            sa.Column("evidence_cutoff", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("collector_id", sa.String(length=128), nullable=False),
            sa.Column("reviewer_id", sa.String(length=128), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("searches_json", sa.Text(), nullable=False),
            sa.Column("archive_checks_json", sa.Text(), nullable=False),
            sa.Column("rejection_reasons_json", sa.Text(), nullable=False),
            sa.Column("packet_hash", sa.String(length=64), nullable=False),
            sa.CheckConstraint(
                "split IN ('development', 'validation', 'test')",
                name="ck_historical_evidence_packet_split",
            ),
            sa.CheckConstraint(
                "status IN ('ready', 'no_eligible_evidence')",
                name="ck_historical_evidence_packet_status",
            ),
            sa.ForeignKeyConstraint(
                ["historical_evidence_release_id"],
                ["historical_evidence_releases.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["evaluation_release_question_id"],
                ["evaluation_release_questions.id"],
            ),
            sa.ForeignKeyConstraint(
                ["evaluation_question_id"], ["evaluation_questions.id"]
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "historical_evidence_release_id",
                "evaluation_release_question_id",
                name="uq_historical_evidence_packet_release_question",
            ),
        )

    tables = set(inspect(bind).get_table_names())
    if "historical_evidence_documents" not in tables:
        op.create_table(
            "historical_evidence_documents",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column(
                "historical_evidence_release_id", sa.String(length=36), nullable=False
            ),
            sa.Column("canonical_url", sa.Text(), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("publisher", sa.Text(), nullable=False),
            sa.Column("source_class", sa.String(length=32), nullable=False),
            sa.Column("source_kind", sa.String(length=32), nullable=False),
            sa.Column("temporal_basis", sa.String(length=32), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("source_available_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("final_capture_url", sa.Text(), nullable=True),
            sa.Column("final_capture_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_original_url", sa.Text(), nullable=True),
            sa.Column("final_capture_verified", sa.Boolean(), nullable=False),
            sa.Column("immutable_adapter_id", sa.String(length=128), nullable=True),
            sa.Column("immutable_version_id", sa.String(length=255), nullable=True),
            sa.Column("immutable_availability_verified", sa.Boolean(), nullable=False),
            sa.Column("mime_type", sa.String(length=128), nullable=False),
            sa.Column("content_sha256", sa.String(length=64), nullable=False),
            sa.Column("extracted_text_sha256", sa.String(length=64), nullable=False),
            sa.Column("byte_length", sa.Integer(), nullable=False),
            sa.Column("text_length", sa.Integer(), nullable=False),
            sa.Column("blob_locator", sa.Text(), nullable=False),
            sa.Column("text_locator", sa.Text(), nullable=False),
            sa.Column("source_license_status", sa.String(length=32), nullable=False),
            sa.Column("source_use_basis", sa.Text(), nullable=False),
            sa.Column("redistribution_allowed", sa.Boolean(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("document_hash", sa.String(length=64), nullable=False),
            sa.CheckConstraint(
                "source_class IN ('primary', 'secondary')",
                name="ck_historical_evidence_document_source_class",
            ),
            sa.CheckConstraint(
                "source_kind IN ('wayback_final_capture', 'immutable_version')",
                name="ck_historical_evidence_document_source_kind",
            ),
            sa.CheckConstraint(
                "temporal_basis IN ('snapshot_date', 'immutable_version')",
                name="ck_historical_evidence_document_temporal_basis",
            ),
            sa.ForeignKeyConstraint(
                ["historical_evidence_release_id"],
                ["historical_evidence_releases.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "historical_evidence_release_id",
                "document_hash",
                name="uq_historical_evidence_release_document_hash",
            ),
        )

    tables = set(inspect(bind).get_table_names())
    if "historical_evidence_candidates" not in tables:
        op.create_table(
            "historical_evidence_candidates",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("packet_id", sa.String(length=36), nullable=False),
            sa.Column("document_id", sa.String(length=36), nullable=True),
            sa.Column("canonical_url", sa.Text(), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column("search_query", sa.Text(), nullable=False),
            sa.Column("search_provider", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("archive_check_status", sa.String(length=128), nullable=False),
            sa.Column("candidate_hash", sa.String(length=64), nullable=False),
            sa.CheckConstraint(
                "status IN ('accepted', 'rejected')",
                name="ck_historical_evidence_candidate_status",
            ),
            sa.ForeignKeyConstraint(
                ["packet_id"], ["historical_evidence_packets.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["document_id"], ["historical_evidence_documents.id"]
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "packet_id", "rank", name="uq_historical_evidence_candidate_rank"
            ),
        )

    tables = set(inspect(bind).get_table_names())
    if "historical_evidence_packet_documents" not in tables:
        op.create_table(
            "historical_evidence_packet_documents",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("packet_id", sa.String(length=36), nullable=False),
            sa.Column("document_id", sa.String(length=36), nullable=False),
            sa.ForeignKeyConstraint(
                ["packet_id"], ["historical_evidence_packets.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["document_id"],
                ["historical_evidence_documents.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "packet_id",
                "document_id",
                name="uq_historical_evidence_packet_document",
            ),
        )

    columns = {item["name"] for item in inspect(bind).get_columns("forecast_experiments")}
    with op.batch_alter_table("forecast_experiments") as batch:
        if "historical_evidence_release_id" not in columns:
            batch.add_column(
                sa.Column(
                    "historical_evidence_release_id",
                    sa.String(length=36),
                    nullable=True,
                )
            )
            batch.create_foreign_key(
                "fk_forecast_experiments_historical_evidence_release_id",
                "historical_evidence_releases",
                ["historical_evidence_release_id"],
                ["id"],
            )
        if "historical_evidence_release_hash" not in columns:
            batch.add_column(
                sa.Column(
                    "historical_evidence_release_hash",
                    sa.String(length=64),
                    nullable=True,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "forecast_experiments" in tables:
        columns = {
            item["name"] for item in inspect(bind).get_columns("forecast_experiments")
        }
        with op.batch_alter_table("forecast_experiments") as batch:
            if "historical_evidence_release_hash" in columns:
                batch.drop_column("historical_evidence_release_hash")
            if "historical_evidence_release_id" in columns:
                batch.drop_constraint(
                    "fk_forecast_experiments_historical_evidence_release_id",
                    type_="foreignkey",
                )
                batch.drop_column("historical_evidence_release_id")
    for table in (
        "historical_evidence_packet_documents",
        "historical_evidence_candidates",
        "historical_evidence_documents",
        "historical_evidence_packets",
        "historical_evidence_releases",
    ):
        if table in set(inspect(bind).get_table_names()):
            op.drop_table(table)
