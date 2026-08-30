"""Add audited manual evidence URL attachments.

Revision ID: 20260829_0029
Revises: 20260826_0028
Create Date: 2026-08-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260829_0029"
down_revision = "20260826_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    required = {"questions", "forecast_nodes", "evidence_items"}
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError(
            "manual evidence prerequisite tables missing: " + ",".join(missing)
        )

    # V1 deliberately requires a user-initiated rerun. Normalize any legacy
    # opt-in value before the ORM guard makes the invariant permanent.
    if "watches" in tables:
        op.execute(sa.text("UPDATE watches SET auto_rerun = 0 WHERE auto_rerun = 1"))

    if "manual_evidence_attachments" not in tables:
        op.create_table(
            "manual_evidence_attachments",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("intake_key", sa.String(length=64), nullable=False),
            sa.Column("question_id", sa.String(length=36), nullable=False),
            sa.Column("target_node_id", sa.String(length=36), nullable=True),
            sa.Column("intended_use", sa.String(length=32), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("mode", sa.String(length=16), nullable=False),
            sa.Column("as_of", sa.DateTime(timezone=True), nullable=True),
            sa.Column("submitted_url", sa.Text(), nullable=False),
            sa.Column("canonical_url", sa.Text(), nullable=False),
            sa.Column("final_url", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("publisher", sa.String(length=256), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("source_available_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("temporal_basis", sa.String(length=32), nullable=False),
            sa.Column("publication_date_source", sa.String(length=128), nullable=True),
            sa.Column("publication_date_verified", sa.Boolean(), nullable=False),
            sa.Column("publication_date_hint", sa.DateTime(timezone=True), nullable=True),
            sa.Column("publication_date_hint_source", sa.String(length=128), nullable=True),
            sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("modified_date_source", sa.String(length=128), nullable=True),
            sa.Column("published_at_unknown", sa.Boolean(), nullable=False),
            sa.Column("source_class", sa.String(length=32), nullable=False),
            sa.Column("raw_content_hash", sa.String(length=64), nullable=False),
            sa.Column("extracted_text_hash", sa.String(length=64), nullable=False),
            sa.Column("extracted_text", sa.Text(), nullable=False),
            sa.Column("content_type", sa.String(length=128), nullable=True),
            sa.Column("byte_length", sa.Integer(), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("as_of_eligible", sa.Boolean(), nullable=False),
            sa.Column("requested_snapshot_url", sa.Text(), nullable=True),
            sa.Column("requested_snapshot_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("final_snapshot_url", sa.Text(), nullable=True),
            sa.Column("final_snapshot_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_original_url", sa.Text(), nullable=True),
            sa.Column("snapshot_verification_status", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "intended_use IN ('general_question_evidence', 'forecast_node')",
                name="ck_manual_evidence_intended_use",
            ),
            sa.CheckConstraint(
                "mode IN ('live', 'backtest')",
                name="ck_manual_evidence_mode",
            ),
            sa.CheckConstraint(
                "status IN ('accepted', 'rejected')",
                name="ck_manual_evidence_status",
            ),
            sa.CheckConstraint(
                "temporal_basis IN ('publication_date', 'snapshot_date', 'retrieval_date')",
                name="ck_manual_evidence_temporal_basis",
            ),
            sa.CheckConstraint(
                "(intended_use = 'forecast_node' AND target_node_id IS NOT NULL) OR "
                "(intended_use = 'general_question_evidence' AND target_node_id IS NULL)",
                name="ck_manual_evidence_target",
            ),
            sa.CheckConstraint(
                "(mode = 'backtest' AND as_of IS NOT NULL) OR "
                "(mode = 'live' AND as_of IS NULL)",
                name="ck_manual_evidence_as_of",
            ),
            sa.ForeignKeyConstraint(["question_id"], ["questions.id"]),
            sa.ForeignKeyConstraint(["target_node_id"], ["forecast_nodes.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("intake_key", name="uq_manual_evidence_intake_key"),
        )

    evidence_columns = {
        column["name"] for column in inspect(bind).get_columns("evidence_items")
    }
    if "manual_evidence_attachment_id" not in evidence_columns:
        with op.batch_alter_table("evidence_items") as batch:
            batch.add_column(
                sa.Column(
                    "manual_evidence_attachment_id",
                    sa.String(length=36),
                    nullable=True,
                )
            )
            batch.create_foreign_key(
                "fk_evidence_items_manual_evidence_attachment_id",
                "manual_evidence_attachments",
                ["manual_evidence_attachment_id"],
                ["id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "evidence_items" in tables:
        evidence_columns = {
            column["name"] for column in inspect(bind).get_columns("evidence_items")
        }
        if "manual_evidence_attachment_id" in evidence_columns:
            with op.batch_alter_table("evidence_items") as batch:
                batch.drop_constraint(
                    "fk_evidence_items_manual_evidence_attachment_id",
                    type_="foreignkey",
                )
                batch.drop_column("manual_evidence_attachment_id")
    if "manual_evidence_attachments" in tables:
        op.drop_table("manual_evidence_attachments")
